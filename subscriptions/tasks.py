from django.core.mail import send_mail
from bs4 import BeautifulSoup
from .models import Subscriber, PlatformProfile, WeeklySnapshot
import requests
import logging
from django.conf import settings
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_EMAIL_WORKERS = 4 # to avoid hitting email provider limits
MAX_FETCH_WORKERS = 10   # safe for Codeforces/LeetCode/CodeChef
MAX_RETRIES = 3
RETRY_BACKOFF = 2  # exponential backoff multiplier

def _is_all_na(data):
    """Check if all values in data dict are 'N/A'."""
    return all(v == 'N/A' for v in data.values())

def _fetch_single_profile(profile):
    """Fetch stats for one profile safely (runs inside thread).
    
    Falls back to existing profile stats if all retries fail.
    """
    platform_name = profile.platform_name
    username = profile.username
    subscriber = profile.subscriber

    try:
        if platform_name == 'LeetCode':
            data = fetch_leetcode_data(username)
        elif platform_name == 'Codeforces':
            data = fetch_codeforces_data(username)
        elif platform_name == 'CodeChef':
            data = fetch_codechef_data(username)
        else:
            return None

        # If all values are N/A (fetch failed), fallback to existing stats
        if _is_all_na(data):
            logger.warning(f"_fetch_single_profile: {platform_name}/{username} fetch failed, using existing stats")
            return {
                "subscriber": subscriber,
                "platform_name": platform_name,
                "username": username,
                "rating": profile.last_rating,  # Use existing values
                "problems_solved": profile.problems_solved,
                "contests": profile.contests_attended,
            }

        problems_solved = data.get('problems_solved', 'N/A')
        rating = data.get('rating', 'N/A')
        contests = data.get('contests', 'N/A')

        problems_solved = -1 if problems_solved == 'N/A' else problems_solved
        rating = -1 if rating == 'N/A' else int(rating)
        contests = -1 if contests == 'N/A' else contests

        return {
            "subscriber": subscriber,
            "platform_name": platform_name,
            "username": username,
            "rating": rating,
            "problems_solved": problems_solved,
            "contests": contests,
        }

    except Exception as e:
        logger.error(f"_fetch_single_profile {platform_name}/{username}: {e}", exc_info=True)
        # Fallback to existing stats on exception
        return {
            "subscriber": subscriber,
            "platform_name": platform_name,
            "username": username,
            "rating": profile.last_rating,
            "problems_solved": profile.problems_solved,
            "contests": profile.contests_attended,
        }

def fetch_leaderboard_data():
    """Parallel version — fetches all profiles concurrently."""
    logger.info("fetch_leaderboard_data: starting PARALLEL fetch")

    profiles = list(PlatformProfile.objects.select_related('subscriber'))
    logger.info(f"fetch_leaderboard_data: {len(profiles)} profiles queued")

    results = []

    # ---- PARALLEL NETWORK CALLS ----
    with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
        future_map = {executor.submit(_fetch_single_profile, p): p for p in profiles}

        for future in as_completed(future_map):
            data = future.result()
            if data:
                results.append(data)

    logger.info(f"fetch_leaderboard_data: fetched {len(results)} profiles, updating DB")

    # ---- DB WRITES (sequential, safe) ----
    for item in results:
        PlatformProfile.objects.update_or_create(
            subscriber=item["subscriber"],
            platform_name=item["platform_name"],
            defaults={
                "username": item["username"],
                "last_rating": item["rating"],
                "problems_solved": item["problems_solved"],
                "contests_attended": item["contests"],
            }
        )

    logger.info("fetch_leaderboard_data: completed")
    return results


