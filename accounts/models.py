import logging
from django.contrib.auth.models import User
from cryptography.fernet import Fernet
from django.db import models
from accounts.utils import get_fernet
from django.utils import timezone
from django.utils.timezone import now
from django.conf import settings
from django.core import signing
from datetime import datetime, timedelta
# accounts/models.py
import openai
from accounts.utils import get_employee_leave_status
from django.conf import settings
from django.db import models
from django.contrib.auth.models import User
from django.contrib.postgres.fields import ArrayField
import json

# Initialize logger
logger = logging.getLogger('accounts')


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    github_username = models.CharField(max_length=255, unique=True, blank=True, null=True)
    dob = models.DateField(null=True, blank=True, verbose_name="Date of Birth")

    GENDER_CHOICES = (
        ("male", "Male"),
        ("female", "Female"),
        ("other", "Other"),
    )
    gender = models.CharField(max_length=10, choices=GENDER_CHOICES, null=True, blank=True)
    date_of_joining = models.DateField(null=True, blank=True, verbose_name="Date of Joining")
    github_token = models.TextField(blank=True, null=True)
    github_outside_collaborator_name = models.TextField(blank=True, null=True)
    aliases = ArrayField(models.CharField(max_length=255, blank=True),default=list,blank=True,help_text="List of possible commit names/emails this user might use"
    )
    alias_access_token = ArrayField(models.TextField(),default=list,blank=True, help_text="Multiple GitHub tokens for fetching commits")
    last_login = models.DateTimeField(blank=True, null=True)
    ignore_missed_checkin = models.BooleanField(default=False, help_text="Exclude this user from missed check-in notifications.")
    bytequest_employee_id = models.CharField(max_length=20, null=True, blank=True, verbose_name="ByteQuest Employee ID",help_text="Internal employee ID for ByteQuest.")
    razorpay_employee_id = models.CharField(max_length=20, null=True, blank=True, verbose_name="Razorpay Employee ID", help_text="Employee ID as recognized by RazorpayX Payroll.")
    google_id = models.CharField(max_length=255, blank=True, null=True)
    google_access_token = models.TextField(blank=True, null=True)
    google_avatar_url = models.URLField(blank=True, null=True)


    def all_names(self):
        names = {self.user.username}
        if self.aliases:
            names.update(self.aliases)
        return names

    @property
    def github_token_decrypted(self):
        if self.github_token:
            try:
                return signing.loads(self.github_token)
            except signing.BadSignature:
                logger.exception(f"Token signature mismatch for {self.user.username}")
        return ""
    
    @github_token_decrypted.setter
    def github_token_decrypted(self,value):
        if value:
            self.github_token = signing.dumps(value)
        else:
            self.github_token = None


 
    

    @property
    def alias_access_token_decrypted(self):
        """
        Returns a list of decrypted alias tokens.
        Skips invalid/malformed tokens instead of crashing.
        """
        decrypted = []
        valid_tokens = []

        for token in self.alias_access_token or []:
            try:
                decrypted_token = signing.loads(token)
                decrypted.append(decrypted_token)
                valid_tokens.append(token)  # Keep valid tokens
            except signing.BadSignature:
                logger.warning(f"Alias token signature mismatch for {self.user.username}. Skipping invalid token.")

        # Optional: overwrite DB with only valid tokens to prevent repeated errors
        if len(valid_tokens) != len(self.alias_access_token):
            self.alias_access_token = valid_tokens
            self.save(update_fields=['alias_access_token'])

        return decrypted

    @alias_access_token_decrypted.setter
    def alias_access_token_decrypted(self, values):
        if values:
            self.alias_access_token = [signing.dumps(v.strip()) for v in values if v.strip()]
        else:
            self.alias_access_token = []

    def get_commits_by_date_range(self, start_date=None, end_date=None, repo_name=None, limit=None):
        """
        Get commits for this user profile by date range.
        Defaults to yesterday if no dates provided.
        
        Args:
            start_date (datetime, optional): Start date for filtering commits
            end_date (datetime, optional): End date for filtering commits
            repo_name (str, optional): Filter commits for a specific repository
            limit (int, optional): Limit the number of results returned
            
        Returns:
            QuerySet: GitCommit objects for this user within the date range
        """
        # Default to yesterday if no dates provided
        if start_date is None and end_date is None:
            yesterday = now().date() - timedelta(days=1)
            start_date = datetime.combine(yesterday, datetime.min.time())
            end_date = datetime.combine(yesterday, datetime.max.time())
        
        # Build the queryset directly to avoid circular import
        queryset = self.user.gitcommit_set.all()
        
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
            
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
            
        if repo_name:
            queryset = queryset.filter(repo_name=repo_name)
            
        # Order by date descending (most recent first)
        queryset = queryset.order_by('-date')
        
        if limit:
            queryset = queryset[:limit]
            
        return queryset


    def __str__(self):
        return f"{self.user.username}'s Profile"
    

