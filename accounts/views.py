
from django.conf import settings
from django.shortcuts import get_object_or_404, render, redirect
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden, JsonResponse
from django.db.models import Count, Prefetch, Avg, Q, Sum
from django.core.paginator import Paginator
from django.db.models.functions import TruncDate
from django.contrib.auth import login as auth_login
from django.utils.http import url_has_allowed_host_and_scheme
 

import requests
from django.shortcuts import render, get_object_or_404, redirect
from django.contrib.auth.decorators import login_required

from .forms import CommentForm

# from django.urls import reverse
import requests
from .models import ScriptRunLog, UserProfile, GitCommit, FileChange, Repo,  Prompt, UserProfile, Bug, Comment
from datetime import datetime,date, timezone
from django.utils.timezone import now, timedelta
from datetime import timedelta
import calendar
from django.utils.timezone import make_aware, get_current_timezone
from collections import defaultdict
from django.db.models.functions import Coalesce
from django.db.models import FloatField, Value
 
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_POST
from accounts.utils import get_fernet
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
import logging
from django.views.decorators.csrf import csrf_protect
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth
from django.utils import timezone 
from django.utils.timezone import localdate
from django.views.decorators.cache import cache_page
from django.db import transaction
from accounts.management.commands.commit_ai_analyzer import analyze_patch_with_admin_prompt
from django.db import IntegrityError


logger = logging.getLogger('accounts')
ORG_EMAIL_DOMAIN = "@bytequests.com"
ALLOWED_OUTSIDE_EMAILS = ["muditsjain@gmail.com"]

def get_ist_time(dt=None):
    """
    Convert a UTC datetime to IST.
    If dt is None, use current UTC time.
    """
    if dt is None:
        dt = datetime.utcnow()
    return dt + timedelta(hours=5, minutes=30)

def ping(request):
    logger.debug("Ping endpoint called")
    return JsonResponse({
        'status': 'ok',
        'timestamp': datetime.now().isoformat()
    })


@csrf_protect
def register(request):
    logger.debug("Register view accessed")
    if request.user.is_authenticated:
        logger.info(f"Authenticated user {request.user.username} tried to access register page, redirecting to home.")
        return redirect('home')
    logger.info("Redirecting to GitHub OAuth for registration")
    return redirect('github_login')


@csrf_protect
def login(request):
    next_url = request.GET.get("next")
    if next_url:
        request.session["next"] = next_url

    if request.GET.get('github') == '1':
        return redirect('github_login')
    return render(request, 'login.html')


def github_login(request):
    logger.debug("Redirecting user to GitHub OAuth login")
    github_auth_url = (
        f"https://github.com/login/oauth/authorize?"
        f"client_id={settings.GITHUB_CLIENT_ID}&"
        f"scope=read:user user:email repo read:org&" 

        f"redirect_uri={settings.GITHUB_OAUTH_CALLBACK}"
    )
    return redirect(github_auth_url)


# Helper to count lines added and removed
def count_lines_changed(changes):
    logger.debug("Starting count_lines_changed")
    added = removed = 0
    if not changes:
        logger.warning("No changes input provided (None or empty string)")
        return added, removed

    lines = changes.splitlines()
    logger.debug(f"Total lines to process: {len(lines)}")

    for index, line in enumerate(lines, start=1):
        logger.debug(f"Processing line {index}: {line}")
        if line.startswith('+++') or line.startswith('---'):
            logger.debug(f"Ignored diff metadata line {index}")
            continue
        if line.startswith('+'):
            added += 1
            logger.debug(f"Line {index} marked as added. Total added: {added}")
        elif line.startswith('-'):
            removed += 1
            logger.debug(f"Line {index} marked as removed. Total removed: {removed}")

    logger.info(f"Finished processing. Lines added: {added}, lines removed: {removed}")
    return added, removed


