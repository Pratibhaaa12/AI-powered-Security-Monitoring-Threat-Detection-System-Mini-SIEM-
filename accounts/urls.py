from django.shortcuts import redirect
from django.urls import path

from . import views

urlpatterns = [
    path('', lambda request: redirect('login', permanent=True)),
    path('register/', views.register, name='register'),
    path('rate-commit/<int:commit_id>/', views.rate_commit, name='rate_commit'),
    path('login/', views.login, name='login'),
    path('home/', views.home, name='home'),
    path('home/<int:user_id>/', views.home, name='home_with_id'),
    path('profile/', views.profile, name='profile'),
    path('commits-data/', views.commits_data, name='commits_data'),
    path('logout/', views.logout_view, name='logout'),
    path('ping/', views.ping, name='ping'),
    path("github/login/", views.github_login, name="github_login"),
    path("accounts/github/login/callback/", views.github_callback, name="github_callback"),
    path('bug-dashboard/', views.bug_dashboard, name='bug-dashboard'),
    path("bug/<int:bug_id>/", views.bug_detail, name="bug_detail"),
    path('bugs/<int:bug_id>/comment/', views.add_comment, name='add_comment'),
    path('bugs/add/', views.add_bug, name='add_bug'),
    path('bugs/edit/<int:bug_id>/', views.edit_bug, name='edit_bug'),
    path('project-metrics/', views.project_metrics_data, name='project_metrics_data'),
    path("repo/<int:repo_id>/", views.repo_detail, name="repo_detail"),
    path("google/login/", views.google_login, name="google_login"),
    path("google/callback/", views.google_callback, name="google_callback"),
]