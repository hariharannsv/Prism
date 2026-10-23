"""Utility functions for logging and other core helpers."""

import re
import traceback
from datetime import datetime

from core.mongo_connection import get_db_connection_machine
from core.utility.log import log_traceability

ADMIN_ROLE_ID = "2"


def normalize_role_id(role_id):
    """Normalize Mongo/session role_id to a string (handles int 2 vs \"2\")."""
    if role_id is None or role_id == "":
        return ""
    try:
        return str(int(role_id))
    except (TypeError, ValueError):
        return str(role_id).strip()


def lookup_role_id(db, raw_value):
    """Resolve role id from numeric value or PMS_role role_name (e.g. Admin → 2)."""
    if raw_value is None or raw_value == "":
        return None

    normalized = normalize_role_id(raw_value)
    if normalized.isdigit():
        return normalized

    name = str(raw_value).strip()
    role_collection = db["PMS_role"]
    role_doc = role_collection.find_one({"role_name": name})
    if not role_doc:
        role_doc = role_collection.find_one(
            {"role_name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}
        )
    if role_doc and role_doc.get("role_id") not in (None, ""):
        return normalize_role_id(role_doc["role_id"])
    return None


def resolve_login_role_id(db, employee_code):
    """Read role from users.employees and PMS_assignrole (assignrole wins)."""
    code = str(employee_code or "").strip()
    if not code:
        return None

    employee_collection = db["users.employees"]
    emp = employee_collection.find_one({"employee_code": code})
    role_id = None

    if emp:
        if emp.get("role_id") not in (None, ""):
            role_id = lookup_role_id(db, emp["role_id"])
        if not role_id and emp.get("role_name"):
            role_id = lookup_role_id(db, emp["role_name"])

    assign = db["PMS_assignrole"].find_one({"employee_code": code})
    if assign and assign.get("role_name"):
        assigned = lookup_role_id(db, assign["role_name"])
        if assigned:
            role_id = assigned

    return role_id


def sync_session_role(request):
    """Refresh session role_id from MongoDB (fixes stale role after assignment)."""
    if "user_id" not in request.session:
        return normalize_role_id(request.session.get("role_id"))

    employee_code = request.session.get("employee_code")
    plant_code = request.session.get("plant_code")
    if not employee_code or not plant_code:
        return normalize_role_id(request.session.get("role_id"))

    try:
        db = get_db_connection_machine(plant_code)
        fresh = resolve_login_role_id(db, employee_code)
        if fresh:
            if fresh != normalize_role_id(request.session.get("role_id")):
                request.session["role_id"] = fresh
            return fresh
    except Exception:
        log_traceability(f"Error syncing session role: {traceback.format_exc()}")

    return normalize_role_id(request.session.get("role_id"))


def log_event(request, level, message, username=None, name=None):
    """
    Logs an event to the PMS_logs collection.
    level: 0=info, 1=success, 2=warning, 3=error
    message: Log message
    username: Optional username
    name: Optional employee name
    """
    plant_code = ""
    if not plant_code:
        plant_code = request.session.get("plant_code")

    if not plant_code:
        # Cannot log without knowing which DB to use
        raise ValueError("Plant code is required for logging PMS")
    db = get_db_connection_machine(plant_code)
    logs_collection = db["PMS_logs"]
    log_entry = {
        "timestamp": datetime.now(),
        "level": level,
        "message": message,
        "ip": get_client_ip(request),
        "username": username or request.session.get("employee_code", "---"),
        "name": name or request.session.get("employee_name", "---"),
    }
    logs_collection.insert_one(log_entry)


def get_client_ip(request):
    """
    Returns the client IP address from the request.
    """
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        ip = x_forwarded_for.split(",")[0]
    else:
        ip = request.META.get("REMOTE_ADDR")
    return ip