@login_required
def home(request, user_id=None):
    if user_id:
        if not request.user.is_staff:
            return HttpResponseForbidden("You are not allowed to view this page.")
        target_user = get_object_or_404(User, id=user_id)
    else:
        target_user = request.user
    user_email = target_user.email
    logger.debug(f"Loading home view for {user_email}")

    # Step 1: Get valid commit IDs
    logger.debug("Filtering valid commit IDs")
    valid_commit_ids = (
        FileChange.objects.exclude(changes__isnull=True)
        .exclude(changes__exact='')
        .values_list('commit_id', flat=True).distinct()
    )
    logger.debug(f"Found {valid_commit_ids.count()} valid commit IDs")

    # Step 2: Fetch commits for user
    commits = GitCommit.objects.filter(author_email=user_email, id__in=valid_commit_ids).order_by('-date')
    logger.debug(f"Total commits fetched: {commits.count()}")

    response_data = []
    for commit in commits:
        logger.debug(f"Processing commit {commit.commit_hash}")
        file_changes = FileChange.objects.filter(commit=commit).exclude(changes__isnull=True).exclude(changes__exact='').values('filename', 'changes').distinct()
        logger.debug(f"Found {file_changes.count()} file changes for commit {commit.commit_hash}")
        if not file_changes.exists():
            logger.debug(f"Skipping commit {commit.commit_hash} with no changes")
            continue
        file_changes = FileChange.objects.filter(commit=commit).order_by('file_url').distinct('file_url')

        response_dict = {
            'id': commit.id,
            'repo_name': commit.repo_name,
            'date': get_ist_time(commit.date),
            'message': commit.message,
            'is_rated': commit.is_rated,
            'rating': commit.rating,
            'rating_reason': commit.rating_reason,
            'raw_gpt_output': commit.raw_gpt_output,
            'suggested_message': commit.suggested_message,
            'commit_hash': commit.commit_hash,
            'org_name': commit.org_name,
            'file_changes': file_changes.count(),
            'commit_type': commit.commit_type,
            'message_rating': commit.message_rating,
            'ai_generated_score': commit.ai_generated_score,
        }

        added, removed = 0, 0
        for change in file_changes:
            a, r = count_lines_changed(change.changes)
            added += a
            removed += r
        response_dict['line_changes'] = added + removed
        logger.debug(f"Commit {commit.commit_hash}: +{added}, -{removed}")

        response_data.append(response_dict)

    # Step 5: Last script run
    run_obj = ScriptRunLog.objects.filter(name="fetch_git_commits").first()
    last_run = get_ist_time(run_obj.last_run_at).strftime('%Y-%m-%d %H:%M:%S') if run_obj else "Not available"

    logger.debug(f"Last script run at: {last_run}")

    # Step 6: Pagination
    try:
        page_size = int(request.GET.get('page_size', 25))
    except ValueError:
        page_size = 25
    paginator = Paginator(response_data, page_size)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    logger.debug(f"Serving page {page_obj.number} of {paginator.num_pages}")

    return render(request, 'home.html', {
        'page_obj': page_obj,
        'user': request.user,
        'target_user': target_user,        
        'target_user_id': getattr(target_user, 'id', None),
        'target_username': getattr(target_user, 'username', None),
        'page_size': page_size,
        'page_sizes': [25, 50, 100],
        'last_run_time': last_run
    })


@login_required
def profile(request):
    logger.debug(f"Accessing profile for {request.user.username}")
    profile, created = UserProfile.objects.get_or_create(user=request.user)
    if created:
        logger.info(f"New UserProfile created for {request.user.username}")
        profile.github_token = None
        profile.github_outside_collaborator_name = ""
        profile.alias_access_token = []
        profile.save()

    if request.method == 'POST':
        logger.debug("Profile update POST received")
        user = request.user
        first_name = request.POST.get('first_name')
        last_name = request.POST.get('last_name')
        email = request.POST.get('email')

        user.first_name = first_name
        user.last_name = last_name
        user.email = email

        try:
            user.save()
        except IntegrityError:
            logger.exception("Integrity error while saving User")
            return redirect("profile")

        razorpay_id = request.POST.get('razorpay_employee_id', '').strip()
        profile.razorpay_employee_id = razorpay_id or None

        bytequest_id = request.POST.get('bytequest_employee_id', '').strip()
        profile.bytequest_employee_id = bytequest_id or None

        collaborators_raw = request.POST.get("github_outside_collaborator_name", "")
        if collaborators_raw:
            # Merge existing collaborators with new ones
            existing = set(profile.github_outside_collaborator_name.split(",")) if profile.github_outside_collaborator_name else set()
            new = set(c.strip() for c in collaborators_raw.split(",") if c.strip())
            merged = existing.union(new)
            profile.github_outside_collaborator_name = ",".join(sorted(merged))
        aliases_raw = request.POST.get('aliases', '')
        if aliases_raw:
            profile.aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
        alias_tokens_raw = request.POST.get("alias_access_token", "")
        if alias_tokens_raw:
            tokens_list = [t.strip() for t in alias_tokens_raw.split(",") if t.strip()]
            profile.alias_access_token_decrypted = tokens_list
        profile.dob = request.POST.get("dob") or None
        profile.gender = request.POST.get("gender") or None
        profile.date_of_joining = request.POST.get("date_of_joining") or None

        try:
            profile.save()
        except IntegrityError:
            logger.exception("Integrity error while saving UserProfile")
            return redirect("profile")

        logger.info(f"Profile updated for {user.username}")
        return redirect('home')

    return render(request, 'profile.html', {"profile": profile})


