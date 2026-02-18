# GitHub Actions Setup - Weekly Reports

## Overview
This GitHub Actions workflow automatically triggers weekly reports every **Monday at 9:00 AM UTC**.

The workflow calls the `/api/weekly-update/` endpoint which:
1. Records weekly snapshots for all profiles
2. Sends per-user weekly reports via email
3. Sends a global admin summary

## Setup Instructions

### 1. Configure GitHub Secrets

Go to your GitHub repository → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add the following secrets:

#### `SKILLTRACKER_API_URL`
- **Value**: Your deployed SkillTracker API URL (without trailing slash)
- **Example**: `https://skilltracker.onrender.com` or `https://your-domain.com`

#### `SKILLTRACKER_API_SECRET`
- **Value**: A secret token for API authentication (optional, only if you add auth to the endpoint)
- **Note**: Currently, the endpoint has no authentication. If you want to add security:
  1. Modify `/api/weekly-update/` in `views.py` to require an API key
  2. Store that key in this secret
  3. The workflow will send it as a Bearer token

### 2. Modify Endpoint Security (Optional)

If you want to protect the endpoint from unauthorized calls, edit `subscriptions/views.py`:

```python
@api_view(['POST'])
def weekly_update(request):
    """Endpoint to record a snapshot and email subscribers.
    
    Requires API authentication via bearer token.
    """
    # Option 1: Check authorization header
    auth_header = request.headers.get('Authorization', '')
    expected_token = os.getenv('WEEKLY_UPDATE_TOKEN')
    
    if f"Bearer {expected_token}" != auth_header:
        logger.warning("weekly_update: unauthorized access attempt")
        return Response({'error': 'unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
    
    # ... rest of the function
```

Then add `WEEKLY_UPDATE_TOKEN` as an environment variable in your Render.com deployment settings.

### 3. Schedule Customization

To change when the reports run, edit `.github/workflows/weekly-reports.yml`:

**Current**: Every Monday at 9:00 AM UTC
```yaml
cron: '0 9 * * 1'
```

**Common alternatives**:
- `'0 9 * * 1'` → Monday 9 AM UTC
- `'0 6 * * 1'` → Monday 6 AM UTC
- `'30 8 * * 1'` → Monday 8:30 AM UTC
- `'0 0 * * 1'` → Monday 12:00 AM UTC (midnight)

**Cron format**: `minute hour day month weekday`
- Minute: 0-59
- Hour: 0-23 (UTC)
- Day: 1-31 (ignored with *)
- Month: 1-12 (ignored with *)
- Weekday: 0 = Sunday, 1 = Monday, ..., 6 = Saturday

### 4. Manual Trigger

To manually test the workflow:
1. Go to your GitHub repo → **Actions** tab
2. Select "Weekly Reports - Monday Morning" workflow
3. Click **Run workflow** → **Run workflow**

This will immediately trigger the weekly reports without waiting for Monday.

### 5. Monitor Execution

Check workflow status:
1. Go to **Actions** tab in GitHub
2. Find "Weekly Reports - Monday Morning"
3. Click on recent runs to see logs
4. Logs show:
   - ✅ Success with HTTP 200 response
   - ❌ Failure if API endpoint is unreachable

## Troubleshooting

### Workflow fails with "connection refused"
- **Cause**: API URL is incorrect or app is offline
- **Fix**: 
  1. Verify `SKILLTRACKER_API_URL` secret is correct
  2. Make sure your Render app is running
  3. Test the endpoint manually: `curl https://your-api-url/api/weekly-update/ -X POST`

### Workflow fails with HTTP 401
- **Cause**: Authorization header is incorrect
- **Fix**: 
  1. Check `SKILLTRACKER_API_SECRET` matches the token in your app
  2. If not using auth, remove the Authorization header from the workflow

### Workflow never runs
- **Cause**: GitHub Actions might be disabled or workflow syntax is invalid
- **Fix**:
  1. Verify GitHub Actions are enabled in repo settings
  2. Check workflow file syntax in Actions → "Weekly Reports" tab for errors

### Emails not being sent
- **Cause**: Email configuration in Django settings
- **Fix**: Verify in `settings.py`:
  ```python
  EMAIL_HOST = 'smtp.gmail.com'  (or your SMTP provider)
  EMAIL_PORT = 587
  EMAIL_USE_TLS = True
  EMAIL_HOST_USER = env('EMAIL_HOST_USER')  (set in Render env vars)
  EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD')
  DEFAULT_FROM_EMAIL = env('DEFAULT_FROM_EMAIL')
  ```

## What Happens When Workflow Runs

1. **Records weekly snapshots** for all profiles (stores current stats vs last week)
2. **Sends per-user emails** showing:
   - Problems solved (delta from last week)
   - Rating changes
   - Contests attended (delta)
3. **Sends admin summary** with global totals
4. **Logs all activities** for audit trail

## Example Email Notification

Users receive:
```
Hello user@example.com,

Here are your changes from the past week:

• LeetCode (username):
  - Problems Solved: +15
  - Rating Change: +25
  - Contests Attended: +1

• Codeforces (cf_username):
  - Problems Solved: +8
  - Rating Change: -12
  - Contests Attended: 0

Keep up the good work!
SkillTracker
```

## Advanced: Slack Notifications

To send Slack notifications when the workflow completes:

```yaml
- name: Slack notification
  if: always()
  uses: slackapi/slack-github-action@v1
  with:
    webhook-url: ${{ secrets.SLACK_WEBHOOK }}
    payload: |
      {
        "text": "Weekly reports ${{ job.status }}",
        "blocks": [
          {
            "type": "section",
            "text": {
              "type": "mrkdwn",
              "text": "*Weekly Reports* ${{ job.status == 'success' && '✅' || '❌' }}\nTime: $(date)"
            }
          }
        ]
      }
```

Then add `SLACK_WEBHOOK` secret with your Slack webhook URL.
