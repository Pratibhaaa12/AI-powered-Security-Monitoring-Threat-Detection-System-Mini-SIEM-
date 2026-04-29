from collections import defaultdict
import logging
from datetime import datetime, timedelta, timezone
import requests
import pytz
from django.core.management.base import BaseCommand
from django.utils.timezone import now
from django.conf import settings
from accounts.models import UserProfile, ScriptRunLog, GitCommit
from django.db.models import Q
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from django.db.models import Avg
from accounts.models import UserProfile, ScriptRunLog, GitCommit
from timeero.models import TimeeroTimesheet, TimeeroUser, TimeeroBreak, TimeeroCustomField
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

IST_TZ = pytz.timezone("Asia/Kolkata")
def ist_day_to_utc_range(day):
    """
    Convert an IST calendar day into correct UTC datetime range
    """
    ist = pytz.timezone("Asia/Kolkata")

    ist_start = ist.localize(datetime.combine(day, datetime.min.time()))
    ist_end = ist.localize(datetime.combine(day, datetime.max.time()))

    return ist_start.astimezone(pytz.UTC), ist_end.astimezone(pytz.UTC)

def format_time(dt):
    return dt.strftime("%H:%M") if dt else "N/A"

def format_day_with_name(day):
    return f"{day.strftime('%Y-%m-%d')} ({day.strftime('%A')})"