@login_required
def commits_data(request):
    logger.debug("Fetching commits_data")
    response = []
    try:
        user_id = request.GET.get("user_id") or request.user.id
        target_user = get_object_or_404(User, id=user_id)
        user_email = target_user.email
        logger.debug(f"User email for commits_data: {user_email}")

        # Valid commits
        valid_commit_ids = (
            FileChange.objects.exclude(changes__isnull=True)
            .exclude(changes__exact="")
            .values_list("commit_id", flat=True)
            .distinct()
        )
        # Date range: last 30 days
        end_date = timezone.now().date()
        start_date = end_date - timedelta(days=30)

        commits_qs = (
            GitCommit.objects.filter(
                author_email=user_email,
                id__in=valid_commit_ids,
                date__date__range=(start_date, end_date),
            )
            .prefetch_related("file_changes")
            .order_by("date")
        )

        # Aggregate daily commits
        daily_data = (
            commits_qs.annotate(date_only=TruncDate("date"))
            .values("date_only")
            .annotate(commit_count=Count("id"))
            .order_by("date_only")
        )

        commits_dict = {item['date_only']: item['commit_count'] for item in daily_data}

        current_date = start_date
        while current_date <= end_date:
            count = commits_dict.get(current_date, 0)
            commits_on_date = commits_qs.filter(date__date=current_date)

            total_added = total_removed = 0
            for commit in commits_on_date:
                for fc in commit.file_changes.all():
                    a, r = count_lines_changed(fc.changes)
                    total_added += a
                    total_removed += r

            response.append({
                'date': current_date.strftime("%Y-%m-%d"),
                'count': count,
                'label': target_user.username,
                'lines_added': total_added,
                'lines_removed': total_removed,
                'total_lines_changed': total_added + total_removed,
            })
            current_date += timedelta(days=1)

        logger.debug(f"Total days in graph: {len(response)}")

    except Exception as e:
        logger.exception("Error in commits_data")
        response = []

    return JsonResponse(response, safe=False)


@login_required
def logout_view(request):
    logger.info(f"{request.user.username} logging out")
    auth_logout(request)
    return redirect('login')


@login_required
def rate_commit(request, commit_id):
    logger.debug(f"rate_commit called for commit_id={commit_id}")

    if request.method == "POST":
        try:
            commit = GitCommit.objects.get(id=commit_id)

            # Directly call the model function; it handles commit_type normalization internally
            rating = commit.rate_with_gpt()
            if rating is None:
                return JsonResponse({
                    "success": False,
                    "error": "Commit could not be rated (merge commit,revert commit, already rated, or no user assigned)."
                }, status=400)

            # SAME LOGIC AS commit_ai_analyzer.py
            latest_prompt = (
                Prompt.objects.filter(type="commit_rating")
                .exclude(prompt__isnull=True)
                .order_by("-created_date", "-id")
                .first()
            )
            admin_prompt_text = latest_prompt.prompt if latest_prompt else ""

            file_changes = FileChange.objects.filter(commit=commit)
            total_score = 0
            files_analyzed = 0
            ai_reason = ""

            for fc in file_changes:
                patch = fc.changes
                if patch and len(patch.strip()) > 50:
                    ai_result = analyze_patch_with_admin_prompt(commit.commit_hash, patch, admin_prompt_text)
                    if ai_result and isinstance(ai_result, dict):
                        total_score += ai_result["score"]
                        files_analyzed += 1
                        ai_reason = (ai_result.get("reason") or "").strip()

            ai_score = round(total_score / files_analyzed) if files_analyzed else 0
            commit.append_ai_analysis(
                ai_score=ai_score,
                ai_reason=ai_reason,
                ai_raw_json={"ai_code_percentage": ai_score, "reason": ai_reason}
            )
            commit.save()

            return JsonResponse({
                "success": True,
                "rating": commit.rating,
                "message_rating": commit.message_rating,
                "commit_type": commit.commit_type,
                "reason": commit.rating_reason,
                "raw_gpt_output": commit.raw_gpt_output,
                "suggested_message": commit.suggested_message,
                "ai_score": ai_score
            })

        except GitCommit.DoesNotExist:
            logger.error(f"Commit {commit_id} not found")
            return JsonResponse({"success": False, "error": "Commit not found"}, status=404)
        except Exception as e:
            logger.exception("Error rating commit")
            return JsonResponse({"success": False, "error": str(e)}, status=500)

    return JsonResponse({"success": False, "error": "Invalid request"}, status=400)



