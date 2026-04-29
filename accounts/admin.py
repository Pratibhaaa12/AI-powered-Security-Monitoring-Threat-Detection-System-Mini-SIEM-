from collections import defaultdict
from django.contrib import admin
from django.contrib.auth.models import User
from django.urls import path
from django.utils.datastructures import MultiValueDictKeyError
from django.template.response import TemplateResponse
from django.utils.timezone import now, timedelta, localdate
from django.db.models.functions import Coalesce
from django.db.models import OuterRef, Subquery, IntegerField, FloatField, Value, Count, Avg
from django.db.models import Count, Avg, Q, Sum, F
from django.contrib.admin import AdminSite
from django.utils.html import format_html
from django.shortcuts import render
from django.contrib import admin
from .models import GitCommit
from rangefilter.filters import DateRangeFilter
from django.utils.translation import gettext_lazy as _
from django.contrib import admin, messages
from datetime import datetime
import calendar
from django.utils.timezone import make_aware, get_current_timezone
 
from .models import UserProfile, GitCommit, FileChange, ScriptRunLog, Prompt, Bug, Comment, DailyCommitSummary, AdminProjectMetricsView, Repo, Branch, EmployeeAttendance
from django.contrib import admin
from rangefilter.filters import DateRangeFilter
@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("bug", "user", "text", "created_at")
    list_filter = ("created_at", "user")
    search_fields = ("text", "bug__title", "user__username")

@admin.action(description="Rate selected commits with GPT")
def rate_commits_with_gpt(modeladmin, request, queryset):
    from accounts.management.commands.commit_ai_analyzer import analyze_patch_with_admin_prompt
    rated, failed = 0, 0
    
    latest_prompt = (
        Prompt.objects.filter(type="commit_rating")
        .exclude(prompt__isnull=True)
        .order_by("-created_date", "-id")
        .first()
    )
    admin_prompt_text = latest_prompt.prompt if latest_prompt else ""

    for commit in queryset:
        try:
            rating = commit.rate_with_gpt()
            if rating is None:
                failed += 1
                continue

            file_changes = FileChange.objects.filter(commit=commit)
            total_score = 0
            files_analyzed = 0
            ai_reason = ""

            for fc in file_changes:
                patch = fc.changes
                if patch and len(patch.strip()) > 50:
                    result = analyze_patch_with_admin_prompt(commit.commit_hash, patch, admin_prompt_text)
                    if result and isinstance(result, dict):
                        total_score += result["score"]
                        files_analyzed += 1
                        ai_reason = (result.get("reason") or "").strip()

            ai_score = round(total_score / files_analyzed) if files_analyzed else 0

            commit.append_ai_analysis(
                ai_score=ai_score,
                ai_reason=ai_reason,
                ai_raw_json={"ai_code_percentage": ai_score, "reason": ai_reason},
            )

            rated += 1

        except Exception as e:
            failed += 1
            messages.error(request, f"Commit {commit.commit_hash[:7]}: {str(e)}")

    messages.success(request, f"Rated {rated} commits, Failed {failed}")

@admin.register(Bug)
class BugAdmin(admin.ModelAdmin):
    list_display = ("title", "repo", "status", "severity", "assigned_to", "date_reported")
    list_filter = ("status", "severity", "date_reported")
    search_fields = ("title", "repo", "description", "reported_by", "assigned_to")
    autocomplete_fields = ("assigned_to", "reported_by")


@admin.register(Prompt)
class PromptAdmin(admin.ModelAdmin):
    list_display = ('id', 'type', 'prompt', 'created_date', 'commit_rated_count', 'accuracy')
    search_fields = ('type', 'prompt')
    readonly_fields = ('created_date',)

                 
@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "github_username", "dob", "gender", "date_of_joining", "aliases", "last_login", "ignore_missed_checkin")   
    list_filter = ("ignore_missed_checkin",)
    search_fields = ("user__username", "user__email", "github_username", "aliases")
    list_editable = ("ignore_missed_checkin",)



@admin.register(FileChange)
class FileChangeAdmin(admin.ModelAdmin):
    list_display = ("commit", "filename", "date")
    search_fields = ("filename", "changes")
admin.site.register(ScriptRunLog)


