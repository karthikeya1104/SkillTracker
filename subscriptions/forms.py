from django import forms
from django.core.exceptions import ValidationError
import requests
from .models import Subscriber, PlatformProfile
from .tasks import fetch_codechef_data, fetch_codeforces_data, fetch_leetcode_data

def validate_leetcode_username(value):
    url = "https://leetcode.com/graphql"
    query = f"""
    {{
        matchedUser(username: "{value}") {{
            username
        }}
    }}
    """
    payload = {"query": query}
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        json_response = response.json()

        if not json_response.get("data", {}).get("matchedUser"):
            raise ValidationError(f"LeetCode username '{value}' does not exist.")
    except ValidationError as ve:
        raise ve
    except requests.exceptions.RequestException as req_err:
        raise ValidationError(f"Failed to reach LeetCode. {req_err}")
    except Exception as e:
        raise ValidationError(f"An unexpected error occurred: {str(e)}")
    return value

def validate_codeforces_username(value):
    user_info_url = f"https://codeforces.com/api/user.info?handles={value}"
    try:
        response = requests.get(user_info_url, timeout=5)
        
        if response.status_code != 200:
            raise ValidationError(f"Codeforces username '{value}' does not exist.")
        
        user_info = response.json()
        
        if user_info['status'] != 'OK' or len(user_info['result']) == 0:
            raise ValidationError(f"CodeForces username {value} does not exist.")
    
    except requests.exceptions.RequestException:
        raise ValidationError("Failed to reach Codeforces. Please check your connection.")
    return value

def validate_codechef_username(value):
    url = f"https://www.codechef.com/users/{value}"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Connection": "keep-alive",
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)

        if response.status_code == 404:
            raise ValidationError(f"CodeChef username {value} does not exist.")

        if response.status_code == 403:
            raise ValidationError("CodeChef blocked the request (anti-bot protection). Try again later.")

    except requests.exceptions.RequestException as e:
        raise ValidationError(f"Failed to reach CodeChef: {str(e)}")

    return value

class PlatformProfileForm(forms.ModelForm):
    platform_name = forms.ChoiceField(choices=PlatformProfile.PLATFORM_CHOICES, required=True)
    username = forms.CharField(max_length=100)

    class Meta:
        model = PlatformProfile
        fields = ['platform_name', 'username']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # If the form is bound to an instance (i.e., updating an existing profile)
        if self.instance and self.instance.pk:
            # Fix the platform_name field to the instance's platform and disable it
            self.fields['platform_name'].initial = self.instance.platform_name
            self.fields['platform_name'].disabled = True

    def clean_username(self):
        # Get the platform name from the instance if updating, or from cleaned_data if adding
        platform_name = self.instance.platform_name if self.instance.pk else self.cleaned_data.get('platform_name')
        username = self.cleaned_data.get('username')

        # Map platform to validation and fetching functions
        platform_validators = {
            'LeetCode': (validate_leetcode_username, fetch_leetcode_data),
            'Codeforces': (validate_codeforces_username, fetch_codeforces_data),
            'CodeChef': (validate_codechef_username, fetch_codechef_data),
        }

        # Ensure the platform has a validator and fetcher
        if platform_name in platform_validators:
            # Validate the username
            platform_validators[platform_name][0](username)  # Raises ValidationError if invalid
            
            # Fetch additional data
            fetched_data = platform_validators[platform_name][1](username)
            
            # Update instance fields with fetched data
            rating = fetched_data.get("rating")
            problems_solved = fetched_data.get("problems_solved")
            contests_attended = fetched_data.get("contests")

            self.instance.last_rating = -1 if rating == 'N/A' else rating
            self.instance.problems_solved = -1 if problems_solved == 'N/A' else problems_solved
            self.instance.contests_attended = -1 if contests_attended == 'N/A' else contests_attended

        return username


class SubscriberProfileForm(forms.ModelForm):
    platform_name = forms.ChoiceField(choices=PlatformProfile.PLATFORM_CHOICES, required=False)
    username = forms.CharField(max_length=100, required=False)

    class Meta:
        model = Subscriber
        fields = ['email']

    def clean(self):
        cleaned_data = super().clean()
        platform_name = cleaned_data.get('platform_name')
        username = cleaned_data.get('username')

        if platform_name and username:
            # Map platform to validation and fetching functions
            platform_validators = {
                'LeetCode': (validate_leetcode_username, fetch_leetcode_data),
                'Codeforces': (validate_codeforces_username, fetch_codeforces_data),
                'CodeChef': (validate_codechef_username, fetch_codechef_data),
            }

            if platform_name in platform_validators:
                # Validate username
                platform_validators[platform_name][0](username)  # Raises ValidationError if invalid
                # Fetch data (if needed in the view)
                self.fetched_data = platform_validators[platform_name][1](username)
            else:
                raise forms.ValidationError("Invalid platform selected.")
        elif platform_name or username:
            raise forms.ValidationError("Both platform and username are required if either is provided.")

        return cleaned_data