from django.shortcuts import render, redirect, get_object_or_404
from .models import Subscriber, PlatformProfile
from .forms import SubscriberProfileForm, PlatformProfileForm
from django.contrib import messages
from django.contrib.auth import logout
from .tasks import send_report_email, fetch_leaderboard_data
from django.core.paginator import Paginator
from django.http import JsonResponse

def home(request):
    """Home page for the subscriber."""
    subscriber = None
    platforms = []
    email_error = None

    # Check if there's a subscriber email in the session
    email = request.session.get('subscriber_email')

    if email:
        try:
            subscriber = Subscriber.objects.get(email=email)
            platforms = PlatformProfile.objects.filter(subscriber=subscriber)
        except Subscriber.DoesNotExist:
            del request.session['subscriber_email']
    
    elif request.method == 'POST':
        # If no subscriber in session, handle the email input
        email = request.POST.get('email')
        try:
            # Check if the email exists in the Subscriber model
            subscriber = Subscriber.objects.get(email=email)
            request.session['subscriber_email'] = email  # Save email in session
            platforms = PlatformProfile.objects.filter(subscriber=subscriber)
        except Subscriber.DoesNotExist:
            email_error = "Email not found. Please subscribe first."
    
    if request.method == 'POST' and 'send_report' in request.POST:
        if subscriber:
            # Trigger the report email sending task for the logged-in subscriber
            send_report_email(subscriber)
            messages.success(request, "Your daily report has been sent successfully!")
            # Redirect to prevent resubmission on page refresh
            return redirect('home')
        else:
            messages.error(request, "You need to log in to send the report.")
            
    return render(request, 'home.html', {
        'subscriber': subscriber,
        'platforms': platforms,
        'email_error': email_error,
    })



def send_daily_report(request):
    """View to send the daily report emails to the logged-in subscriber."""
    email = request.session.get('subscriber_email')

    if not email:
        return redirect('home')  # If no email in session, redirect to home page

    try:
        subscriber = Subscriber.objects.get(email=email)
        send_report_email(subscriber)  # Send the email using the task
        messages.success(request, "Your daily report has been sent successfully!")
    except Subscriber.DoesNotExist:
        messages.error(request, "Subscriber not found. Please log in again.")
    
    return redirect('home')  # Redirect back to the home page after sending the email

def update_platform_username(request, platform_name, username):
    email = request.session.get('subscriber_email')
    if not email:
        return redirect('home')

    # Fetch the correct platform profile by matching platform_name, username, and subscriber
    platform = get_object_or_404(
        PlatformProfile, platform_name=platform_name, username=username, subscriber__email=email
    )

    if request.method == 'POST':
        platform_form = PlatformProfileForm(request.POST, instance=platform)
        if platform_form.is_valid():
            platform_form.save()
            messages.success(request, "Username updated successfully!")
            return redirect('home')
    else:
        platform_form = PlatformProfileForm(instance=platform)

    return render(request, 'update_platform_username.html', {
        'platform_form': platform_form,
        'platform': platform,
    })

def subscribe(request):
    """Handle subscription and redirect to home with subscriber details."""
    if request.method == 'POST':
        form = SubscriberProfileForm(request.POST)
        if form.is_valid():
            subscriber = form.save(commit=False)
            subscriber.save()

            platform_name = form.cleaned_data.get('platform_name')
            username = form.cleaned_data.get('username')

            if platform_name and username:
                # Access fetched data from the form
                fetched_data = getattr(form, 'fetched_data', {})
                PlatformProfile.objects.create(
                    subscriber=subscriber,
                    platform_name=platform_name,
                    username=username,
                    last_rating=fetched_data.get("rating") if fetched_data.get("rating") != 'N/A' else -1,
                    problems_solved=fetched_data.get("problems_solved") if fetched_data.get("problems_solved") != 'N/A' else -1,
                    contests_attended=fetched_data.get("contests") if fetched_data.get("contests") != 'N/A' else -1,
                )

            request.session['subscriber_email'] = subscriber.email
            return redirect('home')
        else:
            messages.error(request, "Error in the subscription form. Please check your input.")
    else:
        form = SubscriberProfileForm()

    return render(request, 'subscribe.html', {'form': form})

def add_platform_profile(request):
    """Add platform profile for the logged-in subscriber or ask for email if no session."""
    email = request.session.get('subscriber_email')
    
    if not email:
        if request.method == 'POST':
            email = request.POST.get('email')
            try:
                subscriber = Subscriber.objects.get(email=email)
                request.session['subscriber_email'] = subscriber.email
                return redirect('add_platform_profile')
            except Subscriber.DoesNotExist:
                # If the email does not exist, give a message to subscribe first and redirect to the home page
                messages.error(request, "Email does not exist. Please subscribe first.")
                return redirect('home')  # Redirecting to the home page
        return render(request, 'ask_email.html')

    try:
        subscriber = Subscriber.objects.get(email=email)
    except Subscriber.DoesNotExist:
        return redirect('add_platform_profile')

    # Check if the platform profile with the same username already exists for this subscriber
    if request.method == 'POST':
        form = PlatformProfileForm(request.POST)
        if form.is_valid():
            platform_name = form.cleaned_data['platform_name']
            username = form.cleaned_data['username']
            
            # Check if the platform profile already exists for this subscriber
            if PlatformProfile.objects.filter(subscriber=subscriber, platform_name=platform_name).exists():
                messages.info(request, "You already have this platform. If you want to edit it, do so from the home page.")
                return redirect('home')
            
            # If it doesn't exist, save the new profile
            profile = form.save(commit=False)
            profile.subscriber = subscriber
            profile.save()
            return redirect('home')
    else:
        form = PlatformProfileForm()

    return render(request, 'add_platform_profile.html', {'form': form, 'subscriber': subscriber})

