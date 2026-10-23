"""Load and update navbar module enable flags per role in MongoDB."""

from django.core.cache import cache

from core.mongo_connection import get_db_connection_machine
from core.nav_module_settings import (
    _base_enabled_map,
    _costing_module,
    _normalize_module_key,
    _settings_module,
    get_enabled_map,
    is_nav_admin,
    module_visible,
)
from core.nav_modules import NAV_MODULE_KEYS, get_nav_modules
from core.plant_access import CROSS_PLANT_VIEW_MODULE_KEYS
from core.utils import normalize_role_id

COLLECTION = "PMS_nav_role_module_settings"
CACHE_KEY_PREFIX = "nav_role_modules_enabled:"


def _cache_key(plant_code, role_id):
    """Build cache key for plant + role module map."""
    plant = (plant_code or "").strip()
    role = normalize_role_id(role_id) or "0"
    return f"{CACHE_KEY_PREFIX}{plant}:{role}"


def invalidate_role_cache(plant_code, role_id):
    """Clear cached role module map after a toggle."""
    code = (plant_code or "").strip()
    role = normalize_role_id(role_id)
    if code and role:
        cache.delete(_cache_key(code, role))


def list_roles_for_admin(plant_code):
    """Return role options for the settings page dropdown."""
    db = get_db_connection_machine(plant_code)
    roles = list(
        db["PMS_role"]
        .find({}, {"_id": 0, "role_id": 1, "role_name": 1})
        .sort("role_id", 1)
    )
    out = []
    for row in roles:
        role_id = normalize_role_id(row.get("role_id"))
        if not role_id:
            continue
        out.append(
            {
                "role_id": role_id,
                "role_name": row.get("role_name") or f"Role {role_id}",
            }
        )
    return out


def _resolve_role_id(plant_code, role_id):
    """Validate role_id exists in PMS_role."""
    normalized = normalize_role_id(role_id)
    if not normalized:
        return None
    db = get_db_connection_machine(plant_code)
    role = None
    if normalized.isdigit():
        role = db["PMS_role"].find_one(
            {"role_id": int(normalized)}, {"_id": 0, "role_id": 1}
        )
    if not role:
        role = db["PMS_role"].find_one(
            {"role_id": normalized}, {"_id": 0, "role_id": 1}
        )
    if not role:
        return None
    return normalize_role_id(role.get("role_id"))


def ensure_role_modules_seeded(db, role_id):
    """Ensure default enabled rows exist for each module and role."""
    col = db[COLLECTION]
    normalized_role = normalize_role_id(role_id)
    for mod in get_nav_modules():
        if mod.get("no_toggle"):
            continue
        col.update_one(
            {"role_id": normalized_role, "module_key": mod["key"]},
            {
                "$setOnInsert": {
                    "role_id": normalized_role,
                    "module_key": mod["key"],
                    "label": mod["label"],
                    "code": mod["code"],
                    "sort_order": mod["order"],
                    "enabled": True,
                }
            },
            upsert=True,
        )


def get_role_enabled_map(plant_code, role_id):
    """Return {module_key: bool} for a role within a plant DB."""
    if not plant_code:
        return _base_enabled_map()

    normalized_role = normalize_role_id(role_id)
    if not normalized_role:
        return _base_enabled_map()

    cache_key = _cache_key(plant_code, normalized_role)
    cached = cache.get(cache_key)
    if cached is not None and "costing" in cached and "settings" in cached:
        cached = dict(cached)
        cached["settings"] = True
        return cached

    db = get_db_connection_machine(plant_code)
    ensure_role_modules_seeded(db, normalized_role)
    col = db[COLLECTION]
    rows = list(
        col.find(
            {"role_id": normalized_role},
            {"_id": 0, "module_key": 1, "enabled": 1},
        )
    )
    enabled = _base_enabled_map()
    for row in rows:
        key = row.get("module_key")
        if key in enabled:
            enabled[key] = bool(row.get("enabled", True))

    enabled["settings"] = True
    cache.set(cache_key, enabled, 60)
    return enabled


def list_modules_for_role_admin(plant_code, role_id):
    """Full module list for role settings cards."""
    normalized_role = _resolve_role_id(plant_code, role_id)
    if not normalized_role:
        return []

    db = get_db_connection_machine(plant_code)
    ensure_role_modules_seeded(db, normalized_role)
    col = db[COLLECTION]
    rows = {
        r["module_key"]: r for r in col.find({"role_id": normalized_role}, {"_id": 0})
    }
    out = []
    for mod in get_nav_modules():
        if mod.get("no_toggle"):
            continue
        row = rows.get(mod["key"], {})
        out.append(
            {
                "key": mod["key"],
                "label": mod.get("label") or row.get("label") or mod["label"],
                "code": mod.get("code") or row.get("code") or "",
                "enabled": bool(row.get("enabled", True)),
                "sort_order": mod["order"],
            }
        )
    out.sort(key=lambda item: item["sort_order"])
    return out


def set_role_module_enabled(plant_code, role_id, module_key, enabled, employee_code=""):
    """Persist a role module toggle."""
    normalized_role = _resolve_role_id(plant_code, role_id)
    if not normalized_role:
        return False

    module_key = _normalize_module_key(module_key)
    if module_key == "settings":
        return False

    allowed = NAV_MODULE_KEYS | {"costing", "settings"}
    if module_key not in allowed:
        return False

    db = get_db_connection_machine(plant_code)
    ensure_role_modules_seeded(db, normalized_role)
    col = db[COLLECTION]
    mod = next((item for item in get_nav_modules() if item["key"] == module_key), None)
    if not mod and module_key == "costing":
        mod = _costing_module()
    if not mod and module_key == "settings":
        mod = _settings_module()
    if not mod:
        return False

    col.update_one(
        {"role_id": normalized_role, "module_key": module_key},
        {
            "$set": {
                "enabled": bool(enabled),
                "updated_by": employee_code or "",
            },
            "$setOnInsert": {
                "role_id": normalized_role,
                "label": mod["label"],
                "code": mod["code"],
                "sort_order": mod["order"],
            },
        },
        upsert=True,
    )
    invalidate_role_cache(plant_code, normalized_role)
    return True


def get_effective_enabled_map(plant_code, role_id):
    """Merge plant and role toggles into one navbar visibility map."""
    plant_enabled = get_enabled_map(plant_code)
    role_enabled = get_role_enabled_map(plant_code, role_id)
    keys = set(plant_enabled.keys()) | set(role_enabled.keys())
    return {
        key: module_visible(plant_enabled, role_id, key, role_enabled) for key in keys
    }


def get_nav_modules_for_user(plant_code, role_id, *, is_cross_plant_view=False):
    """
    Navbar visibility for current session context.

    Admin: all modules at every plant (incl. settings).
    Home plant: plant toggles AND role toggles.
    Other plant (read-only): fixed corp-admin modules only (no settings).
    """
    if is_nav_admin(role_id):
        enabled = _base_enabled_map()
        enabled["settings"] = True
        return enabled

    if is_cross_plant_view:
        # Fixed corp-admin set only — ignore other plant's module toggles.
        enabled = {key: False for key in _base_enabled_map()}
        for key in CROSS_PLANT_VIEW_MODULE_KEYS:
            enabled[key] = True
        enabled["settings"] = False
        return enabled

    return get_effective_enabled_map(plant_code, role_id)
