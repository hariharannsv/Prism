"""Home plant vs active plant — cross-plant read-only viewing."""

from core.nav_module_settings import is_nav_admin
from core.utils import sync_session_role

# Fixed sidebar when viewing a plant other than home (corp admin / viewer set).
CROSS_PLANT_VIEW_MODULE_KEYS = frozenset(
    {
        "dashboard",
        "press",
        "furnace",
        "tool_management",
        "tool_calibration",
        "report",
        "traceability",
    }
)


def set_login_plants(request, plant_code, plant_name=""):
    """Set home + active plant at login."""
    code = (plant_code or "").strip()
    name = (plant_name or "").strip()
    request.session["home_plant_code"] = code
    request.session["plant_code"] = code
    if name:
        request.session["home_plant_name"] = name
        request.session["plant_name"] = name


def ensure_home_plant_session(request):
    """Backfill home plant for sessions created before this feature."""
    if (request.session.get("home_plant_code") or "").strip():
        return
    active = (request.session.get("plant_code") or "").strip()
    if not active:
        return
    request.session["home_plant_code"] = active
    active_name = (request.session.get("plant_name") or "").strip()
    if active_name:
        request.session["home_plant_name"] = active_name


def get_home_plant_code(request):
    """User's assigned plant from login."""
    ensure_home_plant_session(request)
    home = (request.session.get("home_plant_code") or "").strip()
    active = (request.session.get("plant_code") or "").strip()
    return home or active


def is_readonly_plant_view(request):
    """True when viewing a plant other than the user's home plant."""
    role_id = sync_session_role(request) or request.session.get("role_id")
    if is_nav_admin(role_id):
        return False

    ensure_home_plant_session(request)
    home = (request.session.get("home_plant_code") or "").strip()
    active = (request.session.get("plant_code") or "").strip()
    if not home or not active:
        return False
    return home != active


def filter_cross_plant_nav_modules(enabled_map):
    """Keep only corp-admin cross-plant modules; hide settings."""
    return {
        key: bool(enabled_map.get(key)) and key in CROSS_PLANT_VIEW_MODULE_KEYS
        for key in enabled_map
    }