def extract_attendance_from_db(email, day):
    try:
        user = TimeeroUser.objects.get(email=email)
    except TimeeroUser.DoesNotExist:
        return []
    start_dt = datetime.combine(day, datetime.min.time()).replace(tzinfo=timezone.utc)
    end_dt = datetime.combine(day, datetime.max.time()).replace(tzinfo=timezone.utc)
    sheets = TimeeroTimesheet.objects.filter(
        user=user,
        clock_in_time__gte=start_dt,
        clock_in_time__lte=end_dt,
    ).order_by("clock_in_time").select_related("user").prefetch_related("breaks", "custom_fields")

    sessions = []
    for ts in sheets:
        cin = ts.clock_in_time
        cout = ts.clock_out_time
        break_seconds = 0
        for br in ts.breaks.all():
            if br.start and br.end:
                break_seconds += (br.end - br.start).total_seconds()

        total_duration = work_duration = "N/A"
        if cin and cout:
            total_sec = (cout - cin).total_seconds()
            work_sec = total_sec - break_seconds
            th = int(total_sec // 3600)
            tm = int((total_sec % 3600) // 60)
            wh = int(work_sec // 3600)
            wm = int((work_sec % 3600) // 60)

            total_duration = f"{th}h {tm}m"
            work_duration = f"{wh}h {wm}m"
        cf_val = ts.custom_fields.filter(field_key="684").first()
        raw_mode = cf_val.field_value if cf_val else ts.job_name or "N/A"

        raw = (raw_mode or "").strip().lower()

        if raw in ["wfh", "home", "work from home"]:
            mode = "🏠: WFH"
        elif raw in ["wfo", "office", "work from office"]:
            mode = "🏢: WFO"
        else:
            mode = raw_mode.title()


        sessions.append({
            "in": format_time(cin),
            "out": format_time(cout),
            "total": total_duration,
            "work": work_duration,
            "mode": mode,
        })

    return sessions



def commit_summary(profile, day):
    start_dt, end_dt = ist_day_to_utc_range(day)

    qs = profile.get_commits_by_date_range(start_dt, end_dt).exclude(
        Q(message__startswith="Merge") |
        Q(message__startswith="Revert") |
        Q(is_merge=True) |
        Q(is_revert=True)
    )

    add = sub = 0
    repos = defaultdict(int)
    repo_ai_scores = defaultdict(list)

    for c in qs:
        # Count + / -
        for fc in c.file_changes.all():
            for line in (fc.changes or "").splitlines():
                if line.startswith("+") and not line.startswith("+++"):
                    add += 1
                if line.startswith("-") and not line.startswith("---"):
                    sub += 1
        key = (
            f"https://github.com/{c.org_name}/{c.repo_name}"
            if c.org_name and c.repo_name
            else c.repo_name or c.org_name or c.url or "Unknown Repo"
        )
        repos[key] += 1
        if c.ai_generated_score is not None:
            repo_ai_scores[key].append(c.ai_generated_score)
    repo_ai_avg = {
        r: round(sum(scores) / len(scores), 1) if scores else 0
        for r, scores in repo_ai_scores.items()
    }
    stats = qs.aggregate(
        avg_rating=Avg("rating"),
        avg_msg_rating=Avg("message_rating")
    )
    combined_avg = round(
        ((stats["avg_rating"] or 0) + (stats["avg_msg_rating"] or 0)) / 2,
        2
    )

    return qs.count(), add, sub, repos, combined_avg, repo_ai_avg


 
def user_block(user, sessions, commits, day):
    is_github_user = bool(getattr(user.profile, "github_username", None))
    count, add, sub, repos, combined_avg, repo_ai_avg = commits

    title = f"👤 *{user.username}*"
    fullname = f"{user.first_name} {user.last_name}".strip()
    if fullname:
        title += f" (_{fullname}_)"

    txt = f"━━━━━━━━━━━━━━━━━━━━━━\n{title}\n"

    if sessions:
        for idx, s in enumerate(sessions, 1):
            txt += (f"   • Session {idx}: "
                f"⏳ In: {s['in']}  🔚 Out: {s['out']}  "
                f"🕒 TSpan: {s['total']}  🕑 WSpan: {s['work']}   "
                f"• {s['mode']}\n"
            )
    else:
        txt += "   • No attendance recorded\n"

    if count:
        txt += f"💻 Commits: {count} | ➕ {add} | ➖ {sub}| ⭐ Avg Rating: {combined_avg}\n"
        for r, n in repos.items():
            ai_avg = repo_ai_avg.get(r, 0)
            txt += f"🔗 {r} — {n} commit(s) | 🤖 AI Code %: {ai_avg}\n"
    else:
        txt += "💻 No commits\n"

    daily_url = f"https://teamrewards.011bq.app/daily-report/?username={user.username}&date={day}"
    if is_github_user:
        txt += f"\n🔎 <{daily_url}|View More Details>\n"

    return txt
def send(text):
    token = settings.SLACK_BOT_TOKEN
    channel = settings.SLACK_CHANNEL_ID
    if not token or not channel:
        return logger.error("Missing Slack config")
    try:
        WebClient(token=token).chat_postMessage(channel=channel, text=text)
    except SlackApiError as e:
        logger.error(f"Slack Error: {e.response.get('error')}")


def get_report_day(today):
    weekday = today.weekday()
    if weekday == 6:
        return None, False
    if weekday == 0:
        return today.date() - timedelta(days=2), True
    return today.date() - timedelta(days=1), True


class Command(BaseCommand):
    help = "Show daily summary of previous day's commits + Timeero attendance (UTC In/Out + IST commits)"
    def add_arguments(self, parser):
        parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
        parser.add_argument("--mode", choices=["first_last", "latest_open", "longest", "all"], default="all", help="Output preference")
        parser.add_argument(
            "--date",
            type=str,
            help="Specify the date for report in YYYY-MM-DD format (default: yesterday)"
        )
    def handle(self, *args, **kwargs):
        debug = kwargs.get("debug")
        mode = kwargs.get("mode")
        date_str = kwargs.get("date")
        if debug:
            logger.setLevel(logging.DEBUG)
            logger.debug("Debug mode enabled!")

        today = now().astimezone(IST_TZ)
        day, should_send = get_report_day(today)

        if date_str:
            try:
                day = datetime.strptime(date_str, "%Y-%m-%d").date()
            except:
                logger.error("Invalid date")

        if not should_send and not date_str:
            logger.info("Sunday detected — skipping notification")
            return

        profiles = UserProfile.objects.select_related("user").order_by("user__username")
        blocks = []
        total_commits = 0
        used_emails = set()
        for p in profiles:
            email = (p.user.email or "").lower()
            if not email or email in used_emails:
                continue
            used_emails.add(email)
            sessions = extract_attendance_from_db(email, day)
            commits = commit_summary(p, day)
            total_commits += commits[0]
            blocks.append(user_block(p.user, sessions, commits, day))
        unknown = GitCommit.objects.filter(date__date=day, user__isnull=True).count()
        final = (
            f"📊 Daily Report — {format_day_with_name(day)}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📈 Total Commits: {total_commits}\n"
            f"❓ Unknown Commits: {unknown}\n\n"
            + "\n".join(blocks)
        )
        print(final)
        send(final)

        ScriptRunLog.objects.update_or_create(
            name="notify_commits",
            defaults={"last_run_at": now()}
        )

        logger.info("Daily summary sent.")
