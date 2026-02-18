from django.shortcuts import get_object_or_404
import logging
import time

logger = logging.getLogger(__name__)
from .models import Subscriber, PlatformProfile
from .forms import SubscriberProfileForm, PlatformProfileForm
from django.contrib.auth import logout
from .tasks import send_report_email, fetch_leaderboard_data, record_weekly_stats, send_all_weekly_reports, fetch_leetcode_data, fetch_codeforces_data, fetch_codechef_data
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.core.cache import cache
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page

# DRF imports for API views
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status


# ---------------- CACHE + RATE LIMIT HELPERS ----------------

def invalidate_leaderboard_cache():
    """
    Clear all leaderboard cache variations.
    Uses Redis SCAN via django-redis delete_pattern.
    """
    cache.delete_pattern("leaderboard:*")


def check_refresh_rate_limit(profile_id, email, limit_seconds=60):
    """
    Limit refresh to once per profile per user per minute.
    Returns (allowed, retry_after_seconds)
    """
    key = f"refresh_lock:{profile_id}:{email}"
    last = cache.get(key)
    now = int(time.time())

    if last:
        remaining = limit_seconds - (now - last)
        if remaining > 0:
            return False, remaining

    cache.set(key, now, timeout=limit_seconds)
    return True, 0


# helper to serialize platform profiles

def serialize_profile(profile):
    return {
        'id': profile.id,
        'platform_name': profile.platform_name,
        'username': profile.username,
        'last_rating': profile.last_rating,
        'problems_solved': profile.problems_solved,
        'contests_attended': profile.contests_attended,
        'subscriber_id': profile.subscriber_id,
    }


def serialize_subscriber(subscriber):
    return {
        'id': subscriber.id,
        'email': subscriber.email,
        'group': subscriber.group,
    }

@api_view(['GET'])
def my_profiles(request):
    """Get all profiles for the currently logged-in user."""
    email = request.session.get('subscriber_email')
    logger.info(f"my_profiles request - email: {email}")
    if not email:
        logger.warning("my_profiles: no session email found")
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    
    try:
        subscriber = Subscriber.objects.get(email=email)
        profiles = PlatformProfile.objects.filter(subscriber=subscriber)
        logger.info(f"my_profiles retrieved {len(profiles)} profiles for {email}")
        return Response({
            'subscriber': serialize_subscriber(subscriber),
            'profiles': [serialize_profile(p) for p in profiles],
        })
    except Subscriber.DoesNotExist:
        logger.error(f"my_profiles: subscriber not found for email {email}")
        return Response({'error': 'subscriber not found'}, status=status.HTTP_404_NOT_FOUND)


