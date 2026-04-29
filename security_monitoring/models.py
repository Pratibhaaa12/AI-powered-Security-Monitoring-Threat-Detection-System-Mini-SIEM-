from django.conf import settings
from django.db import models


class SecurityEventLog(models.Model):
    EVENT_TYPE_CHOICES = [
        ("failed_login", "Failed login"),
        ("login_success", "Login success"),
        ("logout", "Logout"),
        ("api_call", "API call"),
        ("api_unauthorized", "API unauthorized"),
        ("rate_limited", "Rate limited"),
        ("admin_action", "Admin action"),
        ("suspicious_ip", "Suspicious IP / login"),
        ("commit_anomaly", "Commit anomaly"),
    ]

    event_type = models.CharField(max_length=50, choices=EVENT_TYPE_CHOICES, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="security_events",
    )
    attempted_username = models.CharField(max_length=255, blank=True, default="", db_index=True)

    # Stored as CharField to support proxies / forwarded IP formats.
    ip_address = models.CharField(max_length=64, blank=True, default="", db_index=True)
    user_agent = models.TextField(blank=True, default="")

    request_method = models.CharField(max_length=10, blank=True, default="")
    request_path = models.CharField(max_length=500, blank=True, default="")
    status_code = models.IntegerField(null=True, blank=True)
    occurred_at = models.DateTimeField(auto_now_add=True, db_index=True)

    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["event_type", "occurred_at"]),
            models.Index(fields=["ip_address", "occurred_at"]),
        ]

    def __str__(self) -> str:
        who = self.user_id or self.attempted_username or "unknown"
        return f"{self.event_type} ({who}) @ {self.occurred_at.isoformat()}"

