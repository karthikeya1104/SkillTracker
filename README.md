# SkillTracker

This repository now functions as a **Django REST API backend** with CORS support and Redis caching for the leaderboard. All HTML templates have been removed; the front‑end should be a separate application (React/Vue/etc.).

## Quick start (development)

1. Create and activate a Python virtual environment.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in values (`DATABASE_URL`, `REDIS_URL`, etc.).
4. `python manage.py makemigrations` (new model for weekly snapshots may be added)
5. `python manage.py migrate`
6. `python manage.py runserver`

## Production notes

- Render deployment uses `Procfile` and environment variables. Set `REDIS_URL` to your Redis add‑on.
- Static asset serving is no longer handled by Django; the frontend must bundle and host its own files. The `whitenoise` dependency and static settings have been removed to simplify the backend.
- The app exposes a `/health/` endpoint for health checks.
- A weekly scheduler (GitHub Actions, cron job, Render cron) can POST to `/api/weekly-update/` to snapshot stats and trigger summary emails.  Use the already‑connected production database; just run `python manage.py migrate` after pulling changes so that the `WeeklySnapshot` table is created.
- CORS origins are controlled via `CORS_ALLOWED_ORIGINS` or `CORS_ORIGIN_ALLOW_ALL`.
- Security flags (HSTS, SSL redirect, cookie security) are enabled when `DEBUG=False`.
Refer to the documentation in `SkillTracker/settings.py` for configuration details.

Operational checklist before deploying

- Ensure `DATABASE_URL`, `REDIS_URL`, `DJANGO_SECRET_KEY`, and email credentials are set as environment variables on the host.
- Run `python manage.py migrate` after deployment to create the `WeeklySnapshot` table.
- Add the GitHub Actions secrets `SKILLTRACKER_API_URL` (your deployed URL) and `SKILLTRACKER_API_SECRET` (optional token) if using the provided workflow.
- Protect `/api/weekly-update/` with a token or IP restriction in production to avoid unauthorized triggers.
- Monitor logs (ERROR/WARN) during the first week after deployment; the app now has extensive logging.

Additional docs
- API reference: `Api_readme.md`
- GitHub Actions workflow (weekly reports): `.github/workflows/weekly-reports.yml`