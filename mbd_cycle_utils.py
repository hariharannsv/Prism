"""MBD collection cycle-time helpers (machine dashboard + OEE tasks)."""

from datetime import datetime

# Machines whose total cycle time = sum of all station cycle times (seconds)
MBD_MULTI_STATION_SUM_MACHINES = frozenset({"KM-BN-01"})

# Values >= this are treated as milliseconds (tag-based MBD).
CYCLE_MS_THRESHOLD = 1000.0
# K-Palayam machine_data / MBD tags store bare numbers as minutes (not seconds).
CYCLE_MINUTES_NUMERIC_PLANTS = frozenset({"041"})
# Bare numbers above this (as minutes) are rejected before ×60 conversion.
CYCLE_MINUTES_NUMERIC_MAX = 180.0


def parse_hhmmss_to_seconds(value):
    """Parse 'HH:MM:SS' / 'MM:SS' → integer seconds."""
    if value is None:
        return 0
    text = str(value).strip()
    if not text:
        return 0
    if ":" not in text:
        return normalize_machine_cycle_seconds(value)
    parts = text.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = (float(p) for p in parts)
            return max(0, int(round(hours * 3600 + minutes * 60 + seconds)))
        if len(parts) == 2:
            minutes, seconds = (float(p) for p in parts)
            return max(0, int(round(minutes * 60 + seconds)))
    except (TypeError, ValueError):
        return 0
    return 0


def normalize_machine_cycle_seconds(value, part_count=0, plant_code=None):
    """
    Convert machine cycle time to seconds (per part).

    - HH:MM:SS / MM:SS strings → seconds (minutes component × 60)
    - Values >= 1000 → milliseconds → seconds
    - K-Palayam (041) bare numbers → minutes → seconds
    - Otherwise → already seconds (e.g. 24, 25) — no conversion
    """
    if value is None:
        return 0
    if isinstance(value, str) and ":" in value:
        return parse_hhmmss_to_seconds(value)

    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0

    if num <= 0:
        return 0

    if num >= CYCLE_MS_THRESHOLD:
        seconds = num / 1000.0
    elif (
        plant_code in CYCLE_MINUTES_NUMERIC_PLANTS and num <= CYCLE_MINUTES_NUMERIC_MAX
    ):
        # K-Palayam: 2.5 → 2.5 min; bare 25 → 25 sec (not 25 min).
        fractional = abs(num - round(num)) > 1e-9
        if fractional or num <= 9:
            seconds = num * 60.0
        else:
            seconds = num
    else:
        seconds = num

    if part_count and part_count > 1:
        seconds = seconds / float(part_count)

    return max(0, int(round(seconds)))


def _coerce_doc_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
    return None


def cycle_window_seconds(doc):
    """Seconds from cycle_start → cycle_stop when both timestamps are valid."""
    if not isinstance(doc, dict):
        return 0
    start = None
    stop = None
    for start_key, stop_key in (
        ("cycle_start_utc", "cycle_Stop_utc"),
        ("cycle_start_utc", "cycle_stop_utc"),
        ("cycle_start", "cycle_stop"),
    ):
        start = _coerce_doc_datetime(doc.get(start_key))
        stop = _coerce_doc_datetime(doc.get(stop_key))
        if start and stop:
            break
    if not start or not stop or stop <= start:
        return 0
    delta = int((stop - start).total_seconds())
    if 1 <= delta <= 3600:
        return delta
    return 0


def shift_scoped_machine_runtime_seconds(doc_runtime, shift_runtime_seconds):
    """
    Use machine_data overall_run_time only when it fits the current shift window.

    Fanuc CNC timers (P-Palayam GR-TU, etc.) store cumulative power-on time
    (100+ hours) — that must not replace today's 8 AM → now elapsed time.
    Per-cycle / cutting timers (under ~5 min) are also rejected.
    """
    if not doc_runtime or doc_runtime <= 0:
        return 0
    if not shift_runtime_seconds or shift_runtime_seconds <= 0:
        return int(doc_runtime)
    if doc_runtime > shift_runtime_seconds * 1.1:
        return 0
    min_valid = max(300.0, shift_runtime_seconds * 0.05)
    if doc_runtime < min_valid:
        return 0
    return int(doc_runtime)


def structured_doc_cycle_seconds(doc, plant_code=None):
    """
    Per-part cycle time from structured Mbd / machine_data document.

    Fanuc feeds store machine_cycle_time as HH:MM:SS (minutes in the middle
    field, e.g. 00:02:25 → 145 s). Prefer that over cycle_start→cycle_stop
    wall-clock window when present.
    """
    if not isinstance(doc, dict):
        return 0

    raw = doc.get("machine_cycle_time")
    if raw not in (None, ""):
        if isinstance(raw, str) and ":" in raw:
            parsed = parse_hhmmss_to_seconds(raw)
            if parsed > 0:
                return parsed
        parsed = normalize_machine_cycle_seconds(
            raw, part_count=0, plant_code=plant_code
        )
        if parsed > 0:
            return parsed

    window = cycle_window_seconds(doc)
    if window > 0:
        return window

    return 0


def mbd_value_to_seconds(value):
    """Convert MBD numeric value (milliseconds) to seconds."""
    try:
        return float(value) / 1000.0 if value else 0.0
    except (TypeError, ValueError):
        return 0.0


def mbd_station_index_from_tag(tag):
    """Parse station number from tag e.g. 'MACHINE CYCLE TIME STATION 2' -> 2."""
    if not tag.startswith("MACHINE CYCLE TIME STATION"):
        return 0
    try:
        return int(tag.rsplit(" ", 1)[-1])
    except (ValueError, IndexError):
        return 0


def mbd_aggregate_cycle_time_seconds(value, part_count=0, plant_code=None):
    """Cycle time from 'MACHINE CYCLE TIME' tag — ms, seconds, or minutes by plant."""
    _ = part_count  # tag value is per-part; do not divide by shift part count
    return normalize_machine_cycle_seconds(value, part_count=0, plant_code=plant_code)


def finalize_mbd_cycle_time(source, data):
    """
    Set data['cycle_time'] (seconds).
    KM-BN-01: sum of station 1+2+3 (ms -> sec each).
    Others: use aggregate tag if present, else average of stations.
    """
    stations = data.get("station_cycle_seconds") or {}

    if source in MBD_MULTI_STATION_SUM_MACHINES and stations:
        data["cycle_time"] = int(round(sum(stations[idx] for idx in sorted(stations))))
        return

    if "cycle_time" in data:
        return

    if stations:
        vals = [stations[idx] for idx in sorted(stations)]
        data["cycle_time"] = int(round(sum(vals) / len(vals)))
    else:
        data["cycle_time"] = 0