def github_callback(request):
    logger.debug("GitHub callback triggered")

    code = request.GET.get("code")
    if not code:
        logger.error("No code received in callback")
        return render(request, "error.html", {"message": "No code provided"})

    # --- Exchange code for access token ---
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    data = {
        "client_id": settings.GITHUB_CLIENT_ID,
        "client_secret": settings.GITHUB_CLIENT_SECRET,
        "code": code,
        "redirect_uri": settings.GITHUB_OAUTH_CALLBACK,
    }

    response = requests.post(token_url, data=data, headers=headers)
    token_json = response.json()
    logger.debug("Token response: %s", token_json)

    access_token = token_json.get("access_token")
    if not access_token:
        logger.error("Failed to fetch access token")
        return render(request, "error.html", {"message": "Failed to get access token from GitHub"})
 
    user_response = requests.get(
        "https://api.github.com/user",
        headers={"Authorization": f"token {access_token}"}
    )
    if user_response.status_code != 200:
        logger.error("Failed to fetch GitHub user: %s", user_response.text)
        return render(request, "error.html", {"message": "Failed to fetch GitHub user"})

    user_data = user_response.json()
    github_username = user_data.get("login")
    github_email = user_data.get("email")
    github_name = user_data.get("name")  # <-- Full name

    

    logger.debug("GitHub user fetched: %s (name=%s, email=%s)", github_username, github_name, github_email)

    # --- If email is private, fetch from /user/emails ---
    if not github_email:
        emails_response = requests.get(
            "https://api.github.com/user/emails",
            headers={"Authorization": f"token {access_token}"}
        )
        if emails_response.status_code == 200:
            emails = emails_response.json()
            primary_emails = [e["email"] for e in emails if e.get("primary") and e.get("verified")]
            if primary_emails:
                github_email = primary_emails[0]
    logger.debug("Fetched email from /user/emails: %s", github_email)

    if github_email:
        email_lower = github_email.lower()
        is_org_email = email_lower.endswith(ORG_EMAIL_DOMAIN)
        is_whitelisted = email_lower in [e.lower() for e in ALLOWED_OUTSIDE_EMAILS]

        if not is_org_email and not is_whitelisted:
            return render(
                request,
                "error.html",
                {"message": "Access denied. You are not allowed to log in."},
            )
    org_name = None
    orgs_response = requests.get(
        "https://api.github.com/user/orgs",
        headers={"Authorization": f"token {access_token}"}
    )
    logger.debug("Org fetch status=%s, text=%s", orgs_response.status_code, orgs_response.text)

    if orgs_response.status_code == 200:
        orgs_data = orgs_response.json()
        if isinstance(orgs_data, list) and orgs_data:
            org_name = orgs_data[0].get("login")
            logger.debug("Organization found: %s", org_name)
        else:
            logger.warning("No organizations found for user %s, checking collaborator repos", github_username)

       
            repos_response = requests.get(
                "https://api.github.com/user/repos?affiliation=collaborator",
                headers={"Authorization": f"token {access_token}"}
            )
            if repos_response.status_code == 200:
                repos_data = repos_response.json()
                if repos_data:
                    org_name = repos_data[0]["owner"]["login"]
                    logger.debug("Outside collaborator detected, using repo owner as org: %s", org_name)
            else:
                logger.warning("Failed to fetch repos for collaborator: %s", repos_response.text)
    else:
        logger.warning("Failed to fetch orgs for %s: %s", github_username, orgs_response.text)

   
    user, created = User.objects.get_or_create(username=github_username)
     
    if created:
        logger.info("New user %s created from GitHub callback", github_username)
        user.set_unusable_password()
        user.save()

    if github_email:
        user.email = github_email

    if github_name:  # <-- save full name
        user.first_name = github_name.split(" ")[0] if " " in github_name else github_name
        user.last_name = " ".join(github_name.split(" ")[1:]) if " " in github_name else ""
    user.save()

    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.github_username = github_username
    profile.github_token_decrypted = access_token   

    profile.github_outside_collaborator_name = org_name


    profile.save()

    logger.info("User %s authenticated via GitHub (org: %s)", github_username, org_name or profile.github_outside_collaborator_name)

  
    user.backend = "django.contrib.auth.backends.ModelBackend"
    auth_login(request, user)

    next_url = request.session.pop("next", None)

    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("home")






