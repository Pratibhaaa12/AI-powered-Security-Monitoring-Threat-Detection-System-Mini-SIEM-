import logging
import pytz
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.conf import settings

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from accounts.models import UserProfile, ScriptRunLog
from timeero.models import TimeeroUser

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

IST_TZ = pytz.timezone("Asia/Kolkata")


def send(text):
    token = settings.SLACK_BOT_TOKEN
    channel = settings.SLACK_CHANNEL_ID
    if not token or not channel:
        return logger.error("Missing Slack config")

    try:
        WebClient(token=token).chat_postMessage(
            channel=channel,
            text=text
        )
    except SlackApiError as e:
        logger.error(f"Slack Error: {e.response.get('error')}")


class Command(BaseCommand):
    help = "Send birthday notifications and reminders to Slack"

    def handle(self, *args, **kwargs):
        log, _ = ScriptRunLog.objects.get_or_create(
            name="birthday_notification"
        )
        log.run_count += 1
        log.last_run_at = now()
        log.logs = (
            f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Run count: {log.run_count}"
        )
        log.save()

        today = now().astimezone(IST_TZ).date()
        tomorrow = today + timedelta(days=1)

        profiles = UserProfile.objects.select_related("user").filter(
            dob__isnull=False
        )

        if not profiles.exists():
            logger.info("No users with DOB.")
            return

        for profile in profiles:
            dob = profile.dob
            if not dob:
                continue

            user = profile.user
            email = (user.email or "").strip().lower()

            first_name = user.first_name or ""
            last_name = user.last_name or ""
            full_name = f"{first_name} {last_name}".strip() or user.username

            slack_mention = full_name
            if email:
                timeero_user = TimeeroUser.objects.filter(
                    email__iexact=email
                ).first()
                if timeero_user and timeero_user.slack_user_id:
                    slack_mention = f"<@{timeero_user.slack_user_id}>"

            gender = (getattr(profile, "gender", "") or "").lower()
            if gender == "male":
                pronoun = "He"
            elif gender == "female":
                pronoun = "She"
            else:
                pronoun = "They"

            if dob.day == today.day and dob.month == today.month:
                message = f"🎉 *Happy Birthday {slack_mention}* 🥳🎂"
                print(message)
                send(message)

                logger.info(
                    f"Birthday wish sent for {user.username}"
                )

            elif dob.day == tomorrow.day and dob.month == tomorrow.month:
                message = (
                    f"🎉 *Tomorrow is {slack_mention}'s birthday!* 🎂\n"
                    f"{pronoun} might be on leave tomorrow"
                )
                print(message)
                send(message)

                logger.info(
                    f"Birthday reminder sent for {user.username}"
                )

        log.logs += "\nExecution completed successfully."
        log.last_run_at = now()
        log.save()
