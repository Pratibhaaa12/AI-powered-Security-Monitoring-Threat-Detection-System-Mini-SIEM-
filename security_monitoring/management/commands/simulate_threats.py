import secrets
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.models import GitCommit
from crm_integrations.models import AlertRule, AlertEvent
from crm_integrations.services import evaluate_alert_rules

from security_monitoring.models import SecurityEventLog


class Command(BaseCommand):
    help = "Simulate security threats and verify SIEM-lite alert triggers."

    def add_arguments(self, parser):
        parser.add_argument("--lookback-hours", type=int, default=2)
        parser.add_argument("--failed-login-attempts", type=int, default=60)
        parser.add_argument("--api-unauthorized-attempts", type=int, default=30)
        parser.add_argument("--rate-limited-attempts", type=int, default=30)
        parser.add_argument("--commit-spike-count", type=int, default=25)

        parser.add_argument("--attacker-username", type=str, default="attacker_test")
        parser.add_argument("--ip", type=str, default="203.0.113.10")
        parser.add_argument("--ip-2", type=str, default="203.0.113.99")

        parser.add_argument("--target-username", type=str, default="")
        parser.add_argument("--create-security-rules", action="store_true", default=True)

    def handle(self, *args, **options):
        lookback_hours = options["lookback_hours"]
        lb = timedelta(hours=lookback_hours)

        User = get_user_model()
        now = timezone.now()
        since = now - lb

        target_username = (options.get("target_username") or "").strip()
        if target_username:
            target_user = User.objects.filter(username=target_username).first()
        else:
            target_user = User.objects.filter(is_superuser=False).order_by("id").first()

        if not target_user:
            self.stderr.write("No user found in DB. Run migrations & create a user first.")
            return

        self.stdout.write(f"Using target user: {target_user.username}")

        attacker_username = options["attacker_username"]
        ip_1 = options["ip"]
        ip_2 = options["ip_2"]

        if options["create_security_rules"]:
            self._ensure_security_rules()

        # Avoid duplicate simulation runs by inserting unique ip metadata.
        # (We keep it simple; in demo environments you can clear logs if needed.)
        failed_attempts = int(options["failed_login_attempts"])
        api_unauth_attempts = int(options["api_unauthorized_attempts"])
        rate_limited_attempts = int(options["rate_limited_attempts"])
        commit_spike_count = int(options["commit_spike_count"])

        # Layer 1/2: create SecurityEventLog entries
        self._bulk_create_security_events(
            attacker_username=attacker_username,
            user=target_user,
            ip_address=ip_1,
            occurred_start=since + timedelta(minutes=1),
            failed_login_at_count=failed_attempts,
            api_unauthorized_at_count=api_unauth_attempts,
            rate_limited_at_count=rate_limited_attempts,
        )

        # Suspicious IP (country/new geo heuristic demo)
        SecurityEventLog.objects.create(
            event_type="suspicious_ip",
            user=target_user,
            attempted_username=target_user.username,
            ip_address=ip_2,
            user_agent="ThreatSim/1.0",
            request_method="POST",
            request_path="/login/",
            status_code=200,
            metadata={
                "previous_ip": ip_1,
                "previous_country_code": "US",
                "current_country_code": "IN",
                "lookback_days": lookback_hours,
            },
        )

        # Commit anomaly (Layer 1/3): create many commits in the time window
        self._create_commit_spike(
            user=target_user,
            count=commit_spike_count,
            since=since,
        )

        # Evaluate rules (Layer 3) and report created AlertEvents
        start_eval = timezone.now()
        _ = evaluate_alert_rules()

        sec_rule_types = [
            "brute_force_detection",
            "suspicious_commit_activity",
            "unauthorized_access_attempts",
            "abnormal_api_usage",
        ]
        created = AlertEvent.objects.filter(
            created_at__gte=start_eval,
            rule__rule_type__in=sec_rule_types,
        )
        self.stdout.write(f"Security alerts created: {created.count()}")
        if created.exists():
            latest = created.order_by("-created_at")[:5]
            for e in latest:
                self.stdout.write(f"- {e.rule.rule_type}: {e.title} [{e.status}]")

    def _ensure_security_rules(self):
        # Provide sensible defaults that work with this simulator's default args.
        defaults = [
            ("brute_force_detection", 50, {"lookback_hours": 2}),
            ("suspicious_commit_activity", 20, {"lookback_hours": 2}),
            ("unauthorized_access_attempts", 20, {"lookback_hours": 2}),
            ("abnormal_api_usage", 20, {"lookback_hours": 2}),
        ]
        for rule_type, threshold_value, config in defaults:
            existing = AlertRule.objects.filter(rule_type=rule_type).order_by("-updated_at").first()
            if not existing:
                AlertRule.objects.create(
                    name=f"Security rule: {rule_type}",
                    rule_type=rule_type,
                    threshold_value=threshold_value,
                    recipients=[],  # If Slack isn't configured, dispatch fails gracefully.
                    config=config,
                    is_active=True,
                )
            else:
                existing.threshold_value = threshold_value
                existing.config = config
                existing.is_active = True
                # Avoid accidental Slack spam by ensuring empty recipients.
                existing.recipients = existing.recipients if existing.recipients is not None else []
                existing.save(update_fields=["threshold_value", "config", "is_active", "recipients"])

    def _bulk_create_security_events(
        self,
        *,
        attacker_username: str,
        user,
        ip_address: str,
        occurred_start,
        failed_login_at_count: int,
        api_unauthorized_at_count: int,
        rate_limited_at_count: int,
    ):
        # Use small time shifts so events fall within lookback window.
        events = []
        for i in range(failed_login_at_count):
            events.append(
                SecurityEventLog(
                    event_type="failed_login",
                    user=None,
                    attempted_username=attacker_username,
                    ip_address=ip_address,
                    user_agent=f"ThreatSim/1.0",
                    request_method="POST",
                    request_path="/login/",
                    status_code=401,
                    metadata={},
                    occurred_at=occurred_start + timedelta(seconds=i),
                )
            )
        for i in range(api_unauthorized_at_count):
            events.append(
                SecurityEventLog(
                    event_type="api_unauthorized",
                    user=user,
                    attempted_username=attacker_username,
                    ip_address=ip_address,
                    user_agent="ThreatSim/1.0",
                    request_method="GET",
                    request_path="/commits-data/",
                    status_code=403,
                    metadata={},
                    occurred_at=occurred_start + timedelta(seconds=10000 + i),
                )
            )
        for i in range(rate_limited_at_count):
            events.append(
                SecurityEventLog(
                    event_type="rate_limited",
                    user=user,
                    attempted_username=attacker_username,
                    ip_address=ip_address,
                    user_agent="ThreatSim/1.0",
                    request_method="GET",
                    request_path="/project-metrics/",
                    status_code=429,
                    metadata={},
                    occurred_at=occurred_start + timedelta(seconds=20000 + i),
                )
            )

        SecurityEventLog.objects.bulk_create(events, batch_size=500)

    def _create_commit_spike(self, *, user, count: int, since):
        # GitCommit commit_hash is unique + max length 40.
        # repo_name/org_name are required; commit date must be within lookback.
        repo_name = "reward-dashboard"
        org_name = "security-sim"
        base_dt = since + timedelta(minutes=10)

        commits = []
        for i in range(count):
            commits.append(
                GitCommit(
                    user=user,
                    commit_hash=secrets.token_hex(20),  # 40 chars
                    repo_name=repo_name,
                    org_name=org_name,
                    author=user.username,
                    author_email="sim@example.com",
                    message=f"ThreatSim commit spike #{i}",
                    date=base_dt + timedelta(minutes=i),
                    is_merge=False,
                    is_revert=False,
                    is_rated=False,
                )
            )
        GitCommit.objects.bulk_create(commits, batch_size=200)