@login_required
def bug_dashboard(request):
    user_bugs = Bug.objects.all()

    # Metrics
    metrics = {
        "total": user_bugs.count(),
        "open": user_bugs.filter(status="Open").count(),
        "in_progress": user_bugs.filter(status="In Progress").count(),
        "resolved": user_bugs.filter(status__in=["Resolved", "Closed"]).count(),
    }

    # Filters
    severity = request.GET.get("severity")
    status = request.GET.get("status")
    keyword = request.GET.get("q")
    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")

    bugs = user_bugs
    if severity:
        bugs = bugs.filter(severity=severity)
    if status:
        bugs = bugs.filter(status=status)
    if keyword:
        bugs = bugs.filter(Q(title__icontains=keyword) | Q(description__icontains=keyword))
    if date_from and date_to:
        bugs = bugs.filter(date_reported__range=[date_from, date_to])

    return render(request, "bug_dashboard.html", {
        "metrics": metrics,
        "bugs": bugs,
        "users": User.objects.all(),
        "repos": Repo.objects.all(),
        "request": request,
    })


@login_required
def bug_detail(request, bug_id):
    bug = get_object_or_404(Bug, id=bug_id)
    comments = bug.comments.all()

    if request.method == "POST":
        form = CommentForm(request.POST)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.user = request.user
            comment.bug = bug
            comment.save()
            return redirect("bug_detail", bug_id=bug.id)
    else:
        form = CommentForm()

    return render(request, "bug_detail.html", {"bug": bug, "comments": comments, "form": form})
 

@login_required
def add_comment(request, bug_id):
    if request.method == "POST":
        bug = get_object_or_404(Bug, id=bug_id)
        text = request.POST.get("comment")
        if text.strip():
            Comment.objects.create(bug=bug, user=request.user, text=text.strip())
    return redirect("bug-dashboard")

@login_required
def add_bug(request):
    if request.method == "POST":
        title = request.POST.get("title")
        description = request.POST.get("description")
        severity = request.POST.get("severity")
        repo_id = request.POST.get("repo")
        reported_by = request.user          
        assigned_to_id = request.POST.get("assigned_to")
        assigned_user = User.objects.get(id=assigned_to_id) if assigned_to_id else None

        repo = Repo.objects.get(id=repo_id) if repo_id else None

        Bug.objects.create(
            title=title,
            description=description,
            severity=severity,
            status="Open",
            reported_by=reported_by,
            assigned_to=assigned_user,
            repo=repo
        )

    return redirect("bug-dashboard")


@login_required
def edit_bug(request, bug_id):
    bug = get_object_or_404(Bug, id=bug_id)

    if request.user != bug.reported_by and request.user != bug.assigned_to:
        return redirect("bug-dashboard")

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        description = request.POST.get("description", "").strip()
        severity = request.POST.get("severity", bug.severity)
        status = request.POST.get("status", bug.status)
        assigned_to_id = request.POST.get("assigned_to") or None
        repo_id = request.POST.get("repo") or None

        assigned_user = User.objects.get(id=assigned_to_id) if assigned_to_id else None
        repo = Repo.objects.get(id=repo_id) if repo_id else None

        bug.title = title or bug.title
        bug.description = description or bug.description
        bug.severity = severity
        bug.status = status
        bug.assigned_to = assigned_user
        bug.repo = repo
        bug.save()

    return redirect("bug-dashboard")