class GitCommit(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    commit_hash = models.CharField(max_length=40, unique=True)
    repo_name = models.CharField(max_length=255)
    org_name = models.CharField(max_length=255, blank=True, null=True)
    author = models.CharField(max_length=255, null=True, blank=True)
    author_email = models.CharField(null=True, blank=True)
    message = models.TextField(null=True, blank=True,default=None)
    suggested_message = models.TextField(null=True, blank=True, default=None)

    date = models.DateTimeField()
    rating = models.IntegerField(null=True, blank=True)
    message_rating = models.IntegerField(null=True, blank=True)
    is_rated = models.BooleanField(default=False)
    rating_reason = models.TextField(null=True, blank=True, default=None)
    raw_gpt_output = models.JSONField(default=dict, blank=True, null=True)
    raw_data = models.JSONField(default=dict, blank=True, null=True)
    commit_type = models.CharField(max_length=50, blank=True, null=True)
    is_merge = models.BooleanField(default=False)
    is_revert = models.BooleanField(default=False)
    url = models.URLField(max_length=500, blank=True, null=True)
    ai_generated_score = models.IntegerField(null=True, blank=True, verbose_name="AI Code Percentage",help_text="Estimated percentage of the code changes generated by AI (0-100).")


    def __str__(self):
        return f"{self.repo_name}: {self.commit_hash[:7]} - {self.message[:50]}"

    @classmethod
    def get_user_profile_by_author(cls, commit):
        """
        Find UserProfile by matching commit author information
        with github_username or aliases.
        
        Args:
            commit (GitCommit): The commit object to match against
            
        Returns:
            User: The matched User object, or None if no match found
        """
        # Import UserProfile to avoid circular import
        from django.apps import apps
        UserProfile = apps.get_model('accounts', 'UserProfile')
        all_profiles = UserProfile.objects.all()
        
        # Extract all possible author/committer fields from commit
        author_fields = []
        # Add author and author_email from commit fields
        if commit.author:
            author_fields.append(commit.author)
        if commit.author_email:
            author_fields.append(commit.author_email)
        # Extract from raw_data if available
        if commit.raw_data:
            # Get author.login and committer.login from top level
            if commit.raw_data.get('author') and commit.raw_data['author'].get('login'):
                author_fields.append(commit.raw_data['author']['login'])
            if commit.raw_data.get('committer') and commit.raw_data['committer'].get('login'):
                author_fields.append(commit.raw_data['committer']['login'])


            # Get commit.author fields
            if commit.raw_data.get('commit', {}).get('author'):
                commit_author = commit.raw_data['commit']['author']
                if commit_author.get('name'):
                    author_fields.append(commit_author['name'])
                if commit_author.get('email'):
                    author_fields.append(commit_author['email'])
            # Get commit.committer fields
            if commit.raw_data.get('commit', {}).get('committer'):
                commit_committer = commit.raw_data['commit']['committer']
                if commit_committer.get('name'):
                    author_fields.append(commit_committer['name'])
                if commit_committer.get('email'):
                    author_fields.append(commit_committer['email'])

        # Try to match user with any of the extracted fields
        for field in author_fields:
            if field:  # Skip empty fields
                for profile in all_profiles:
                    # Check if field matches github_username
                    if profile.github_username and (
                        profile.github_username.lower() == field.lower()
                    ):
                        return profile.user
                    # Check if field matches any alias
                    if profile.aliases:
                        for alias in profile.aliases:
                            if alias and alias.lower() == field.lower():
                                return profile.user                        
        return None

    def save(self, *args, **kwargs):
        # Auto-set user if not already set
        is_new_commit = self.pk is None
        if not self.user:
            self.user = self.get_user_profile_by_author(self)
        
        logger.debug(f"Saving GitCommit: {self.commit_hash} by {self.author_email}")
        if self.user and not self.is_rated:
            try:
                profile = self.user.profile
                razorpay_id = profile.razorpay_employee_id
                if razorpay_id:
                    commit_date = self.date.date()
                    leave_result = get_employee_leave_status(razorpay_id, commit_date)
                    if leave_result['success']:
                        leave_status = leave_result['data'].get('status', 'PRESENT')
                        skipped_statuses = ['LEAVE', 'HOLIDAY', 'WEEKEND', 'ABSENT_CONFIRMED']
                        if leave_status in skipped_statuses:
                            self.rating = 0
                            self.message_rating = 0
                            self.is_rated = True
                            self.rating_reason = f"Commit on Day Off (Razorpay Status: {leave_status})"
                            logger.info(f"Commit {self.commit_hash} skipped: Employee was on {leave_status}.")
                            update_fields = ["rating", "message_rating", "is_rated", "rating_reason"]
                            kwargs['update_fields'] = update_fields
                            super().save(*args, **kwargs)
                            return
                        logger.debug(f"Commit {self.commit_hash}: Status {leave_status}. Proceeding with normal save/rating flow.")
                    else:
                        logger.warning(f"Could not check Razorpay leave status: {leave_result['error']}")
            except Exception as e:
                logger.error(f"Internal error during leave check for {self.user.username}: {e}")
        super().save(*args, **kwargs)
    
    @classmethod
    def get_commits_by_user(cls, user, start_date=None, end_date=None, repo_name=None, limit=None):
        """
        Fetch commits related to a specific user from the GitCommit model.
        
        Args:
            user (User): The user to fetch commits for
            start_date (datetime, optional): Filter commits from this date onwards
            end_date (datetime, optional): Filter commits up to this date
            repo_name (str, optional): Filter commits for a specific repository
            limit (int, optional): Limit the number of results returned
            
        Returns:
            QuerySet: Filtered GitCommit objects for the user
        """
        queryset = cls.objects.filter(user=user)
        
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
            
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
            
        if repo_name:
            queryset = queryset.filter(repo_name=repo_name)
            
        # Order by date descending (most recent first)
        queryset = queryset.order_by('-date')
        
        if limit:
            queryset = queryset[:limit]
            
        return queryset
    
    def rate_with_gpt(self, model="gpt-4o", max_tokens=200):
        """
        Rate this commit using OpenAI GPT.
        Saves rating, commit_type, explanation, and raw output.
        """


        if self.is_merge or (self.message or "").startswith("Merge") or (self.message or "").startswith("Revert"):
            logger.info(f"Skipping merge/revert commit {self.commit_hash}")
            return None

        if not self.user_id:
            logger.info(f"Skipping commit {self.commit_hash} (no user assigned)")
            return None



        file_names = []
        code_blocks = []

        for fc in self.file_changes.all():
            if fc.filename:
                file_names.append(fc.filename)

            if fc.changes:
                code_blocks.append(
                    f"--- FILE: {fc.filename} ---\n{fc.changes}"
                )

        file_list = "\n".join(file_names) if file_names else "No files available"
        code_changes = "\n\n".join(code_blocks) if code_blocks else "No code changes available"


        # dynamic prompt from admin
        latest_prompt = (
            Prompt.objects.filter(type="commit_rating")
            .exclude(created_date__isnull=True)
            .order_by("-created_date", "-id")
            .first()
        )

        # Default built-in template that forces a strict JSON-only response.
        default_template = (
            "You are an expert software engineering productivity analyst. "
            "Given a Git commit message and the list of changed files, "
            "you must rate how good the commit is and how good the message is.\n\n"
            "Return ONLY a strict JSON object with the following keys: "
            '{"rating": int 1-5, "message_rating": int 1-5, '
            '"commit_type": string, "rating_reason": string, '
            '"suggested_message": string }. No extra text.\n\n'
            "Commit message:\n{commit_message}\n\n"
            "Changed files (one per line):\n{changed_files}\n\n"
            "Code changes:\n{code_changes}\n"
        )

        if latest_prompt and latest_prompt.prompt:
            prompt_template = latest_prompt.prompt or ""
            if "{commit_message}" not in prompt_template and "{changed_files}" not in prompt_template:
                logger.warning("Latest Prompt(id=%s, type=commit_rating) is missing "
                    "{commit_message}/{changed_files} placeholders; "
                    "falling back to built-in template.",
                    latest_prompt.id,
                )
                prompt_template = default_template
        else:
            logger.warning(
                "No active or compatible prompt found for commit_rating; "
                "using built-in default template.",
            )
            prompt_template = default_template

        prompt = (
            prompt_template
            .replace("{commit_message}", self.message or "No commit message")
            .replace("{changed_files}", file_list or "No files changed")
            .replace("{code_changes}", code_changes)
        )



        try:
            client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=0,
            )

            gpt_output = (response.choices[0].message.content or "").strip()

            if not gpt_output:
                logger.error(f"Empty GPT output while rating commit {self.commit_hash}")
                return None

            # Try strict JSON parsing first
            try:
                parsed_json = json.loads(gpt_output)
            except json.JSONDecodeError as e:
                # Fallback: extract the first JSON object from the text
                start = gpt_output.find("{")
                end = gpt_output.rfind("}")

                if start != -1 and end != -1 and start < end:
                    candidate = gpt_output[start : end + 1]
                    try:
                        parsed_json = json.loads(candidate)
                        logger.warning(
                            f"Non-JSON wrapper detected in GPT output for commit {self.commit_hash}; "
                            "successfully parsed inner JSON."
                        )
                    except json.JSONDecodeError as inner_e:
                        logger.error(
                            f"Failed to parse GPT JSON for commit {self.commit_hash}: {inner_e}. "
                            f"Raw output: {gpt_output}"
                        )
                        return None
                else:
                    logger.error(
                        f"GPT output for commit {self.commit_hash} does not contain a JSON object. "
                        f"Raw output: {gpt_output} (original error: {e})"
                    )
                    return None

            # Save all relevant fields
            
            self.rating = parsed_json.get("rating")
            self.message_rating = parsed_json.get("message_rating")
            self.commit_type = parsed_json.get("commit_type") or "Unknown"

            reason = parsed_json.get("rating_reason") or ""
            ai_phrases = [
                "AI code percentage",
                "AI Code %",
                "AI-generated code",
                "AI-generated logic",
                "AI percentage"
            ]
            parts = [p.strip() for p in reason.split(".") if p.strip()]
            filtered_parts = [p for p in parts if not any(ai_word in p for ai_word in ai_phrases)]
            cleaned_reason = ". ".join(filtered_parts).strip()
            self.rating_reason = cleaned_reason

            self.suggested_message = parsed_json.get("suggested_message")
            self.is_rated = True

            parsed_json["rating_reason"] = cleaned_reason

            parsed_json.pop("ai_code_percentage", None)

            self.raw_gpt_output = parsed_json

            self.save(update_fields=["rating", "commit_type", "rating_reason", "raw_gpt_output", "suggested_message", "message_rating", "is_rated"])

            # Increment rated_count for the prompt
            latest_prompt.commit_rated_count = (latest_prompt.commit_rated_count or 0) + 1
            latest_prompt.save(update_fields=["commit_rated_count"])

            logger.info(f"Commit {self.commit_hash} rated: {self.rating}/5 ({self.commit_type})")
            return self.rating

        except Exception as e:
            logger.error(f"Failed to rate commit {self.commit_hash}: {e}")
            return None
        
    def append_ai_analysis(self, ai_score, ai_reason, ai_raw_json):
        self.ai_generated_score = ai_score

        final_reason = f"The AI code is {ai_score}% because {ai_reason.strip()}"

        if self.rating_reason:
            self.rating_reason = f"{self.rating_reason.strip()}. {final_reason}"
        else:
            self.rating_reason = final_reason

        raw_copy = dict(self.raw_gpt_output or {})
        raw_copy.pop("ai_analysis", None)
        raw_copy["ai_code_percentage"] = ai_score
        raw_copy["ai_code_reason"] = ai_reason.strip()
        self.raw_gpt_output = raw_copy

        self.save(update_fields=["ai_generated_score", "rating_reason", "raw_gpt_output"])


    @classmethod
    def get_commits_by_author_info(cls, author_name=None, author_email=None, start_date=None, end_date=None, repo_name=None, limit=None):
        """
        Fetch commits by author name or email (useful when user is not linked).
        
        Args:
            author_name (str, optional): Filter by author name
            author_email (str, optional): Filter by author email
            start_date (datetime, optional): Filter commits from this date onwards
            end_date (datetime, optional): Filter commits up to this date
            repo_name (str, optional): Filter commits for a specific repository
            limit (int, optional): Limit the number of results returned
            
        Returns:
            QuerySet: Filtered GitCommit objects for the author
        """
        queryset = cls.objects.all()
        
        if author_name:
            queryset = queryset.filter(author__icontains=author_name)
            
        if author_email:
            queryset = queryset.filter(author_email__icontains=author_email)
            
        if start_date:
            queryset = queryset.filter(date__gte=start_date)
            
        if end_date:
            queryset = queryset.filter(date__lte=end_date)
            
        if repo_name:
            queryset = queryset.filter(repo_name=repo_name)
            
        # Order by date descending (most recent first)
        queryset = queryset.order_by('-date')
        
        if limit:
            queryset = queryset[:limit]
            
        return queryset


