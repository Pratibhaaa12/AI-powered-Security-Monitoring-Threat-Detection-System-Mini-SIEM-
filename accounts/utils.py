import base64
import logging
import uuid
from reward_dashboard.razorpay_constants import RAZORPAY_ATTENDANCE_URL
from django.conf import settings
from cryptography.fernet import Fernet
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
import requests
import json
import logging
from datetime import date, datetime, timedelta
# Initialize logger for the accounts app
logger = logging.getLogger('accounts')

logger = logging.getLogger(__name__)


def get_fernet_key():
    try:
        password = settings.SECRET_KEY.encode()
        salt = b'a'  # Should remain constant and secret
        logger.debug("Starting Fernet key derivation.")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100_000,
            backend=default_backend()
        )
        key = base64.urlsafe_b64encode(kdf.derive(password))
        logger.debug("Fernet key derived successfully.")
        return key
    except Exception as e:
        logger.exception("Failed to derive Fernet key")
        raise


def get_fernet():
    try:
        key = get_fernet_key()
        logger.debug("Creating Fernet object.")
        return Fernet(key)
    except Exception as e:
        logger.exception("Failed to initialize Fernet")
        raise



def get_employee_leave_status(email: str, check_date: date) -> dict:
    """
    Fetch attendance status and check-in time for an employee from Razorpay Payroll API.
    """
    from accounts.models import EmployeeAttendance, UserProfile

    if not email:
        return {"success": False, "error": "Missing email", "data": {}}

    url = RAZORPAY_ATTENDANCE_URL

    payload = {
        "auth": {
            "id": str(settings.RAZORPAYX_API_ID),
            "key": settings.RAZORPAYX_API_KEY
        },
        "request": {
            "type": "attendance",
            "sub-type": "fetch"
        },
        "data": {
            "email": email,
            "employee-type": "employee",
            "date": check_date.isoformat()
        }
    }

    headers = {"Content-Type": "application/json"}

    try:
        json_payload = json.dumps(payload)
        logger.info(f"Sending payload to Razorpay Attendance API: {json_payload}")

        response = requests.post(url, headers=headers, data=json_payload, timeout=20)
        raw_text = response.text.strip()
        logger.info(f"Raw response: {raw_text}")

        response.raise_for_status()

        # ✅ Always parse JSON safely
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from Razorpay: {e}")
            return {"success": False, "error": "Invalid JSON response", "data": {"raw": raw_text}}

        # ✅ Handle Razorpay error structure
        if "error" in data:
            error_msg = data["error"].get("message", "Unknown error") if isinstance(data["error"], dict) else str(data["error"])
            logger.warning(f"Razorpay returned an error: {error_msg}")
            return {
                "success": False,
                "error": error_msg,
                "data": {}
            }

        inner = data.get("data", {})
        status_desc = str(inner.get("status", {}).get("description", "unknown")).lower()
        check_in_time = inner.get("check-in") or inner.get("check_in") or "N/A"
        check_out_time = inner.get("check-out") or inner.get("check_out") or "N/A"
        duration = "N/A"
        try:
            if all(isinstance(t, str) and ":" in t for t in [check_in_time, check_out_time]):
                t1 = datetime.strptime(check_in_time, "%H:%M:%S")
                t2 = datetime.strptime(check_out_time, "%H:%M:%S")
                diff = t2 - t1
                hours, remainder = divmod(diff.seconds, 3600)
                minutes = remainder // 60
                duration = f"{hours}h {minutes}m"
        except Exception as e:
            logger.debug(f"Duration calculation failed for {email}: {e}")

        try:
            # find user by email (via profile)
            profile = UserProfile.objects.filter(user__email=email).first()
            if profile:
                EmployeeAttendance.objects.update_or_create(
                    user=profile.user,
                    date=check_date,
                    defaults={
                        "status": status_desc,
                        "check_in": check_in_time,
                        "check_out": check_out_time,
                        "duration": duration,
                    }
                )
                logger.info(f"Attendance saved for {profile.user.username} ({check_date}) — {duration}")
        except Exception as e:
            logger.error(f"Failed to save attendance for {email}: {e}")

        return {
            "success": True,
            "data": {
                "status": status_desc,
                "check_in": check_in_time,
                "check_out": check_out_time,
                "duration": duration,
            },
        }

    except requests.exceptions.RequestException as e:
        logger.error(f"Razorpay Attendance API request failed: {e}")
        return {"success": False, "error": str(e), "data": {}}

    
def get_employee_attendance_range(email: str, start_date: date, end_date: date):
    """
    Fetch attendance details for an employee between a date range.
    (Calls single-date API multiple times since Razorpay doesn’t support fetch-range natively.)
    """
    results = []
    current_date = start_date

    while current_date <= end_date:
        result = get_employee_leave_status(email, current_date)
        results.append({
            "date": current_date.isoformat(),
            "success": result.get("success"),
            "data": result.get("data", {}),
            "error": result.get("error", "")
        })
        current_date += timedelta(days=1)

    return results
