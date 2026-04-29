from django.core.management.base import BaseCommand

from crm_integrations.services import evaluate_alert_rules


class Command(BaseCommand):
    help = "Evaluate CRM/workflow alert rules and dispatch notifications."

    def handle(self, *args, **options):
        created = evaluate_alert_rules()
        self.stdout.write(self.style.SUCCESS(f"Alert evaluation complete. Events created: {created}"))
