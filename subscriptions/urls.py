from django.urls import path
from . import views

urlpatterns = [
    path('', views.home, name='home'),
    path('subscribe/', views.subscribe, name='subscribe'),
    path('unsubscribe/', views.unsubscribe_view, name='unsubscribe'),
    path('add_platform_profile/', views.add_platform_profile, name='add_platform_profile'),
    path('update-platform-username/<int:platform_id>/', views.update_platform_username, name='update-platform-username'),
    path('send_daily_report/', views.send_daily_report, name='send_daily_report'),
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    path('logout/', views.user_logout, name='logout'),
    path('trigger-leaderboard/', views.fetch_leaderboard_data_view, name='trigger-leaderboard'),
    path('create_or_join_group/', views.create_or_join_group, name='create_or_join_group'),
]
