import json
import logging
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils.timezone import now
from django.conf import settings
from openai import OpenAI
from timeero.models import TimeeroUser, TimeeroTimesheet
from accounts.models import GitCommit, Prompt, FileChange, ScriptRunLog
from timeero.models import DailyReport


logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)


class Command(BaseCommand):
    help = "Generate and store daily reports with AI code relevance scoring"

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Single report date in YYYY-MM-DD"
        )
        parser.add_argument(
            "--start-date",
            type=str,
            help="Start date in YYYY-MM-DD (inclusive)"
        )
        parser.add_argument(
            "--end-date",
            type=str,
            help="End date in YYYY-MM-DD (inclusive)"
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug logging"
        )

    def handle(self, *args, **kwargs):
        date_str = kwargs.get("date")
        start_date_str = kwargs.get("start_date")
        end_date_str = kwargs.get("end_date")
        debug = kwargs.get("debug")

        if debug:
            logger.setLevel(logging.DEBUG)
            self.stdout.write(self.style.WARNING("🔍 DEBUG MODE ENABLED"))

        log, _ = ScriptRunLog.objects.get_or_create(name="generate_daily_reports")
        log.run_count += 1
        log.last_run_at = now()
        log.logs = (
            f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Run count: {log.run_count}"
        )
        log.save()

        if date_str:
            try:
                start_date = end_date = datetime.strptime(
                    date_str, "%Y-%m-%d"
                ).date()
            except ValueError:
                self.stderr.write(self.style.ERROR("Invalid --date format"))
                return

        elif start_date_str and end_date_str:
            try:
                start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR("Invalid start/end date format"))
                return

            if start_date > end_date:
                self.stderr.write(
                    self.style.ERROR("start-date cannot be after end-date")
                )
                return
        else:
            start_date = end_date = (now() - timedelta(days=1)).date()

        self.stdout.write(
            self.style.WARNING(
                f"Generating daily reports from {start_date} to {end_date}"
            )
        )

        prompt_obj = Prompt.objects.filter(
            type="daily_report_scoring"
        ).order_by("-id").first()

        if not prompt_obj or not prompt_obj.prompt:
            self.stderr.write(
                self.style.ERROR("Daily report AI prompt not found")
            )
            return

        base_prompt = prompt_obj.prompt

        total_created = 0
        total_skipped = 0

        current_date = start_date
        while current_date <= end_date:

            users = User.objects.filter(
                id__in=GitCommit.objects.filter(
                    date__date=current_date,
                    is_merge=False,
                    is_revert=False
                ).values_list("user_id", flat=True).distinct()
            )

            for user in users:
                if DailyReport.objects.filter(
                    user=user,
                    report_date=current_date
                ).exists():
                    total_skipped += 1
                    continue

                commits_qs = GitCommit.objects.filter(
                    user=user,
                    date__date=current_date,
                    is_merge=False,
                    is_revert=False
                ).order_by("date")

                if not commits_qs.exists():
                    continue

                tasks = []
                updates = []

                timeero_user = TimeeroUser.objects.filter(
                    email__iexact=(user.email or "").strip()
                ).first()

                if timeero_user:
                    sheets = TimeeroTimesheet.objects.filter(
                        user=timeero_user,
                        clock_in_time__date=current_date
                    )

                    for sheet in sheets:
                        for cf in sheet.custom_fields.all():
                            if cf.field_key == "687" and cf.field_value:
                                tasks.append(cf.field_value.strip())
                            elif cf.field_key == "688" and cf.field_value:
                                updates.append(cf.field_value.strip())

                tasks_text = "\n".join(tasks) if tasks else "None"
                updates_text = "\n".join(updates) if updates else "None"

                commits_snapshot = []
                commit_messages = []

                for c in commits_qs:
                    commit_messages.append(c.message)
                    commits_snapshot.append({
                        "message": c.message,
                        "url": c.url,
                    })

                commits_text = "\n".join(commit_messages) if commit_messages else "None"

                code_changes_blocks = []

                for commit in commits_qs:
                    file_changes = FileChange.objects.filter(commit=commit)
                    if file_changes.exists():
                        for fc in file_changes:
                            code_changes_blocks.append(
                                f"--- COMMIT: {commit.message} | FILE: {fc.filename} ---\n{fc.changes}"
                            )

                BATCH_SIZE = 5
                batch_evidence = []
                batch_percentages = []

                for i in range(0, len(code_changes_blocks), BATCH_SIZE):
                    batch_code = "\n\n".join(code_changes_blocks[i:i + BATCH_SIZE])

                    prompt = (
                        base_prompt
                        .replace("{{tasks}}", tasks_text)
                        .replace("{{updates}}", updates_text)
                        .replace("{{commits}}", commits_text)
                        .replace("{{code_changes}}", batch_code)
                    )

                    try:
                        response = client.chat.completions.create(
                            model="gpt-4o",
                            messages=[{"role": "user", "content": prompt}]
                        )

                        raw = response.choices[0].message.content.strip()
                        if raw.startswith("```"):
                            raw = raw.strip("`")
                            if raw.lower().startswith("json"):
                                raw = raw[4:].strip()

                        data = json.loads(raw)
                        if data.get("reason"):
                            batch_evidence.append(data["reason"])
                        if data.get("percentage") is not None:
                            batch_percentages.append(float(data["percentage"]))

                    except Exception:
                        logger.exception(
                            f"AI batch failed for user {user.username} on {current_date}"
                        )

                combined_code_evidence = "\n".join(batch_evidence)

                final_prompt = (
                    base_prompt
                    .replace("{{tasks}}", tasks_text)
                    .replace("{{updates}}", updates_text)
                    .replace("{{commits}}", commits_text)
                    .replace("{{code_changes}}", combined_code_evidence)
                )

                percentage = None
                reason = None

                try:
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=[{"role": "user", "content": final_prompt}]
                    )

                    raw = response.choices[0].message.content.strip()

                    if raw.startswith("```"):
                        raw = raw.strip("`")
                        if raw.lower().startswith("json"):
                            raw = raw[4:].strip()

                    data = json.loads(raw)
                    percentage = data.get("percentage")
                    reason = data.get("reason")

                except Exception:
                    logger.exception(
                        f"AI failed for user {user.username} on {current_date}"
                    )

                DailyReport.objects.create(
                    user=user,
                    report_date=current_date,
                    tasks_text="\n".join(tasks),
                    updates_text="\n".join(updates),
                    commits=commits_snapshot,
                    code_relevance_percentage=percentage,
                    code_relevance_reason=reason,
                )

                total_created += 1

            current_date += timedelta(days=1)

        summary = [
            "\n=== EXECUTION SUMMARY ===",
            f"Created reports: {total_created}",
            f"Skipped (already existed): {total_skipped}",
            f"Completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        log.logs += "\n" + "\n".join(summary)
        log.last_run_at = now()
        log.save()

        self.stdout.write(
            self.style.SUCCESS(
                f"Daily report generation complete. "
                f"Created: {total_created}, Skipped: {total_skipped}"
            )
        )
