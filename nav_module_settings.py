"""Load and update navbar module enable flags in MongoDB."""

from django.core.cache import cache

from core.mongo_connection import get_db_connection_machine
from core.nav_modules import (
    COSTING_MODULE,
    LEGACY_COSTING_KEYS,
    LEGACY_MODULE_KEYS,
    LEGACY_SETTINGS_KEYS,
    NAV_MODULE_KEYS,
    SETTINGS_MODULE,
    get_nav_modules,
)
from core.utils import normalize_role_id

COLLECTION = "PMS_nav_module_settings"
CACHE_KEY_PREFIX = "nav_modules_enabled:"
CACHE_SECONDS = 60
ADMIN_ROLE_ID = "2"


def _cache_key(plant_code):
    """Cache Key."""
    return f"{CACHE_KEY_PREFIX}{plant_code}"


def invalidate_plant_cache(plant_code):
    """Clear cached navbar module map when user switches plant."""
    code = (plant_code or "").strip()
    if code:
        cache.delete(_cache_key(code))


def _costing_module():
    """Costing Module."""
    return next(
        (m for m in get_nav_modules() if m["key"] == "costing"),
        dict(COSTING_MODULE),
    )


def _settings_module():
    """Settings Module."""
    return next(
        (m for m in get_nav_modules() if m["key"] == "settings"),
        dict(SETTINGS_MODULE),
    )


def _base_enabled_map():
    """Base Enabled Map."""
    enabled = {m["key"]: True for m in get_nav_modules()}
    enabled["settings"] = True
    return enabled


def _merge_legacy_keys(col, enabled, legacy_keys, target_key, module_def):
    """Merge Legacy Keys."""
    legacy_rows = list(
        col.find(
            {"module_key": {"$in": list(legacy_keys)}},
            {"_id": 0, "module_key": 1, "enabled": 1},
        )
    )
    if legacy_rows:
        merged_enabled = any(bool(r.get("enabled", True)) for r in legacy_rows)
        target_row = col.find_one({"module_key": target_key}, {"_id": 0, "enabled": 1})
        if target_row is None:
            col.update_one(
                {"module_key": target_key},
                {
                    "$set": {
                        "module_key": target_key,
                        "label": module_def["label"],
                        "code": module_def["code"],
                        "sort_order": module_def["order"],
                        "enabled": merged_enabled,
                    }
                },
                upsert=True,
            )
            enabled[target_key] = merged_enabled
        else:
            enabled[target_key] = bool(target_row.get("enabled", True))
        col.delete_many({"module_key": {"$in": list(legacy_keys)}})

    col.update_one(
        {"module_key": target_key},
        {
            "$set": {
                "label": module_def["label"],
                "code": module_def["code"],
                "sort_order": module_def["order"],
            },
            "$setOnInsert": {
                "module_key": target_key,
                "enabled": True,
            },
        },
        upsert=True,
    )
    target_row = col.find_one({"module_key": target_key}, {"_id": 0, "enabled": 1})
    if target_row is not None:
        enabled[target_key] = bool(target_row.get("enabled", True))
    else:
        enabled.setdefault(target_key, True)
    return enabled


def ensure_nav_modules_seeded(db):
    """Ensure Nav Modules Seeded."""
    col = db[COLLECTION]
    for mod in get_nav_modules():
        col.update_one(
            {"module_key": mod["key"]},
            {
                "$setOnInsert": {
                    "module_key": mod["key"],
                    "label": mod["label"],
                    "code": mod["code"],
                    "sort_order": mod["order"],
                    "enabled": True,
                }
            },
            upsert=True,
        )
    _merge_legacy_keys(
        col, _base_enabled_map(), LEGACY_COSTING_KEYS, "costing", _costing_module()
    )
    _merge_legacy_keys(
        col, _base_enabled_map(), LEGACY_SETTINGS_KEYS, "settings", _settings_module()
    )


def migrate_legacy_modules(col, enabled):
    """Migrate Legacy Modules."""
    enabled = _merge_legacy_keys(
        col, enabled, LEGACY_COSTING_KEYS, "costing", _costing_module()
    )
    enabled = _merge_legacy_keys(
        col, enabled, LEGACY_SETTINGS_KEYS, "settings", _settings_module()
    )
    return enabled


def get_enabled_map(plant_code):
    """Return {module_key: bool} for all registered modules."""
    if not plant_code:
        return _base_enabled_map()

    cache_key = _cache_key(plant_code)
    cached = cache.get(cache_key)
    if cached is not None and "costing" in cached and "settings" in cached:
        cached = dict(cached)
        cached["settings"] = True
        return cached

    db = get_db_connection_machine(plant_code)
    ensure_nav_modules_seeded(db)
    col = db[COLLECTION]
    rows = list(col.find({}, {"_id": 0, "module_key": 1, "enabled": 1}))
    enabled = _base_enabled_map()
    for row in rows:
        key = row.get("module_key")
        if key in LEGACY_MODULE_KEYS:
            continue
        if key in enabled:
            enabled[key] = bool(row.get("enabled", True))

    enabled = migrate_legacy_modules(col, enabled)
    enabled["settings"] = True

    cache.set(cache_key, enabled, CACHE_SECONDS)
    return enabled


def list_modules_for_admin(plant_code):
    """Full module list for settings page cards."""
    db = get_db_connection_machine(plant_code)
    ensure_nav_modules_seeded(db)
    col = db[COLLECTION]
    migrate_legacy_modules(col, _base_enabled_map())
    rows = {r["module_key"]: r for r in col.find({}, {"_id": 0})}
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
    out.sort(key=lambda x: x["sort_order"])
    return out


def _normalize_module_key(module_key):
    """Normalize Module Key."""
    if module_key in LEGACY_COSTING_KEYS:
        return "costing"
    if module_key in LEGACY_SETTINGS_KEYS:
        return "settings"
    return module_key


def set_module_enabled(plant_code, module_key, enabled, employee_code=""):
    """Set Module Enabled."""
    module_key = _normalize_module_key(module_key)
    if module_key == "settings":
        return False
    allowed = NAV_MODULE_KEYS | {"costing", "settings"}
    if module_key not in allowed:
        return False
    db = get_db_connection_machine(plant_code)
    ensure_nav_modules_seeded(db)
    col = db[COLLECTION]
    mod = next((m for m in get_nav_modules() if m["key"] == module_key), None)
    if not mod and module_key == "costing":
        mod = _costing_module()
    if not mod and module_key == "settings":
        mod = _settings_module()
    if not mod:
        return False
    col.update_one(
        {"module_key": module_key},
        {
            "$set": {
                "enabled": bool(enabled),
                "updated_by": employee_code or "",
            },
            "$setOnInsert": {
                "label": mod["label"],
                "code": mod["code"],
                "sort_order": mod["order"],
            },
        },
        upsert=True,
    )
    cache.delete(_cache_key(plant_code))
    return True


def is_nav_admin(role_id):
    """Is Nav Admin."""
    return normalize_role_id(role_id) == ADMIN_ROLE_ID


def module_visible(enabled_map, role_id, module_key, role_enabled_map=None):
    """Return True if module should appear in navbar for this plant/user."""
    module_key = _normalize_module_key(module_key)
    normalized_role = normalize_role_id(role_id)
    if module_key == "settings":
        return is_nav_admin(normalized_role)
    plant_ok = enabled_map.get(module_key, True)
    if role_enabled_map is None:
        return plant_ok
    role_ok = role_enabled_map.get(module_key, True)
    return plant_ok and role_ok
