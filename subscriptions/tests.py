from django.test import TestCase, override_settings
from django.urls import reverse
from .models import Subscriber, PlatformProfile, WeeklySnapshot


class WeeklyUpdateTest(TestCase):
    """Ensure the weekly-update endpoint creates snapshots and returns success."""

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_weekly_update_creates_snapshot_and_returns_ok(self):
        # create a subscriber with a profile
        sub = Subscriber.objects.create(email='test@example.com')
        prof = PlatformProfile.objects.create(
            subscriber=sub,
            platform_name='LeetCode',
            username='foo',
            last_rating=100,
            problems_solved=10,
            contests_attended=1,
        )
        response = self.client.post(reverse('weekly_update'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('weekly_processed', response.json().get('status', ''))
        # one snapshot should be created for the profile
        self.assertTrue(WeeklySnapshot.objects.filter(profile=prof).exists())
