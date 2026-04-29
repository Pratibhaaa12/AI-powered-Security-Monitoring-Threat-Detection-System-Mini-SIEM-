from django.urls import path
from . import views

urlpatterns = [
    path("daily-report/", views.daily_report, name="daily_report"),
]
