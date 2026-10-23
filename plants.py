"""Plant codes and display names for session / module settings."""

PLANT_NAMES = {
    "041": "Kpalayam - Bosch Plant",
    "002": "COE Plant",
    "034": "Pongalur Plant",
    "003": "Annur Plant",
    "143": "Vallam Plant",
    "123": "P-Palayam Plant",
}


def get_plant_display(plant_code, session_name=""):
    """Get Plant Display."""
    code = (plant_code or "").strip()
    name = (session_name or "").strip()
    if name:
        return name
    if code in PLANT_NAMES:
        return PLANT_NAMES[code]
    return f"Plant {code}" if code else ""