def fetch_leetcode_data(username):
    """Fetch data from LeetCode API with retry logic."""
    logger.debug(f"fetch_leetcode_data: requesting {username}")
    url = "https://leetcode.com/graphql"
    query = f"""
    {{
        matchedUser(username: "{username}") {{
            username
            submitStats: submitStatsGlobal {{
                acSubmissionNum {{
                    difficulty
                    count
                    submissions
                }}
            }}
        }}
        userContestRanking(username:  "{username}") 
        {{ attendedContestsCount rating globalRanking totalParticipants topPercentage }}
    }}
    """
    payload = {"query": query}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(f"fetch_leetcode_data: attempt {attempt}/{MAX_RETRIES} for {username}")
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            json_response = response.json()

            # Check if the user exists in the response
            if not json_response.get("data", {}).get("matchedUser"):
                logger.warning(f"fetch_leetcode_data: user {username} not found")
                return {
                    'problems_solved': 'User not found',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }
            
            # Extract submission stats
            submit_stats = json_response["data"]["matchedUser"]["submitStats"]["acSubmissionNum"]
            problems_solved = sum(item["count"] for item in submit_stats) // 2

            # Extract contest ranking information
            contest_data = json_response["data"].get("userContestRanking", None)
            
            # If contest data is not available, assign default 'N/A' values
            if contest_data:
                rating = contest_data.get("rating", "N/A")
                contests_attended = contest_data.get("attendedContestsCount", "N/A")
            else:
                rating = "N/A"
                contests_attended = "N/A"

            logger.debug(f"fetch_leetcode_data: {username} success - problems={problems_solved}, rating={rating}")
            return {
                'problems_solved': problems_solved,
                'rating': int(rating) if rating != 'N/A' else rating,
                'contests': contests_attended
            }

        except Exception as e:
            if attempt < MAX_RETRIES:
                backoff_time = (RETRY_BACKOFF ** (attempt - 1))
                logger.warning(f"fetch_leetcode_data: attempt {attempt} failed for {username}, retrying in {backoff_time}s - {str(e)}")
                time.sleep(backoff_time)
            else:
                logger.error(f"fetch_leetcode_data: all {MAX_RETRIES} attempts failed for {username}: {e}", exc_info=True)
                # Return all N/A to signal fallback to existing stats
                return {
                    'problems_solved': 'N/A',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }


def fetch_codeforces_data(username):
    """Fetch data from Codeforces API with retry logic."""
    logger.debug(f"fetch_codeforces_data: requesting {username}")
    user_info_url = f"https://codeforces.com/api/user.info?handles={username}"
    user_status_url = f"https://codeforces.com/api/user.status?handle={username}"
    user_rating_url = f"https://codeforces.com/api/user.rating?handle={username}"
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(f"fetch_codeforces_data: attempt {attempt}/{MAX_RETRIES} for {username}")
            # Fetch user info
            user_info_response = requests.get(user_info_url, timeout=10)
            user_info_response.raise_for_status()
            user_info = user_info_response.json()

            if user_info['status'] != 'OK' or not user_info.get('result'):
                logger.warning(f"fetch_codeforces_data: user {username} not found")
                return {
                    'problems_solved': 'User not found',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }
            
            # Extract rating
            rating = user_info['result'][0].get('rating', 'N/A')
            logger.debug(f"fetch_codeforces_data: {username} rating = {rating}")

            # Fetch user submissions to calculate problems solved
            user_status_response = requests.get(user_status_url, timeout=10)
            user_status_response.raise_for_status()
            user_status = user_status_response.json()

            if user_status['status'] == 'OK':
                # Filter submissions with 'verdict' = 'OK' (correct solutions)
                solved_problems = set()
                for submission in user_status['result']:
                    if submission.get('verdict') == 'OK':
                        problem = submission['problem']
                        problem_id = f"{problem.get('contestId', '')}_{problem.get('index', '')}"
                        solved_problems.add(problem_id)
                problems_solved = len(solved_problems)
                logger.debug(f"fetch_codeforces_data: {username} problems_solved = {problems_solved}")
            else:
                logger.warning(f"fetch_codeforces_data: failed to fetch submissions for {username}")
                problems_solved = 'N/A'

            # Fetch user contests to calculate total contests attended
            user_rating_response = requests.get(user_rating_url, timeout=10)
            user_rating_response.raise_for_status()
            user_rating = user_rating_response.json()

            if user_rating['status'] == 'OK':
                contests_attended = len(user_rating['result'])
                logger.debug(f"fetch_codeforces_data: {username} contests_attended = {contests_attended}")
            else:
                logger.warning(f"fetch_codeforces_data: failed to fetch contests for {username}")
                contests_attended = 'N/A'

            logger.debug(f"fetch_codeforces_data: {username} success")
            return {
                'problems_solved': problems_solved,
                'rating': rating,
                'contests': contests_attended
            }

        except Exception as e:
            if attempt < MAX_RETRIES:
                backoff_time = (RETRY_BACKOFF ** (attempt - 1))
                logger.warning(f"fetch_codeforces_data: attempt {attempt} failed for {username}, retrying in {backoff_time}s - {str(e)}")
                time.sleep(backoff_time)
            else:
                logger.error(f"fetch_codeforces_data: all {MAX_RETRIES} attempts failed for {username}: {e}", exc_info=True)
                # Return all N/A to signal fallback to existing stats
                return {
                    'problems_solved': 'N/A',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }

