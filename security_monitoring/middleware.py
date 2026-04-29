from __future__ import annotations

from typing import Optional

from django.http import HttpResponse

from .models import SecurityEventLog


def _get_client_ip(request) -> str:
    if not request:
        return ""
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or ""


def _should_log_security_event(request, response: HttpResponse) -> bool:
    content_type = (response.headers.get("Content-Type") or "").lower()
    # Log auth/rate limit problems & JSON API-like responses.
    if response.status_code in (401, 403):
        return True
    if response.status_code == 429:
        return True
    if content_type.startswith("application/json"):
        # Avoid logging for simple GET pages that return JSON "graph" data constantly,
        # but still capture abnormal patterns (401/403/429 already handled above).
        return request.method in ("POST", "PUT", "PATCH", "DELETE")
    return False


def _log_event(event_type: str, *, request, response: HttpResponse, status_code: Optional[int] = None) -> None:
    SecurityEventLog.objects.create(
        event_type=event_type,
        user=getattr(request, "user", None) if getattr(request, "user", None) and getattr(request.user, "is_authenticated", False) else None,
        attempted_username="",
        ip_address=_get_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT", "") or ""),
        request_method=request.method,
        request_path=request.path,
        status_code=status_code if status_code is not None else response.status_code,
        metadata={},
    )


class SecurityMonitoringMiddleware:
    """
    Layer 2: Security logging & audit events.
    - Failed auth / forbidden / rate-limit responses
    - Admin actions (staff-only POST to admin/bare mutation endpoints)
    - API call abuse signals via response codes
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Admin actions
        try:
            if (
                getattr(request, "user", None)
                and request.user.is_authenticated
                and request.user.is_staff
                and request.method == "POST"
                and (
                    request.path.startswith("/admin/")
                    or request.path.startswith("/crm/")
                    or "/bugs/add/" in request.path
                    or "/bugs/edit/" in request.path
                    or request.path.startswith("/reports/")
                )
            ):
                _log_event("admin_action", request=request, response=response)
        except Exception:
            # Never break request processing due to monitoring.
            pass

        # Auth/API problems
        try:
            if _should_log_security_event(request, response):
                if response.status_code == 429:
                    _log_event("rate_limited", request=request, response=response, status_code=response.status_code)
                elif response.status_code in (401, 403):
                    # Treat as "unauthorized access attempts" in the SOC-lite sense.
                    _log_event("api_unauthorized", request=request, response=response, status_code=response.status_code)
                else:
                    _log_event("api_call", request=request, response=response, status_code=response.status_code)
        except Exception:
            pass

        return response

