# Deployment Checklist

## 1) Prerequisites
- Python 3.11+ installed
- PostgreSQL available
- Required API credentials available (GitHub, Google, OpenAI, Slack, Dynamics)

## 2) Environment Setup
- Copy `.env.example` to `.env`
- Fill all required variables, especially:
  - `SECRET_KEY`, `DB_*`, `OPENAI_API_KEY`
  - `SLACK_BOT_TOKEN`, `SLACK_CHANNEL_ID`
  - OAuth variables if login providers are used

## 3) Install Dependencies
- `pip install -r requirements.txt`

## 4) Database
- Create PostgreSQL database/user
- Run migrations:
  - `python manage.py migrate`
- (Optional) Create admin:
  - `python manage.py createsuperuser`

## 5) Static Files
- Collect static files for production:
  - `python manage.py collectstatic --noinput`

## 6) CRM Module Validation
- Login as staff/admin user
- Open CRM dashboard: `/crm/`
- Save Dynamics config
- Trigger:
  - `Sync Contacts`
  - `Enrich Contacts`
  - `Run Alerts Now`

## 7) Background/Scheduled Jobs
- Configure scheduler (cron/Celery/Task Scheduler) for:
  - `python manage.py run_crm_alerts`
  - existing report/notification commands in `accounts` and `timeero`

## 8) Production Hardening
- `DEBUG=False`
- Restrict `ALLOWED_HOSTS`
- Use HTTPS and secure cookies
- Store secrets in secure secret manager
- Ensure logging and error monitoring are enabled

## 9) Smoke Test
- Open home page and login flow
- Test Excel export/import
- Verify CRM dashboard loads and actions succeed
- Verify Slack alerts are delivered

## 10) Go-Live Signoff
- Business signoff on KPI and Priority Scorecard
- Technical signoff on integrations and alerts
- Deployment timestamp recorded
