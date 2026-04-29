from django.shortcuts import render, get_object_or_404
from accounts.models import Repo, GitCommit, FileChange, UserProfile
from .models import RepoGoal
from datetime import datetime, timedelta
from django.utils.timezone import make_aware, is_naive

def ensure_aware(dt):
    try:
        if dt is None:
            return None
        if is_naive(dt):
            return make_aware(dt)
        return dt
    except Exception:
        return dt

def repos_list(request):
    user_repos = Repo.objects.filter(users=request.user).distinct().prefetch_related("repo_goals")
    repos = []
    for r in user_repos:

        current_goal = (
            r.repo_goals.filter(status="active").first()
            or r.repo_goals.filter(status="pending").first()
            or r.repo_goals.filter(status="completed").last()
        )

        repos.append({
            "id": r.id,
            "repo_name": r.repo_name,
            "org_name": r.org_name,
            "goal": current_goal.goal_text if current_goal else None
        })
    return render(request, "goals/repo-goals.html", {"repos": repos})

def repo_detail_page(request, repo_id):
    repo = get_object_or_404(Repo, id=repo_id)

    if not repo.users.filter(id=request.user.id).exists() and not request.user.is_staff:
        return render(request, "403.html", status=403)

    goal_obj = (
        repo.repo_goals.filter(status="active").first()
        or repo.repo_goals.filter(status="pending").first()
        or repo.repo_goals.filter(status="completed").last()
    )

    if not goal_obj:
        context = {
            "repo": repo,
            "goal": None,
            "goal_date": None,
            "total_commits": 0,
            "file_changes": 0,
            "percent": 0,
            "user_commit_data": []
        }
        return render(request, "goals/repo-detail.html", context)

    goal_text = goal_obj.goal_text
    goal_date = goal_obj.created_at
    deadline = goal_obj.deadline

    raw = goal_obj.raw_data or {}
    last_processed = raw.get("last_processed_date")

    if last_processed:
        try:
            end_date = datetime.strptime(last_processed, "%Y-%m-%d").date()
        except Exception:
            end_date = (datetime.now().date() - timedelta(days=1))
    else:
        end_date = (datetime.now().date() - timedelta(days=1))

    today = datetime.now().date()
    if end_date > today:
        end_date = today

    commits_qs = GitCommit.objects.filter(
        repo_name=repo.repo_name,
        org_name=repo.org_name,
        date__date__gte=goal_date.date(),
        date__date__lte=end_date
    ).select_related("user")

    total_commits = commits_qs.count()
    file_changes = FileChange.objects.filter(commit__in=commits_qs).count()

    # prepare user data and attach AI percent (if available)
    ai_data = goal_obj.raw_data or {}
    ai_user_map = {}
    if isinstance(ai_data, dict) and ai_data.get("user_contributions"):
        for entry in ai_data["user_contributions"]:
            try:
                ai_user_map[entry.get("username")] = entry.get("percentage", 0)
            except Exception:
                pass

    ai_progress = ai_data.get("progress_percentage", 0) if isinstance(ai_data, dict) else 0
    ai_summary = ai_data.get("summary") if isinstance(ai_data, dict) else None
    ai_goal_status = ai_data.get("goal_status") if isinstance(ai_data, dict) else None
    ai_deadline_pred = ai_data.get("deadline_prediction") if isinstance(ai_data, dict) else None

    # fallback percent (if AI not present)
    percent = ai_progress if ai_progress is not None else 0

    user_commit_data = []

    for user in repo.users.all():
        commit_count = commits_qs.filter(user=user).count()
        ai_percent = ai_user_map.get(user.username, 0)
        user_commit_data.append({
            "user": user,
            "commits": commit_count,
            "ai_percent": ai_percent
        })

    context = {
        "repo": repo,
        "goal": goal_text,
        "goal_date": goal_date,
        "deadline": goal_obj.deadline,
        "total_commits": total_commits,
        "file_changes": file_changes,
        "percent": percent,
        "ai_progress": percent,
        "ai_summary": ai_summary,
        "ai_goal_status": ai_goal_status,
        "ai_deadline": ai_deadline_pred,
        "user_commit_data": user_commit_data,
    }
    coming_from = request.GET.get("from", "user")
    context["coming_from"] = coming_from


    return render(request, "goals/repo-detail.html", context)

def admin_manage_goals(request):
    user_repos = Repo.objects.all().prefetch_related("repo_goals")
    return render(request, "admin/manage_goals.html", {"repos": user_repos})

def repo_goal_list(request, repo_id):
    repo = get_object_or_404(Repo, id=repo_id)

    running_goal = (
        repo.repo_goals.filter(status="active").first()
        or repo.repo_goals.filter(status="pending").first()
        or repo.repo_goals.filter(status="completed").last()
    )

    return render(request, "admin/repo_goal_list.html", {"repo": repo, "running_goal": running_goal})
