import logging
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils.timezone import now

from timeero.services import fetch_timeero_and_save
from accounts.models import ScriptRunLog

logger = logging.getLogger(__name__)
class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=str,
            required=False,
            help="Start date in YYYY-MM-DD (default: yesterday)"
        )
        parser.add_argument(
            "--end",
            type=str,
            required=False,
            help="End date in YYYY-MM-DD (default: yesterday)"
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug logging"
        )
    def handle(self, *args, **kwargs):
        start = kwargs.get("start")
        end = kwargs.get("end")
        debug = kwargs.get("debug")
        if not start or not end:
            yesterday = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
            start = start or yesterday
            end = end or yesterday
        if debug:
            logger.setLevel(logging.DEBUG)
            self.stdout.write(self.style.WARNING("🔍 DEBUG MODE ENABLED"))
            logger.debug(f"Start date received: {start}")
            logger.debug(f"End date received: {end}")
        try:
            datetime.strptime(start, "%Y-%m-%d")
            datetime.strptime(end, "%Y-%m-%d")
        except ValueError:
            self.stderr.write(self.style.ERROR("Invalid date. Format must be YYYY-MM-DD"))
            return

        log, _ = ScriptRunLog.objects.get_or_create(name="save_timeero_data")
        log.run_count += 1
        log.last_run_at = now()
        log.logs = (
            f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Run count: {log.run_count}\n"
            f"Date range: {start} to {end}"
        )
        log.save()

        self.stdout.write(self.style.WARNING(f"Fetching Timeero data from {start} to {end}…"))
        try:
            saved = fetch_timeero_and_save(start, end, debug=debug)
        except Exception as e:
            logger.exception("Failed to save Timeero data")
            self.stderr.write(self.style.ERROR(f"Error: {e}"))

            log.logs = (log.logs or "") + f"\nFAILED with error: {str(e)}"
            log.last_run_at = now()
            log.save()

            return
        if debug:
            logger.debug(f"Total records saved: {saved}")

        self.stdout.write(self.style.SUCCESS(f"Saved {saved} Timeero timesheet records."))

        log.logs = (log.logs or "") + (
            f"\n=== EXECUTION SUMMARY ==="
            f"\nRecords saved: {saved}"
            f"\nExecution completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        log.last_run_at = now()
        log.save()
