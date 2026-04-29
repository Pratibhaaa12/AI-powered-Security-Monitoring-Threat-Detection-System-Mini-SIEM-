from django.urls import path
from . import views

urlpatterns = [
    path('admin/manage-goals/', views.admin_manage_goals, name='admin_manage_goals'),
    path('admin/manage-goals/<int:repo_id>/', views.repo_goal_list, name='repo_goal_list'),
    path('repos/', views.repos_list, name='goals_repos_list'),
    path('repos/<int:repo_id>/', views.repo_detail_page, name='goal_repo_detail'),
]
