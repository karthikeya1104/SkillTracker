from django.db import models

class Subscriber(models.Model):
    """Model to store subscriber details."""
    email = models.EmailField(unique=True)
    group = models.CharField(max_length=50, null=True, blank=True, default=None)
    date_subscribed = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.email

class PlatformProfile(models.Model):
    """Model to store platform-specific details for each subscriber."""
    PLATFORM_CHOICES = [
        ('LeetCode', 'LeetCode'),
        ('CodeChef', 'CodeChef'),
        ('Codeforces', 'Codeforces'),
    ]

    STATUS_CHOICES = [
        ('Pending', 'Pending'),
        ('Success', 'Success'),
        ('Failed', 'Failed'),
    ]

    subscriber = models.ForeignKey(
        Subscriber,
        on_delete=models.CASCADE,
        related_name='platform_profiles'
    )
    platform_name = models.CharField(max_length=50, choices=PLATFORM_CHOICES, db_index=True)
    username = models.CharField(max_length=100, db_index=True)
    
    last_rating = models.IntegerField(null=True, blank=True, default=None)
    problems_solved = models.IntegerField(null=True, blank=True, default=None)
    contests_attended = models.IntegerField(null=True, blank=True, default=None)
    
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('subscriber', 'platform_name')

    def __str__(self):
        return f"{self.subscriber.email} - {self.platform_name} ({self.username})"
