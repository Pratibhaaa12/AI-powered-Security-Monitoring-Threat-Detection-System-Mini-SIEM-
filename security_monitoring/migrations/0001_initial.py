from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="SecurityEventLog",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "event_type",
                    models.CharField(db_index=True, max_length=50, choices=[
                        ("failed_login", "Failed login"),
                        ("login_success", "Login success"),
                        ("logout", "Logout"),
                        ("api_call", "API call"),
                        ("api_unauthorized", "API unauthorized"),
                        ("rate_limited", "Rate limited"),
                        ("admin_action", "Admin action"),
                        ("suspicious_ip", "Suspicious IP / login"),
                        ("commit_anomaly", "Commit anomaly"),
                    ]),
                ),
                (
                    "attempted_username",
                    models.CharField(db_index=True, default="", max_length=255, blank=True),
                ),
                (
                    "ip_address",
                    models.CharField(db_index=True, default="", max_length=64, blank=True),
                ),
                ("user_agent", models.TextField(default="", blank=True)),
                ("request_method", models.CharField(default="", max_length=10, blank=True)),
                ("request_path", models.CharField(default="", max_length=500, blank=True)),
                ("status_code", models.IntegerField(blank=True, null=True)),
                ("occurred_at", models.DateTimeField(db_index=True, auto_now_add=True)),
                ("metadata", models.JSONField(default=dict, blank=True)),
                (
                    "user",
                    models.ForeignKey(
                        related_name="security_events",
                        null=True,
                        blank=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(fields=["event_type", "occurred_at"], name="security_ev_log_event_type_1b5e1a_idx"),
                    models.Index(fields=["ip_address", "occurred_at"], name="security_ev_log_ip_ad2c2b_idx"),
                ],
            },
        ),
    ]

