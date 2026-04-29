from django.shortcuts import render, redirect
from datetime import datetime
from django.utils.timezone import now, timedelta
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.db.models import Avg

from timeero.models import DailyReport
from accounts.models import GitCommit, FileChange


@login_required(login_url="/login")
def daily_report(request):
    selected_date = request.GET.get("date")

    try:
        if selected_date:
            date_obj = datetime.strptime(selected_date, "%Y-%m-%d").date()
        else:
            date_obj = now().date() - timedelta(days=1)
    except ValueError:
        date_obj = now().date() - timedelta(days=1)

    logged_user = request.user
    selected_username = request.GET.get("username")

    if not logged_user.is_staff:
        if selected_username and selected_username != logged_user.username:
            return redirect(f"/daily-report/?username={logged_user.username}&date={selected_date}")

        user = logged_user
    else:
        if selected_username:
            user = User.objects.filter(username=selected_username).first() or logged_user
        else:
            user = logged_user

    # Fetch AI-generated daily report (tasks/updates/summary)
    report = DailyReport.objects.filter(
        user=user,
        report_date=date_obj
    ).first()

    if report:
        commit_list = []
        for c in report.commits:
            msg = c.get("message")
            url = c.get("url")

            if url:
                html = f'<a href="{url}" target="_blank" class="commit-text">{msg}</a>'
            else:
                html = f'<span class="commit-text">{msg}</span>'

            commit_list.append(html)

        report_rows = [{
            "tasks_block": report.tasks_text or "—",
            "updates_block": report.updates_text or "—",
            "commits_block": commit_list,
            "percentage": report.code_relevance_percentage,
            "reason": report.code_relevance_reason,
        }]
    else:
        report_rows = [{
            "tasks_block": "—",
            "updates_block": "—",
            "commits_block": [],
            "percentage": None,
            "reason": None,
        }]

    # Git-based productivity metrics for this user and date
    productivity = None
    commits_qs = (
        GitCommit.objects.filter(
            user=user,
            date__date=date_obj,
            is_merge=False,
            is_revert=False,
        )
        .prefetch_related("file_changes")
    )

    if commits_qs.exists():
        stats = commits_qs.aggregate(
            avg_rating=Avg("rating"),
            avg_msg_rating=Avg("message_rating"),
            avg_ai=Avg("ai_generated_score"),
        )

        total_commits = commits_qs.count()
        total_added = 0
        total_removed = 0

        for commit in commits_qs:
            for fc in commit.file_changes.all():
                changes = fc.changes or ""
                for line in changes.splitlines():
                    if line.startswith("+++") or line.startswith("---"):
                        continue
                    if line.startswith("+"):
                        total_added += 1
                    elif line.startswith("-"):
                        total_removed += 1

        avg_rating = stats.get("avg_rating") or 0
        avg_msg_rating = stats.get("avg_msg_rating") or 0
        avg_ai = stats.get("avg_ai")

        if avg_rating or avg_msg_rating:
            quality = (avg_rating + avg_msg_rating) / 2.0
        else:
            quality = 0.0

        # Simple, understandable productivity score
        volume_factor = 1 + (total_commits / 10.0)
        if avg_ai is not None:
            ai_factor = max(0.5, 1 - (avg_ai / 150.0))
        else:
            ai_factor = 1.0

        productivity_score = round(quality * volume_factor * ai_factor, 2)

        productivity = {
            "total_commits": total_commits,
            "lines_added": total_added,
            "lines_removed": total_removed,
            "avg_rating": round(avg_rating, 2) if avg_rating else 0,
            "avg_message_rating": round(avg_msg_rating, 2) if avg_msg_rating else 0,
            "avg_ai_percentage": round(avg_ai, 1) if avg_ai is not None else None,
            "productivity_score": productivity_score,
        }

    context = {
        "report_date": date_obj.strftime("%d %B %Y"),
        "report_date_raw": date_obj.strftime("%Y-%m-%d"),
        "report_rows": report_rows,
        "selected_username": selected_username,
        "productivity": productivity,
    }

    if logged_user.is_staff:
        github_user_ids = GitCommit.objects.values_list("user_id", flat=True).distinct()
        context["all_users"] = User.objects.filter(id__in=github_user_ids).order_by("username")

    return render(request, "daily_report.html", context)
