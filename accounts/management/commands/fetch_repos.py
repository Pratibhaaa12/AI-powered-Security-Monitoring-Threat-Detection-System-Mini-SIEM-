import requests
import os
import time
import logging
from openai import OpenAI
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from urllib3.util.retry import Retry
from django.db.models import Q
from accounts.models import UserProfile, Repo, ScriptRunLog
from django.utils.timezone import make_aware, now
from django.conf import settings
from requests.adapters import HTTPAdapter

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
    help = 'Fetch and store GitHub repositories for users (no duplicates, total repos count)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--debug',
            action='store_true',
            help='Run in debug mode (bypass time restrictions)',
        )
        parser.add_argument(
            '--user_id',
            type=int,
            help='Fetch repositories for a specific user ID only',
        )

    def handle(self, *args, **kwargs):
        print("Starting GitHub repository fetch script...")
        logger.info("GitHub repository fetch script started")

        now_ist = get_ist_now()
        print(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")
        logger.info(f"Current IST time: {now_ist.strftime('%Y-%m-%d %H:%M')}")

        debug_mode = kwargs.get('debug', False)
        if debug_mode:
            print("Running in debug mode - bypassing time restrictions")
            logger.info("Running in debug mode - bypassing time restrictions")

        print("Starting to fetch repositories...")
        self.fetch_repos(user_id=kwargs.get("user_id"))
        print("Github Repository fetch completed!")

    def get_session(self):
        """Create a requests session with retry logic"""
        session = requests.Session()
        retries = Retry(
            total=5,
            backoff_factor=1,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"]
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
        """Handle paginated GitHub API results"""
        session = self.get_session()
        results = []
        url = initial_url
        while url:
            try:
                resp = session.get(url, headers=headers, params=params, timeout=30)
                resp.raise_for_status()
            except requests.exceptions.RequestException as e:
                logger.error(f"Request failed: {e}")
                break
            results.extend(resp.json())
            url = resp.links.get("next", {}).get("url")
        return results

    def fetch_repos(self, user_id=None):
        print("Starting GitHub repository fetch process...")
        logger.info("Starting GitHub repository fetch process.")

        log, _ = ScriptRunLog.objects.get_or_create(name="fetch_repos")

        # Initialize logs with execution start info
        execution_logs = [
            f"Execution started at: {now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Run count: {log.run_count + 1}"
        ]
        log.last_run_at = now()
        log.run_count += 1
        log.logs = "\n".join(execution_logs)
        log.save()

        # Fetch users with valid GitHub tokens
        users = UserProfile.objects.filter(
            Q(github_token__isnull=False) & ~Q(github_token__exact='') |
            Q(alias_access_token__isnull=False) & ~Q(alias_access_token__exact=[])
        )
        if user_id:
            users = users.filter(user__id=user_id)

        log.logs += f"\nUsers to process: {users.count()}"
        log.save()

        processed_users = 0  
        total_unique_repos = set()

        for profile in users:
            user = profile.user
            print(f"Processing user: {user.username}")
            logger.info(f"Processing user: {user.username}")

            user_repo_count = 0
            seen_repos = set()
            all_tokens = []

            if profile.github_token_decrypted:
                all_tokens.append(profile.github_token_decrypted)
            if profile.alias_access_token_decrypted:
                all_tokens.extend(profile.alias_access_token_decrypted)

            user_repos_combined = []

            for token in all_tokens:
                headers = {
                    'Authorization': f'token {token}',
                    'Accept': 'application/vnd.github.v3+json',
                }
                try:
                    # Fetch user repos
                    user_repos_url = "https://api.github.com/user/repos?visibility=all&affiliation=owner,collaborator,organization_member"
                    user_repos = self.get_paginated_results(user_repos_url, headers)

                    # Fetch org repos
                    orgs_url = "https://api.github.com/user/orgs"
                    orgs = self.get_paginated_results(orgs_url, headers)
                    for org in orgs:
                        org_name = org["login"]
                        org_repos_url = f"https://api.github.com/orgs/{org_name}/repos"
                        org_repos = self.get_paginated_results(org_repos_url, headers, params={'type': 'all'})
                        user_repos.extend(org_repos)

                    # Merge user + org repos
                    for repo in user_repos:
                        unique_key = f"{repo['owner']['login']}/{repo['name']}"
                        if unique_key not in seen_repos:
                            seen_repos.add(unique_key)
                            user_repos_combined.append(repo)

                except Exception as e:
                    logger.error(f"Token failed for user {user.username}: {e}")
                    continue

            print(f"Found {len(user_repos_combined)} total repositories (user + org ) for {user.username}")
            logger.info(f"Found {len(user_repos_combined)} total repositories (user + org) for {user.username}")

            # Save repos in DB
            for repo in user_repos_combined:
                repo_name = repo['name']
                owner = repo['owner']['login']
                unique_key = f"{owner}/{repo_name}"
                total_unique_repos.add(unique_key)
                user_repo_count += 1
                # Save repo in DB without duplicates and link user
                repo_obj, created = Repo.objects.get_or_create(
                    repo_name=repo_name,
                    org_name=owner,
                    defaults={
                        "created_at": make_aware(datetime.strptime(repo['created_at'], "%Y-%m-%dT%H:%M:%SZ")),
                        "updated_at": make_aware(datetime.strptime(repo['updated_at'], "%Y-%m-%dT%H:%M:%SZ")),
                    }
                )
                # Ensure M2M link once
                if not repo_obj.users.filter(id=user.id).exists():
                    repo_obj.users.add(user)
                    repo_obj.save()

                if created:
                    print(f"New repo added: {unique_key} --- {user.username}")
                else:
                    print(f"Repo exists: {unique_key} --- {user.username}")

            processed_users += 1
            log.logs += f"\nUser {user.username}: {user_repo_count} unique repos processed"
            log.save()

        # Final summary
        final_summary = [
            "\n=== EXECUTION SUMMARY ===",
            f"Processed users: {processed_users}",
            f"Total unique repositories: {len(total_unique_repos)}",
            f"Execution completed at: {now().strftime('%Y-%m-%d %H:%M:%S')}"
        ]
        log.logs += "\n" + "\n".join(final_summary)
        log.save()

        ScriptRunLog.objects.update_or_create(
            name="fetch_repos",
            defaults={"last_run_at": now()}
        )

        logger.info("GitHub repository fetch completed successfully.")