@api_view(['POST'])
def refresh_profile(request, profile_id):
    """Refresh a specific profile by fetching the latest data from the platform API.
    
    Only the profile owner can refresh their own profile.
    """
    logger.info(f"refresh_profile request - profile_id: {profile_id}")
    email = request.session.get('subscriber_email')
    if not email:
        logger.warning(f"refresh_profile {profile_id}: no session email")
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        logger.error(f"refresh_profile {profile_id}: subscriber not found for {email}")
        return Response({'error': 'subscriber not found'}, status=status.HTTP_404_NOT_FOUND)
    
    # Ensure the profile belongs to the current user
    profile = get_object_or_404(PlatformProfile, id=profile_id, subscriber=subscriber)
    # --- RATE LIMIT ---
    allowed, retry = check_refresh_rate_limit(profile_id, email)
    if not allowed:
        return Response(
            {
                "error": "rate_limited",
                "detail": f"You have been rate limited. Try again in {retry} seconds."
            },
            status=status.HTTP_429_TOO_MANY_REQUESTS
        )
    logger.info(f"refresh_profile {profile_id}: {profile.platform_name}/{profile.username} owned by {email}")
    
    # Fetch fresh data based on platform
    platform_name = profile.platform_name
    username = profile.username
    
    fetch_map = {
        'LeetCode': fetch_leetcode_data,
        'Codeforces': fetch_codeforces_data,
        'CodeChef': fetch_codechef_data,
    }
    
    try:
        if platform_name in fetch_map:
            logger.debug(f"refresh_profile {profile_id}: fetching from {platform_name}")
            data = fetch_map[platform_name](username)
            logger.debug(f"refresh_profile {profile_id}: fetched rating={data.get('rating')}, problems={data.get('problems_solved')}, contests={data.get('contests')}")
            # Update profile with fresh data
            profile.last_rating = -1 if data.get('rating') == 'N/A' else data.get('rating')
            profile.problems_solved = -1 if data.get('problems_solved') == 'N/A' else data.get('problems_solved')
            profile.contests_attended = -1 if data.get('contests') == 'N/A' else data.get('contests')
            profile.save()
            invalidate_leaderboard_cache()
            logger.info(f"refresh_profile {profile_id}: successfully updated for {email}")
            return Response({
                'status': 'refreshed',
                'profile': serialize_profile(profile),
            })
        else:
            logger.error(f"refresh_profile {profile_id}: unknown platform {platform_name}")
            return Response({'error': 'unknown platform'}, status=status.HTTP_400_BAD_REQUEST)
    except Exception as e:
        logger.error(f"refresh_profile {profile_id}: error refreshing {platform_name}/{username} - {str(e)}", exc_info=True)
        return Response(
            {'error': 'refresh failed', 'detail': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET', 'POST'])
def home(request):
    #\"\"\"API equivalent of the home page, returning subscriber info and platforms.\"\"\"
    messages = []
    subscriber = None
    platforms = []
    email_error = None

    # session-based login (cookie)
    email = request.session.get('subscriber_email')

    if email:
        try:
            subscriber = Subscriber.objects.get(email=email)
            platforms = list(PlatformProfile.objects.filter(subscriber=subscriber))
        except Subscriber.DoesNotExist:
            del request.session['subscriber_email']
            email = None
    if request.method == 'POST':
        data = request.data
        if 'email' in data:
            email = data.get('email')
            try:
                subscriber = Subscriber.objects.get(email=email)
                request.session['subscriber_email'] = email
                platforms = list(PlatformProfile.objects.filter(subscriber=subscriber))
            except Subscriber.DoesNotExist:
                email_error = "Email not found. Please subscribe first."
        if data.get('send_report'):
            if subscriber:
                send_report_email(subscriber)
                messages.append("Daily report queued")
            else:
                messages.append("You need to log in to send the report.")
    response = {
        'subscriber': serialize_subscriber(subscriber) if subscriber else None,
        'platforms': [serialize_profile(p) for p in platforms],
        'email_error': email_error,
        'messages': messages,
    }
    return Response(response)



@api_view(['POST'])
def send_daily_report(request):
    """API endpoint to queue the daily report for the logged-in subscriber."""
    email = request.session.get('subscriber_email')
    if not email:
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    try:
        subscriber = Subscriber.objects.get(email=email)
        send_report_email(subscriber)
        return Response({'status': 'queued'})
    except Subscriber.DoesNotExist:
        return Response({'error': 'subscriber missing'}, status=status.HTTP_404_NOT_FOUND)

@api_view(['GET','PUT','PATCH'])
def update_platform_username(request, platform_name, username):
    email = request.session.get('subscriber_email')
    if not email:
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    platform = get_object_or_404(
        PlatformProfile, platform_name=platform_name, username=username, subscriber__email=email
    )
    if request.method in ['PUT', 'PATCH']:
        form = PlatformProfileForm(request.data, instance=platform)
        if form.is_valid():
            form.save()
            invalidate_leaderboard_cache()
            return Response({'status': 'updated', 'profile': serialize_profile(platform)})
        return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)
    # GET request: return existing data
    return Response({'profile': serialize_profile(platform)})

@api_view(['POST'])
def subscribe(request):
    """Create a new subscriber (and optional initial platform).
    
    Fast checks:
    1. If platform + username provided, check if already registered globally.
    2. Only then validate against remote API.
    """
    logger.info(f"subscribe request - email: {request.data.get('email')}")
    email = request.data.get('email')
    platform_name = request.data.get('platform_name')
    username = request.data.get('username')
    
    # Fast check: is this platform/username combo already registered?
    if platform_name and username and PlatformProfile.objects.filter(
        platform_name=platform_name, username=username
    ).exists():
        logger.warning(f"subscribe: profile already exists {platform_name}/{username}")
        return Response(
            {'error': 'profile exists', 'detail': f'Profile already registered: {platform_name} ({username})'},
            status=status.HTTP_409_CONFLICT
        )
    
    form = SubscriberProfileForm(request.data)
    if form.is_valid():
        subscriber = form.save()
        logger.info(f"subscribe: created new subscriber {email} with id {subscriber.id}")
        platform_name = form.cleaned_data.get('platform_name')
        username = form.cleaned_data.get('username')
        if platform_name and username:
            fetched_data = getattr(form, 'fetched_data', {})
            profile = PlatformProfile.objects.create(
                subscriber=subscriber,
                platform_name=platform_name,
                username=username,
                last_rating=fetched_data.get("rating") if fetched_data.get("rating") != 'N/A' else -1,
                problems_solved=fetched_data.get("problems_solved") if fetched_data.get("problems_solved") != 'N/A' else -1,
                contests_attended=fetched_data.get("contests") if fetched_data.get("contests") != 'N/A' else -1,
            )
            logger.info(f"subscribe: created profile {platform_name}/{username} for subscriber {email}")
        
        request.session['subscriber_email'] = subscriber.email
        invalidate_leaderboard_cache()
        logger.info(f"subscribe: session set for {email}")

        return Response({'subscriber': serialize_subscriber(subscriber)})
    
    logger.error(f"subscribe: form validation failed for {email} - {form.errors}")
    return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def add_platform_profile(request):
    """Add a platform profile for the logged-in subscriber.
    
    Fast checks:
    1. Check if subscriber is logged in.
    2. Check if profile already exists (quick DB lookup).
    3. Only then validate against remote API.
    """
    logger.info(f"add_platform_profile request")
    email = request.session.get('subscriber_email')
    if not email:
        logger.warning("add_platform_profile: no session email")
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    
    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        logger.error(f"add_platform_profile: subscriber not found for {email}")
        return Response({'error': 'subscriber missing'}, status=status.HTTP_404_NOT_FOUND)
    
    platform_name = request.data.get('platform_name')
    username = request.data.get('username')
    logger.info(f"add_platform_profile: {email} adding {platform_name}/{username}")
    
    # Fast check 1: profile already exists for THIS subscriber?
    if platform_name and PlatformProfile.objects.filter(
        subscriber=subscriber, platform_name=platform_name
    ).exists():
        logger.warning(f"add_platform_profile: {email} already has {platform_name}")
        return Response(
            {'error': 'profile exists', 'detail': f'You already have {platform_name} registered.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Fast check 2: profile already exists globally (any subscriber on this platform/username)?
    if platform_name and username and PlatformProfile.objects.filter(
        platform_name=platform_name, username=username
    ).exists():
        logger.warning(f"add_platform_profile: {platform_name}/{username} already registered globally")
        return Response(
            {'error': 'profile exists', 'detail': f'Profile already registered: {platform_name} ({username})'},
            status=status.HTTP_409_CONFLICT
        )
    
    # Only validate against remote API if checks pass
    form = PlatformProfileForm(request.data)
    if form.is_valid():
        profile = form.save(commit=False)
        profile.subscriber = subscriber
        profile.save()
        invalidate_leaderboard_cache()
        logger.info(f"add_platform_profile: successfully created {platform_name}/{username} for {email}")
        return Response({'profile': serialize_profile(profile)})
    
    logger.error(f"add_platform_profile: form validation failed for {email} - {form.errors}")
    return Response({'errors': form.errors}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def user_logout(request):
    """Clear session and log out."""
    logout(request)
    return Response({'status': 'logged out'})

@api_view(['POST'])
def unsubscribe_view(request):
    """Remove subscriber either from session or by email field."""
    email = request.session.get('subscriber_email') or request.data.get('email')
    if not email:
        return Response({'error': 'email required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        subscriber = Subscriber.objects.get(email=email)
        subscriber.delete()
        invalidate_leaderboard_cache()
        if 'subscriber_email' in request.session:
            del request.session['subscriber_email']
        return Response({'status': 'unsubscribed'})
    except Subscriber.DoesNotExist:
        return Response({'error': 'not found'}, status=status.HTTP_404_NOT_FOUND)

@api_view(['GET'])
def leaderboard(request):
    """Return leaderboard JSON with optional sorting/filtering/pagination.
    
       Includes current user's ranking efficiently using cached rank map.
    """
    email = request.session.get('subscriber_email')
    if not email:
        logger.warning("leaderboard: no session email")
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)

    subscriber = Subscriber.objects.get(email=email)

    sort_by = request.query_params.get('sort_by', 'rating')
    platform_filter = request.query_params.get('platform')
    group_filter = request.query_params.get('group')
    page = request.query_params.get('page', 1)

    logger.info(f"leaderboard request - email: {email}, sort_by: {sort_by}, platform: {platform_filter}, group: {group_filter}, page: {page}")

    # Cache key
    cache_key = f"leaderboard:{group_filter}:{platform_filter}:{sort_by}"
    cached = cache.get(cache_key)

    if cached:
        ordered_ids, rank_map = cached
        logger.debug(f"leaderboard: cache hit {cache_key}")
    else:
        logger.debug(f"leaderboard: cache miss {cache_key}, querying DB")

        qs = PlatformProfile.objects.all()

        if subscriber.group and group_filter == subscriber.group:
            qs = qs.filter(subscriber__group=subscriber.group)

        if platform_filter:
            qs = qs.filter(platform_name=platform_filter)

        qs = qs.order_by('-problems_solved' if sort_by == 'problems_solved' else '-last_rating')

        ordered_ids = list(qs.values_list('id', flat=True))
        rank_map = {pid: idx + 1 for idx, pid in enumerate(ordered_ids)}

        cache.set(cache_key, (ordered_ids, rank_map), 3600)  # 1 hour safe cache
        logger.info(f"leaderboard: cached {len(ordered_ids)} entries")

    # Fetch only required objects efficiently
    profiles_map = {
        p.id: serialize_profile(p)
        for p in PlatformProfile.objects.filter(id__in=ordered_ids)
    }

    all_profiles = [profiles_map[i] for i in ordered_ids if i in profiles_map]

    # -------- USER RANKINGS (O(1)) --------
    user_profiles = subscriber.platform_profiles.all()
    if platform_filter:
        user_profiles = user_profiles.filter(platform_name=platform_filter)

    user_rankings = {}
    for user_profile in user_profiles:
        rank = rank_map.get(user_profile.id)
        if rank:
            user_rankings[user_profile.platform_name] = {
                'rank': rank,
                'total_in_leaderboard': len(ordered_ids),
                'profile': profiles_map.get(user_profile.id),
            }

    # -------- PAGINATION --------
    paginator = Paginator(all_profiles, 10)
    page_obj = paginator.get_page(page)

    logger.info(f"leaderboard: page {page_obj.number}/{paginator.num_pages} returned")

    return Response({
        'results': list(page_obj),
        'page': page_obj.number,
        'pages': paginator.num_pages,
        'sort_by': sort_by,
        'filters': {
            'platform': platform_filter,
            'group': group_filter,
        },
        'user_rankings': user_rankings,
    })


@api_view(['POST'])
def fetch_leaderboard_data_view(request):
    """Fetch latest leaderboard data from platform APIs and clear cache."""
    try:
        logger.info("fetch_leaderboard_data_view: fetching latest data from platform APIs")
        fetch_leaderboard_data()
        
        # Clear all leaderboard caches since data changed
        logger.debug("fetch_leaderboard_data_view: clearing leaderboard cache")
        cache_keys = [
            'leaderboard:None:None:rating',
            'leaderboard:None:None:problems_solved',
        ]
        # Also clear group/platform-specific caches (simplified for now)
        for key in cache_keys:
            cache.delete(key)
        
        logger.info("fetch_leaderboard_data_view: data fetched and cache cleared")
        return Response({'status': 'success'})
    except Exception as e:
        logger.error(f"fetch_leaderboard_data_view: error - {str(e)}", exc_info=True)
        return Response({'status': 'error', 'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
def weekly_update(request):
    """Endpoint to record a snapshot and email subscribers.

    Intended to be called once per week by an external scheduler (GitHub Actions, cron, etc.).
    """
    logger.info("weekly_update: starting weekly stats snapshot and email process")
    try:
        logger.info("weekly_update: recording weekly stats")
        record_weekly_stats()
        logger.info("weekly_update: sending all weekly reports")
        send_all_weekly_reports()
        invalidate_leaderboard_cache()
        logger.info("weekly_update: completed successfully")
        
        return Response({'status': 'weekly_processed'})
    except Exception as e:
        logger.error(f"weekly_update error: {str(e)}", exc_info=True)
        return Response({'status': 'error', 'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
def create_or_join_group(request):
    """Create/join/leave group via JSON action field."""
    logger.info("create_or_join_group: group action request")
    email = request.session.get('subscriber_email')
    if not email:
        logger.warning("create_or_join_group: no session email")
        return Response({'error': 'not logged in'}, status=status.HTTP_401_UNAUTHORIZED)
    subscriber = Subscriber.objects.get(email=email)
    action = request.data.get('action')
    logger.info(f"create_or_join_group: {email} action={action}")
    if action == 'create_group':
        new_group_name = request.data.get('group_name')
        if subscriber.group:
            logger.warning(f"create_or_join_group: {email} already in group {subscriber.group}")
            return Response({'error': 'already in group'}, status=status.HTTP_400_BAD_REQUEST)
        if Subscriber.objects.filter(group=new_group_name).exists():
            logger.warning(f"create_or_join_group: group {new_group_name} already exists")
            return Response({'error': 'group exists'}, status=status.HTTP_400_BAD_REQUEST)
        subscriber.group = new_group_name
        subscriber.save()
        logger.info(f"create_or_join_group: {email} created and joined group {new_group_name}")
        return Response({'status': 'joined', 'group': new_group_name})
    elif action == 'join_group':
        group_name = request.data.get('existing_group_name')
        if not Subscriber.objects.filter(group=group_name).exists():
            logger.warning(f"create_or_join_group: group {group_name} not found")
            return Response({'error': 'group not found'}, status=status.HTTP_404_NOT_FOUND)
        if subscriber.group:
            logger.warning(f"create_or_join_group: {email} already in group {subscriber.group}")
            return Response({'error': 'already in group'}, status=status.HTTP_400_BAD_REQUEST)
        subscriber.group = group_name
        subscriber.save()
        logger.info(f"create_or_join_group: {email} joined group {group_name}")
        return Response({'status': 'joined', 'group': group_name})
    elif action == 'leave_group':
        if subscriber.group:
            old = subscriber.group
            subscriber.group = None
            subscriber.save()
            logger.info(f"create_or_join_group: {email} left group {old}")
            return Response({'status': 'left', 'group': old})
        logger.warning(f"create_or_join_group: {email} not in any group")
        return Response({'error': 'not in group'}, status=status.HTTP_400_BAD_REQUEST)
    logger.error(f"create_or_join_group: unknown action {action} from {email}")
    return Response({'error': 'unknown action'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
def health(request):
    """Simple health endpoint for load balancers or uptime checks."""
    return Response({'status': 'ok'})


def api_fetch_data_view(request):
    """API view to return stats for given usernames on each platform."""
    platforms = {
        'leetcode': 'LeetCode',
        'codechef': 'CodeChef',
        'codeforces': 'Codeforces',
    }

    result = {}

    for param, platform_name in platforms.items():
        username = request.GET.get(param)
        if not username:
            result[param] = {
                'problems_solved': 'No username provided',
                'rating': 'N/A',
                'contests': 'N/A'
            }
            continue

        try:
            profile = (
                PlatformProfile.objects
                .filter(platform_name__iexact=platform_name, username=username)
                .order_by('-id')
                .first()
            )

            if profile:
                result[param] = {
                    'problems_solved': profile.problems_solved,
                    'rating': profile.last_rating,
                    'contests': profile.contests_attended
                }
            else:
                result[param] = {
                    'problems_solved': 'User not found',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }

        except Exception as e:
            result[param] = {
                'problems_solved': 'Error',
                'rating': str(e),
                'contests': 'N/A'
            }

    return JsonResponse(result)
