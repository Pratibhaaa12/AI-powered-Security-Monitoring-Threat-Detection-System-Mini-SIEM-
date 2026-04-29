import logging
import calendar
from collections import defaultdict
from datetime import datetime

from django.db.models import Avg, Q
from django.db.models.functions import Coalesce
from django.db.models import FloatField, Value
from django.shortcuts import render
from django.utils.timezone import now, make_aware
from django.contrib.auth.decorators import login_required

from accounts.models import UserProfile, GitCommit
logger = logging.getLogger('leaderboard')

@login_required
def leaderboard_view(request):
    logger.debug("Generating leaderboard")

    month_year = request.GET.get("month_year")

    if month_year:
        year, month = map(int, month_year.split("-"))
    else:
        today = now()
        year, month = today.year, today.month
        month_year = f"{year}-{month:02d}"  

    # timezone-aware
    start_date = make_aware(datetime(year, month, 1, 0, 0, 0))
    last_day = calendar.monthrange(year, month)[1]
    end_date = make_aware(datetime(year, month, last_day, 23, 59, 59))

    # Name for summary
    month_name = f"{calendar.month_name[month]} {year}"

    user_profiles = UserProfile.objects.select_related("user").all()
    
    leaderboard = []
    total_commits = 0
    total_file_changes = 0

    for profile in user_profiles:
        user = profile.user

        #  SHOW ONLY GITHUB USERS
        if not profile.github_token_decrypted:  
            continue
        
        # get commits from date range 
        user_commits = profile.get_commits_by_date_range(
            start_date=start_date,
            end_date=end_date
        ).exclude(Q(message__startswith="Merge") | Q(message__startswith="Revert")).prefetch_related("file_changes")

        # filter by author match
        possible_authors = {user.username, user.email}
        if profile.aliases:
            possible_authors.update(profile.aliases)

        matched_commits = [
            c for c in user_commits
            if (c.author and c.author in possible_authors) or
               (c.author_email and c.author_email in possible_authors)
        ]

        commits_count = len(matched_commits)
        user_file_changes = 0
        repos = defaultdict(int)

        if commits_count:
            for commit in matched_commits:
                file_changes_count = commit.file_changes.filter(
                    changes__isnull=False
                ).exclude(changes__exact="").count()
                user_file_changes += file_changes_count

                repo_url = f"https://github.com/{commit.org_name}/{commit.repo_name}"
                repos[repo_url] += 1

        total_commits += commits_count
        total_file_changes += user_file_changes

        commit_stats = (
            GitCommit.objects.filter(id__in=[c.id for c in matched_commits])
            .aggregate(
                avg_rating=Coalesce(Avg("rating"), Value(0.0, output_field=FloatField())),
                avg_message_rating=Coalesce(Avg("message_rating"), Value(0.0, output_field=FloatField())),
            )
        )

        combined_avg = (
            (commit_stats["avg_rating"] + commit_stats["avg_message_rating"]) / 2.0
            if commit_stats else 0.0
        )


        leaderboard.append({
            "author": user.get_full_name() or user.username,
            "total_commits": commits_count,
            "total_file_changes": user_file_changes,
            "repos": repos,
            "avg_rating": round(combined_avg, 2),
            "is_current_user": request.user.id == user.id,
        })
    
    # sort + rank
    leaderboard.sort(key=lambda u: (-u["avg_rating"], -u["total_commits"], -u["total_file_changes"]))
    for index, u in enumerate(leaderboard, start=1):
        u["rank"] = index

    return render(request, "leaderboard.html", {
        "leaderboard": leaderboard,
        "total_commits": total_commits,
        "total_file_changes": total_file_changes,
        "month_name": month_name,
        "month_year":month_year,
    })