@admin.register(GitCommit)
class GitCommitAdmin(admin.ModelAdmin):
    search_fields = ("commit_hash", "repo_name", "author", "author_email", "message", "suggested_message", "user__username", "user__first_name","user__last_name",  "user__email")
    list_display = ("user", "repo_name", "author", "author_email", "message","message_rating", "suggested_message", "rating","rating_reason", "raw_gpt_output", "date", "is_merge", "is_revert", "ai_generated_score")
    list_filter = ('user',("date", DateRangeFilter))
    actions = ["assign_users_action", rate_commits_with_gpt, "analyze_ai_percentage_action"]
    autocomplete_fields = ["user"]

    def assign_users_action(self, request, queryset):
        """
        Admin action: call get_user_profile_by_author (from GitCommit model)
        on each selected commit.
        """
        updated = 0
        for commit in queryset:
            if not commit.user:  
                user = GitCommit.get_user_profile_by_author(commit) 
                if user:
                    commit.user = user
                    commit.save(update_fields=["user"])
                    updated += 1

        self.message_user(request, f"{updated} commits successfully linked to users.")

    assign_users_action.short_description = "Identify and assign users"

    def changelist_view(self, request, extra_context=None):
        today = now().date()

        summary = (
            GitCommit.objects.filter(date__date=today)
            .exclude(Q(message__startswith="Merge") | Q(message__startswith="Revert"))   
            .values("author")
            .annotate(commit_count=Count("id"))
            .order_by("-commit_count")
        )

        extra_context = extra_context or {}
        extra_context["summary"] = summary
        extra_context["date"] = today

        return super().changelist_view(request, extra_context=extra_context)



@admin.register(DailyCommitSummary)
class DailyCommitSummaryAdmin(admin.ModelAdmin):
    list_display = ("author", "commit_count")
    search_fields = ("author",)
    change_list_template = "admin/daily_commit_summary.html"

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

    def changelist_view(self, request, extra_context=None):

        try:
            date_str = request.GET["date"]
        except MultiValueDictKeyError:

            date_str = str(localdate() - timedelta(days=1))
 
        commits = (
            GitCommit.objects.filter(date__date=date_str)
            .exclude(Q(message__startswith="Merge") | Q(message__startswith="Revert"))
        )

        author_summary = defaultdict(lambda: {"commits": 0, "lines_changed": 0})

        for commit in commits:
            lines_changed = 0
            for fc in commit.file_changes.all():
                if fc.changes:
                    for line in fc.changes.splitlines():
                        if line.startswith("+") and not line.startswith("+++"):
                            lines_changed += 1
                        elif line.startswith("-") and not line.startswith("---"):
                            lines_changed += 1
            author_summary[commit.author]["commits"] += 1
            author_summary[commit.author]["lines_changed"] += lines_changed

        summary = [
            {
                "author": author,
                "commit_count": data["commits"],
                "lines_changed": data["lines_changed"],
            }
            for author, data in author_summary.items()
        ]

        context = {
            "summary": summary,
            "date": date_str,
            "title": "Daily Commit Summary",
            "opts": self.model._meta,
            "app_label": self.model._meta.app_label,
        }
        return render(request, self.change_list_template, context)

@admin.register(AdminProjectMetricsView)
class ProjectMetricsAdmin(admin.ModelAdmin):
    change_list_template = "admin/project_metrics.html"

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False

    def changelist_view(self, request, extra_context=None):
        context = {
            **self.admin_site.each_context(request),
            "title": "Project Metrics",
            "opts": self.model._meta,
        }
        return TemplateResponse(request, self.change_list_template, context)


@admin.register(Repo)
class RepoAdmin(admin.ModelAdmin):
    list_display = ('repo_name', 'org_name', 'get_users', 'created_at', 'updated_at', 'branches', 'first_commit_date')
    search_fields = ('repo_name', 'org_name', 'users__username')

    def get_users(self, obj):
        return ", ".join([user.username for user in obj.users.all()])
    get_users.short_description = 'Users'

@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ('repo', 'branch_name', 'last_commit_username','updated_at')
    search_fields = ('branch_name', 'last_commit_username','repo__repo_name')


@admin.register(EmployeeAttendance)
class EmployeeAttendanceAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "status", "check_in", "check_out", "duration", "fetched_at")
    list_filter = ("status", "date")
    search_fields = ("user__username", "user__email")
    ordering = ("-date",)