class FileChange(models.Model):
    commit = models.ForeignKey(GitCommit, on_delete=models.CASCADE, related_name='file_changes')
    filename = models.CharField(max_length=500, blank=True, null=True)
    changes = models.TextField(blank=True, null=True)
    date = models.DateTimeField(auto_now=True)
    file_url = models.URLField(max_length=500, blank=True, null=True)


class ScriptRunLog(models.Model):
    name = models.CharField(max_length=255)
    last_run_at = models.DateTimeField(default=now)
    run_count = models.IntegerField(default=0)
    logs = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} ran at {self.last_run_at}"

    def save(self, *args, **kwargs):
        logger.info(f"Updating ScriptRunLog for: {self.name}")
        super().save(*args, **kwargs)


class Prompt(models.Model):
    type = models.CharField(max_length=100)
    prompt = models.TextField()
    created_date = models.DateTimeField(auto_now_add=True)
    commit_rated_count = models.IntegerField(default=0)
    accuracy = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.type} - {self.prompt[:30]}"
    


class Repo(models.Model):
    repo_name = models.CharField(max_length=255)
    org_name = models.CharField(max_length=255)  
    users = models.ManyToManyField(User, related_name="repos")  

    created_at = models.DateTimeField()  
    updated_at = models.DateTimeField()
    branches = models.IntegerField(default=0)  
    first_commit_date = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = ('repo_name', 'org_name')

    def __str__(self):
        return f"{self.org_name}/{self.repo_name}"



