# accounts/management/commands/notify_missed_checkins.py
from email import message as email_message
import logging
from datetime import date
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils.timezone import localtime, now
from django.contrib.auth import get_user_model
from slack_sdk import WebClient
from accounts.models import UserProfile
from slack_sdk.errors import SlackApiError
from accounts.utils import get_employee_leave_status

logger = logging.getLogger(__name__)
User = get_user_model()


def _is_on_leave(leave_api_result):
    if not leave_api_result or not isinstance(leave_api_result, dict):
        logger.debug("Leave API result missing or invalid: %s", leave_api_result)
        return False

    data = leave_api_result.get("data") or {}
    status = (leave_api_result.get("status") or data.get("status") or "").lower()
    logger.debug("Leave API data: %s | status=%s", data, status)

    if "leave" in status or data.get("on_leave") is True or data.get("is_on_leave") is True:
        logger.debug("Detected leave keywords or flags in leave API result.")
        return True

    if data.get("leave_days") or data.get("leaves"):
        logger.debug("Detected leave days/leaves key in leave API result.")
        return True

    return False


def _has_checked_in(att_api_result):
    if not att_api_result or not isinstance(att_api_result, dict):
        logger.debug("Attendance API result missing or invalid: %s", att_api_result)
        return False

    data = att_api_result.get("data") or {}
    logger.debug("Attendance API data: %s", data)

    # ✅ Directly check for check-in or check_in field
    check_in_time = data.get("check_in") or data.get("check-in")
    if check_in_time and check_in_time not in ("N/A", None, ""):
        logger.debug("Detected valid check-in time: %s", check_in_time)
        return True

    # Keep previous fallbacks for other data structures
    records = data.get("attendance") or data.get("records") or data.get("checkins")
    if records:
        try:
            if isinstance(records, (list, tuple)) and len(records) > 0:
                logger.debug("Detected attendance records: %s", records)
                return True
            if isinstance(records, dict) and records.get("in"):
                logger.debug("Detected 'in' timestamp in attendance dict: %s", records)
                return True
        except Exception as e:
            logger.debug("Error parsing attendance records: %s", e)

    present_flag = data.get("present") or data.get("is_present")
    if present_flag is True:
        logger.debug("Detected 'present' flag in attendance API result.")
        return True

    last_checkin = data.get("last_checkin") or data.get("last_clock_in")
    if last_checkin:
        logger.debug("Detected last_checkin timestamp: %s", last_checkin)
        return True

    return False



class Command(BaseCommand):
    help = "Show users who didn't check-in today (excluding those on leave)."

    def handle(self, *args, **options):
        today = localtime(now()).date()
        missed = []

        logger.info("=== Checking missed check-ins for %s ===", today)

        # Define the queryset first
        qs = User.objects.filter(is_active=True).exclude(is_staff=True)

        # Hardcoded usernames to always ignore
        ALWAYS_IGNORE = {"shashank-011bq", "mudit2108"}

        # Update their profiles automatically to ignore
        for uname in ALWAYS_IGNORE:
            user = User.objects.filter(username=uname).first()
            if user:
                profile, _ = UserProfile.objects.get_or_create(user=user)
                if not profile.ignore_missed_checkin:
                    profile.ignore_missed_checkin = True
                    profile.save()
                    logger.info(f"Auto-updated {uname}: ignore_missed_checkin=True")

        # Exclude them from query
        qs = qs.exclude(username__in=ALWAYS_IGNORE)
        logger.debug("Found %d active non-staff users.", qs.count())

        for user in qs:
            logger.debug("Processing user: %s (ID: %s)", user.username, user.id)
            try:
                profile = getattr(user, "profile", None) or UserProfile.objects.filter(user=user).first()
                if not profile:
                    logger.debug("No UserProfile found for user %s. Skipping.", user.username)
                    continue

                if getattr(profile, "ignore_missed_checkin", False):
                    logger.info("%s is in ignore list. Skipping missed check-in check.", user.username)
                    self.stdout.write(self.style.NOTICE(f"Ignored: {user.username} ({user.first_name} {user.last_name})"))
                    continue
                

                # Use email for API call (Razorpay expects email)
                email = getattr(user, "email", None)
                if not email:
                    logger.debug("No email found for user %s. Skipping.", user.username)
                    continue

                logger.debug("Fetching leave/attendance data for email=%s", email)
                try:
                    api_result = get_employee_leave_status(email, today)
                    logger.debug("API result for %s: %s", email, api_result)
                except Exception as exc:
                    logger.exception("API error for %s: %s", email, exc)
                    api_result = None

                if _is_on_leave(api_result):
                    logger.info("%s is on leave today", user.username)
                    continue

                if _has_checked_in(api_result):
                    logger.info("%s has checked in today", user.username)
                    continue

                logger.warning("%s did NOT check in today.", user.username)
                full_name = f"{user.first_name} {user.last_name}".strip()
                display_name = f"{user.username} ({full_name})" if full_name else user.username
                missed.append(display_name)

            except Exception as e:
                logger.exception("Error processing user %s: %s", getattr(user, "username", "unknown"), e)
                continue
        client = WebClient(token=getattr(settings, "REWARD_SLACK_BOT_TOKEN", None))
        channel_id = getattr(settings, "REWARD_SLACK_CHANNEL_ID", None)
        # ✅ Show result locally (no Slack)
        if not missed:
            logger.info("All users have checked in or are on leave.")
            self.stdout.write(self.style.SUCCESS(f"✅ All users have checked in or are on leave for {today}."))
        else:
            logger.warning("Total missed check-ins: %d", len(missed))
            self.stdout.write(self.style.WARNING(f"⚠️ Missed check-ins for {today}:"))
            for name in missed:
                logger.debug("Missed check-in: %s", name)
                self.stdout.write(f" - {name}")
        if client and channel_id:
            try:
                if not missed:
                    message_text = f":white_check_mark: All users have checked in or are on leave for {today}."
                else:
                    missed_list = "\n".join(f"• {name}" for name in missed)
                    message_text = f":warning: Missed check-ins for {today}:\n{missed_list}"
                client.chat_postMessage(channel=channel_id, text=message_text)
                logger.info("Posted message to Slack channel %s", channel_id)
            except SlackApiError as e:
                logger.error("Slack API error: %s", e.response["error"])
        else:
            logger.warning("Slack credentials missing in settings. Skipping Slack message.")

        logger.info("=== Completed missed check-in check for %s ===", today)
