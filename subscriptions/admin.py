from django.contrib import admin
from .models import Subscriber, PlatformProfile

@admin.register(Subscriber)
class SubscriberAdmin(admin.ModelAdmin):
    list_display = ('email', 'group', 'date_subscribed')

@admin.register(PlatformProfile)
class PlatformProfileAdmin(admin.ModelAdmin):
    list_display = ('subscriber', 'platform_name', 'username', 'last_rating', 'problems_solved', 'contests_attended')