@login_required
@cache_page(None)
def project_metrics_data(request):
    """
    Returns JSON data for project_metrics.html, specifically the time series
    for the top repository based on total commits.
    """
    # Get date range from request or default to last 30 days
    end_date_str = request.GET.get("end_date")
    start_date_str = request.GET.get("start_date")

    tz = get_current_timezone()
    if end_date_str and start_date_str:
        start = datetime.fromisoformat(start_date_str)
        end = datetime.fromisoformat(end_date_str)
    else:
        today = datetime.now()
        end = today
        start = today - timedelta(days=30)

    start = make_aware(datetime.combine(start.date(), datetime.min.time()), timezone=tz)
    end = make_aware(datetime.combine(end.date(), datetime.max.time()), timezone=tz)
    # FILTER ALL COMMITS FIRST for last 30 days
    all_commits = (GitCommit.objects.filter(date__range=[start, end])
        .select_related()
        .prefetch_related(
            Prefetch("file_changes", to_attr="prefetched_file_changes")
        )
        .only("id", "org_name", "repo_name", "message", "author_email", "author")
    )

    # Get unique repos in this date range
    repo_groups = defaultdict(list)
    for c in all_commits:
        repo_groups[(c.org_name, c.repo_name)].append(c)

    repo_list = []
    colors = ["blue", "red", "green", "yellow", "pink", "indigo", "purple", "orange"]

    for idx, ((org_name, repo_name), commits_qs) in enumerate(repo_groups.items()):
        total_commits = len(commits_qs)
        total_additions = 0
        total_deletions = 0
        merges = 0
        users_set = set()
        for commit in commits_qs:
            if commit.message and commit.message.startswith("Merge"):
                merges += 1
            for fc in getattr(commit, "prefetched_file_changes", []):
                added, removed = count_lines_changed(fc.changes)
                total_additions += added
                total_deletions += removed
            users_set.add(commit.author_email or commit.author)
        users_list = []
        for i, u in enumerate(users_set):
            initials = (u.split("@")[0][0].upper() if "@" in u else u[0].upper()) if u else '?'
            user_dict = {"initials": initials, "color": colors[i % len(colors)], "user_id": None, "username": u}

            user_obj = None
            if "@" in u:
                user_obj = User.objects.filter(email=u).only("id", "username").first()
            else:
                user_obj = User.objects.filter(username=u).only("id", "username").first()

            if user_obj:
                user_dict["user_id"] = user_obj.id
                user_dict["username"] = user_obj.username
            users_list.append(user_dict)

        repo_list.append({
            "id": commits_qs[0].id,
            "name": repo_name,
            "org_name": org_name,
            "commits": total_commits,
            "additions": total_additions,
            "deletions": total_deletions,
            "merges": merges,
            "users": users_list
        })
    repo_list.sort(key=lambda r: r['commits'], reverse=True)
    # Total repos
    total_repos = len(repo_list)
    # Top repo by commits
    top_repo = max(repo_list, key=lambda r: r['commits'], default=None)

    period_type = request.GET.get("period", "day")


    # Choose truncation based on period_type
    trunc_func = TruncDay("date")
    if period_type == "week":
        trunc_func = TruncWeek("date")
    elif period_type == "month":
        trunc_func = TruncMonth("date")

    # Aggregate number of unique repos per period
    time_series_qs = (
        all_commits
        .annotate(period=trunc_func)
        .values("period")
        .annotate(repo_count=Count('repo_name', distinct=True))
    )

    counts_map = {
        (row["period"].date() if hasattr(row["period"], "date") else row["period"]): row["repo_count"]
        for row in time_series_qs if row["period"]
    }

    labels = []
    values = []

    if period_type == "week":
        current = (start - timedelta(days=start.weekday())).date()
        fmt = lambda d: f"Week {d.isocalendar()[1]}, {d.year}"
        step = timedelta(weeks=1)
    elif period_type == "month":
        current = start.date().replace(day=1)
        fmt = lambda d: d.strftime("%B %Y")
        step = None
    else:
        current = start.date()
        fmt = lambda d: d.strftime("%Y-%m-%d")
        step = timedelta(days=1)

    end = end.date()  

    while current <= end:
        if period_type == "week":
            key = current
        elif period_type == "month":
            key = current.replace(day=1)
        else:
            key = current

        labels.append(fmt(current))
        values.append(counts_map.get(key, 0))

        if period_type == "month":
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1, day=1)
            else:
                current = current.replace(month=current.month + 1, day=1)
        else:
            current += step

    # REPO GROWTH CHART DATA 
    repos = Repo.objects.all().order_by("created_at")

    if repos.exists():
        first_date = repos.first().created_at.date()
        last_date = repos.last().created_at.date()
    else:
        first_date = last_date = now().date()

    start_date_only = start.date() if isinstance(start, datetime) else start
    end_date_only = end.date() if isinstance(end, datetime) else end

    growth_start = max(first_date, start_date_only)
    growth_end = min(last_date, end_date_only)

    if growth_end < end_date_only:
        growth_end = end_date_only

    filtered_repos = repos.filter(created_at__date__range=[growth_start, growth_end])

    growth_labels, growth_values = [], []
    count = repos.filter(created_at__date__lt=growth_start).count()

    current_date = growth_start
    while current_date <= growth_end:
        daily_new = filtered_repos.filter(created_at__date=current_date).count()
        count += daily_new
        growth_labels.append(current_date.strftime("%Y-%m-%d"))
        growth_values.append(count)
        current_date += timedelta(days=1)

    min_y_value = repos.filter(created_at__date__lt=growth_start).count()
    max_y_value = count
    data = {
        "total_repos": total_repos,
        "top_repo_name": top_repo['name'] if top_repo else "N/A",
        "top_repo_commits": top_repo['commits'] if top_repo else 0,
        "repos": repo_list,
        "time_series": {"labels": labels, "data": values},
        "repo_growth": {"labels": growth_labels,"data": growth_values,"min_y": min_y_value,"max_y": max_y_value,},
    }

    return JsonResponse(data)

