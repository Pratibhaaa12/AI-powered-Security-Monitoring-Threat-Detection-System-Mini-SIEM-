from django.contrib import admin
from django.urls import path
from django.shortcuts import render
from accounts.models import Repo
from django.http import JsonResponse
from datetime import datetime, date
from .models import AdminRepoGoalsView, RepoGoal

@admin.register(AdminRepoGoalsView)
class RepoGoalsAdmin(admin.ModelAdmin):

    # custom admin page url
    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path("", self.admin_site.admin_view(self.repo_goals_page), name="repo_goals_page"),
            path("save-goal/", self.admin_site.admin_view(self.save_goal), name="save_repo_goal"),
            path("delete-goal/", self.admin_site.admin_view(self.delete_goal), name="delete_repo_goal"),
        ]
        return custom + urls

    # page logic
    def repo_goals_page(self, request):
        repos = Repo.objects.all().prefetch_related("repo_goals")
        return render(request, "admin/manage_goals.html", {"repos": repos})

    def _parse_deadline(self, value):
        """Safely convert deadline string → date object"""
        if not value:
            return None

        if isinstance(value, date):
            return value

        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except:
            return None

    def _safe_deadline_for_json(self, value):
        """Convert date → isoformat or string → date → isoformat"""
        if not value:
            return None

        if isinstance(value, str):  
            try:
                value = datetime.strptime(value, "%Y-%m-%d").date()
            except:
                return None

        return value.isoformat()

    def save_goal(self, request):
        repo_id = request.POST.get("repo_id")
        goal_id = request.POST.get("goal_id")
        goal_text = request.POST.get("goal_text", "").strip()
        deadline = request.POST.get("deadline")
        status = request.POST.get("status", "pending")

        if not repo_id or not goal_text:
            return JsonResponse({"success": False})

        repo = Repo.objects.get(id=repo_id)

        deadline_date = self._parse_deadline(deadline)

        if goal_id:
            try:
                goal_obj = RepoGoal.objects.get(id=goal_id, repo=repo)

                goal_obj.goal_text = goal_text
                goal_obj.deadline = deadline_date
                goal_obj.status = status
                goal_obj.save()

                return JsonResponse({
                    "success": True,
                    "created": False,
                    "goal_id": goal_obj.id,
                    "goal_text": goal_obj.goal_text,
                    "deadline": self._safe_deadline_for_json(goal_obj.deadline),
                    "status": goal_obj.status,
                })

            except RepoGoal.DoesNotExist:
                return JsonResponse({"success": False, "error": "goal_not_found"})

        # CREATE NEW GOAL
        goal_obj = RepoGoal.objects.create(
            repo=repo,
            goal_text=goal_text,
            org_name=repo.org_name,
            repo_name=repo.repo_name,
            deadline=deadline_date,
            status=status,
        )

        return JsonResponse({
            "success": True,
            "created": True,
            "goal_id": goal_obj.id,
            "goal_text": goal_obj.goal_text,
            "deadline": self._safe_deadline_for_json(goal_obj.deadline),
            "status": goal_obj.status,
        })

    def delete_goal(self, request):
        repo_id = request.POST.get("repo_id")
        goal_id = request.POST.get("goal_id")

        if not repo_id:
            return JsonResponse({"success": False})

        if goal_id:
            RepoGoal.objects.filter(id=goal_id, repo_id=repo_id).delete()
        else:
            RepoGoal.objects.filter(repo_id=repo_id).delete()

        return JsonResponse({"success": True})

@admin.register(RepoGoal)
class RepoGoalAdmin(admin.ModelAdmin):
    list_display = ("repo", "order", "status", "goal_text", "created_at", "deadline", "raw_data")
    search_fields = ("repo__repo_name", "goal_text", "status")
    list_filter = ("status", "created_at")
