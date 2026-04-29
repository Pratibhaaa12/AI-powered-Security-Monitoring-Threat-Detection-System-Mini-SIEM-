import logging
import json
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils.timezone import now, make_aware, is_naive
from openai import OpenAI

from goals.models import RepoGoal
from accounts.models import Prompt, GitCommit, FileChange, Repo, ScriptRunLog

logger = logging.getLogger(__name__)
client = OpenAI(api_key=settings.OPENAI_API_KEY)


def ensure_aware(dt):
    """Return timezone-aware datetime. If dt is naive, make_aware it using Django settings."""
    try:
        return make_aware(dt) if dt and is_naive(dt) else dt
    except Exception:
        return dt


class Command(BaseCommand):
    help = "Generates AI progress analysis for repos using admin GoalPrompt."

    def add_arguments(self, parser):
        parser.add_argument(
            "--repo",
            type=str,
            help="Target repo (Format: orgname/reponame)"
        )

    def handle(self, *args, **kwargs):
        print("\n🚀 Starting Goal Progress Analysis...\n")

        # Script run log
        log, _ = ScriptRunLog.objects.get_or_create(name="goal_progress")
        log.run_count += 1
        log.last_run_at = now()
        log.logs = f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}\nRun count: {log.run_count}\n"
        log.save()

        print("🔍 Fetching latest goal-analysis prompt...")

        # Fetch latest admin prompt
        prompt_obj = (
            Prompt.objects.filter(type="goal-analysis")
            .exclude(created_date__isnull=True)
            .order_by("-created_date", "-id")
            .first()
        )
        if not prompt_obj:
            print("❌ ERROR: No goal-analysis prompt found. Aborting.\n")
            log.logs += "❌ No goal-analysis prompt found.\n"
            log.save()
            return

        print(f"✔ Using prompt created at: {prompt_obj.created_date}")
        base_prompt = prompt_obj.prompt

        # Repo filter
        specific_repo = kwargs.get("repo")
        if specific_repo:
            if "/" not in specific_repo:
                print("❌ Invalid repo format. Use org/repo")
                return
            org_name_arg, repo_name_arg = specific_repo.split("/", 1)

            repos = Repo.objects.filter(
                org_name__iexact=org_name_arg.strip(),
                repo_name__iexact=repo_name_arg.strip(),
                repo_goals__status="active"
            ).distinct()
        else:
            repos = Repo.objects.filter(repo_goals__status="active").distinct()

        if not repos.exists():
            print("❌ No matching repos found with ACTIVE goals.\n")
            return

        total_processed = 0

        for repo in repos:
            print(f"\nProcessing Repository: {repo.org_name}/{repo.repo_name}")
            # Get ONLY active goal
            goal_obj = repo.repo_goals.filter(status="active").order_by("order").first()

            if not goal_obj:
                print("  ⚠️ No ACTIVE goal found — skipping this repo")
                continue

            print(f"🎯 Active Goal Found: {goal_obj.goal_text}")

            raw = goal_obj.raw_data or {}

            last_processed = raw.get("last_processed_date")
            if last_processed:
                try:
                    start_date = datetime.strptime(last_processed, "%Y-%m-%d").date() + timedelta(days=1)
                except Exception:
                    start_date = goal_obj.created_at.date()
            else:
                activated_at = raw.get("activated_at")
                if activated_at:
                    start_date = datetime.strptime(activated_at, "%Y-%m-%d").date()
                else:
                    start_date = (now() - timedelta(days=1)).date()
            end_date = (now() - timedelta(days=1)).date()

            if start_date > end_date:
                print("No new commits to process for this repo")
                continue

            print(f"Processing range: {start_date} → {end_date}\n")

            commits = GitCommit.objects.filter(
                repo_name__iexact=(goal_obj.repo_name or repo.repo_name).strip(),
                org_name__iexact=(goal_obj.org_name or repo.org_name).strip(),
                date__date__gte=start_date,
                date__date__lte=end_date
            ).select_related("user").order_by("date")

            print(f"Total commits found: {commits.count()}")

            file_changes = FileChange.objects.filter(commit__in=commits)
            print(f"Total file changes found: {file_changes.count()}\n")

            code_changes_blocks = []

            for commit in commits:
                fc_qs = FileChange.objects.filter(commit=commit)
                if fc_qs.exists():
                    for fc in fc_qs:
                        code_changes_blocks.append(
                            f"--- COMMIT: {commit.message} | FILE: {getattr(fc, 'filename', 'unknown')} ---\n{fc.changes}"
                        )
                else:
                    code_changes_blocks.append(
                        f"--- COMMIT: {commit.message} ---\n(no code diff available)"
                    )

            code_changes = "\n\n".join(code_changes_blocks) if code_changes_blocks else "None"


            # Build commit summary
            commit_summary = []
            for c in commits:
                d = c.date
                try:
                    d_str = ensure_aware(d).strftime("%Y-%m-%d %H:%M:%S") if d else None
                except Exception:
                    d_str = str(d)
                commit_summary.append({
                    "user": c.user.username if c.user else None,
                    "message": c.message,
                    "hash": c.commit_hash,
                    "date": d_str
                })

            # File changes summary
            file_change_summary = []
            for f in file_changes:
                file_name = (
                    getattr(f, "filename", None) or
                    getattr(f, "file", None) or
                    getattr(f, "path", None) or
                    getattr(f, "file_name", None) or
                    "unknown"
                )
                file_change_summary.append({
                    "file": file_name,
                    "changes": getattr(f, "changes", None),
                    "file_url": getattr(f, "file_url", None)
                })

            limited_commit_summary = commit_summary[:50]
            limited_file_change_summary = file_change_summary[:80]

            # user contributions by commits
            users_summary = {}
            for c in commits:
                if c.user:
                    users_summary.setdefault(c.user.username, 0)
                    users_summary[c.user.username] += 1

            previous_progress = raw.get("progress_percentage", 0)

            final_prompt = (
                base_prompt
                    .replace("{{repo_name}}", repo.repo_name)
                    .replace("{{org_name}}", repo.org_name)
                    .replace("{{goal_text}}", goal_obj.goal_text)
                    .replace("{{start_date}}", start_date.strftime("%Y-%m-%d"))
                    .replace("{{commit_count}}", str(commits.count()))
                    .replace("{{file_change_count}}", str(file_changes.count()))
                    .replace("{{commit_summary}}", json.dumps(limited_commit_summary))
                    .replace("{{file_change_summary}}", json.dumps(limited_file_change_summary))
                    .replace("{{user_summary}}", json.dumps(users_summary))
                    .replace("{{previous_progress}}", str(previous_progress))
                    .replace("{{code_changes}}", code_changes)

            )

            try:
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": final_prompt}],
                )
                ai_text = response.choices[0].message.content
                print("🤖 AI response received successfully.")
            except Exception as e:
                ai_text = f'AI ERROR: {str(e)}'
                print("❌ AI request failed:", e)
                logger.exception(f"AI call failed for {repo.repo_name}")

            try:
                start_idx = ai_text.find("{")
                end_idx = ai_text.rfind("}")
                extracted = json.loads(ai_text[start_idx:end_idx + 1])
            except Exception:
                extracted = {"error": "invalid_json", "raw_text": ai_text[:200]}

            raw_increment = extracted.get("progress_increment", 0)
            if commits.count() == 0:
                raw_increment = 0


            summary_text = (extracted.get("summary") or "").lower()
            off_goal_signals = [
                "not related to the goal",
                "unrelated to the goal",
                "outside the goal",
                "does not contribute to the goal",
                "goal-related work is still pending"
            ]
            if any(s in summary_text for s in off_goal_signals):
                raw_increment = 0

            remaining = max(0, 100 - previous_progress)
            increment = min(raw_increment, remaining)
            new_progress = previous_progress + increment


            prev_users = {u["username"]: u["percentage"] for u in raw.get("user_contributions", [])}
            for u in extracted.get("user_contributions", []):
                prev_users[u["username"]] = prev_users.get(u["username"], 0) + u.get("percentage", 0)

            raw["progress_percentage"] = new_progress
            raw["summary"] = extracted.get("summary")
            raw["goal_status"] = extracted.get("goal_status")
            raw["deadline_prediction"] = extracted.get("deadline_prediction")
            raw["last_processed_date"] = end_date.strftime("%Y-%m-%d")
            raw["user_contributions"] = [
                {"username": k, "percentage": v} for k, v in prev_users.items()
            ]

            goal_obj.raw_data = raw

            if new_progress >= 100:
                goal_obj.status = "completed"

                next_goal = RepoGoal.objects.filter(
                    repo=repo,
                    status="pending"
                ).order_by("order").first()

                if next_goal:
                    next_goal.status = "active"
                    next_goal.raw_data = {
                        "activated_at": now().strftime("%Y-%m-%d")
                    }
                    next_goal.save()

            goal_obj.save()

            log.logs += f"\nProcessed {repo.org_name}/{repo.repo_name} - commits: {commits.count()}, files: {file_changes.count()}\n"
            log.logs += json.dumps(raw, indent=2) + "\n"
            log.last_run_at = now()
            log.save()

            prompt_obj.commit_rated_count += 1
            prompt_obj.save()

            total_processed += 1
            print(f"🎉 Completed repo {total_processed}/{repos.count()}")

        # Final summary
        summary = (
            f"\n=== EXECUTION SUMMARY ===\n"
            f"Total repos processed: {total_processed}\n"
            f"Completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        log.logs += summary
        log.last_run_at = now()
        log.save()

        print(summary)
        print("✅ Goal Progress Analysis Complete.")
