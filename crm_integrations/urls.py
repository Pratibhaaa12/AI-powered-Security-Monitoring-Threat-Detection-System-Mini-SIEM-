from django.urls import path

from crm_integrations import views


urlpatterns = [
    path("", views.crm_dashboard, name="crm_dashboard"),
    path("dynamics/config/", views.save_dynamics_connection, name="save_dynamics_connection"),
    path("dynamics/sync-contacts/", views.sync_contacts, name="sync_dynamics_contacts"),
    path("dynamics/enrich-contacts/", views.enrich_contacts, name="enrich_crm_contacts"),
    path("alerts/rules/create/", views.create_alert_rule, name="create_alert_rule"),
    path("alerts/run/", views.run_alerts, name="run_crm_alerts"),
]
