from django.urls import path

from .views import security_dashboard

urlpatterns = [
    path("security-dashboard/", security_dashboard, name="security_dashboard"),
]

