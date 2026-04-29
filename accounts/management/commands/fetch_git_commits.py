import requests
import re
import os
import time
import logging
import json
import concurrent.futures
from openai import OpenAI
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from urllib3.util.retry import Retry
from accounts import models
from django.db.models import Q, Count
from django.contrib.auth import get_user_model

from accounts.models import UserProfile, GitCommit, FileChange, ScriptRunLog, Repo, Branch
from django.utils.timezone import make_aware, now, localtime
from django.conf import settings
from requests.adapters import HTTPAdapter
import threading
USER_TOKEN_LOCKS = {}


# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Console output
    ]
)

client = OpenAI(api_key=settings.OPENAI_API_KEY)
logger = logging.getLogger('accounts')

def get_ist_now():
    return datetime.utcnow() + timedelta(hours=5, minutes=30)


class Command(BaseCommand):
    help = 'Fetch and store GitHub commits repo-wise using tokens of users in that repo'

    def add_arguments(self, parser):
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (bypass time restrictions)',
        )
        parser.add_argument(
            '--date',
            type=str,
            help='Fetch commits for a specific date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--start-date',
            type=str,
            help='Fetch commits from start date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--end-date',
            type=str,
            help='Fetch commits until end date (YYYY-MM-DD)',
        )
        parser.add_argument(
            '--repo',
            type=str,
            help='Fetch commits for a specific repo (format: org_name/repo_name)',
        )
        parser.add_argument(
            '--org',
            type=str,
            help='Fetch commits for a specific org name',
        )
        parser.add_argument(
            '--user-id',
            type=int,
            help='Fetch commits only for repos linked with this user ID',
        )

    def handle(self, *args, **kwargs):
        self.repo_arg = kwargs.get('repo', None)
        self.org_arg = kwargs.get('org', None)
        self.user_id_arg = kwargs.get('user_id', None)
        print("🚀 Starting GitHub commit fetch script...")
        logger.info("GitHub commit fetch script started.")

        now_ist = get_ist_now()
        today = now_ist.date()
        print(f"📅 Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")

        # Check if we're in debug mode (force execution)
        debug_mode = kwargs.get('debug', False)
        if debug_mode:
            print("🐛 Running in debug mode - bypassing time restrictions")
            logger.info("Running in debug mode - bypassing time restrictions")
        print("📥 Starting to fetch git commits...")
        self.fetch_git_commits(date=kwargs.get("date"),start_date=kwargs.get("start_date"),end_date=kwargs.get("end_date"),user_id=self.user_id_arg)
        print("Git commit fetch completed!")
    def get_session(self):
        """Create a requests session with retry logic"""
        session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS", "POST"]
        )
        adapter = HTTPAdapter(max_retries=retries)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session
    
    def get_next_page_url(self, headers):
        link = headers.get('Link')
        if link:
            parts = link.split(',')
            for part in parts:
                if 'rel="next"' in part:
                    return part[part.find('<') + 1:part.find('>')]
        

    def get_paginated_results(self, initial_url, headers, params=None):
        session = self.get_session()
        results = []
        url = initial_url
        while url:
            try:
                resp = session.get(url, headers=headers, params=params, timeout=30)
                
                # Detect and handle rate limit
                if resp.status_code == 403 and 'X-RateLimit-Remaining' in resp.headers:
                    remaining = resp.headers.get('X-RateLimit-Remaining')
                    if remaining == '0':
                        reset_time = int(resp.headers.get('X-RateLimit-Reset', time.time() + 60))
                        sleep_for = max(reset_time - time.time(), 60)
                        reset_at = datetime.utcfromtimestamp(reset_time).strftime("%Y-%m-%d %H:%M:%S")
                        logger.warning(f"⚠ GitHub API rate limit reached. Sleeping {sleep_for:.0f}s (resets at {reset_at} UTC).")
                        time.sleep(sleep_for)
                        continue
                    else:
                        logger.error(f"403 Forbidden - not rate limit. Response: {resp.text}")
                        break

                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                break
            # Successful response
            try:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data)
                else:
                    results.append(data)
            except Exception:
                logger.error("Failed to parse JSON from response while paginating.")
                break
            url = resp.links.get("next", {}).get("url")
        return results

    def _process_repo(self, repo_obj, start_dt, end_dt, user_id, log):
        owner = repo_obj.org_name
        repo_name = repo_obj.repo_name
        print(f"Processing repo: {owner}/{repo_name}")
        logger.info(f"Processing repo: {owner}/{repo_name}")

        #process only the specific user if --user-id is provided
        if user_id is not None:
            users_in_repo_qs = repo_obj.users.filter(id=user_id)
        else:
            users_in_repo_qs = repo_obj.users.all()

        # Convert queryset to list for sorting and iteration
        users_in_repo_list = list(users_in_repo_qs)

        try:
            user_repo_map = getattr(self, "USER_REPO_COUNT_MAP", {})
        except Exception:
            user_repo_map = {}

        # Build a default count for any user not present in map
        def user_repo_count(u):
            return user_repo_map.get(u.id, 0)

        # Sort users by global repo count (ascending)
        users_in_repo_list_sorted = sorted(users_in_repo_list, key=lambda u: user_repo_count(u))

        print(f"Found {len(users_in_repo_list_sorted)} users linked with repo {repo_name}")
        logger.info(f"Found {len(users_in_repo_list_sorted)} users linked with repo {repo_name}")

        print("Sorted users (by repo access count asc):")
        for u in users_in_repo_list_sorted:
            print(f"   {u.username} -> {user_repo_count(u)} repos")

        seen_commits = set()
        session = self.get_session()
        user_commits = 0
        total_commits = 0

        if not users_in_repo_list_sorted:
            log.logs += f"\nRepo {owner}/{repo_name}: 0 users linked, skipping"
            log.save()
            return (0, 0)

        user_idx = 0
        last_tried_user_idx = 0
        last_tried_token_idx = 0

        rate_limit_resets = []

        final_headers_for_repo = None

        while user_idx < len(users_in_repo_list_sorted):
            user = users_in_repo_list_sorted[user_idx]
            last_tried_user_idx = user_idx
            user_profile = UserProfile.objects.filter(user=user).first()
            if not user_profile:
                user_idx += 1
                continue

            all_tokens = []
            if getattr(user_profile, "github_token_decrypted", None):
                all_tokens.append(user_profile.github_token_decrypted)
            # Safe alias token parsing
            if getattr(user_profile, "alias_access_token_decrypted", None):
                alias_tokens = user_profile.alias_access_token_decrypted
                if isinstance(alias_tokens, str):
                    try:
                        alias_tokens = json.loads(alias_tokens)
                    except Exception:
                        alias_tokens = [t.strip() for t in alias_tokens.split(",") if t.strip()]
                if isinstance(alias_tokens, list):
                    all_tokens.extend(alias_tokens)

            if not all_tokens:
                print(f"⚠ No tokens available for user {user.username}; skipping this user.")
                logger.warning(f"No tokens for user {user.username} while processing {owner}/{repo_name}.")
                user_idx += 1
                continue

            token_idx = 0
            token_success_for_user = False
            while token_idx < len(all_tokens):
                token = all_tokens[token_idx]
                last_tried_token_idx = token_idx
                headers = {
                    'Authorization': f'token {token}',
                    'Accept': 'application/vnd.github.v3+json',
                }

                # try to fetch branches with this token to check validity and rate limit status
                try:
                    probe_url = f"https://api.github.com/repos/{owner}/{repo_name}/branches"
                    probe_resp = session.get(probe_url, headers=headers, timeout=20)
                except requests.exceptions.RequestException as e:
                    logger.error(f"Request failed during probe for {user.username}: {e}")
                    token_idx += 1
                    continue

                # Rate limit detection
                if probe_resp.status_code == 403 and 'X-RateLimit-Remaining' in probe_resp.headers:
                    remaining = probe_resp.headers.get('X-RateLimit-Remaining')
                    if remaining == '0':
                        reset_time = probe_resp.headers.get('X-RateLimit-Reset')
                        try:
                            reset_val = int(reset_time)
                        except Exception:
                            reset_val = int(time.time() + 60)
                        rate_limit_resets.append(reset_val)
                        token_idx += 1
                        continue
                    else:
                        token_idx += 1
                        continue

                if probe_resp.status_code in (401, 404):
                    token_idx += 1
                    continue

                if probe_resp.status_code >= 500:
                    token_idx += 1
                    continue

                token_success_for_user = True
                final_headers_for_repo = headers  # store headers for later latest-commit fetch
                print(f"Using token of user {user.username} for repo {owner}/{repo_name}")
                logger.info(f"Using user {user.username} to fetch {owner}/{repo_name}")
                # Acquire per-user token lock (prevents parallel token usage)
                if user.id not in USER_TOKEN_LOCKS:
                    USER_TOKEN_LOCKS[user.id] = threading.Lock()

                with USER_TOKEN_LOCKS[user.id]:


                    rate_limit_resets = []

                # FIXED BRANCH FETCH LOGIC
                branches = []
                branches_fetched = False

                branches_url = f"https://api.github.com/repos/{owner}/{repo_name}/branches"
                branches = self.get_paginated_results(branches_url, headers)
                if branches:
                    branches_fetched = True

                if not branches_fetched:
                    try:
                        resp_b = session.get(branches_url, headers=headers, timeout=30)
                        if resp_b.status_code == 200:
                            try:
                                branches = resp_b.json()
                                branches_fetched = True
                            except Exception:
                                logger.error(f"Failed to parse branches JSON for {owner}/{repo_name}")
                                branches = []
                    except requests.exceptions.RequestException as e:
                        logger.error(f"Branch fetch failed for {owner}/{repo_name} with user {user.username}: {e}")
                        branches = []

                Repo.objects.filter(id=repo_obj.id).update(branches=len(branches) if branches else 0)

                if not branches:
                    # No branches found → use repo created_at as updated_at
                    Repo.objects.filter(id=repo_obj.id).update(updated_at=repo_obj.created_at)
                    logger.warning(f"No branches fetched for {owner}/{repo_name}. Setting updated_at = created_at.")
                    return (1, 0)


                # 2. For each branch, fetch commits
                for branch in branches:
                    branch_name = branch.get('name') if isinstance(branch, dict) else branch
                    logger.info(f"[{user.username}] Repo: {repo_name}, Branch: {branch_name}")

                    if not branch_name:
                        continue

                    commits_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits"
                    params = {
                        'since': f"{start_dt}T00:00:00Z",
                        'until': f"{end_dt}T23:59:59Z",
                        'sha': branch_name
                    }

                    commits = self.get_paginated_results(commits_url, headers, params=params)

                    # BRANCH TABLE 
                    if commits:
                        # commits may be list of dicts or a single dict appended earlier; ensure list
                        commits_list = commits if isinstance(commits, list) else [commits]
                        sorted_commits = sorted(
                            commits_list,
                            key=lambda c: c['commit']['author']['date'],
                            reverse=True
                        )
                        latest_commit = sorted_commits[0]
                    else:
                        # If no commits in date range → fetch latest commit from GitHub for this branch
                        latest_commit_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits"
                        params_latest = {'sha': branch_name, 'per_page': 1}
                        try:
                            resp_latest = session.get(latest_commit_url, headers=headers, params=params_latest, timeout=30)
                        except requests.exceptions.RequestException as e:
                            logger.error(f"Failed to fetch latest commit for {owner}/{repo_name}: {e}")
                            resp_latest = None

                        if resp_latest and resp_latest.status_code == 200 and resp_latest.json():
                            latest_commit = resp_latest.json()[0]
                        else:
                            latest_commit = None

                    if latest_commit:
                        last_commit_username = (
                            (latest_commit.get('author', {}) or {}).get('login')
                            or latest_commit['commit']['author']['name']
                        )
                        try:
                            last_commit_date = make_aware(
                                datetime.strptime(latest_commit['commit']['author']['date'], "%Y-%m-%dT%H:%M:%SZ")
                            )
                        except Exception:
                            last_commit_date = now()
                    else:
                        last_commit_username = None
                        last_commit_date = None

                    if last_commit_date:
                        updated_at_value = last_commit_date
                    else:
                        # Try to fetch branch creation date from branch API data
                        branch_commit_info = branch.get('commit', {}) if isinstance(branch, dict) else {}
                        commit_info = branch_commit_info.get('commit', {}).get('committer', {})
                        commit_date_str = commit_info.get('date') if isinstance(commit_info, dict) else None
                        if commit_date_str:
                            try:
                                updated_at_value = make_aware(datetime.strptime(commit_date_str, "%Y-%m-%dT%H:%M:%SZ"))
                            except Exception:
                                updated_at_value = now()
                        else:
                            updated_at_value = now()  

                    models.Branch.objects.update_or_create(
                        repo=repo_obj,
                        branch_name=branch_name,
                        defaults={
                            'last_commit_username': last_commit_username,
                            'updated_at': updated_at_value,
                        }
                    )
                    logger.info(
                        f"Branch added/updated: {branch_name} | "
                        f"Last commit by {last_commit_username or 'N/A'} on {updated_at_value}"
                    )

                    for commit in commits:
                        commit_hash = commit['sha']
                        if commit_hash in seen_commits:
                            continue  # skip duplicate commit across branches
                        seen_commits.add(commit_hash)

                        message = commit['commit']['message']
                        date_str = commit['commit']['author']['date']
                        try:
                            date = make_aware(datetime.strptime(date_str, "%Y-%m-%dT%H:%M:%SZ"))
                        except Exception:
                            date = now()

                        commit_details_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits/{commit_hash}"
                        # Commit Details (Never Skip, Wait on Rate Limit, Resume Exactly) 

                        def fetch_commit_details():
                            while True:
                                try:
                                    resp = session.get(commit_details_url, headers=headers, timeout=30)

                                    if resp.status_code == 403 and "X-RateLimit-Remaining" in resp.headers:
                                        remaining = resp.headers.get("X-RateLimit-Remaining")
                                        if remaining == "0":
                                            reset_ts = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                                            reset_ist = datetime.utcfromtimestamp(reset_ts) + timedelta(hours=5, minutes=30)
                                            wait_s = max(reset_ts - time.time(), 5)

                                            logger.warning(
                                                f"[{user.username}] Commit details RATE LIMIT. "
                                                f"Sleeping {wait_s:.0f}s until {reset_ist.strftime('%Y-%m-%d %H:%M:%S')} IST"
                                            )

                                            time.sleep(wait_s)
                                            continue

                                    if resp.status_code >= 500:
                                        logger.warning(
                                            f"[{user.username}] Server error {resp.status_code} in commit details. Retrying..."
                                        )
                                        time.sleep(3)
                                        continue

                                    if resp.status_code == 200:
                                        return resp.json()

                                    logger.warning(
                                        f"[{user.username}] Unexpected status {resp.status_code} for commit details. Retrying..."
                                    )
                                    time.sleep(2)

                                except Exception as e:
                                    logger.warning(f"[{user.username}] Commit details exception: {e}")
                                    time.sleep(2)


                        commit_data = fetch_commit_details()
                        files = commit_data.get('files', [])
                        # Get author information from commit details response
                        # Prioritize login over name for author identification with null checks                        
                        author = (commit_data.get('author', {}).get('login') if commit_data.get('author') else None) or commit_data['commit']['author']['name']
                        author_email = commit_data['commit']['author'].get('email') if commit_data['commit']['author'] else None
                        # Get the user who actually made the commit (regardless of current logged-in user)

                        is_merge = False
                        is_revert = False
                        if message.startswith("Merge"):
                            is_merge = True
                        if message.startswith("Revert"):
                            is_revert = True
                        commit_url = f"https://github.com/{owner}/{repo_name}/commit/{commit_hash}"

                        git_commit, _ = GitCommit.objects.update_or_create(
                            commit_hash=commit_hash,
                            defaults={
                                'repo_name': repo_name,
                                'author': author,
                                'author_email': author_email,
                                'message': message,
                                'date': date,
                                'org_name': owner,
                                'raw_data': commit_data,
                                'is_merge': is_merge,
                                'is_revert': is_revert,
                                'url': commit_url,
                            }
                        )
                        # Avoid duplicate FileChange entries for the same commit
                        existing_files = set(
                            FileChange.objects.filter(commit=git_commit).values_list('filename', flat=True)
                        )

                        for file in files:
                            filename = file.get('filename')
                            if filename in existing_files:
                                continue  # Skip if already saved

                            FileChange.objects.create(
                                commit=git_commit,
                                filename=filename,
                                changes=file.get('patch', ''),
                                file_url=file.get('blob_url') or file.get('raw_url') or ''
                            )
                        matched_profiles = []
                        if git_commit.author_email and user_profile.user and git_commit.author_email == user_profile.user.email:
                            matched_profiles.append(user_profile)
                        if git_commit.author and user_profile.github_outside_collaborator_name:
                            outside_collaborators = [
                                c.strip() for c in user_profile.github_outside_collaborator_name.split(",") if c.strip()
                            ]
                            if git_commit.author in outside_collaborators:
                                matched_profiles.append(user_profile)
                        if matched_profiles:
                            matched_profile = matched_profiles[0]
                            git_commit.user = matched_profile.user
                            user_commits += 1
                            total_commits += 1
                            git_commit.save()
                        else:
                            git_commit.save()

                        print(f"Repo: {owner}/{repo_name}")
                        print(f"Commit by: {author}")
                        print(f"Message: {message}\n")

                repo_handled = True
                user_token_success = True
                break

            if token_success_for_user:
                print(f"✅ Repo {owner}/{repo_name} fetched successfully using user {user.username}. Moving to next repo.")
                logger.info(f"Repo {owner}/{repo_name} completed with user {user.username}.")
                break 

            user_idx += 1

            if user_idx >= len(users_in_repo_list_sorted):
                if rate_limit_resets:
                    earliest_reset = min(rate_limit_resets)
                    now_ts = int(time.time())
                    sleep_seconds = max(earliest_reset - now_ts, 0)

                    reset_dt_utc = datetime.utcfromtimestamp(earliest_reset)
                    reset_dt_ist = reset_dt_utc + timedelta(hours=5, minutes=30)

                    msg = (
                        f"\n All tokens for repo {owner}/{repo_name} are rate-limited.\n"
                        f"Will resume at: {reset_dt_ist.strftime('%Y-%m-%d %H:%M:%S')} IST\n"
                        f"Waiting {sleep_seconds} seconds...\n"
                    )

                    print(msg)
                    logger.warning(msg)

                    time.sleep(sleep_seconds + 1)

                    print(f"Resuming repo {owner}/{repo_name} now...\n")
                    logger.info(f"Resuming repo {owner}/{repo_name} after rate limit reset.")

                    user_idx = last_tried_user_idx
                    rate_limit_resets = []

                    continue
                else:
                    logger.info(f"No working tokens found for any user of {owner}/{repo_name}. Skipping repo.")
                    break


        log.logs += f"\nRepo {owner}/{repo_name}: {len(seen_commits)} unique commits"
        log.save()

        # Update Repo.updated_at with the latest commit date
        # FINAL FIX: latest commit = max branch updated_at
        latest_commit_date_to_set = Branch.objects.filter(
            repo=repo_obj
        ).order_by('-updated_at').values_list('updated_at', flat=True).first()

        if latest_commit_date_to_set:
            Repo.objects.filter(id=repo_obj.id).update(updated_at=latest_commit_date_to_set)
        
        # FIRST COMMIT OF REPO
        first_commit_date_to_set = None

        try:
            first_url = f"https://api.github.com/repos/{owner}/{repo_name}/commits"
            params_first = {'per_page': 1}  

            headers_to_use = final_headers_for_repo or {'Accept': 'application/vnd.github.v3+json'}
            first_resp = session.get(first_url, headers=headers_to_use, params=params_first, timeout=30)

            if first_resp.status_code == 200:
                link_header = first_resp.headers.get("Link", "")

                last_page_url = None
                for part in link_header.split(","):
                    if 'rel="last"' in part:
                        last_page_url = part[part.find("<") + 1 : part.find(">")]
                        break

                if last_page_url:
                    last_resp = session.get(last_page_url, headers=headers_to_use, timeout=30)

                    if last_resp.status_code == 200:
                        data = last_resp.json()
                        if data:
                            oldest_commit = data[-1]
                            first_date_str = oldest_commit['commit']['author']['date']
                            try:
                                first_commit_date_to_set = make_aware(
                                    datetime.strptime(first_date_str, "%Y-%m-%dT%H:%M:%SZ")
                                )
                            except Exception:
                                first_commit_date_to_set = None

        except Exception as e:
            logger.error(f"Error fetching first commit for {owner}/{repo_name}: {e}")

        if first_commit_date_to_set:
            try:
                Repo.objects.filter(id=repo_obj.id).update(first_commit_date=first_commit_date_to_set)
                logger.info(
                    f"First commit date saved for {owner}/{repo_name}: {first_commit_date_to_set}"
                )
            except Exception as e:
                logger.error(f"Failed to update first_commit_date for {owner}/{repo_name}: {e}")

        # Return stats for aggregation.
        return (1, total_commits)

    def fetch_git_commits(self, repo=None, date=None, start_date=None, end_date=None, user_id=None):
        previous_day = (now() - timedelta(days=1)).date()
        if start_date and end_date:
            try:
                start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                print(f" Invalid date format for range. Using default yesterday.")
                start_dt = end_dt = previous_day
            print(f"📅 Fetching commits from: {start_dt} to {end_dt}")
            logger.info(f"Fetching commits from: {start_dt} to {end_dt}")
        elif date:
            try:
                previous_day = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                print(f" Invalid date format: {date}. Using default yesterday.")
            print(f"📅 Fetching commits from specific day: {previous_day}")
            logger.info(f"Fetching commits from specific day: {previous_day}")
            start_dt = end_dt = previous_day
        else:
            print(f"📅 Fetching commits from previous day: {previous_day}")
            logger.info(f"Fetching commits from previous day: {previous_day}")
            start_dt = end_dt = previous_day

        log, _ = ScriptRunLog.objects.get_or_create(name="fetch_git_commits")
        
        # Initialize logs with execution start info
        execution_logs = [
            f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Target date: {start_dt} to {end_dt}",
            f"Run count: {log.run_count + 1}"
        ]

        log.last_run_at = now()
        log.run_count += 1
        log.logs = "\n".join(execution_logs)
        log.save()
        print("Starting GitHub commit fetch process (repo-wise)...")
        logger.info("Starting GitHub commit fetch process (repo-wise).")

        # Determine repos to process
        processed_repos = 0
        total_commits = 0

        if self.repo_arg:
            try:
                org_name, repo_name = self.repo_arg.split('/')
                repos = Repo.objects.filter(org_name=org_name, repo_name=repo_name)
                if not repos.exists():
                    print(f"No repo found with name: {self.repo_arg}")
                    logger.warning(f"No repo found with name: {self.repo_arg}")
                    return
            except ValueError:
                print(f"Invalid --repo format: {self.repo_arg}. Use org_name/repo_name")
                logger.error(f"Invalid --repo format: {self.repo_arg}")
                return
        elif self.org_arg:
            repos = Repo.objects.filter(org_name=self.org_arg)
            print(f"Filtering repos for org: {self.org_arg} ({repos.count()} found)")
            logger.info(f"Filtering repos for org: {self.org_arg} ({repos.count()} found)")
        elif user_id is not None:
            try:
                user = models.User.objects.get(id=user_id)
            except models.User.DoesNotExist:
                print(f"No user found with id: {user_id}")
                logger.error(f"No user found with id: {user_id}")
                return
            repos = Repo.objects.filter(users__id=user_id).distinct()
            print(f"Filtering repos for user_id={user_id} ({repos.count()} found)")
            logger.info(f"Filtering repos for user_id={user_id} ({repos.count()} found)")
        else:
            repos = Repo.objects.all().order_by("id")
        print(f"Found {repos.count()} repositories in database")
        logger.info(f"Found {repos.count()} repositories in database")

        # Update logs with repo count
        log.logs += f"\nRepositories to process: {repos.count()}"
        log.save()

        # Build USER -> REPO COUNT list (ASC)
        User = get_user_model()

        print("\nCalculating repo access count for all users...")
        logger.info("Calculating repo access count for all users...")

        user_repo_counts_qs = (
            User.objects
            .filter(repos__isnull=False)
            .annotate(total_repos=Count("repos", distinct=True))
            .values("id", "username", "total_repos")
        )

        user_repo_list = sorted(list(user_repo_counts_qs), key=lambda x: x["total_repos"])

        print("\n============= USER → REPO COUNT =============")
        for ur in user_repo_list:
            print(f"👤 {ur['username']} → {ur['total_repos']} repos")
        print("====================================================\n")

        logger.info(f"User repo access list created ({len(user_repo_list)} users).")

        USER_REPO_COUNT_MAP = {u["id"]: u["total_repos"] for u in user_repo_list}
        setattr(self, "USER_REPO_COUNT_MAP", USER_REPO_COUNT_MAP)

        repo_list = list(repos)
        max_workers = 3
        # Continuous pool (3 running at once, next starts when one finishes)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_repo = {executor.submit(self._process_repo, repo_obj, start_dt, end_dt, user_id, log): repo_obj for repo_obj in repo_list}
            for future in concurrent.futures.as_completed(future_to_repo):
                repo_obj = future_to_repo[future]
                try:
                    repo_count, repo_commits = future.result()
                    processed_repos += repo_count
                    total_commits += repo_commits
                    print(f"Completed: {repo_obj.org_name}/{repo_obj.repo_name}")
                except Exception as exc:
                    logger.error(f"Exception in {repo_obj.org_name}/{repo_obj.repo_name}: {exc}")

        # Final summary
        final_summary = [
            f"\n=== EXECUTION SUMMARY ===",
            f"Processed repos: {processed_repos}",
            f"Total commits found: {total_commits}",
            f"Execution completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}"
        ]
        
        log.logs += "\n" + "\n".join(final_summary)
        log.save()

        ScriptRunLog.objects.update_or_create(
            name="fetch_git_commits",
            defaults={"last_run_at": now()}
        )
        logger.info("GitHub commit fetch completed.")