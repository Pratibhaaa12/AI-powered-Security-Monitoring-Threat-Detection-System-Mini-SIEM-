from django.db import models
from django.utils import timezone

# Create your models here.
class AdminRepoGoalsView(models.Model):
    class Meta:
        managed = False
        verbose_name = "Set Repo Goals"
        verbose_name_plural = "Set Repo Goals"

    def __str__(self):
        return "Set Repo Goals"
    
class RepoGoal(models.Model):
    STATUS_CHOICES = [("pending", "Pending"),("active", "Active"),("completed", "Completed"),]
    repo = models.ForeignKey("accounts.Repo",on_delete=models.CASCADE,related_name="repo_goals")
    org_name = models.CharField(max_length=255, blank=True)
    repo_name = models.CharField(max_length=255, blank=True)
    goal_text = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    deadline = models.DateField(null=True, blank=True)
    order = models.PositiveIntegerField(default=1)  
    status = models.CharField(max_length=20,choices=STATUS_CHOICES,default="pending")
    raw_data = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ["order", "created_at"]

    def __str__(self):
        return f"{self.repo.repo_name} - {self.goal_text[:25]}..."

    def save(self, *args, **kwargs):
        if self.repo:
            self.org_name = self.repo.org_name or ""
            self.repo_name = self.repo.repo_name or ""

        if not self.pk and not self.deadline:
            self.deadline = (self.created_at + timezone.timedelta(days=180)).date()

        if not self.pk:
            last_goal = RepoGoal.objects.filter(repo=self.repo).order_by("-order").first()
            self.order = (last_goal.order + 1) if last_goal else 1

        super().save(*args, **kwargs)
