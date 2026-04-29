import logging
from datetime import timedelta

import requests
from django.db.models import Count
from django.db import transaction
from django.utils import timezone

from accounts.models import Bug, GitCommit
from crm_integrations.models import AlertEvent, AlertRule, CRMConnection, CRMContact
from security_monitoring.models import SecurityEventLog
from django.conf import settings
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)


def _token_endpoint(tenant_id):
    return f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"


def refresh_dynamics_access_token(connection):
    scope = connection.scope.strip() or f"{connection.base_url}/.default"
    data = {
        "grant_type": "client_credentials",
        "client_id": connection.client_id,
        "client_secret": connection.client_secret,
        "scope": scope,
    }
    response = requests.post(_token_endpoint(connection.tenant_id), data=data, timeout=20)
    response.raise_for_status()
    payload = response.json()

    connection.access_token = payload.get("access_token")
    expires_in = int(payload.get("expires_in", 3600))
    connection.token_expires_at = timezone.now() + timedelta(seconds=max(expires_in - 120, 60))
    connection.last_error = ""
    connection.save(update_fields=["access_token", "token_expires_at", "last_error", "updated_at"])
    return connection.access_token


def get_connection():
    return CRMConnection.objects.filter(is_active=True).order_by("-updated_at").first()


def get_valid_access_token(connection):
    if connection.is_token_valid:
        return connection.access_token
    return refresh_dynamics_access_token(connection)


