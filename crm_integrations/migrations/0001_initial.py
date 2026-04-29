from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="AlertRule",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("rule_type", models.CharField(choices=[("stale_open_bugs", "Stale Open Bugs"), ("low_commit_quality", "Low Commit Quality")], max_length=100)),
                ("is_active", models.BooleanField(default=True)),
                ("threshold_value", models.IntegerField(default=3)),
                ("recipients", models.JSONField(blank=True, default=list, help_text="Slack channels, emails, or usernames.")),
                ("config", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CRMConnection",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("provider", models.CharField(choices=[("dynamics", "Microsoft Dynamics 365")], default="dynamics", max_length=50)),
                ("tenant_id", models.CharField(max_length=255)),
                ("client_id", models.CharField(max_length=255)),
                ("client_secret", models.TextField()),
                ("base_url", models.URLField(help_text="Example: https://<org>.crm.dynamics.com")),
                ("scope", models.CharField(default="", max_length=255)),
                ("access_token", models.TextField(blank=True, null=True)),
                ("refresh_token", models.TextField(blank=True, null=True)),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("is_active", models.BooleanField(default=True)),
                ("last_synced_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="CRMContact",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("external_id", models.CharField(max_length=255, unique=True)),
                ("full_name", models.CharField(blank=True, max_length=255, null=True)),
                ("email", models.EmailField(blank=True, max_length=254, null=True)),
                ("phone", models.CharField(blank=True, max_length=100, null=True)),
                ("owner_name", models.CharField(blank=True, max_length=255, null=True)),
                ("raw_data", models.JSONField(blank=True, default=dict)),
                ("updated_from_crm_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.CreateModel(
            name="AlertEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(default="created", max_length=30)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("sent_at", models.DateTimeField(blank=True, null=True)),
                ("rule", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="events", to="crm_integrations.alertrule")),
            ],
        ),
    ]