def user_logout(request):
    """Handle user logout."""
    logout(request)
    return redirect('home')

def unsubscribe_view(request):
    """Handle the unsubscribe request."""
    subscriber_email = request.session.get('subscriber_email')

    if subscriber_email:
        try:
            subscriber = Subscriber.objects.get(email=subscriber_email)
            subscriber.delete()
            messages.success(request, "You have been successfully unsubscribed.")
            
            del request.session['subscriber_email']
            
            return redirect('home')
        except Subscriber.DoesNotExist:
            messages.error(request, "Your session email does not exist in our records.")
            return redirect('home')

    if request.method == 'POST':
        email = request.POST.get('email')
        
        try:
            subscriber = Subscriber.objects.get(email=email)
            subscriber.delete() 
            messages.success(request, "You have been successfully unsubscribed.")
            return redirect('home')
        except Subscriber.DoesNotExist:
            messages.error(request, "Email not found. Please try again.")
    
    return render(request, 'unsubscribe.html')

def leaderboard(request):
    """Display the leaderboard of registered users with options for sorting and filtering by platform."""
    
    subscriber_email = request.session.get('subscriber_email')
    
    if not subscriber_email:
        return redirect('home')
    
    # Get the logged-in subscriber instance
    subscriber = Subscriber.objects.get(email=subscriber_email)
    subscriber_platforms = PlatformProfile.objects.filter(subscriber__email=subscriber_email).values_list('username', flat=True)

    # Get query parameters for sorting and filtering
    sort_by = request.GET.get('sort_by', 'rating')
    platform_filter = request.GET.get('platform', None)
    group_filter = request.GET.get('group', None)

    # Fetch leaderboard data from the database (all users)
    leaderboard_data = PlatformProfile.objects.all()

    # If a group is selected, filter by that group (disregard platform and sorting)
    if subscriber.group and group_filter == subscriber.group:
        leaderboard_data = leaderboard_data.filter(subscriber__group=subscriber.group)
        
    if platform_filter:
        leaderboard_data = leaderboard_data.filter(platform_name=platform_filter)
    
    # Sort the leaderboard data by selected criterion (rating or problems solved)
    if sort_by == 'problems_solved':
        leaderboard_data = leaderboard_data.order_by('-problems_solved')
    else:
        leaderboard_data = leaderboard_data.order_by('-last_rating')

    # Paginate the leaderboard data (10 entries per page)
    paginator = Paginator(leaderboard_data, 10)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    # Pass the sorted, filtered, and paginated data to the template
    return render(request, 'leaderboard.html', {
        'leaderboard_data': page_obj.object_list,
        'page_obj': page_obj,
        'sort_by': sort_by,
        'platform_filter': platform_filter,
        'group_filter': group_filter,
        'subscriber': subscriber,
        'subscriber_platforms': subscriber_platforms,
    })

def fetch_leaderboard_data_view(request):
    try:
        fetch_leaderboard_data()
        return JsonResponse({'status': 'success', 'message': 'Leaderboard data fetched successfully'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})

def create_or_join_group(request):
    """Allow users to create, join, or leave a group."""
    subscriber_email = request.session.get('subscriber_email')

    if not subscriber_email:
        return redirect('home')

    subscriber = Subscriber.objects.get(email=subscriber_email)

    if request.method == "POST":
        action = request.POST.get('action')

        if action == 'create_group':
            # Create a new group
            new_group_name = request.POST.get('group_name')
            
            if subscriber.group:
                messages.error(request, "You are already part of a group!")
                return redirect('create_or_join_group')
            
            if Subscriber.objects.filter(group=new_group_name).exists():
                messages.error(request, "Group already exist!")
                return redirect('create_or_join_group')
            
            subscriber.group = new_group_name
            subscriber.save()
            messages.success(request, f"You have successfully created and joined the group: {new_group_name}")
            return redirect('home')

        elif action == 'join_group':
            # Join an existing group
            group_name = request.POST.get('existing_group_name')

            if not Subscriber.objects.filter(group=group_name).exists():
                messages.error(request, "Group does not exist!")
                return redirect('create_or_join_group')

            if subscriber.group:
                messages.error(request, "You are already part of a group!")
                return redirect('create_or_join_group')

            subscriber.group = group_name
            subscriber.save()
            messages.success(request, f"You have successfully joined the group: {group_name}")
            return redirect('home')

        elif action == 'leave_group':
            # Leave the current group
            if subscriber.group:
                messages.success(request, f"You have left the group: {subscriber.group}")
                subscriber.group = None
                subscriber.save()
            else:
                messages.error(request, "You are not in any group!")
            
            return redirect('create_or_join_group')

    return render(request, 'create_or_join_group.html', {
        'subscriber': subscriber,
    })
