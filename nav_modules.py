"""Registry of top-level navbar modules for Module Settings."""

NAV_MODULES = [
    {"key": "project", "label": "Project", "code": "001", "order": 1},
    {"key": "dashboard", "label": "Dashboard", "code": "002", "order": 2},
    {
        "key": "production_planning",
        "label": "Production Planning",
        "code": "003",
        "order": 3,
    },
    {"key": "downtime", "label": "Downtime", "code": "004", "order": 4},
    {
        "key": "settings",
        "label": "Settings",
        "code": "005",
        "order": 5,
        "no_toggle": True,
    },
    {"key": "machine", "label": "Machine", "code": "006", "order": 6},
    {"key": "cell_master", "label": "Cell Master", "code": "007", "order": 7},
    {"key": "costing", "label": "Costing", "code": "008", "order": 8},
    {"key": "tool_management", "label": "Tool Management", "code": "009", "order": 9},
    {
        "key": "tool_calibration",
        "label": "Tool Calibration",
        "code": "010",
        "order": 10,
    },
    {"key": "report", "label": "Report", "code": "011", "order": 11},
    {"key": "quality", "label": "Quality", "code": "012", "order": 12},
    {"key": "traceability", "label": "Traceability", "code": "013", "order": 13},
    {"key": "packing", "label": "Packing", "code": "014", "order": 14},
    {"key": "final_assembly", "label": "Final Assembly", "code": "015", "order": 15},
    {"key": "furnace", "label": "Furnace", "code": "016", "order": 16},
    {"key": "email", "label": "Email", "code": "017", "order": 17},
    {"key": "press", "label": "press", "code": "018", "order": 18},
    {"key": "line_master", "label": "Line Master", "code": "019", "order": 19},
    {
        "key": "line_monitoring",
        "label": "Line Monitoring",
        "code": "020",
        "order": 20,
    },
]

NAV_MODULE_KEYS = {m["key"] for m in NAV_MODULES}

LEGACY_COSTING_KEYS = ("machine_costing", "part_costing")
LEGACY_SETTINGS_KEYS = ("role", "device_status")

COSTING_MODULE = {
    "key": "costing",
    "label": "Costing",
    "code": "008",
    "order": 8,
}

SETTINGS_MODULE = {
    "key": "settings",
    "label": "Settings",
    "code": "005",
    "order": 5,
    "no_toggle": True,
}

LEGACY_MODULE_KEYS = LEGACY_COSTING_KEYS + LEGACY_SETTINGS_KEYS


def get_nav_modules():
    """Registry for UI/DB — merges legacy costing/settings keys into single modules."""
    mods = []
    has_costing = False
    has_settings = False
    for mod in NAV_MODULES:
        if mod["key"] in LEGACY_MODULE_KEYS:
            continue
        if mod["key"] == "costing":
            has_costing = True
        if mod["key"] == "settings":
            has_settings = True
        mods.append(mod)
    if not has_costing:
        mods.append(dict(COSTING_MODULE))
    if not has_settings:
        mods.append(dict(SETTINGS_MODULE))
    return sorted(mods, key=lambda m: m["order"])


COLLAPSE_ID_TO_KEY = {
    "menu-project": "project",
    "menu-dashboard": "dashboard",
    "menu-plan": "production_planning",
    "menu-planning": "downtime",
    "menu-settings": "settings",
    "menu-settings-role": "settings",
    "menu-machine": "machine",
    "menu-cell": "cell_master",
    "menu-costing": "costing",
    "menu-cost-machine": "costing",
    "menu-part-cost": "costing",
    "menu-tool": "tool_management",
    "tool_calib": "tool_calibration",
    "menu-oee": "report",
    "menu-quality": "quality",
    "menu-trace": "traceability",
    "menu-packing": "packing",
    "menu-finalass": "final_assembly",
    "menu-furnace": "furnace",
    "menu-press": "press",
}

URL_NAME_TO_KEY = {
    "devices": "settings",
    "email": "email",
    "createrole": "settings",
    "assignrole": "settings",
    "module_settings": "settings",
    "role_module_settings": "settings",
    "shift_settings": "settings",
    "shift_settings_list": "settings",
    "shift_settings_save": "settings",
    "shift_settings_delete": "settings",
    "add_cost": "costing",
    "machinecostreport": "costing",
    "addpartcost": "costing",
    "partcostreport": "costing",
    "line_master": "line_master",
    "line_master_list": "line_master",
    "line_master_save": "line_master",
    "line_master_update": "line_master",
    "line_master_delete": "line_master",
    "line_monitoring": "line_monitoring",
    "line_monitoring_project_select": "line_monitoring",
}