@login_required
def repo_detail(request, repo_id):
    repo_commit = get_object_or_404(GitCommit, id=repo_id)
    org_name = repo_commit.org_name
    repo_name = repo_commit.repo_name
    tz = get_current_timezone()

    all_data = request.GET.get("all_data")
    start_date_str = request.GET.get("start_date")
    end_date_str = request.GET.get("end_date")

    if all_data == "1":
        # All commits
        commits_qs = GitCommit.objects.filter(org_name=org_name, repo_name=repo_name)
        first_commit = commits_qs.order_by("date").only("date").first()
        last_commit = commits_qs.order_by("-date").only("date").first()
        start_date = first_commit.date if first_commit else None
        end_date = last_commit.date if last_commit else None
    else:
        # Default last 30 days or custom range
        if start_date_str and end_date_str:
            try:
                start = datetime.fromisoformat(start_date_str)
                end = datetime.fromisoformat(end_date_str)
            except ValueError:
                end = datetime.now()
                start = end - timedelta(days=30)
        else:
            end = datetime.now()
            start = end - timedelta(days=30)

        start_date = make_aware(datetime.combine(start.date(), datetime.min.time()), timezone=tz)
        end_date = make_aware(datetime.combine(end.date(), datetime.max.time()), timezone=tz)
        commits_qs = GitCommit.objects.filter(
            org_name=org_name,
            repo_name=repo_name,
            date__range=[start_date, end_date]
        )

        # first/last commit 
        first_commit = GitCommit.objects.filter(org_name=org_name, repo_name=repo_name).order_by("date").only("date").first()
        last_commit = GitCommit.objects.filter(org_name=org_name, repo_name=repo_name).order_by("-date").only("date").first()
    first_commit_date = first_commit.date if first_commit else None
    last_commit_date = last_commit.date if last_commit else None
    if last_commit_date:
        now_time = timezone.now()
        diff = now_time - last_commit_date
        hours = diff.total_seconds() / 3600

        if hours < 24:
            last_commit_ago = f"{int(hours)} hour{'s' if int(hours) != 1 else ''} ago"
        else:
            days = int(hours // 24)
            last_commit_ago = f"{days} day{'s' if days != 1 else ''} ago"
    else:
        last_commit_ago = "N/A"

    # Graph:commits over time
    labels = []
    data = []
    if start_date and end_date:
        commits_by_date = commits_qs.annotate(day=TruncDate('date')).values('day').annotate(count=Count('id')).order_by('day')
        commits_map = {c['day']: c['count'] for c in commits_by_date}
        delta = (end_date.date() - start_date.date()).days
        labels = [(start_date.date() + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(delta + 1)]
        data = [commits_map.get(start_date.date() + timedelta(days=i), 0) for i in range(delta + 1)]

    # Commits per user
    users_list = commits_qs.values("author").annotate(commits=Count("id")).order_by("-commits")
    users_list = [{"username": u["author"] or "Unknown", "commits": u["commits"]} for u in users_list]
    # Commits per user chart data
    user_labels = [u["username"] for u in users_list]
    user_data = [u["commits"] for u in users_list]


    # Totals
    total_commits = commits_qs.count()
    merges = commits_qs.filter(message__startswith="Merge").count()
    reverts = commits_qs.filter(message__startswith="Revert").count()

    repo_obj = Repo.objects.filter(org_name=org_name, repo_name=repo_name).first()
    repo_data = {
        "repo_name": repo_name,
        "org_name": org_name,
        "commits": total_commits,
        "branches": repo_obj.branches if repo_obj else 0,
        "merges": merges,
        "reverts": reverts,
        "first_commits": repo_obj.first_commit_date.strftime("%Y-%m-%d") if repo_obj and repo_obj.first_commit_date else "N/A",
        "date": last_commit_date.strftime("%Y-%m-%d") if last_commit_date else "N/A",
        "last_commit_ago": last_commit_ago,
        "users": users_list,
    }

    context = {
        "repo": repo_data,
        "time_series_labels": labels,
        "time_series_data": data,
        "user_labels": user_labels,        
        "user_data": user_data,
        "start_date": start_date.strftime("%Y-%m-%d") if start_date else "",
        "end_date": end_date.strftime("%Y-%m-%d") if end_date else "",
    }
    return render(request, "admin/repo_detail.html", context)



def google_login(request):
    google_auth_url =(
        "https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={settings.GOOGLE_CLIENT_ID}&"
        "response_type=code&"
        f"redirect_uri={settings.GOOGLE_OAUTH_CALLBACK}&"
        "scope=openid email profile"
    )
    return redirect(google_auth_url)


def google_callback(request):
    code = request.GET.get("code")
    if not code:
        return render(request, "error.html", {"message": "No code returned from Google."})
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": settings.GOOGLE_CLIENT_ID,
        "client_secret": settings.GOOGLE_CLIENT_SECRET,
        "redirect_uri": settings.GOOGLE_OAUTH_CALLBACK,
        "grant_type": "authorization_code",
    }

    token_response = requests.post(token_url, data=data)
    token_json = token_response.json()

    access_token = token_json.get("access_token")
    id_token = token_json.get("id_token")

    if not access_token:
        return render(request, "error.html", {"message": "Failed to get access token from Google."})
    

    userinfo_response = requests.get(
        "https://www.googleapis.com/oauth2/v3/userinfo",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    if userinfo_response.status_code != 200:
        return render(request, "error.html", {"message": "Failed to fetch Google user info."})


    userinfo = userinfo_response.json()
    email = userinfo.get("email")
    name = userinfo.get("name")
    picture = userinfo.get("picture")
    google_id = userinfo.get("sub")

    if not email:
        return render(request, "error.html", {"message": "Google account has no public email."})

    email_lower = email.lower()
    is_org_email = email_lower.endswith(ORG_EMAIL_DOMAIN)
    is_whitelisted = email_lower in [e.lower() for e in ALLOWED_OUTSIDE_EMAILS]

    try:
        user = User.objects.get(email=email)
        created = False
    except User.DoesNotExist:
        if not is_org_email and not is_whitelisted:
            return render(
                request,
                "error.html",
                {"message": "Access denied. You are not allowed to log in."},
            )

        user = User.objects.create(
            username=email.split("@")[0],
            email=email,
            first_name=name.split(" ")[0] if name else "",
            last_name=" ".join(name.split(" ")[1:]) if name else "",
        )
        user.set_unusable_password()
        user.save()
        created = True


    profile, _ = UserProfile.objects.get_or_create(user=user)

    try:
        if not profile.google_id:
            profile.google_id = google_id
        if not profile.google_access_token:
            profile.google_access_token = access_token
        if not profile.google_avatar_url:
            profile.google_avatar_url = picture

        profile.save()
    except Exception:
        logger.exception("Google profile update failed")

    user.backend = "django.contrib.auth.backends.ModelBackend"
    auth_login(request, user)

    next_url = request.session.pop("next", None)

    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    return redirect("home")
