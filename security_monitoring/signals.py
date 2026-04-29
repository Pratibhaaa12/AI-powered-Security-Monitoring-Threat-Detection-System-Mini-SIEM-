from __future__ import annotations

from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.dispatch import receiver
from django.utils import timezone

from .models import SecurityEventLog


def _get_client_ip(request) -> str:
    if not request:
        return ""
    # If behind a proxy/CDN, the real client IP often comes from X-Forwarded-For.
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _get_user_agent(request) -> str:
    if not request:
        return ""
    return request.META.get("HTTP_USER_AGENT", "") or ""


def _get_country_code(request) -> str:
    # Optional (works only if your proxy/CDN sets a country header).
    if not request:
        return ""
    return (
        request.META.get("HTTP_CF_IPCOUNTRY")
        or request.META.get("HTTP_X_IP_COUNTRY")
        or request.META.get("HTTP_X_COUNTRY")
        or ""
    )


@receiver(user_login_failed)
def track_failed_login(sender, credentials, request, **kwargs):
    attempted_username = ""
    if credentials:
        attempted_username = (
            credentials.get("username")
            or credentials.get("email")
            or credentials.get("identifier")
            or ""
        )
        attempted_username = str(attempted_username or "").strip()

    ip_address = _get_client_ip(request)
    user_agent = _get_user_agent(request)
    country_code = _get_country_code(request)

    SecurityEventLog.objects.create(
        event_type="failed_login",
        attempted_username=attempted_username,
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=getattr(request, "method", "") or "",
        request_path=getattr(request, "path", "") or "",
        status_code=401,
        metadata={"country_code": country_code} if country_code else {},
    )


@receiver(user_logged_in)
def track_login_success(sender, user, request, **kwargs):
    ip_address = _get_client_ip(request)
    user_agent = _get_user_agent(request)
    country_code = _get_country_code(request)

    SecurityEventLog.objects.create(
        event_type="login_success",
        user=user,
        attempted_username=getattr(user, "username", "") or "",
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=getattr(request, "method", "") or "",
        request_path=getattr(request, "path", "") or "",
        status_code=200,
        metadata={"country_code": country_code} if country_code else {},
    )

    # Suspicious IP detection (lightweight heuristic):
    # if the same user logs in from a different IP recently, flag it.
    lookback_days = 30
    last_seen = (
        SecurityEventLog.objects.filter(
            event_type="login_success",
            user=user,
            ip_address__isnull=False,
        )
        .exclude(ip_address="")
        .order_by("-occurred_at")
        .first()
    )
    if last_seen and last_seen.ip_address and ip_address and last_seen.ip_address != ip_address:
        SecurityEventLog.objects.create(
            event_type="suspicious_ip",
            user=user,
            attempted_username=getattr(user, "username", "") or "",
            ip_address=ip_address,
            user_agent=user_agent,
            request_method=getattr(request, "method", "") or "",
            request_path=getattr(request, "path", "") or "",
            status_code=200,
            metadata={
                "previous_ip": last_seen.ip_address,
                "previous_country_code": (last_seen.metadata or {}).get("country_code", ""),
                "current_country_code": country_code,
                "lookback_days": lookback_days,
            },
        )


@receiver(user_logged_out)
def track_logout(sender, request, user, **kwargs):
    ip_address = _get_client_ip(request)
    user_agent = _get_user_agent(request)
    country_code = _get_country_code(request)

    SecurityEventLog.objects.create(
        event_type="logout",
        user=user,
        attempted_username=getattr(user, "username", "") or "",
        ip_address=ip_address,
        user_agent=user_agent,
        request_method=getattr(request, "method", "") or "",
        request_path=getattr(request, "path", "") or "",
        status_code=200,
        metadata={"country_code": country_code} if country_code else {},
    )

