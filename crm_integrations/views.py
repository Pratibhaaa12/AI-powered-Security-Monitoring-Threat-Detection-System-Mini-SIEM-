import logging
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Avg
from django.shortcuts import redirect, render
from django.http import HttpResponseForbidden, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_POST
import pytz

from accounts.models import Bug, GitCommit
from crm_integrations.models import AlertEvent, AlertRule, CRMConnection, CRMContact
from crm_integrations.services import enrich_contacts_from_domains, evaluate_alert_rules, sync_dynamics_contacts

logger = logging.getLogger(__name__)


def _require_staff(user):
    return user.is_authenticated and user.is_staff


@login_required
def crm_dashboard(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to view CRM dashboard.")

    connection = CRMConnection.objects.filter(is_active=True).order_by("-updated_at").first()
    selected_org = (request.GET.get("org") or "").strip()
    selected_tz = (request.GET.get("tz") or "Asia/Kolkata").strip()
    if selected_tz not in pytz.all_timezones:
        selected_tz = "Asia/Kolkata"
    active_tz = pytz.timezone(selected_tz)
    rules = AlertRule.objects.order_by("-updated_at")
    events = AlertEvent.objects.select_related("rule").order_by("-created_at")[:30]
    for event in events:
        event.local_created_at = timezone.localtime(event.created_at, active_tz)
    contacts_count = CRMContact.objects.count()
    now = timezone.now()

    open_bugs_qs = Bug.objects.filter(status__in=["Open", "In Progress"]).select_related("repo", "assigned_to")
    if selected_org:
        open_bugs_qs = open_bugs_qs.filter(repo__org_name__iexact=selected_org)
    severity_weights = {"Low": 1, "Medium": 2, "High": 3, "Critical": 5}
    priority_items = []

    for bug in open_bugs_qs:
        age_days = max((now - bug.date_reported).days, 0)
        age_weight = 1
        if age_days >= 14:
            age_weight = 3
        elif age_days >= 7:
            age_weight = 2
        blocker_weight = 2 if bug.status == "Open" else 1
        impact_weight = severity_weights.get(bug.severity, 1)
        priority_score = impact_weight * age_weight * blocker_weight

        priority_items.append(
            {
                "title": bug.title,
                "repo_name": f"{bug.repo.org_name}/{bug.repo.repo_name}" if bug.repo else "-",
                "assignee": bug.assigned_to.username if bug.assigned_to else "Unassigned",
                "severity": bug.severity,
                "status": bug.status,
                "age_days": age_days,
                "priority_score": priority_score,
            }
        )

    priority_items.sort(key=lambda item: item["priority_score"], reverse=True)
    top_priority_items = priority_items[:20]

    total_bugs = Bug.objects.count()
    closed_bugs = Bug.objects.filter(status__in=["Resolved", "Closed"]).count()
    open_bugs = len(priority_items)
    open_bug_aging_avg = round(sum(item["age_days"] for item in priority_items) / open_bugs, 2) if open_bugs else 0
    defect_closure_rate = round((closed_bugs / total_bugs) * 100, 2) if total_bugs else 0

    recent_window = now - timedelta(days=7)
    recent_avg_commit_quality = (
        GitCommit.objects.filter(date__gte=recent_window, is_rated=True, rating__isnull=False).aggregate(avg=Avg("rating")).get("avg") or 0
    )
    recent_avg_commit_quality = round(recent_avg_commit_quality, 2)
    assignee_workload = {}
    for item in priority_items:
        assignee_workload[item["assignee"]] = assignee_workload.get(item["assignee"], 0) + 1
    assignee_workload_items = sorted(
        [{"assignee": name, "open_items": count} for name, count in assignee_workload.items()],
        key=lambda entry: entry["open_items"],
        reverse=True,
    )[:10]

    org_options = (
        Bug.objects.exclude(repo__isnull=True)
        .values_list("repo__org_name", flat=True)
        .distinct()
    )
    org_options = sorted([org for org in org_options if org])

    return render(
        request,
        "crm_integrations/dashboard.html",
        {
            "connection": connection,
            "rules": rules,
            "events": events,
            "contacts_count": contacts_count,
            "kpi_data": {
                "total_bugs": total_bugs,
                "open_bugs": open_bugs,
                "closed_bugs": closed_bugs,
                "defect_closure_rate": defect_closure_rate,
                "open_bug_aging_avg": open_bug_aging_avg,
                "recent_avg_commit_quality": recent_avg_commit_quality,
            },
            "priority_items": top_priority_items,
            "assignee_workload_items": assignee_workload_items,
            "org_options": org_options,
            "selected_org": selected_org,
            "selected_tz": selected_tz,
            "timezone_options": ["Asia/Kolkata", "UTC", "Europe/London", "America/New_York", "Asia/Singapore"],
        },
    )


@login_required
@require_POST
def save_dynamics_connection(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to update CRM settings.")

    tenant_id = (request.POST.get("tenant_id") or "").strip()
    client_id = (request.POST.get("client_id") or "").strip()
    client_secret = (request.POST.get("client_secret") or "").strip()
    base_url = (request.POST.get("base_url") or "").strip()
    scope = (request.POST.get("scope") or "").strip()

    if not all([tenant_id, client_id, client_secret, base_url]):
        return JsonResponse({"success": False, "error": "tenant_id, client_id, client_secret, and base_url are required."}, status=400)

    connection, _ = CRMConnection.objects.update_or_create(
        provider="dynamics",
        defaults={
            "tenant_id": tenant_id,
            "client_id": client_id,
            "client_secret": client_secret,
            "base_url": base_url,
            "scope": scope,
            "is_active": True,
        },
    )
    return JsonResponse({"success": True, "connection_id": connection.id})


@login_required
@require_POST
def create_alert_rule(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to create alert rules.")

    name = (request.POST.get("name") or "").strip()
    rule_type = (request.POST.get("rule_type") or "").strip()
    threshold = request.POST.get("threshold_value", "3")
    recipients_raw = (request.POST.get("recipients") or "").strip()
    lookback_hours = request.POST.get("lookback_hours", "24")

    if not name or not rule_type:
        return JsonResponse({"success": False, "error": "name and rule_type are required."}, status=400)

    try:
        threshold_value = int(threshold)
    except ValueError:
        return JsonResponse({"success": False, "error": "threshold_value must be a number."}, status=400)

    recipients = [r.strip() for r in recipients_raw.split(",") if r.strip()]
    config = {}
    if rule_type in {"low_commit_quality", "brute_force_detection", "suspicious_commit_activity", "unauthorized_access_attempts", "abnormal_api_usage"}:
        try:
            config["lookback_hours"] = max(1, int(lookback_hours))
        except ValueError:
            config["lookback_hours"] = 24

    AlertRule.objects.create(
        name=name,
        rule_type=rule_type,
        threshold_value=max(1, threshold_value),
        recipients=recipients,
        config=config,
        is_active=True,
    )

    return redirect("crm_dashboard")


@login_required
@require_POST
def sync_contacts(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to trigger CRM sync.")

    limit = request.POST.get("limit", 100)
    try:
        synced = sync_dynamics_contacts(limit=int(limit))
        return JsonResponse({"success": True, "synced_contacts": synced})
    except Exception as exc:
        logger.exception("Dynamics sync failed")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@login_required
@require_POST
def enrich_contacts(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to run enrichment.")
    limit = request.POST.get("limit", 100)
    try:
        enriched = enrich_contacts_from_domains(limit=int(limit))
        return JsonResponse({"success": True, "enriched_contacts": enriched})
    except Exception as exc:
        logger.exception("Contact enrichment failed")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)


@login_required
@require_POST
def run_alerts(request):
    if not _require_staff(request.user):
        return HttpResponseForbidden("You are not allowed to run alerts.")
    try:
        created = evaluate_alert_rules()
        return JsonResponse({"success": True, "events_created": created})
    except Exception as exc:
        logger.exception("Alert evaluation failed")
        return JsonResponse({"success": False, "error": str(exc)}, status=500)