def fetch_codechef_data(username):
    """Fetch data from CodeChef by scraping with retry logic."""
    logger.debug(f"fetch_codechef_data: requesting {username}")
    url = f"https://www.codechef.com/users/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/114.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.google.com"
    }
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.debug(f"fetch_codechef_data: attempt {attempt}/{MAX_RETRIES} for {username}")
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()

            if response.url == "https://www.codechef.com/":
                logger.warning(f"fetch_codechef_data: user {username} not found")
                return {
                    'problems_solved': 'User not found',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }

            soup = BeautifulSoup(response.text, 'html.parser')
            problems_section = soup.find('section', class_='rating-data-section problems-solved')
            total_problems_solved = None
            total_contests_attended = None
            if problems_section:
                h3_tags = problems_section.find_all('h3')
                if h3_tags:
                    total_problems_solved = h3_tags[-1].text.split(":")[1].strip()

            rating_section = soup.find('div', class_='rating-number')
            rating = rating_section.text.strip() if rating_section else 'N/A'
            contests_section = soup.find('div', class_='contest-participated-count')
            total_contests_attended = contests_section.find('b').text.strip() if contests_section else None
            
            logger.debug(f"fetch_codechef_data: {username} success - problems={total_problems_solved}, rating={rating}")
            return {
                'problems_solved': total_problems_solved or 'N/A',
                'rating': rating or 'N/A',
                'contests': total_contests_attended or 'N/A',
            }
        except Exception as e:
            if attempt < MAX_RETRIES:
                backoff_time = (RETRY_BACKOFF ** (attempt - 1))
                logger.warning(f"fetch_codechef_data: attempt {attempt} failed for {username}, retrying in {backoff_time}s - {str(e)}")
                time.sleep(backoff_time)
            else:
                logger.error(f"fetch_codechef_data: all {MAX_RETRIES} attempts failed for {username}: {e}", exc_info=True)
                # Return all N/A to signal fallback to existing stats
                return {
                    'problems_solved': 'N/A',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }


def send_report_email(subscriber):
    """Send a report email to a specific subscriber."""
    logger.info(f"send_report_email: queuing report for {subscriber.email}")
    platform_profiles = PlatformProfile.objects.filter(subscriber=subscriber)
    logger.debug(f"send_report_email: {subscriber.email} has {platform_profiles.count()} profiles")

    email_subject = 'Your Daily Coding Activity Report'
    email_body = f"""
    <html>
    <body color="#333">
        <p>Hello {subscriber.email},</p>
        <p>Here is your activity report:</p>
        <ul>
    """

    for profile in platform_profiles:
        platform_name = profile.platform_name
        username = profile.username

        try:
            # Fetch data for each platform
            if platform_name == 'LeetCode':
                data = fetch_leetcode_data(username)
            elif platform_name == 'Codeforces':
                data = fetch_codeforces_data(username)
            elif platform_name == 'CodeChef':
                data = fetch_codechef_data(username)
            else:
                data = {
                    'problems_solved': 'N/A',
                    'rating': 'N/A',
                    'contests': 'N/A'
                }

            logger.debug(f"send_report_email: {subscriber.email} {platform_name} - rating: {data.get('rating')}, problems: {data.get('problems_solved')}")

            # Append data to the email body (HTML)
            email_body += f"""
            <li>
                <strong>{platform_name} ({username})</strong><br>
                <ul>
                    <li>Problems Solved: {data.get('problems_solved', 'N/A')}</li>
                    <li>Rating: {data.get('rating', 'N/A')}</li>
                    <li>Contests Attended: {data.get('contests', 'N/A')}</li>
                </ul>
            </li>
            """

        except Exception as e:
            logger.error(f"send_report_email: error fetching data for {platform_name} ({username}) for {subscriber.email}: {str(e)}", exc_info=True)
            email_body += f"""
            <li>
                <strong>{platform_name} ({username})</strong><br>
                <ul>
                    <li>Error fetching data.</li>
                </ul>
            </li>
            """

    email_body += f"""
        </ul>
        <p>Best Regards,<br>SkillTracker</p>
    </body>
    </html>
    """

    # Send the email
    try:
        send_mail(
            email_subject,
            email_body,
            settings.DEFAULT_FROM_EMAIL,  # Your sending email
            [subscriber.email],  # Recipient email
            fail_silently=False,
            html_message=email_body  # Use HTML format
        )
        logger.info(f"send_report_email: successfully sent to {subscriber.email}")
    except Exception as e:
        logger.error(f"send_report_email: error sending email to {subscriber.email}: {str(e)}", exc_info=True)