class Bug(models.Model):
    SEVERITY_CHOICES = [
        ("Low", "Low"),
        ("Medium", "Medium"),
        ("High", "High"),
        ("Critical", "Critical"),
    ]

    STATUS_CHOICES = [
        ("Open", "Open"),
        ("In Progress", "In Progress"),
        ("Resolved", "Resolved"),
        ("Closed", "Closed"),
    ]

    title = models.CharField(max_length=255)
    description = models.TextField()
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="Low")
    status = models.CharField(max_length=15, choices=STATUS_CHOICES, default="Open")
    repo = models.ForeignKey(Repo, on_delete=models.SET_NULL,null=True,blank=True,related_name="bugs")
    reported_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="reported_bugs")
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="assigned_bugs")
    date_reported = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.title} - {self.status}"
    
    @property
    def comments_text(self):
        return " | ".join(self.comments.values_list("text", flat=True))




class Comment(models.Model):
    bug = models.ForeignKey(Bug, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    text = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Comment by {self.user.username} on {self.bug.title}"

class DailyCommitSummary(models.Model):
    author = models.CharField(max_length=255)
    commit_count = models.IntegerField()

    class Meta:
        managed = False
        verbose_name = "Daily Commit Summary"
        verbose_name_plural = "Daily Commit Summary"

    def __str__(self):
        return f"{self.author} - {self.commit_count}"
  
    
class AdminProjectMetricsView(models.Model):
    class Meta:
        managed = False
        verbose_name = "Project Metrics"
        verbose_name_plural = "Project Metrics"  


    
class Branch(models.Model):
    repo = models.ForeignKey(Repo, on_delete=models.CASCADE, related_name='repo_branches')
    branch_name = models.CharField(max_length=255)
    last_commit_username = models.CharField(max_length=255, blank=True, null=True)

    updated_at = models.DateTimeField()

    class Meta:
        unique_together = ('repo', 'branch_name')

    def __str__(self):
        return f"{self.repo.repo_name} - {self.branch_name}"
    

class EmployeeAttendance(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='attendance_records')
    date = models.DateField()
    status = models.CharField(max_length=50, default="unknown")
    check_in = models.CharField(max_length=20, blank=True, null=True)
    check_out = models.CharField(max_length=20, blank=True, null=True)
    duration = models.CharField(max_length=20, blank=True, null=True)
    fetched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "date")
        ordering = ["-date"]

    def __str__(self):
        return f"{self.user.username} - {self.date} ({self.status})"