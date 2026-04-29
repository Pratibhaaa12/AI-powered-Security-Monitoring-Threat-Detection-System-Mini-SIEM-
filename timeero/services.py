import logging
import requests
from datetime import datetime
from django.db import transaction
from collections import defaultdict

from .models import TimeeroUser, TimeeroTimesheet, TimeeroCustomField, TimeeroBreak
logger = logging.getLogger(__name__)
TIMEERO_URL = "https://timeero-internal.011bq.app/api/timeero/timesheets/"
def parse_datetime(dt_str):
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except Exception:
        return None

def fetch_timeero(start, end, debug=False):
    if debug:
        logger.debug(f"[DEBUG] fetch_timeero start={start}, end={end}")
    page = 1
    grouped = defaultdict(list)
    while True:
        if debug:
            logger.debug(f"[DEBUG] Fetching page {page}")
        response = requests.get(
            TIMEERO_URL,
            params={"date_range": f"{start},{end}", "page_size": 1000, "page": page},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if debug:
            logger.debug(f"[DEBUG] Received {len(results)} results on page {page}")
        for ts in results:
            user = ts.get("user", {})
            email = (user.get("email") or "").lower()
            if email:
                grouped[email].append(ts)
        if not data.get("next"):
            if debug:
                logger.debug("[DEBUG] No next page. Done.")
            break
        page += 1
    return grouped

def fetch_timeero_and_save(start, end, debug=False):
    if debug:
        logger.debug(f"[DEBUG] fetch_timeero_and_save start={start}, end={end}")
    page = 1
    saved_count = 0
    while True:
        if debug:
            logger.debug(f"[DEBUG] Calling Timeero API page {page}")
        response = requests.get(
            TIMEERO_URL,
            params={"date_range": f"{start},{end}", "page_size": 1000, "page": page},
            timeout=15
        )
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])
        if debug:
            logger.debug(f"[DEBUG] Page {page} returned {len(results)} timesheets")
        if not results:
            if debug:
                logger.debug("[DEBUG] No results on this page. Stopping.")
            break
        with transaction.atomic():
            for ts in results:
                u = ts.get("user", {})
                if debug:
                    logger.debug(f"[DEBUG] Saving user {u.get('email')} (ID: {u.get('timeero_user_id')})")
                user_obj, _ = TimeeroUser.objects.update_or_create(
                    timeero_user_id=u.get("timeero_user_id"),
                    defaults={
                        "first_name": u.get("first_name"),
                        "last_name": u.get("last_name"),
                        "email": u.get("email"),
                        "company_employee_id": u.get("company_employee_id"),
                        "slack_user_id": u.get("slack_user_id"),
                    }
                )
                if debug:
                    logger.debug(f"[DEBUG] Saving timesheet {ts.get('timeero_timesheet_id')}")
                ts_obj, _ = TimeeroTimesheet.objects.update_or_create(
                    timeero_timesheet_id=ts.get("timeero_timesheet_id"),
                    defaults={
                        "user": user_obj,
                        "notes": ts.get("notes"),
                        "job_id": ts.get("job_id"),
                        "job_name": ts.get("job_name"),
                        "clock_in_time": parse_datetime(ts.get("clock_in_time")),
                        "clock_in_address": ts.get("clock_in_address"),
                        "clock_in_latitude": ts.get("clock_in_latitude"),
                        "clock_in_longitude": ts.get("clock_in_longitude"),
                        "clock_out_time": parse_datetime(ts.get("clock_out_time")),
                        "clock_out_latitude": ts.get("clock_out_latitude"),
                        "clock_out_longitude": ts.get("clock_out_longitude"),
                        "created_at": parse_datetime(ts.get("created_at")),
                    }
                )
                TimeeroCustomField.objects.filter(timesheet=ts_obj).delete()
                cf_data = ts.get("custom_fields") or {}
                if debug:
                    logger.debug(f"[DEBUG] Saving {len(cf_data)} custom fields")
                if isinstance(cf_data, dict):
                    cf_objs = [
                        TimeeroCustomField(
                            timesheet=ts_obj,
                            field_key=str(k),
                            field_value=str(v)
                        ) for k, v in cf_data.items()
                    ]
                    TimeeroCustomField.objects.bulk_create(cf_objs)
                TimeeroBreak.objects.filter(timesheet=ts_obj).delete()
                break_objs = []
                if debug:
                    logger.debug(f"[DEBUG] Saving {len(ts.get('breaks') or [])} breaks")
                for b in ts.get("breaks") or []:
                    break_objs.append(
                        TimeeroBreak(
                            timesheet=ts_obj,
                            timeero_break_id=b.get("timeero_break_id"),
                            start=parse_datetime(b.get("start")),
                            end=parse_datetime(b.get("end")),
                            duration_in_minutes=b.get("duration_in_minutes") or 0
                        )
                    )
                if break_objs:
                    TimeeroBreak.objects.bulk_create(break_objs)
                saved_count += 1
                if debug:
                    logger.debug(f"[DEBUG] Saved so far: {saved_count}")
        if not data.get("next"):
            if debug:
                logger.debug("[DEBUG] No next page. Finished saving all data.")
            break
        page += 1
    if debug:
        logger.debug(f"[DEBUG] Total timesheets saved: {saved_count}")
    return saved_count
