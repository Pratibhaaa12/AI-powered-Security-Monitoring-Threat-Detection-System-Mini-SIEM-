from django.contrib import admin
from .models import TimeeroUser, TimeeroTimesheet, TimeeroCustomField, TimeeroBreak, DailyReport
from rangefilter.filters import DateRangeFilter

class TimeeroCustomFieldInline(admin.TabularInline):
    model = TimeeroCustomField
    extra = 0

class TimeeroBreakInline(admin.TabularInline):
    model = TimeeroBreak
    extra = 0

@admin.register(TimeeroUser)
class TimeeroUserAdmin(admin.ModelAdmin):
    list_display = ("timeero_user_id", "first_name", "last_name", "email", "company_employee_id", "slack_user_id")
    search_fields = ("first_name", "last_name", "email", "company_employee_id")

@admin.register(TimeeroTimesheet)
class TimeeroTimesheetAdmin(admin.ModelAdmin):
    list_display = ("timeero_timesheet_id", "user", "clock_in_time", "clock_out_time", "created_at")
    search_fields = ("timeero_timesheet_id", "user__email", "user__first_name", "user__last_name")
    list_filter = ("clock_in_time", "clock_out_time")
    inlines = [TimeeroCustomFieldInline, TimeeroBreakInline]

@admin.register(TimeeroCustomField)
class TimeeroCustomFieldAdmin(admin.ModelAdmin):
    list_display = ("timesheet", "field_key", "field_value")
    search_fields = ("field_key", "field_value")

@admin.register(TimeeroBreak)
class TimeeroBreakAdmin(admin.ModelAdmin):
    list_display = ("timeero_break_id", "timesheet", "start", "end", "duration_in_minutes")
    search_fields = ("timeero_break_id",)

@admin.register(DailyReport)
class DailyReportAdmin(admin.ModelAdmin):
    list_display = ("user", "report_date", "tasks_text", "updates_text", "commits", "code_relevance_percentage", "code_relevance_reason")
    search_fields = ("user__username", "user__email")
    list_filter = ('user',("report_date", DateRangeFilter))