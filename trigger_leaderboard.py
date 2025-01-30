import requests

API_URL = "https://skilltracker-1yk8.onrender.com/trigger-leaderboard/"

def trigger_leaderboard():
    try:
        print(f"Calling API: {API_URL}")
        response = requests.get(API_URL, timeout=80)
        
        if response.status_code == 200:
            print("API call successful:", response.json())
        else:
            print(f"API call failed with status code {response.status_code}: {response.text}")

    except requests.exceptions.Timeout:
        print("API call timed out after 80 seconds.")
    except Exception as e:
        print("Error while calling API:", str(e))

if __name__ == "__main__":
    trigger_leaderboard()
