from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Count
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone

from accounts.models import GitCommit
from crm_integrations.models import AlertEvent, AlertRule

from .models import SecurityEventLog


SECURITY_RULE_TYPES = {
    "brute_force_detection",
    "suspicious_commit_activity",
    "unauthorized_access_attempts",
    "abnormal_api_usage",
}


def _get_rule_threshold(rule_type: str, default: int) -> int:
    rule = (
        AlertRule.objects.filter(rule_type=rule_type, is_active=True)
        .order_by("-updated_at")
        .first()
    )
    if not rule:
        return default
    try:
        return int(rule.threshold_value or default)
    except Exception:
        return default


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


@login_required
def security_dashboard(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("You are not allowed to view the Security Dashboard.")

    lookback_hours = _safe_int(request.GET.get("lookback_hours", "24"), 24)
    since = timezone.now() - timedelta(hours=lookback_hours)

    failed_login_qs = (
        SecurityEventLog.objects.filter(event_type="failed_login", occurred_at__gte=since)
        .order_by("-occurred_at")
        .select_related("user")
    )
    failed_login_logs = [
        {
            "id": e.id,
            "time": e.occurred_at.strftime("%Y-%m-%d %H:%M:%S"),
            "user": (e.user.username if e.user else "") or e.attempted_username or "unknown",
            "attempted_username": e.attempted_username,
            "ip": e.ip_address,
            "status": e.status_code,
            "country_code": (e.metadata or {}).get("country_code", ""),
        }
        for e in failed_login_qs[:50]
    ]

    brute_force_threshold = _get_rule_threshold("brute_force_detection", 50)
    failed_by_attempt = (
        failed_login_qs.values("attempted_username", "ip_address")
        .annotate(attempts=Count("id"))
        .order_by("-attempts")[:20]
    )
    suspicious_failed = [
        {**row, "user_label": row.get("attempted_username") or "unknown"}
        for row in failed_by_attempt
        if (row.get("attempts") or 0) >= brute_force_threshold
    ]

    suspicious_ip_logs = (
        SecurityEventLog.objects.filter(event_type="suspicious_ip", occurred_at__gte=since)
        .select_related("user")
        .order_by("-occurred_at")[:20]
    )

    api_unauthorized_logs = SecurityEventLog.objects.filter(
        event_type="api_unauthorized", occurred_at__gte=since
    ).order_by("-occurred_at")[:50]
    rate_limited_logs = SecurityEventLog.objects.filter(
        event_type="rate_limited", occurred_at__gte=since
    ).order_by("-occurred_at")[:50]

    # Commit anomalies (commit spike)
    commit_spike_threshold = _get_rule_threshold("suspicious_commit_activity", 20)
    suspicious_commit_activity = []
    commits_qs = (
        GitCommit.objects.filter(date__gte=since, is_merge=False, is_revert=False)
        .exclude(user__isnull=True)
    )
    per_user_commits = (
        commits_qs.values("user_id")
        .annotate(commit_count=Count("id"))
        .order_by("-commit_count")[:30]
    )
    for row in per_user_commits:
        if (row.get("commit_count") or 0) < commit_spike_threshold:
            continue
        try:
            u = User.objects.filter(id=row["user_id"]).only("username", "first_name", "last_name").first()
        except Exception:
            u = None
        suspicious_commit_activity.append(
            {
                "user_id": row["user_id"],
                "username": u.username if u else str(row["user_id"]),
                "commit_count": row["commit_count"],
            }
        )

    alerts_triggered = (
        AlertEvent.objects.select_related("rule")
        .filter(rule__rule_type__in=list(SECURITY_RULE_TYPES))
        .order_by("-created_at")[:50]
    )

    context = {
        "lookback_hours": lookback_hours,
        "failed_login_logs": failed_login_logs,
        "suspicious_failed": suspicious_failed,
        "suspicious_ip_logs": suspicious_ip_logs,
        "api_unauthorized_logs": api_unauthorized_logs,
        "rate_limited_logs": rate_limited_logs,
        "suspicious_commit_activity": suspicious_commit_activity,
        "alerts_triggered": alerts_triggered,
        "security_rule_types": sorted(SECURITY_RULE_TYPES),
    }

    return render(request, "security_monitoring/security_dashboard.html", context)

