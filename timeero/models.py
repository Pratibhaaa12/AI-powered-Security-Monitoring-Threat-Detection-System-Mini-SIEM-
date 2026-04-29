from django.db import models
from django.contrib.auth.models import User


class TimeeroUser(models.Model):
    timeero_user_id = models.BigIntegerField(unique=True)
    first_name = models.CharField(max_length=200, null=True, blank=True)
    last_name = models.CharField(max_length=200, null=True, blank=True)
    email = models.EmailField(null=True, blank=True, unique=True)
    company_employee_id = models.CharField(max_length=200, null=True, blank=True)
    slack_user_id = models.CharField(max_length=200, null=True, blank=True)
    class Meta:
        indexes = [
            models.Index(fields=["email"]),
            models.Index(fields=["timeero_user_id"]),
        ]
    def __str__(self):
        return f"{self.first_name} {self.last_name} ({self.email})"


class TimeeroTimesheet(models.Model):
    timeero_timesheet_id = models.BigIntegerField(unique=True)
    user = models.ForeignKey(TimeeroUser, on_delete=models.CASCADE, related_name="timesheets")
    notes = models.TextField(null=True, blank=True)
    job_id = models.BigIntegerField(null=True, blank=True)
    job_name = models.CharField(max_length=255, null=True, blank=True)
    clock_in_time = models.DateTimeField(null=True, blank=True)
    clock_in_address = models.TextField(null=True, blank=True)
    clock_in_latitude = models.CharField(max_length=50, null=True, blank=True)
    clock_in_longitude = models.CharField(max_length=50, null=True, blank=True)
    clock_out_time = models.DateTimeField(null=True, blank=True)
    clock_out_latitude = models.CharField(max_length=50, null=True, blank=True)
    clock_out_longitude = models.CharField(max_length=50, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Timesheet {self.timeero_timesheet_id} for {self.user}"

class TimeeroCustomField(models.Model):
    timesheet = models.ForeignKey(TimeeroTimesheet, on_delete=models.CASCADE, related_name="custom_fields")
    field_key = models.CharField(max_length=50)
    field_value = models.TextField(null=True, blank=True)

    def __str__(self):
        return f"{self.field_key}: {self.field_value}"

class TimeeroBreak(models.Model):
    timesheet = models.ForeignKey(TimeeroTimesheet, on_delete=models.CASCADE, related_name="breaks")
    timeero_break_id = models.BigIntegerField()
    start = models.DateTimeField(null=True, blank=True)
    end = models.DateTimeField(null=True, blank=True)
    duration_in_minutes = models.IntegerField(default=0)

    def __str__(self):
        return f"Break {self.timeero_break_id} ({self.duration_in_minutes} min)"

class DailyReport(models.Model):
    user = models.ForeignKey(User,on_delete=models.CASCADE,related_name="daily_reports")
    report_date = models.DateField(db_index=True)
    tasks_text = models.TextField(blank=True)
    updates_text = models.TextField(blank=True)
    commits = models.JSONField(default=list, blank=True)
    code_relevance_percentage = models.PositiveIntegerField(null=True,blank=True)
    code_relevance_reason = models.TextField(null=True,blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "report_date")
        ordering = ["-report_date"]
        indexes = [
            models.Index(fields=["user", "report_date"]),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.report_date}"