def record_weekly_stats():
    """Generate a weekly snapshot for every platform profile.

    This should be invoked once per week (e.g., via GitHub Actions).
    The function also ensures the latest data are fetched before snapshotting.
    """
    logger.info("record_weekly_stats: starting")
    # update all profiles with latest values first
    logger.info("record_weekly_stats: fetching latest leaderboard data")
    fetch_leaderboard_data()

    profiles = PlatformProfile.objects.all()
    logger.info(f"record_weekly_stats: recording snapshots for {profiles.count()} profiles")
    for profile in profiles:
        WeeklySnapshot.objects.create(
            profile=profile,
            last_rating=profile.last_rating,
            problems_solved=profile.problems_solved,
            contests_attended=profile.contests_attended,
        )
        logger.debug(f"record_weekly_stats: snapshot created for {profile.subscriber.email}/{profile.platform_name}")
    logger.info("record_weekly_stats: completed")


def _compile_weekly_changes():
    """Return per-subscriber list of diffs and global totals.

    Returns a tuple `(subscriber_changes, total_problems, total_contests)`,
    where `subscriber_changes` is a dict keyed by Subscriber instance.
    """
    logger.info("_compile_weekly_changes: starting compilation of weekly changes")
    subscriber_changes = {}
    total_problems = 0
    total_contests = 0

    all_subscribers = list(Subscriber.objects.all())
    logger.info(f"_compile_weekly_changes: compiling changes for {len(all_subscribers)} subscribers")

    for subscriber in all_subscribers:
        diffs = []
        profiles = list(subscriber.platform_profiles.all())
        logger.debug(f"_compile_weekly_changes: {subscriber.email} has {len(profiles)} profiles")
        
        for profile in profiles:
            snaps = list(profile.snapshots.order_by('-timestamp')[:2])
            if len(snaps) == 2:
                current, previous = snaps[0], snaps[1]
                problems = (current.problems_solved or 0) - (previous.problems_solved or 0)
                contests = (current.contests_attended or 0) - (previous.contests_attended or 0)
                rating_change = (current.last_rating or 0) - (previous.last_rating or 0)
                diffs.append({
                    'platform': profile.platform_name,
                    'username': profile.username,
                    'problems_solved': problems,
                    'contests_attended': contests,
                    'rating_change': rating_change,
                })
                logger.debug(f"_compile_weekly_changes: {profile.platform_name}/{profile.username} - problems_delta={problems}, contests_delta={contests}, rating_delta={rating_change}")
                total_problems += problems
                total_contests += contests
            else:
                logger.debug(f"_compile_weekly_changes: {profile.platform_name}/{profile.username} insufficient snapshots ({len(snaps)} < 2)")
        if diffs:
            subscriber_changes[subscriber] = diffs
            logger.info(f"_compile_weekly_changes: {subscriber.email} has {len(diffs)} changed profile(s)")
    
    logger.info(f"_compile_weekly_changes: completed - {len(subscriber_changes)} subscribers with changes, total_problems={total_problems}, total_contests={total_contests}")
    return subscriber_changes, total_problems, total_contests


