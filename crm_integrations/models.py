from django.db import models
from django.utils import timezone


class CRMConnection(models.Model):
    PROVIDER_CHOICES = [
        ("dynamics", "Microsoft Dynamics 365"),
    ]

    provider = models.CharField(max_length=50, choices=PROVIDER_CHOICES, default="dynamics")
    tenant_id = models.CharField(max_length=255)
    client_id = models.CharField(max_length=255)
    client_secret = models.TextField()
    base_url = models.URLField(help_text="Example: https://<org>.crm.dynamics.com")
    scope = models.CharField(max_length=255, default="")

    access_token = models.TextField(blank=True, null=True)
    refresh_token = models.TextField(blank=True, null=True)
    token_expires_at = models.DateTimeField(blank=True, null=True)

    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    last_error = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.get_provider_display()} ({self.base_url})"

    @property
    def is_token_valid(self):
        if not self.access_token or not self.token_expires_at:
            return False
        return self.token_expires_at > timezone.now()


class CRMContact(models.Model):
    external_id = models.CharField(max_length=255, unique=True)
    full_name = models.CharField(max_length=255, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=100, blank=True, null=True)
    owner_name = models.CharField(max_length=255, blank=True, null=True)
    raw_data = models.JSONField(default=dict, blank=True)
    updated_from_crm_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.full_name or self.email or self.external_id


class AlertRule(models.Model):
    RULE_CHOICES = [
        ("stale_open_bugs", "Stale Open Bugs"),
        ("low_commit_quality", "Low Commit Quality"),
        ("brute_force_detection", "Brute Force Detection"),
        ("suspicious_commit_activity", "Suspicious Commit Activity"),
        ("unauthorized_access_attempts", "Unauthorized Access Attempts"),
        ("abnormal_api_usage", "Abnormal API Usage"),
    ]

    name = models.CharField(max_length=255)
    rule_type = models.CharField(max_length=100, choices=RULE_CHOICES)
    is_active = models.BooleanField(default=True)
    threshold_value = models.IntegerField(default=3)
    recipients = models.JSONField(default=list, blank=True, help_text="Slack channels, emails, or usernames.")
    config = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.rule_type})"


class AlertEvent(models.Model):
    rule = models.ForeignKey(AlertRule, on_delete=models.SET_NULL, null=True, related_name="events")
    title = models.CharField(max_length=255)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=30, default="created")
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"{self.title} [{self.status}]"
