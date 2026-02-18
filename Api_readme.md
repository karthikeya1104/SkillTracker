# SkillTracker API Documentation

This document outlines the available endpoints, request types, required data, and example responses for the backend API.  The service uses Django REST Framework and communicates via JSON.

---

## Authentication

Currently authentication is cookie‑based. Set the session by calling `/` (home) with an email or use `/subscribe` to register. The server stores `subscriber_email` in the session.

## Endpoints

### `GET /health/`
- **Purpose**: simple uptime/health check
- **Request**: no body
- **Response**:
```json
{ "status": "ok" }
```

SkillTracker API Documentation

This document describes the JSON API offered by the SkillTracker backend (Django REST Framework).

Important notes
- Authentication is session/cookie-based. The frontend should authenticate by POSTing an email on the home endpoint or by subscribing.
- Leaderboard results are cached in Redis for performance; cache keys depend on sort/filter parameters and are invalidated when the backend refreshes data.

Endpoints
---------

GET /health/
- Purpose: simple uptime/health check
- Response: { "status": "ok" }

GET / (home)
- GET: returns current subscriber (if logged in) and their profiles.
- POST: accepts JSON to login with `{ "email": "user@example.com" }` or to request a daily report `{ "send_report": true }`.

POST /subscribe
- Purpose: create a subscriber and optional initial platform profile.
- Request example:
  { "email": "a@b.com", "platform_name": "LeetCode", "username": "foo" }

POST /add_platform_profile
- Purpose: add a platform for the logged-in subscriber.
- Request example: { "platform_name": "Codeforces", "username": "bar" }

GET/PUT/PATCH /update-platform-username/{platform}/{username}/
- GET: return profile
- PUT/PATCH: update username. Body: { "username": "newname" }

POST /logout
- Purpose: clear session

POST /unsubscribe
- Purpose: remove subscriber (session or email-based). Request body may include `email`.

POST /create_or_join_group/
- Purpose: create, join or leave a group. Requires `subscriber_email` in session.
- Request body examples:
  - Create and join: `{ "action": "create_group", "group_name": "team-alpha" }`
  - Join: `{ "action": "join_group", "existing_group_name": "team-alpha" }`
  - Leave: `{ "action": "leave_group" }`

GET /leaderboard
- Purpose: paginated leaderboard with current user's ranking(s).
- Query params: `sort_by` (`rating`|`problems_solved`), `platform`, `group`, `page`.
- Note: results are cached for 30 minutes. Cache is invalidated when data is refreshed.

POST /trigger-leaderboard/ (admin/dev)
- Purpose: force fetching latest data from platform APIs and clear leaderboard cache.

GET /api/fetch-data?leetcode=foo&codeforces=bar
- Purpose: look up stats for arbitrary usernames without subscribing.

POST /api/weekly-update/
- Purpose: scheduled endpoint (GitHub Actions) which:
  1. fetches latest data,
  2. records weekly snapshots,
  3. emails per-subscriber diffs,
  4. sends a global summary email.
- Request: none. Recommended to protect this endpoint with an API token in production.

GET /my-profiles/
- Purpose: list profiles for the logged-in subscriber.

POST /profiles/{id}/refresh/
- Purpose: refresh a specific profile (owner only). Returns the refreshed profile or an error.

Implementation & behavior notes
--------------------------------
- Fetch resilience: platform fetches use retries with exponential backoff. If all retries fail, the system falls back to the previous stored stats instead of overwriting with `N/A`.
- Caching: leaderboard responses are cached using `django-redis`. Cache keys include filter/sort parameters. The `trigger-leaderboard` endpoint clears relevant cache keys after refresh.
- Background/parallelism: fetches run in a ThreadPoolExecutor with a configurable worker cap to avoid overloading third-party APIs.
- Emails: HTML emails are sent using Django's `send_mail` configured via environment variables.
- Weekly scheduler: an example GitHub Actions workflow exists at `.github/workflows/weekly-reports.yml` that posts to `/api/weekly-update/` once per week.

Errors & Edge Cases
-------------------
- Initial profiles with no prior data may return `N/A` for some fields until the first successful fetch.
- If the weekly scheduler is not secured, anyone with the URL can trigger emails — protect it with a token in production.
- Be mindful of rate limits from third-party platforms; the code uses retries and a worker throttle but consider caching fetched responses or using dedicated APIs where available.

References
----------
- See `subscriptions/views.py` and `subscriptions/tasks.py` for exact request/response handling and logging.
