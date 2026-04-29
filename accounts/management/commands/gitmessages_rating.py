import logging
import re
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils.timezone import now, make_aware
from django.db import transaction
from openai import OpenAI
from django.conf import settings
from accounts.models import GitCommit, Prompt, ScriptRunLog, FileChange


logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)

def get_ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

class Command(BaseCommand):
    help = "Rates Git commits using GPT and stores results in the Prompt table."

    def add_arguments(self, parser):
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (bypass time restrictions)',
        )



logger = logging.getLogger(__name__)

def get_ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)

class Command(BaseCommand):
    help = "Rates Git commits using GPT and stores results in the Prompt table."

    def add_arguments(self, parser):
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (bypass time restrictions)',
        )
        parser.add_argument(
            '--user_id',
            type=int,
            help='Rate commits for a specific user ID only',
        )
        parser.add_argument(
            '--date',
            type=str,
            help='Rate commits for a specific date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Start date for date range (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='End date for date range (YYYY-MM-DD)',
        )

    def handle(self, *args, **kwargs):
        rated_count = 0
        print("Running scheduled GPT commit rating job...")

        now_ist = get_ist_now()
        logger.info(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")
        print(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")

        debug_mode = kwargs.get('debug', False)
        if debug_mode:
            logger.info("Running in DEBUG mode")
            print("Running in DEBUG mode")

        # Track script run
        log, _ = ScriptRunLog.objects.get_or_create(name="gitmessages_rating")
        log.run_count += 1
        log.last_run_at = now()
        log.logs = f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}\nRun count: {log.run_count}"
        log.save()

        # Get latest prompt (optional: can be passed to rate_with_gpt if you modify the method)
        latest_prompt = (
            Prompt.objects.filter(type="commit_rating")
            .exclude(created_date__isnull=True)
            .order_by("-created_date", "-id")
            .first()
        )

        if not latest_prompt:
            print("No active prompt found for type='commit_rating'")
            logger.warning("No active prompt found for type='commit_rating'")
            return
        
       # Determine target date (default: yesterday)
        target_date = (now() - timedelta(days=1)).date()
        start_date = None
        end_date = None

        if kwargs.get("date"):
            try:
                target_date = datetime.strptime(kwargs.get("date"), "%Y-%m-%d").date()
            except ValueError:
                print(f"Invalid date format: {kwargs.get('date')}, using yesterday instead.")
        elif kwargs.get("start_date") and kwargs.get("end_date"):
            try:
                start_date = datetime.strptime(kwargs.get("start_date"), "%Y-%m-%d").date()
                end_date = datetime.strptime(kwargs.get("end_date"), "%Y-%m-%d").date()
            except ValueError:
                print(f"Invalid start/end date format. Using previous day instead.")
                start_date = end_date = target_date

        # Fetch unrated commits
        commits_qs = GitCommit.objects.filter(
            is_rated=False
        ).exclude(
            message__startswith="Merge"
        ).exclude(
            message__startswith="Revert"
        ).exclude(
            is_merge=True
        ).exclude(
            is_revert=True
        ).exclude(
            user__isnull=True
        ).order_by("date")

        # Apply date filters
        if start_date and end_date:
            commits_qs = commits_qs.filter(date__date__range=[start_date, end_date])
            print(f"📅 Fetching commits from {start_date} to {end_date}")
        else:
            commits_qs = commits_qs.filter(date__date=target_date)
            print(f"📅 Fetching commits for {target_date}")

        if kwargs.get("user_id"):
            commits_qs = commits_qs.filter(user__id=kwargs.get("user_id"))


        if not commits_qs.exists():
            print("No unrated commits found. Exiting.")
            return

        for commit in commits_qs:
            if not commit.user:
                print(f"Skipping commit {commit.commit_hash[:7]} (no user assigned)")
                continue

            try:
                print(f"\nProcessing commit {commit.commit_hash[:7]} (author={commit.author})")              
                rating = commit.rate_with_gpt()  # <--- Using the model's perfect function

                if rating is not None:
                    print(f"Stored rating {rating} for commit {commit.commit_hash[:7]} (type={commit.commit_type})")
                    print(f"Stored message rating {commit.message_rating} for commit {commit.commit_hash[:7]} (suggested_message={commit.suggested_message})")                       
                    rated_count += 1

                else:
                    print(f"Commit {commit.commit_hash[:7]} could not be rated or was skipped.")

            except Exception as e:
                print(f"Error processing commit {commit.commit_hash[:7]}:", str(e))
                logger.exception(f"Error for commit {commit.commit_hash[:7]}")

        # Summary
        print("\nCommit rating job finished.")
        final_summary = [
            "\n=== EXECUTION SUMMARY ===",
            f"Total commits rated: {rated_count}",
            f"Completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
        log.logs += "\n" + "\n".join(final_summary)
        log.last_run_at = now()
        log.save()