def sync_dynamics_contacts(limit=100):
    connection = get_connection()
    if not connection:
        raise ValueError("No active CRM connection found.")

    token = get_valid_access_token(connection)
    url = f"{connection.base_url.rstrip('/')}/api/data/v9.2/contacts"
    params = {"$top": max(1, min(int(limit), 1000))}
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    response = requests.get(url, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    contacts = data.get("value", [])

    synced = 0
    with transaction.atomic():
        for contact in contacts:
            external_id = contact.get("contactid")
            if not external_id:
                continue
            CRMContact.objects.update_or_create(
                external_id=external_id,
                defaults={
                    "full_name": contact.get("fullname"),
                    "email": contact.get("emailaddress1"),
                    "phone": contact.get("mobilephone") or contact.get("telephone1"),
                    "owner_name": ((contact.get("_ownerid_value@OData.Community.Display.V1.FormattedValue")) or ""),
                    "raw_data": contact,
                    "updated_from_crm_at": timezone.now(),
                },
            )
            synced += 1

    connection.last_synced_at = timezone.now()
    connection.last_error = ""
    connection.save(update_fields=["last_synced_at", "last_error", "updated_at"])
    return synced


def enrich_contacts_from_domains(limit=100):
    """
    Lightweight enrichment using email domain metadata.
    If CLEARBIT_API_KEY is available, it attempts company enrichment;
    otherwise it still enriches basic domain/company guess locally.
    """
    contacts = CRMContact.objects.filter(email__isnull=False).exclude(email="").order_by("-updated_at")[: max(1, min(int(limit), 1000))]
    clearbit_key = getattr(settings, "CLEARBIT_API_KEY", None)
    enriched = 0

    for contact in contacts:
        email = (contact.email or "").strip().lower()
        if "@" not in email:
            continue
        domain = email.split("@", 1)[1]
        raw_data = dict(contact.raw_data or {})
        enrichment = dict(raw_data.get("enrichment", {}))
        enrichment["domain"] = domain
        enrichment["company_guess"] = domain.split(".")[0].replace("-", " ").title()

        if clearbit_key:
            try:
                response = requests.get(
                    "https://company.clearbit.com/v2/companies/find",
                    params={"domain": domain},
                    headers={"Authorization": f"Bearer {clearbit_key}"},
                    timeout=10,
                )
                if response.status_code == 200:
                    payload = response.json()
                    enrichment["company_name"] = payload.get("name")
                    enrichment["company_category"] = (payload.get("category") or {}).get("industry")
                    enrichment["company_employees"] = payload.get("metrics", {}).get("employees")
            except Exception as exc:
                logger.warning("Clearbit enrichment failed for %s: %s", domain, exc)

        raw_data["enrichment"] = enrichment
        contact.raw_data = raw_data
        contact.save(update_fields=["raw_data", "updated_at"])
        enriched += 1
    return enriched


def _send_slack_message(text, channel_override=None):
    token = getattr(settings, "SLACK_BOT_TOKEN", None)
    channel = channel_override or getattr(settings, "SLACK_CHANNEL_ID", None)
    if not token or not channel:
        return False, "Missing Slack token/channel"
    try:
        WebClient(token=token).chat_postMessage(channel=channel, text=text)
        return True, ""
    except SlackApiError as exc:
        return False, str(exc.response.get("error"))


def evaluate_alert_rules():
    created_events = 0
    rules = AlertRule.objects.filter(is_active=True)
    now = timezone.now()
    security_rule_max_events_per_rule = 5

    for rule in rules:
        if rule.rule_type == "stale_open_bugs":
            days = max(1, int(rule.threshold_value or 3))
            cutoff = now - timedelta(days=days)
            stale_count = Bug.objects.filter(status__in=["Open", "In Progress"], date_reported__lt=cutoff).count()
            if stale_count > 0:
                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"{stale_count} stale bugs older than {days} days",
                    payload={"stale_bug_count": stale_count, "threshold_days": days},
                )
                _dispatch_event(event, rule)
                created_events += 1

        elif rule.rule_type == "low_commit_quality":
            hours = int(rule.config.get("lookback_hours", 24))
            lookback = now - timedelta(hours=max(1, hours))
            threshold = int(rule.threshold_value or 3)
            low_count = GitCommit.objects.filter(
                date__gte=lookback,
                is_rated=True,
                rating__isnull=False,
                rating__lt=threshold,
            ).count()
            if low_count > 0:
                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"{low_count} low-quality commits in last {hours}h",
                    payload={"low_commit_count": low_count, "threshold_rating": threshold, "lookback_hours": hours},
                )
                _dispatch_event(event, rule)
                created_events += 1

        # -----------------------------
        # Security “SIEM-lite” rules
        # -----------------------------
        elif rule.rule_type == "brute_force_detection":
            lookback_hours = int(rule.config.get("lookback_hours", 1))
            cutoff = now - timedelta(hours=max(1, lookback_hours))
            threshold = int(rule.threshold_value or 50)

            failed_qs = SecurityEventLog.objects.filter(
                event_type="failed_login", occurred_at__gte=cutoff
            )

            offender_rows = (
                failed_qs.values("attempted_username", "ip_address", "user_id")
                .annotate(attempts=Count("id"))
                .order_by("-attempts")[:50]
            )

            dispatched = 0
            for row in offender_rows:
                attempts = int(row.get("attempts") or 0)
                if attempts < threshold:
                    continue

                user_label = row.get("attempted_username") or str(row.get("user_id") or "unknown")
                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"Brute force: {user_label} ({attempts} failed logins in {lookback_hours}h)",
                    payload={
                        "user_label": user_label,
                        "attempts": attempts,
                        "ip_address": row.get("ip_address", ""),
                        "lookback_hours": lookback_hours,
                    },
                )
                _dispatch_event(event, rule)
                created_events += 1
                dispatched += 1
                if dispatched >= security_rule_max_events_per_rule:
                    break

        elif rule.rule_type == "suspicious_commit_activity":
            lookback_hours = int(rule.config.get("lookback_hours", 24))
            cutoff = now - timedelta(hours=max(1, lookback_hours))
            threshold = int(rule.threshold_value or 20)

            commits_qs = GitCommit.objects.filter(
                date__gte=cutoff,
                is_merge=False,
                is_revert=False,
                user__isnull=False,
            )

            offender_rows = (
                commits_qs.values("user_id")
                .annotate(commit_count=Count("id"))
                .order_by("-commit_count")[:50]
            )

            dispatched = 0
            for row in offender_rows:
                commit_count = int(row.get("commit_count") or 0)
                if commit_count < threshold:
                    continue

                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"Commit spike: user_id={row['user_id']} ({commit_count} commits in {lookback_hours}h)",
                    payload={
                        "user_id": row["user_id"],
                        "commit_count": commit_count,
                        "lookback_hours": lookback_hours,
                    },
                )
                _dispatch_event(event, rule)
                created_events += 1
                dispatched += 1
                if dispatched >= security_rule_max_events_per_rule:
                    break

        elif rule.rule_type == "unauthorized_access_attempts":
            lookback_hours = int(rule.config.get("lookback_hours", 1))
            cutoff = now - timedelta(hours=max(1, lookback_hours))
            threshold = int(rule.threshold_value or 20)

            unauth_qs = SecurityEventLog.objects.filter(
                event_type__in=["api_unauthorized", "suspicious_ip"],
                occurred_at__gte=cutoff,
            )

            offender_rows = (
                unauth_qs.values("ip_address")
                .annotate(attempts=Count("id"))
                .order_by("-attempts")[:50]
            )

            dispatched = 0
            for row in offender_rows:
                attempts = int(row.get("attempts") or 0)
                if attempts < threshold:
                    continue

                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"Unauthorized access: {row.get('ip_address', '')} ({attempts} events in {lookback_hours}h)",
                    payload={
                        "ip_address": row.get("ip_address", ""),
                        "attempts": attempts,
                        "lookback_hours": lookback_hours,
                    },
                )
                _dispatch_event(event, rule)
                created_events += 1
                dispatched += 1
                if dispatched >= security_rule_max_events_per_rule:
                    break

        elif rule.rule_type == "abnormal_api_usage":
            lookback_hours = int(rule.config.get("lookback_hours", 1))
            cutoff = now - timedelta(hours=max(1, lookback_hours))
            threshold = int(rule.threshold_value or 20)

            rate_limited_qs = SecurityEventLog.objects.filter(
                event_type="rate_limited", occurred_at__gte=cutoff
            )

            offender_rows = (
                rate_limited_qs.values("ip_address")
                .annotate(attempts=Count("id"))
                .order_by("-attempts")[:50]
            )

            dispatched = 0
            for row in offender_rows:
                attempts = int(row.get("attempts") or 0)
                if attempts < threshold:
                    continue

                event = AlertEvent.objects.create(
                    rule=rule,
                    title=f"Abnormal API usage (429): {row.get('ip_address', '')} ({attempts} in {lookback_hours}h)",
                    payload={
                        "ip_address": row.get("ip_address", ""),
                        "attempts": attempts,
                        "lookback_hours": lookback_hours,
                    },
                )
                _dispatch_event(event, rule)
                created_events += 1
                dispatched += 1
                if dispatched >= security_rule_max_events_per_rule:
                    break

    return created_events


def _dispatch_event(event, rule):
    recipients = rule.recipients if isinstance(rule.recipients, list) else []
    message = f"Alert: {event.title}\nPayload: {event.payload}"
    sent = False
    errors = []

    if recipients:
        for channel in recipients:
            ok, error = _send_slack_message(message, channel_override=channel)
            sent = sent or ok
            if error:
                errors.append(error)
    else:
        ok, error = _send_slack_message(message)
        sent = sent or ok
        if error:
            errors.append(error)

    event.status = "sent" if sent else "failed"
    event.sent_at = timezone.now() if sent else None
    if errors:
        event.payload = {**event.payload, "dispatch_errors": errors}
    event.save(update_fields=["status", "sent_at", "payload"])
