from django.contrib import admin

from crm_integrations.models import AlertEvent, AlertRule, CRMConnection, CRMContact


@admin.register(CRMConnection)
class CRMConnectionAdmin(admin.ModelAdmin):
    list_display = ("provider", "base_url", "is_active", "last_synced_at", "updated_at")
    list_filter = ("provider", "is_active")
    search_fields = ("base_url", "tenant_id", "client_id")


@admin.register(CRMContact)
class CRMContactAdmin(admin.ModelAdmin):
    list_display = ("full_name", "email", "phone", "owner_name", "updated_from_crm_at")
    search_fields = ("full_name", "email", "external_id")


@admin.register(AlertRule)
class AlertRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "rule_type", "is_active", "threshold_value", "updated_at")
    list_filter = ("rule_type", "is_active")
    search_fields = ("name",)


@admin.register(AlertEvent)
class AlertEventAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "created_at", "sent_at")
    list_filter = ("status", "created_at")
    search_fields = ("title",)
