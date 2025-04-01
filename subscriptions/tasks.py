from django.core.mail import send_mail
from bs4 import BeautifulSoup
from .models import Subscriber, PlatformProfile
import requests
import logging
from django.conf import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_leaderboard_data():
    """Fetch leaderboard data and update the database."""
    leaderboard_data = []

    # Fetch all subscribers
    subscribers = Subscriber.objects.all()

    for subscriber in subscribers:
        platform_profiles = PlatformProfile.objects.filter(subscriber=subscriber)

        for profile in platform_profiles:
            platform_name = profile.platform_name
            username = profile.username

            # Fetch platform-specific data from the respective platform
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

            # Convert 'N/A' to -1 where needed
            problems_solved = data.get('problems_solved', 'N/A')
            rating = data.get('rating', 'N/A')
            contests = data.get('contests', 'N/A')

            # Replace 'N/A' with -1
            problems_solved = -1 if problems_solved == 'N/A' else problems_solved
            rating = -1 if rating == 'N/A' else int(rating)
            contests = -1 if contests == 'N/A' else contests

            # Update or create PlatformProfile with fetched data
            platform_profile, created = PlatformProfile.objects.update_or_create(
                subscriber=subscriber,
                platform_name=platform_name,
                defaults={
                    'username': username,
                    'last_rating': rating,
                    'problems_solved': problems_solved,
                    'contests_attended': contests
                }
            )

            # Add platform data to the leaderboard (for reference, can be used in emails)
            leaderboard_data.append({
                'email': subscriber.email,
                'platform_name': platform_name,
                'username': username,
                'problems_solved': problems_solved,
                'rating': rating,
                'contests': contests,
            })

    return leaderboard_data


def fetch_leetcode_data(username):
    """Fetch data from LeetCode API."""
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

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        json_response = response.json()
        #print(json_response)

        # Check if the user exists in the response
        if not json_response.get("data", {}).get("matchedUser"):
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

        #print(problems_solved, rating, contests_attended)
        return {
            'problems_solved': problems_solved,
            'rating': int(rating) if rating != 'N/A' else rating,
            'contests': contests_attended
        }

    except Exception as e:
        logger.error(f"LeetCode API error for {username}: {e}")
        return {
            'problems_solved': 'N/A',
            'rating': 'N/A',
            'contests': 'N/A'
        }


def fetch_codeforces_data(username):
    """Fetch data from Codeforces API."""
    user_info_url = f"https://codeforces.com/api/user.info?handles={username}"
    user_status_url = f"https://codeforces.com/api/user.status?handle={username}"
    user_rating_url = f"https://codeforces.com/api/user.rating?handle={username}"
    
    try:
        # Fetch user info
        user_info_response = requests.get(user_info_url, timeout=10)
        user_info_response.raise_for_status()
        user_info = user_info_response.json()

        if user_info['status'] != 'OK' or not user_info.get('result'):
            return {
                'problems_solved': 'User not found',
                'rating': 'N/A',
                'contests': 'N/A'
            }
        
        # Extract rating
        rating = user_info['result'][0].get('rating', 'N/A')

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
        else:
            problems_solved = 'N/A'

        # Fetch user contests to calculate total contests attended
        user_rating_response = requests.get(user_rating_url, timeout=10)
        user_rating_response.raise_for_status()
        user_rating = user_rating_response.json()

        if user_rating['status'] == 'OK':
            contests_attended = len(user_rating['result'])
        else:
            contests_attended = 'N/A'

        return {
            'problems_solved': problems_solved,
            'rating': rating,
            'contests': contests_attended
        }

    except Exception as e:
        logger.error(f"Codeforces API error for {username}: {e}")
        return {
            'problems_solved': 'N/A',
            'rating': 'N/A',
            'contests': 'N/A'
        }

def fetch_codechef_data(username):
    """Fetch data from CodeChef by scraping."""
    url = f"https://www.codechef.com/users/{username}"
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()

        if response.url == "https://www.codechef.com/":
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
        
        #print(total_problems_solved, rating)
        return {
            'problems_solved': total_problems_solved or 'N/A',
            'rating': rating or 'N/A',
            'contests': total_contests_attended or 'N/A',
        }
    except Exception as e:
        logger.error(f"CodeChef scraping error for {username}: {e}")
        return {
            'problems_solved': 'N/A',
            'rating': 'N/A',
            'contests': 'Failed to fetch data'
        }


def send_report_email(subscriber):
    """Send a report email to a specific subscriber."""
    platform_profiles = PlatformProfile.objects.filter(subscriber=subscriber)

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
            logger.error(f"Error fetching data for {platform_name} ({username}): {e}")
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
        <p>Best Regards,<br>Skill Tracker</p>
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
        logger.info(f"Email sent successfully to {subscriber.email}")
    except Exception as e:
        logger.error(f"Error sending email to {subscriber.email}: {e}")