def send_weekly_report_email(subscriber, diffs):
    """Email a subscriber their weekly changes.

    `diffs` is a list of dicts produced by `_compile_weekly_changes`.
    """
    logger.info(f"send_weekly_report_email: preparing report for {subscriber.email} with {len(diffs)} profile(s)")
    email_subject = 'Your Weekly Coding Activity Summary'
    email_body = '<html><body>'
    email_body += f'<p>Hello {subscriber.email},</p>'
    email_body += '<p>Here are your changes from the past week:</p><ul>'
    for d in diffs:
        logger.debug(f"send_weekly_report_email: {subscriber.email} - {d['platform']}/{d['username']}: problems={d['problems_solved']}, rating_change={d['rating_change']}, contests={d['contests_attended']}")
        email_body += (
            f"<li><strong>{d['platform']} ({d['username']})</strong><br>"
            f"Problems Solved: {d['problems_solved']}<br>"
            f"Rating Change: {d['rating_change']}<br>"
            f"Contests Attended: {d['contests_attended']}</li>"
        )
    email_body += '</ul><p>Keep up the good work!<br>SkillTracker</p></body></html>'
    try:
        logger.debug(f"send_weekly_report_email: sending email to {subscriber.email}")
        send_mail(
            email_subject,
            email_body,
            settings.DEFAULT_FROM_EMAIL,
            [subscriber.email],
            fail_silently=False,
            html_message=email_body,
        )
        logger.info(f"send_weekly_report_email: successfully sent to {subscriber.email}")
    except Exception as e:
        logger.error(f"send_weekly_report_email: error sending weekly email to {subscriber.email}: {e}", exc_info=True)


#def send_global_weekly_report(total_problems, total_contests):
#    """Send a single email summarizing global totals."""
#    logger.info(f"send_global_weekly_report: preparing global summary - total_problems={total_problems}, total_contests={total_contests}")
#    email_subject = 'Weekly Global Activity Summary'
#    email_body = (
#        '<html><body>'
#        '<p>Hello SkillTracker Admin,</p>'
#        '<p>Here are the overall numbers for this week:</p>'
#        f'<ul><li>Problems solved: {total_problems}</li>'
#        f'<li>Contests attended: {total_contests}</li></ul>'
#        '<p>Regards,<br>SkillTracker</p></body></html>'
#    )
#    try:
#        logger.debug(f"send_global_weekly_report: sending global summary to {settings.DEFAULT_FROM_EMAIL}")
#        send_mail(
#            email_subject,
#            email_body,
#            settings.DEFAULT_FROM_EMAIL,
#            [settings.DEFAULT_FROM_EMAIL],
#            fail_silently=False,
#            html_message=email_body,
#        )
#        logger.info("send_global_weekly_report: successfully sent global summary")
#    except Exception as e:
#        logger.error(f"send_global_weekly_report: error sending global weekly summary: {e}", exc_info=True)


MAX_EMAIL_WORKERS = 4  # safe for Gmail

def _send_single_weekly_email(args):
    """Wrapper so threads don't crash main loop."""
    subscriber, diffs = args
    try:
        send_weekly_report_email(subscriber, diffs)
        return True, subscriber.email
    except Exception as e:
        logger.error(f"email failed for {subscriber.email}: {e}", exc_info=True)
        return False, subscriber.email

def send_all_weekly_reports():
    """Parallel weekly report sender."""
    logger.info("send_all_weekly_reports: starting parallel dispatch")

    subs, tot_probs, tot_contests = _compile_weekly_changes()
    subscribers_list = list(subs.items())

    logger.info(f"send_all_weekly_reports: sending {len(subscribers_list)} emails")

    success = 0
    failed = 0

    # ---- PARALLEL EMAIL SENDING ----
    with ThreadPoolExecutor(max_workers=MAX_EMAIL_WORKERS) as executor:
        futures = [executor.submit(_send_single_weekly_email, item) for item in subscribers_list]

        for future in as_completed(futures):
            ok, email = future.result()
            if ok:
                success += 1
            else:
                failed += 1

    logger.info(f"send_all_weekly_reports: emails sent={success}, failed={failed}")

    # Send global summary AFTER user emails not needed for now, can be re-added later if desired
    # send_global_weekly_report(tot_probs, tot_contests)

    logger.info("send_all_weekly_reports: completed")
