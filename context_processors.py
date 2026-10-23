"""Custom context processors for Django templates."""

from core.nav_module_settings import is_nav_admin
from core.plant_access import (
    ensure_home_plant_session,
    filter_cross_plant_nav_modules,
    get_home_plant_code,
    is_readonly_plant_view,
)
from core.role_module_settings import get_nav_modules_for_user
from core.utils import sync_session_role


def custom_context(request):
    """
    Example custom context processor.
    Returns a dictionary to be added to the template context.
    """
    return {}


def another_context(request):
    """
    Another example context processor.
    """
    return {}


def employee_name(request):
    """
    Adds the employee name from the session to the template context.
    """
    return {"employee_name": request.session.get("employee_name", "")}


def user_role(request):
    """
    Adds the user's role_id from the session to the template context.
    Syncs from MongoDB so admin (role 2) is recognized after assignment.
    """
    return {"role_id": sync_session_role(request)}


def plant_code(request):
    """
    Adds the user's plant code and name from the session to the template context.
    """
    ensure_home_plant_session(request)
    return {
        "plant_code": request.session.get("plant_code", "") or "",
        "plant_name": request.session.get("plant_name", "") or "",
        "home_plant_code": get_home_plant_code(request),
        "home_plant_name": request.session.get("home_plant_name", "") or "",
        "is_home_plant": not is_readonly_plant_view(request),
        "is_readonly_view": is_readonly_plant_view(request),
    }


def show_sidebar(request):
    """
    Hide sidebar on plant picker (/home/). Show elsewhere once a plant is in session.
    """
    path = (request.path or "").rstrip("/")
    is_plant_picker = path == "/home" or path.endswith("/home")
    has_plant = bool((request.session.get("plant_code") or "").strip())
    return {
        "show_sidebar": has_plant and not is_plant_picker,
        "is_plant_picker": is_plant_picker,
    }


def nav_modules(request):
    """Navbar module toggles for current plant, role, and home/cross-plant context."""

    plant_code = request.session.get("plant_code", "") or ""
    role_id = sync_session_role(request)
    cross = is_readonly_plant_view(request)
    enabled = get_nav_modules_for_user(
        plant_code,
        role_id,
        is_cross_plant_view=cross,
    )
    # Belt: never show plant-wide menus on read-only plant switch (non-admin).
    if cross and not is_nav_admin(role_id):
        enabled = filter_cross_plant_nav_modules(enabled)
    return {
        "nav_modules_enabled": enabled,
        "is_nav_admin": is_nav_admin(role_id),
    }
