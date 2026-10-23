"""OEE report machine-source split: Fanuc mtlink_data vs MBD / Fanuc FOCAS live calc."""

from collections import defaultdict
from datetime import date, datetime
from datetime import time as dtime
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from core.mbd_cycle_utils import (
    normalize_machine_cycle_seconds,
    parse_hhmmss_to_seconds,
    shift_scoped_machine_runtime_seconds,
    structured_doc_cycle_seconds,
)
from core.mongo_connection import get_db_connection_machine
from core.utility.log import log_traceability
from core.utility.shift_utils import (
    get_report_day_window,
    get_shift_windows_for_report_date,
)
from core.views.machinedashboard import (
    _clamp_oee_pct,
    _count_counter_values,
    get_daily_planned_qty_map,
    get_unplanned_downtime_map,
)

IST = ZoneInfo("Asia/Kolkata")
FANUC_CATEGORIES = {"fanuc"}
LIVE_CATEGORIES = {"mbd", "fanuc_focas"}
MTLINK_PART_COUNTER_SOURCE = "PartCounter"
MTLINK_PARTS_NUM_SOURCE = "PartsNum_path1"


def _norm_category(raw):
    text = str(raw or "").strip().lower().replace("-", "_")
    return "_".join(text.split())


def iter_report_dates(start_date, end_date):
    """Yield each calendar date from start_date through end_date inclusive."""
    if isinstance(start_date, str):
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        start_d = start_date
    if isinstance(end_date, str):
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        end_d = end_date
    current = start_d
    while current <= end_d:
        yield current
        current += timedelta(days=1)


def report_day_window_utc(report_date, plant_code):
    """Report calendar date D → UTC bounds + IST bounds + duration (from shift_utils)."""
    start_ist, end_ist, duration_minutes = get_report_day_window(
        report_date, plant_code
    )
    return (
        start_ist.astimezone(timezone.utc),
        end_ist.astimezone(timezone.utc),
        start_ist,
        end_ist,
        duration_minutes,
    )


def _identity_values(doc):
    """machine_name / machine_id / serial_number used to match a filter."""
    values = []
    for field in ("machine_name", "machine_id", "serial_number"):
        raw = doc.get(field)
        if raw is None or raw == "":
            continue
        text = str(raw).strip()
        if text:
            values.append(text)
    return values


def _matches_selected(doc, selected):
    if not selected:
        return True
    if isinstance(selected, set):
        wanted = selected
    else:
        wanted = {selected.strip().upper()}
    return any(val.upper() in wanted for val in _identity_values(doc))


def machines_by_oee_source(db, selected_machine=None):
    """
    Split PMS_machine_info by category:
    FANUC → mtlink_data
    any other (mbd, fanuc_focas, blank) → Mbd + machine_data
    """
    if isinstance(selected_machine, (list, tuple, set)):
        selected = {
            str(machine).strip().upper()
            for machine in selected_machine
            if str(machine).strip()
        }
    else:
        selected = str(selected_machine or "").strip().upper()
    by_name = {}

    for doc in db["PMS_machine_info"].find(
        {},
        {
            "machine_name": 1,
            "machine_id": 1,
            "serial_number": 1,
            "category": 1,
            "_id": 0,
        },
    ):
        name = str(doc.get("machine_name") or "").strip()
        if not name:
            continue
        if not _matches_selected(doc, selected):
            continue
        category = _norm_category(doc.get("category"))
        if category in FANUC_CATEGORIES:
            by_name[name] = "fanuc"
        else:
            by_name[name] = "live"

    fanuc_machines = sorted(name for name, src in by_name.items() if src == "fanuc")
    live_machines = sorted(name for name, src in by_name.items() if src == "live")

    return fanuc_machines, live_machines


def apply_fanuc_machine_filter(query, fanuc_machines, selected_machine=None):
    """Restrict stored-collection query to Fanuc machines only (canonical names)."""
    if not fanuc_machines:
        return False
    if (selected_machine or "").strip():
        query["machine"] = (
            fanuc_machines[0]
            if len(fanuc_machines) == 1
            else {"$in": list(fanuc_machines)}
        )
        return True
    query["machine"] = {"$in": list(fanuc_machines)}
    return True


def _machine_query_clause(machines):
    """Push machine names into Mongo (machine_name or source)."""
    names = []
    seen = set()
    for machine in machines or []:
        text = str(machine or "").strip()
        if not text:
            continue
        for variant in (text, text.upper(), text.lower()):
            if variant not in seen:
                seen.add(variant)
                names.append(variant)
    if not names:
        return None
    return {
        "$or": [
            {"machine_name": {"$in": names}},
            {"source": {"$in": names}},
        ]
    }


def _collection(db, wanted):
    for name in db.list_collection_names():
        if name.lower() == wanted.lower():
            return db[name]
    return None


def _timestamp_match(start_ist, end_ist):
    """Match timestamp stored as aware/naive datetime or IST wall-clock string."""
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)
    start_naive_ist = start_ist.replace(tzinfo=None)
    end_naive_ist = end_ist.replace(tzinfo=None)
    start_naive_utc = start_utc.replace(tzinfo=None)
    end_naive_utc = end_utc.replace(tzinfo=None)
    start_str = start_naive_ist.strftime("%Y-%m-%d %H:%M:%S")
    end_str = end_naive_ist.strftime("%Y-%m-%d %H:%M:%S")
    bounds = []
    for field in ("timestamp", "cycle_stop", "cycle_start"):
        bounds.extend(
            [
                {field: {"$gte": start_ist, "$lt": end_ist}},
                {field: {"$gte": start_naive_ist, "$lt": end_naive_ist}},
                {field: {"$gte": start_utc, "$lt": end_utc}},
                {field: {"$gte": start_naive_utc, "$lt": end_naive_utc}},
                {field: {"$gte": start_str, "$lt": end_str}},
            ]
        )
    return {"$or": bounds}


PART_COUNT_TAGS = ("TOTAL PART COUNT", "OK PART COUNT", "PART COUNT")


def _cycle_doc_filter():
    """Rows that represent production events (each row = 1 count)."""
    no_tag = {"$or": [{"tag": {"$exists": False}}, {"tag": None}, {"tag": ""}]}
    return {
        "$or": [
            {"tag": {"$in": list(PART_COUNT_TAGS)}},
            {
                "$and": [
                    no_tag,
                    {
                        "$or": [
                            {"cycle_start": {"$exists": True, "$nin": [None, ""]}},
                            {"cycle_stop": {"$exists": True, "$nin": [None, ""]}},
                            {
                                "machine_cycle_time": {
                                    "$exists": True,
                                    "$nin": [None, ""],
                                }
                            },
                            {"total_part_count": {"$exists": True, "$ne": None}},
                            {"value": {"$exists": True, "$ne": None}},
                        ]
                    },
                ]
            },
        ]
    }


def _is_single_count_doc(doc):
    """True when this document is one production count (ignore numeric magnitude)."""
    tag = str(doc.get("tag") or "").strip().upper()
    if tag:
        return tag in PART_COUNT_TAGS or "PART COUNT" in tag
    if any(
        doc.get(key) not in (None, "")
        for key in ("cycle_start", "cycle_stop", "machine_cycle_time")
    ):
        return True
    if doc.get("total_part_count") not in (None, ""):
        return True
    if doc.get("value") not in (None, ""):
        return True
    return False


def _canon_name(raw, allow_names, alias_to_canon):
    key = str(raw or "").strip()
    if not key:
        return ""
    if key in allow_names:
        return key
    return alias_to_canon.get(key) or alias_to_canon.get(key.upper()) or key


def _parse_numeric_value(raw):
    """Parse tag value to int; return None on failure."""
    if raw is None:
        return None
    try:
        if isinstance(raw, (int, float)):
            return int(raw)
        s = str(raw).strip()
        if not s:
            return None
        return int(float(s)) if "." in s else int(s)
    except (ValueError, TypeError):
        return None


def _sum_actual_counts(rows):
    """Sum actual production counts from report rows."""
    total = 0
    for row in rows:
        actual = (
            row.get("actual") or row.get("total_count") or row.get("total_parts") or 0
        )
        total += _parse_numeric_value(actual) or 0
    return total


def _parse_mtlink_datetime(value):
    """Parse mtlink_data start/end (IST string, UTC Z, or datetime)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _mtlink_start_in_window_match(machines, start_ist, end_ist):
    """Strict mtlink_data pre-filter: event start within [start_ist, end_ist)."""
    start_utc = start_ist.astimezone(timezone.utc)
    end_utc = end_ist.astimezone(timezone.utc)
    start_iso_ist = start_ist.isoformat()
    end_iso_ist = end_ist.isoformat()
    start_iso_utc = start_utc.isoformat().replace("+00:00", "Z")
    end_iso_utc = end_utc.isoformat().replace("+00:00", "Z")
    start_naive_ist = start_ist.replace(tzinfo=None)
    end_naive_ist = end_ist.replace(tzinfo=None)
    return {
        "machine": {"$in": list(machines)},
        "$or": [
            {"start": {"$gte": start_iso_ist, "$lt": end_iso_ist}},
            {"start": {"$gte": start_ist, "$lt": end_ist}},
            {"start": {"$gte": start_iso_utc, "$lt": end_iso_utc}},
            {"start": {"$gte": start_naive_ist, "$lt": end_naive_ist}},
        ],
    }


def _mtlink_start_key(start_local):
    """Dedupe key for one physical part event."""
    return start_local.replace(microsecond=0).isoformat()


def _mtlink_event_cycle_seconds(doc):
    """
    Per-part cycle seconds from mtlink_data start → end (machinedashboard API logic).
    """
    start_dt = _parse_mtlink_datetime(doc.get("start"))
    end_dt = _parse_mtlink_datetime(doc.get("end"))
    if not start_dt or not end_dt or end_dt <= start_dt:
        return 0
    delta = (end_dt - start_dt).total_seconds()
    if 1 <= delta <= 3600:
        return int(round(delta))
    return 0


def _parse_cell_cycle_seconds(raw, plant_code=None):
    """PMS_oee_cell cycleTime → seconds (HH:MM:SS, MM:SS, or minutes by plant rules)."""
    if raw is None or raw == "":
        return 0
    text = str(raw).strip()
    if ":" in text:
        return parse_hhmmss_to_seconds(text)
    return normalize_machine_cycle_seconds(raw, part_count=0, plant_code=plant_code)


MBD_CYCLE_TAGS = (
    "MACHINE CYCLE TIME",
    "MACHINE CYCLE TIME STATION 1",
    "MACHINE CYCLE TIME STATION 2",
    "MACHINE CYCLE TIME STATION 3",
)
MBD_DURATION_TAGS = (
    "OVER ALL RUN TIME",
    "MACHINE DOWN TIME",
    "MACHINE DOWN TIME STATION 1",
    "MACHINE DOWN TIME STATION 2",
    "MACHINE DOWN TIME STATION 3",
)


def _machine_data_cycle_seconds(doc):
    """Elapsed seconds for one valid cycle_start → cycle_stop window."""
    start = _parse_window_timestamp(doc.get("cycle_start_utc"))
    stop = _parse_window_timestamp(doc.get("cycle_stop_utc"))
    if start is None:
        start = _as_ist_wall(doc.get("cycle_start"))
    if stop is None:
        stop = _as_ist_wall(doc.get("cycle_stop"))
    if start is None or stop is None or stop <= start:
        return 0
    duration = int((stop - start).total_seconds())
    return duration if 1 <= duration <= 3600 else 0


def _machine_data_duration_seconds(value):
    """Parse machine_data runtime/downtime values, stored in seconds."""
    if isinstance(value, str) and ":" in value:
        return parse_hhmmss_to_seconds(value)
    try:
        return max(0, int(round(float(value or 0))))
    except (TypeError, ValueError):
        return 0


def _mbd_duration_stats(tag_values, machine):
    """Convert MBD run/downtime tags (stored in milliseconds) to seconds."""
    from core.mbd_cycle_utils import mbd_value_to_seconds

    values = tag_values.get(machine, {})
    run_raw = values.get("OVER ALL RUN TIME")
    runtime = mbd_value_to_seconds(run_raw) if run_raw not in (None, "") else None

    station_down = [
        mbd_value_to_seconds(values[tag])
        for tag in MBD_DURATION_TAGS[2:]
        if values.get(tag) not in (None, "")
    ]
    down_raw = values.get("MACHINE DOWN TIME")
    downtime = (
        sum(station_down)
        if station_down
        else mbd_value_to_seconds(down_raw) if down_raw not in (None, "") else None
    )
    return (
        max(0, int(round(runtime))) if runtime is not None else None,
        max(0, int(round(downtime))) if downtime is not None else None,
    )


def _iter_report_shift_windows(start_date, end_date, plant_code, selected_shift=None):
    """
    Yield (date_label, shift, start_ist, end_ist, start_utc, end_utc, total_minutes).

    date_label is the calendar date the shift starts (Shift 3 on 20 Aug 01:20 → 2026-08-20).
    Production-day iteration starts one day earlier so early-morning Shift 3 is included.
    """
    if isinstance(start_date, str):
        range_start = datetime.strptime(start_date, "%Y-%m-%d").date()
    else:
        range_start = start_date
    if isinstance(end_date, str):
        range_end = datetime.strptime(end_date, "%Y-%m-%d").date()
    else:
        range_end = end_date

    report_start = range_start - timedelta(days=1)
    for report_day in iter_report_dates(report_start, range_end):
        for shift_name, start_ist, end_ist in get_shift_windows_for_report_date(
            report_day, plant_code
        ):
            if selected_shift and shift_name != selected_shift:
                continue
            # Use the production/report day as the date label — Shift 3 (01:00–08:00
            # calendar-next-day) still belongs to the report_day it was configured for.
            if report_day < range_start or report_day > range_end:
                continue
            date_label = report_day.strftime("%Y-%m-%d")
            start_utc = start_ist.astimezone(timezone.utc)
            end_utc = end_ist.astimezone(timezone.utc)
            total_minutes = int((end_ist - start_ist).total_seconds() // 60)
            yield (
                date_label,
                shift_name,
                start_ist,
                end_ist,
                start_utc,
                end_utc,
                total_minutes,
            )


def _mtlink_event_overlaps_window(doc, start_ist, end_ist):
    """True if the mtlink event belongs in this shift (start-in-window or overlap)."""
    start_dt = _parse_mtlink_datetime(doc.get("start"))
    if start_dt is None:
        return False
    start_local = start_dt.astimezone(IST)
    end_dt = _parse_mtlink_datetime(doc.get("end"))
    if end_dt is not None:
        end_local = end_dt.astimezone(IST)
        return start_local < end_ist and end_local >= start_ist
    return start_ist <= start_local < end_ist


def _fetch_mtlink_docs_for_range(db, machines, range_start_ist, range_end_ist):
    """Single mtlink_data query for a report date range (includes overlap lead-in)."""
    coll = _collection(db, "mtlink_data")
    if coll is None or not machines:
        return []
    # Parts that started just before the window can still finish inside it.
    fetch_start = range_start_ist - timedelta(hours=2)
    match = _mtlink_start_in_window_match(machines, fetch_start, range_end_ist)
    try:
        return list(
            coll.find(
                match,
                {"machine": 1, "start": 1, "end": 1, "value": 1, "source": 1, "_id": 0},
            )
        )
    except Exception:
        return []


def _fanuc_mtlink_stats_from_docs(docs, machines, start_ist, end_ist):
    """
    Part counts + avg cycle time for one window.
    """
    machine_set = set(machines)
    counter_rows = {machine: [] for machine in machines}
    fallback_rows = {machine: [] for machine in machines}
    counter_starts = {machine: set() for machine in machines}
    fallback_starts = {machine: set() for machine in machines}
    counter_cycles = {machine: {} for machine in machines}
    fallback_cycles = {machine: {} for machine in machines}

    for doc in docs:
        machine = str(doc.get("machine") or "").strip()
        if machine not in machine_set:
            continue
        value = _parse_numeric_value(doc.get("value"))
        if value is None or value == 0:
            continue
        start_dt = _parse_mtlink_datetime(doc.get("start"))
        if start_dt is None:
            continue
        start_local = start_dt.astimezone(IST)
        if not _mtlink_event_overlaps_window(doc, start_ist, end_ist):
            continue

        source = str(doc.get("source") or MTLINK_PART_COUNTER_SOURCE).strip()
        start_key = _mtlink_start_key(start_local)
        cycle_sec = _mtlink_event_cycle_seconds(doc)
        row = (start_local, value)
        if source == MTLINK_PARTS_NUM_SOURCE:
            fallback_starts[machine].add(start_key)
            fallback_rows[machine].append(row)
            if cycle_sec > 0:
                fallback_cycles[machine][start_key] = cycle_sec
        else:
            counter_starts[machine].add(start_key)
            counter_rows[machine].append(row)
            if cycle_sec > 0:
                counter_cycles[machine][start_key] = cycle_sec

    counts = {}
    cycle_times = {}
    for machine in machines:
        rows = counter_rows[machine]
        starts = counter_starts[machine]
        cycles = counter_cycles[machine]
        if not rows:
            rows = fallback_rows[machine]
            starts = fallback_starts[machine]
            cycles = fallback_cycles[machine]
        if not rows:
            continue
        unique = {}
        for start_local, value in rows:
            unique[_mtlink_start_key(start_local)] = (start_local, value)
        rows = sorted(unique.values(), key=lambda item: item[0])
        values = [item[1] for item in rows]
        counts[machine] = _count_counter_values(values)
        cycle_vals = [cycles[k] for k in starts if k in cycles]
        if cycle_vals:
            cycle_times[machine] = int(round(sum(cycle_vals) / len(cycle_vals)))
    return counts, cycle_times


def _parse_window_timestamp(value):
    """Parse Mbd / machine_data timestamp to IST."""
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=IST)
        return value.astimezone(IST)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        try:
            dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def _parse_downtime_created_at(doc):
    for field in ("createdAt", "created_at", "createdat"):
        val = doc.get(field)
        if val is None:
            continue
        if isinstance(val, datetime):
            if val.tzinfo is None:
                return val.replace(tzinfo=timezone.utc)
            return val.astimezone(timezone.utc)
        try:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (TypeError, ValueError):
            continue
    return None


def _fetch_unplanned_downtime_docs(db, machines, range_start_utc, range_end_utc):
    if not machines:
        return []
    try:
        match = {
            "$and": [
                {
                    "$or": [
                        {"process_name": {"$in": list(machines)}},
                        {"process": {"$in": list(machines)}},
                        {"machine": {"$in": list(machines)}},
                    ]
                },
                {
                    "$or": [
                        {
                            "createdAt": {
                                "$gte": range_start_utc,
                                "$lt": range_end_utc,
                            }
                        },
                        {
                            "created_at": {
                                "$gte": range_start_utc,
                                "$lt": range_end_utc,
                            }
                        },
                        {
                            "createdat": {
                                "$gte": range_start_utc,
                                "$lt": range_end_utc,
                            }
                        },
                    ]
                },
            ]
        }
        return list(
            db["PMS_unplanneddowntime"].find(
                match,
                {
                    "process_name": 1,
                    "process": 1,
                    "machine": 1,
                    "createdAt": 1,
                    "created_at": 1,
                    "createdat": 1,
                    "unplanned_downtime": 1,
                    "_id": 0,
                },
            )
        )
    except Exception:
        return []


def _unplanned_downtime_map_from_docs(docs, machines, start_utc, end_utc):
    totals = {machine: 0 for machine in machines}
    lower_map = {machine.lower(): machine for machine in machines}
    for doc in docs:
        created = _parse_downtime_created_at(doc)
        if created is None or not (start_utc <= created < end_utc):
            continue
        process = doc.get("process_name") or doc.get("process") or doc.get("machine")
        target = None
        if process in totals:
            target = process
        elif process and str(process).lower() in lower_map:
            target = lower_map[str(process).lower()]
        if not target:
            continue
        for entry in doc.get("unplanned_downtime") or []:
            try:
                totals[target] += int(entry.get("minutes") or 0)
            except (TypeError, ValueError):
                continue
    return totals


def _fetch_mbd_docs_for_range(db, machines, range_start_ist, range_end_ist, tags):
    coll = _collection(db, "Mbd")
    if coll is None or not machines:
        return []
    ts_match = _timestamp_match(range_start_ist, range_end_ist)
    match = {
        "$and": [
            ts_match,
            {"source": {"$in": list(machines)}},
            {"tag": {"$in": list(tags)}},
        ]
    }
    try:
        return list(
            coll.find(
                match,
                {"source": 1, "tag": 1, "value": 1, "timestamp": 1, "_id": 0},
            )
        )
    except Exception:
        return []


def _latest_mbd_tag_values_from_docs(docs, machines, start_ist, end_ist):
    machine_set = set(machines)
    best = {}
    for doc in docs:
        source = str(doc.get("source") or "").strip()
        if source not in machine_set:
            continue
        tag = str(doc.get("tag") or "").strip().upper()
        if not tag:
            continue
        ts = _parse_window_timestamp(doc.get("timestamp"))
        if ts is None or not (start_ist <= ts < end_ist):
            continue
        key = (source, tag)
        prev = best.get(key)
        if prev is None or ts > prev[0]:
            best[key] = (ts, doc.get("value"))

    out = defaultdict(dict)
    for (source, tag), (_, value) in best.items():
        out[source][tag] = value
    return out


def _fetch_focas_docs_for_range(db, machines, range_start_ist, range_end_ist):
    """Fetch machine_data rows for the range + one row before start (for delta context)."""
    coll = _collection(db, "machine_data")
    if coll is None or not machines:
        return []
    # Extend fetch window by 2 hours before range start so the delta baseline
    # (the row just before shift start) is always included.
    fetch_start = range_start_ist - timedelta(hours=2)
    ts_match = _timestamp_match(fetch_start, range_end_ist)
    match = {"$and": [ts_match, {"machine_name": {"$in": list(machines)}}]}
    try:
        return list(
            coll.find(
                match,
                {
                    "machine_name": 1,
                    "timestamp": 1,
                    "cycle_start": 1,
                    "total_part_count": 1,
                    "cycle_start_utc": 1,
                    "cycle_stop_utc": 1,
                    "ok_part_count": 1,
                    "not_ok_part_count": 1,
                    "machine_cycle_time": 1,
                    "overall_run_time": 1,
                    "machine_down_time": 1,
                    "_id": 0,
                },
            )
        )
    except Exception:
        return []


def _focas_ts(doc):
    """Best timestamp from a machine_data doc (cycle_start preferred over timestamp)."""
    for field in ("cycle_start", "cycle_stop"):
        ts = _as_ist_wall(doc.get(field))
        if ts is not None:
            return ts
    return None


def _cycle_event_timestamp(doc):
    """Parse cycle time from stored IST wall-clock fields only."""
    for field in ("cycle_start", "cycle_stop"):
        ts = _as_ist_wall(doc.get(field))
        if ts is not None:
            return ts
    return None


def _focas_produced_from_series(pre_val, in_values):
    """
    Parts produced inside the shift window from a cumulative counter series.

    pre_val  : last total_part_count before the window (None if not available)
    in_values: list of total_part_count values inside the window, sorted by time

    Logic (same as Mongo aggregation):
      - For each consecutive pair (prev, curr):
          if curr >= prev  → produced += curr - prev
          else (reset)     → produced += curr  (counter restarted)
      - First pair uses pre_val as the previous value if available.
    """
    if not in_values:
        return 0
    produced = 0
    prev = pre_val  # None means no baseline
    for curr in in_values:
        if prev is None:
            produced += curr
        elif curr >= prev:
            produced += curr - prev
        else:
            produced += curr  # counter reset
        prev = curr
    return max(0, produced)


def _focas_delta_snapshot_from_docs(
    docs, machines, start_ist, end_ist, inclusive_end=False
):
    """
    Produced parts for FOCAS machines using delta counting.

    total_part_count is a cumulative counter that may reset.
    Parts produced = sum of positive deltas in the window, using the row
    just before window start as the baseline (same logic as your Mongo shell query).
    Metadata (cycle_time, run_time, down_time) from the latest in-window row.
    """
    machine_set = set(machines)
    rows_by_machine = {m: [] for m in machines}

    for doc in docs:
        name = str(doc.get("machine_name") or "").strip()
        if name not in machine_set:
            continue
        ts = _focas_ts(doc)
        if ts is None:
            continue
        total = _parse_numeric_value(doc.get("total_part_count"))
        if total is None:
            continue
        rows_by_machine[name].append((ts, total, doc))

    out = {}
    for machine in machines:
        rows = sorted(rows_by_machine[machine], key=lambda r: r[0])
        if not rows:
            continue

        pre_rows = [r for r in rows if r[0] < start_ist]
        if inclusive_end:
            in_rows = [r for r in rows if start_ist <= r[0] <= end_ist]
        else:
            in_rows = [r for r in rows if start_ist <= r[0] < end_ist]
        if not in_rows:
            continue

        pre_val = pre_rows[-1][1] if pre_rows else None
        in_values = [r[1] for r in in_rows]
        produced = _focas_produced_from_series(pre_val, in_values)

        latest_doc = in_rows[-1][2]
        out[machine] = {
            "machine_name": machine,
            "total_part_count": produced,
            "machine_cycle_time": latest_doc.get("machine_cycle_time"),
            "overall_run_time": latest_doc.get("overall_run_time"),
            "machine_down_time": latest_doc.get("machine_down_time"),
        }
    return out


_latest_focas_snapshot_from_docs = _focas_delta_snapshot_from_docs


def _mbd_cycle_time_from_tag_values(tag_values, plant_code=None):
    from core.mbd_cycle_utils import (
        finalize_mbd_cycle_time,
        mbd_aggregate_cycle_time_seconds,
        mbd_station_index_from_tag,
        mbd_value_to_seconds,
    )

    ct_map = {}
    for source, vals in tag_values.items():
        for tag, value in vals.items():
            tag_upper = str(tag or "").strip().upper()
            if source not in ct_map:
                ct_map[source] = {"station_cycle_seconds": {}}
            if tag_upper == "MACHINE CYCLE TIME":
                ct_map[source]["cycle_time"] = mbd_aggregate_cycle_time_seconds(
                    value, plant_code=plant_code
                )
            elif tag_upper.startswith("MACHINE CYCLE TIME STATION"):
                idx = mbd_station_index_from_tag(tag_upper)
                if idx:
                    ct_map[source]["station_cycle_seconds"][idx] = mbd_value_to_seconds(
                        value
                    )
    for source, data in ct_map.items():
        finalize_mbd_cycle_time(source, data)
    return {src: data.get("cycle_time", 0) for src, data in ct_map.items()}


def _fanuc_mtlink_stats_for_window(db, machines, start_ist, end_ist, plant_code=None):
    """
    FANUC mtlink_data part counts + average cycle time for one window.
    """
    if not machines:
        return {}, {}
    try:
        docs = _fetch_mtlink_docs_for_range(db, machines, start_ist, end_ist)
        return _fanuc_mtlink_stats_from_docs(docs, machines, start_ist, end_ist)
    except Exception as exc:
        log_traceability(
            f"FANUC mtlink_data stats query failed: {exc}",
            plant_code=plant_code,
        )
        return {}, {}


def _fanuc_mtlink_counts_for_window(db, machines, start_ist, end_ist, plant_code=None):
    """Backward-compatible wrapper — counts only."""
    counts, _ = _fanuc_mtlink_stats_for_window(
        db, machines, start_ist, end_ist, plant_code=plant_code
    )
    return counts


def _fanuc_cell_cycle_time_map(db, start_utc, end_utc, plant_code=None):
    """Cycle time (seconds) per machine from PMS_oee_cell with minute/HH:MM:SS parsing."""
    out = {}
    try:
        cell_docs = list(
            db["PMS_oee_cell"].find(
                {"processes.process.createdAt": {"$gte": start_utc, "$lt": end_utc}}
            )
        )
        if not cell_docs:
            cell_docs = list(db["PMS_oee_cell"].find({}))
    except Exception:
        return out

    for doc in cell_docs:
        for cell in doc.get("processes", []):
            for proc in cell.get("process", []):
                pname = proc.get("processName")
                ct_raw = proc.get("cycleTime")
                if not pname or pname in out:
                    continue
                sec = _parse_cell_cycle_seconds(ct_raw, plant_code=plant_code)
                if sec > 0:
                    out[pname] = sec
    return out


def _fanuc_cycle_map_from_mtlink(
    db,
    machines,
    start_ist,
    end_ist,
    plant_code=None,
    cell_cycle_times=None,
    mtlink_docs=None,
):
    """Normalize mtlink_data counts into shared OEE input shape."""
    if mtlink_docs is not None:
        counts, mtlink_cycle_times = _fanuc_mtlink_stats_from_docs(
            mtlink_docs, machines, start_ist, end_ist
        )
    else:
        counts, mtlink_cycle_times = _fanuc_mtlink_stats_for_window(
            db, machines, start_ist, end_ist, plant_code=plant_code
        )
    if not counts:
        return {}

    if cell_cycle_times is None:
        start_utc = start_ist.astimezone(timezone.utc)
        end_utc = end_ist.astimezone(timezone.utc)
        cell_cycle_times = _fanuc_cell_cycle_time_map(
            db, start_utc, end_utc, plant_code
        )

    cycle_map = {}
    for machine, part_count in counts.items():
        ct = int(mtlink_cycle_times.get(machine) or cell_cycle_times.get(machine) or 0)
        cycle_map[machine] = {
            "part_count": part_count,
            "good_count": part_count,
            "reject_count": 0,
            "cycle_time": ct,
            "source": "mtlink_data",
        }

    return cycle_map


def fetch_fanuc_daily_records(db, machines, start_date, end_date, plant_code):
    """
    Daywise FANUC OEE rows from mtlink_data.

    Window for report date D = [D shift1 start, D+1 shift1 start) from PMS_shift_config.
    """
    if not machines:
        return []

    rows = []
    for business_day in iter_report_dates(start_date, end_date):
        start_utc, end_utc, start_ist, end_ist, total_minutes = report_day_window_utc(
            business_day, plant_code
        )
        date_label = business_day.strftime("%Y-%m-%d")
        plan_date = date_label
        try:
            planned_map = get_daily_planned_qty_map(
                db, machines, plan_date, plant_code=plant_code
            )
            cycle_map = _fanuc_cycle_map_from_mtlink(
                db, machines, start_ist, end_ist, plant_code=plant_code
            )
            day_rows = _oee_rows_from_counts(
                db,
                cycle_map,
                start_utc,
                end_utc,
                date_label,
                plant_code,
                planned_map=planned_map,
                total_time_minutes=total_minutes,
            )
            rows.extend(day_rows)
            log_traceability(
                f"FANUC OEE Daily source=mtlink_data machines={machines} "
                f"date={date_label} window={start_ist}→{end_ist} records={len(day_rows)}",
                plant_code=plant_code,
            )
        except Exception as exc:
            log_traceability(
                f"FANUC daily mtlink OEE failed for {date_label}: {exc}",
                plant_code=plant_code,
            )
            continue
    return rows


def fetch_fanuc_shift_records(
    db, machines, start_date, end_date, plant_code, selected_shift=None
):
    """Shiftwise FANUC OEE rows from mtlink_data (batched queries per date range)."""
    if not machines:
        return []

    windows = list(
        _iter_report_shift_windows(start_date, end_date, plant_code, selected_shift)
    )
    if not windows:
        return []

    range_start_ist = min(w[2] for w in windows)
    range_end_ist = max(w[3] for w in windows)
    mtlink_docs = _fetch_mtlink_docs_for_range(
        db, machines, range_start_ist, range_end_ist
    )
    range_start_utc = range_start_ist.astimezone(timezone.utc)
    range_end_utc = range_end_ist.astimezone(timezone.utc)
    cell_cycle_times = _fanuc_cell_cycle_time_map(
        db, range_start_utc, range_end_utc, plant_code
    )
    downtime_docs = _fetch_unplanned_downtime_docs(
        db, machines, range_start_utc, range_end_utc
    )

    rows = []
    for (
        date_label,
        shift_name,
        start_ist,
        end_ist,
        start_utc,
        end_utc,
        total_minutes,
    ) in windows:
        try:
            cycle_map = _fanuc_cycle_map_from_mtlink(
                db,
                machines,
                start_ist,
                end_ist,
                plant_code=plant_code,
                cell_cycle_times=cell_cycle_times,
                mtlink_docs=mtlink_docs,
            )
            unplanned = _unplanned_downtime_map_from_docs(
                downtime_docs, machines, start_utc, end_utc
            )
            rows.extend(
                _oee_rows_from_counts(
                    db,
                    cycle_map,
                    start_utc,
                    end_utc,
                    date_label,
                    plant_code,
                    shift=shift_name,
                    total_time_minutes=total_minutes,
                    unplanned_map=unplanned,
                )
            )
        except Exception as exc:
            log_traceability(
                f"FANUC shift mtlink OEE failed for {date_label} {shift_name}: {exc}",
                plant_code=plant_code,
            )
            continue

    log_traceability(
        f"FANUC OEE Shift source=mtlink_data machines={len(machines)} "
        f"windows={len(windows)} records={len(rows)}",
        plant_code=plant_code,
    )
    return rows


def _latest_mbd_tag_values(db, machines, start_ist, end_ist, tags, plant_code=None):
    """
    Delta value per machine+tag for the shift window from Mbd cumulative counters.

    TOTAL PART COUNT is a cumulative counter — returns (in_window_max - pre_window_last)
    clamped to 0, so an idle shift correctly returns 0 even if the machine keeps
    reporting the same counter value throughout the shift.

    For non-cumulative tags the last in-window value is returned as-is.
    """
    if not machines or not tags:
        return {}
    coll = _collection(db, "Mbd")
    if coll is None:
        return {}

    CUMULATIVE_TAGS = {
        "TOTAL PART COUNT",
        "OK PART COUNT",
        "NOT OK PART COUNT",
        *MBD_DURATION_TAGS,
    }

    # --- Pre-window baseline (last doc per machine+tag strictly before window start) ---
    pre_ts_match = _timestamp_match(
        start_ist - timedelta(hours=24), start_ist  # look back up to 24 h
    )
    pre_match = {
        "$and": [
            pre_ts_match,
            {"source": {"$in": list(machines)}},
            {"tag": {"$in": list(tags)}},
        ]
    }
    pre_pipeline = [
        {"$match": pre_match},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": {"source": "$source", "tag": "$tag"},
                "value": {"$first": "$value"},
            }
        },
    ]
    pre_baseline = defaultdict(dict)
    try:
        for doc in coll.aggregate(pre_pipeline):
            source = str(doc["_id"].get("source") or "").strip()
            tag = str(doc["_id"].get("tag") or "").strip().upper()
            if source and tag:
                pre_baseline[source][tag] = doc.get("value")
    except Exception:
        pass  # no baseline → treat as 0

    # --- In-window max value per machine+tag ---
    ts_match = _timestamp_match(start_ist, end_ist)
    match = {
        "$and": [
            ts_match,
            {"source": {"$in": list(machines)}},
            {"tag": {"$in": list(tags)}},
        ]
    }
    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": {"source": "$source", "tag": "$tag"},
                "max_value": {"$max": "$value"},
                "last_value": {"$first": "$value"},
            }
        },
    ]

    out = defaultdict(dict)
    try:
        for doc in coll.aggregate(pipeline):
            source = str(doc["_id"].get("source") or "").strip()
            tag = str(doc["_id"].get("tag") or "").strip().upper()
            if not source or not tag:
                continue
            if tag in CUMULATIVE_TAGS:
                in_max = _parse_numeric_value(doc.get("max_value"))
                baseline = _parse_numeric_value(pre_baseline.get(source, {}).get(tag))
                if in_max is None:
                    continue
                if baseline is None:
                    # No pre-window baseline — use first in-window value as baseline
                    # (shift delta starts from 0 at shift start)
                    out[source][tag] = in_max
                else:
                    out[source][tag] = max(0, in_max - baseline)
            else:
                out[source][tag] = doc.get("last_value")
    except Exception as exc:
        log_traceability(f"MBD latest-tag query failed: {exc}", plant_code=plant_code)
    return out


def _as_ist_wall(value):
    """Parse stored timestamp as IST wall clock (no UTC→IST conversion)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        try:
            cleaned = raw.replace("Z", "").split("+")[0].split(".")[0]
            dt = datetime.fromisoformat(cleaned[:19])
        except (TypeError, ValueError):
            return None
    return datetime(
        dt.year,
        dt.month,
        dt.day,
        dt.hour,
        dt.minute,
        dt.second,
        dt.microsecond,
        tzinfo=IST,
    )


def _mbd_cycle_time_query(start_ist, end_ist):
    """Match Mbd/machine_data docs whose cycle_start or cycle_stop is in window."""
    start_naive = start_ist.replace(tzinfo=None)
    end_naive = end_ist.replace(tzinfo=None)
    start_str_s = start_naive.strftime("%Y-%m-%d %H:%M:%S")
    end_str_s = end_naive.strftime("%Y-%m-%d %H:%M:%S")
    start_str_t = start_naive.strftime("%Y-%m-%dT%H:%M:%S")
    end_str_t = end_naive.strftime("%Y-%m-%dT%H:%M:%S")
    bounds = []
    for field in ("cycle_start", "cycle_stop"):
        bounds.extend(
            [
                {field: {"$gte": start_str_s, "$lt": end_str_s}},
                {field: {"$gte": start_str_t, "$lt": end_str_t}},
            ]
        )
    return {"$or": bounds}


def _fetch_mbd_cycle_docs_for_range(db, machines, start_ist, end_ist):
    """Fetch MBD cycle docs once for all report windows in requested range."""
    coll = _collection(db, "Mbd")
    if coll is None or not machines:
        return []

    machine_set = set(machines)
    query = {
        "$and": [
            _mbd_cycle_time_query(start_ist, end_ist),
            {
                "$or": [
                    {"source": {"$in": list(machine_set)}},
                    {"machine_name": {"$in": list(machine_set)}},
                ]
            },
            {
                "$or": [
                    {"cycle_start": {"$exists": True, "$nin": [None, ""]}},
                    {"cycle_stop": {"$exists": True, "$nin": [None, ""]}},
                ]
            },
        ]
    }
    return list(
        coll.find(
            query,
            {"source": 1, "machine_name": 1, "cycle_start": 1, "cycle_stop": 1},
        )
        .limit(500000)
        .max_time_ms(30000)
    )


def _mbd_cycle_doc_counts(
    db, machines, start_ist, end_ist, plant_code=None, cycle_docs=None
):
    """
    Count Mbd cycle docs in window — each doc = 1 part.
    """
    if not machines or (cycle_docs is not None and not cycle_docs):
        return {}

    coll = None if cycle_docs is not None else _collection(db, "Mbd")
    if cycle_docs is None and coll is None:
        return {}

    machine_set = set(machines)
    alias = {}
    for m in machine_set:
        alias[m] = m
        alias[m.upper()] = m

    cycle_q = _mbd_cycle_time_query(start_ist, end_ist)
    query = {
        "$and": [
            cycle_q,
            {
                "$or": [
                    {"source": {"$in": list(machine_set)}},
                    {"machine_name": {"$in": list(machine_set)}},
                ]
            },
            {
                "$or": [
                    {"cycle_start": {"$exists": True, "$nin": [None, ""]}},
                    {"cycle_stop": {"$exists": True, "$nin": [None, ""]}},
                ]
            },
        ]
    }

    counts = {m: 0 for m in machines}
    cycle_secs_sum = {m: 0.0 for m in machines}
    cycle_secs_cnt = {m: 0 for m in machines}
    runtime_secs_sum = {m: 0 for m in machines}

    try:
        docs = cycle_docs
        if docs is None:
            docs = (
                coll.find(
                    query,
                    {
                        "source": 1,
                        "machine_name": 1,
                        "cycle_start": 1,
                        "cycle_stop": 1,
                    },
                )
                .limit(500000)
                .max_time_ms(30000)
            )
        for doc in docs:
            raw_name = doc.get("source") or doc.get("machine_name")
            if not raw_name:
                continue
            m = alias.get(raw_name) or alias.get(str(raw_name).upper())
            if not m or m not in machine_set:
                continue

            ts_raw = doc.get("cycle_start") or doc.get("cycle_stop")
            ts = _as_ist_wall(ts_raw)
            if ts is None or ts < start_ist or ts >= end_ist:
                continue

            counts[m] += 1

            # Cycle time from timestamps
            cs = _as_ist_wall(doc.get("cycle_start"))
            ce = _as_ist_wall(doc.get("cycle_stop"))
            if cs and ce and ce > cs:
                delta = (ce - cs).total_seconds()
                # Sanity: reject sub-1s and >1h cycles
                if 1 <= delta <= 3600:
                    runtime_secs_sum[m] += int(round(delta))
                    cycle_secs_sum[m] += delta
                    cycle_secs_cnt[m] += 1
    except Exception as exc:
        log_traceability(
            f"Mbd cycle-doc count query failed: {exc}",
            plant_code=plant_code,
        )

    out = {}
    for m in machines:
        total = counts[m]
        if total <= 0:
            continue
        if cycle_secs_cnt[m] > 0:
            avg_ct = int(round(cycle_secs_sum[m] / cycle_secs_cnt[m]))
            # If avg is very small (< 2 s), treat as minutes and convert
            if avg_ct < 2 and avg_ct > 0:
                avg_ct = int(round(avg_ct * 60))
        else:
            avg_ct = 0
        out[m] = {
            "part_count": total,
            "good_count": total,
            "reject_count": 0,
            "cycle_time": avg_ct,
            "run_time_seconds": runtime_secs_sum[m],
            "runtime_from_cycles": True,
        }
    return out


def _fetch_focas_cycle_docs_for_range(db, machines, start_ist, end_ist):
    """Fetch FOCAS cycle docs once for all report windows in requested range."""
    coll = _collection(db, "machine_data")
    if coll is None or not machines:
        return []

    query = {
        "$and": [
            _mbd_cycle_time_query(start_ist, end_ist),
            {"machine_name": {"$in": list(machines)}},
            {
                "$or": [
                    {"cycle_start": {"$exists": True, "$nin": [None, ""]}},
                    {"cycle_stop": {"$exists": True, "$nin": [None, ""]}},
                ]
            },
        ]
    }
    return list(
        coll.find(
            query,
            {
                "machine_name": 1,
                "cycle_start": 1,
                "cycle_stop": 1,
                "cycle_start_utc": 1,
                "cycle_stop_utc": 1,
                "machine_cycle_time": 1,
                "machine_down_time": 1,
            },
        )
        .limit(500000)
        .max_time_ms(30000)
    )


def _focas_cycle_doc_counts(
    db, machines, start_ist, end_ist, plant_code=None, cycle_docs=None
):
    """
    Count machine_data cycle docs in window — each doc = 1 part.
    """
    if not machines or (cycle_docs is not None and not cycle_docs):
        return {}

    coll = None if cycle_docs is not None else _collection(db, "machine_data")
    if cycle_docs is None and coll is None:
        return {}

    machine_set = set(machines)
    cycle_q = _mbd_cycle_time_query(start_ist, end_ist)
    query = {
        "$and": [
            cycle_q,
            {"machine_name": {"$in": list(machine_set)}},
            {
                "$or": [
                    {"cycle_start": {"$exists": True, "$nin": [None, ""]}},
                    {"cycle_stop": {"$exists": True, "$nin": [None, ""]}},
                ]
            },
        ]
    }

    counts = {m: 0 for m in machines}
    cycle_secs_sum = {m: 0.0 for m in machines}
    cycle_secs_cnt = {m: 0 for m in machines}
    runtime_secs_sum = {m: 0 for m in machines}
    downtime_secs_sum = {m: 0 for m in machines}

    try:
        docs = cycle_docs
        if docs is None:
            docs = (
                coll.find(
                    query,
                    {
                        "machine_name": 1,
                        "cycle_start": 1,
                        "cycle_stop": 1,
                        "cycle_start_utc": 1,
                        "cycle_stop_utc": 1,
                        "machine_cycle_time": 1,
                        "machine_down_time": 1,
                    },
                )
                .limit(500000)
                .max_time_ms(30000)
            )
        for doc in docs:
            m = str(doc.get("machine_name") or "").strip()
            if not m or m not in machine_set:
                continue

            ts = _cycle_event_timestamp(doc)
            if ts is None or ts < start_ist or ts >= end_ist:
                continue

            counts[m] += 1

            duration = _machine_data_cycle_seconds(doc)
            runtime_secs_sum[m] += duration
            downtime_secs_sum[m] += _machine_data_duration_seconds(
                doc.get("machine_down_time")
            )

            # Prefer machine_cycle_time field for per-part performance.
            ct_raw = doc.get("machine_cycle_time")
            if ct_raw not in (None, ""):
                ct = structured_doc_cycle_seconds(
                    {"machine_cycle_time": ct_raw}, plant_code=None
                )
                if ct > 0:
                    cycle_secs_sum[m] += ct
                    cycle_secs_cnt[m] += 1
                    continue

            cs = _parse_window_timestamp(doc.get("cycle_start_utc"))
            ce = _parse_window_timestamp(doc.get("cycle_stop_utc"))
            if cs is None:
                cs = _as_ist_wall(doc.get("cycle_start"))
            if ce is None:
                ce = _as_ist_wall(doc.get("cycle_stop"))
            if cs and ce and ce > cs:
                delta = (ce - cs).total_seconds()
                if 1 <= delta <= 3600:
                    cycle_secs_sum[m] += delta
                    cycle_secs_cnt[m] += 1
    except Exception as exc:
        log_traceability(
            f"machine_data cycle-doc count query failed: {exc}",
            plant_code=plant_code,
        )

    out = {}
    for m in machines:
        total = counts[m]
        if total <= 0:
            continue
        if cycle_secs_cnt[m] > 0:
            avg_ct = int(round(cycle_secs_sum[m] / cycle_secs_cnt[m]))
            if avg_ct < 2 and avg_ct > 0:
                avg_ct = int(round(avg_ct * 60))
        else:
            avg_ct = 0
        out[m] = {
            "part_count": total,
            "good_count": total,
            "reject_count": 0,
            "cycle_time": avg_ct,
            "run_time_seconds": runtime_secs_sum[m],
            "downtime_seconds": downtime_secs_sum[m],
            "runtime_from_cycles": True,
        }
    return out


def _mbd_counts_for_window(
    db,
    machines,
    start_ist,
    end_ist,
    plant_code=None,
    tag_values=None,
    cycle_docs=None,
):
    """
    Per-machine MBD counts.

    Primary: count cycle docs (each Mbd doc with cycle_start/stop in window = 1 part).
    Fallback: latest in-window TOTAL PART COUNT tag snapshot (legacy tag-based feeds).

    Both paths run: cycle-doc machines use cycle counts, remaining machines
    always fall back to tag snapshot (they may have no cycle_start/stop fields).
    """
    # Try cycle-doc counting first
    cycle_counts = _mbd_cycle_doc_counts(
        db,
        machines,
        start_ist,
        end_ist,
        plant_code,
        cycle_docs=cycle_docs,
    )

    # Machines not covered by cycle docs → always run tag fallback
    missing = [m for m in machines if m not in cycle_counts]

    out = dict(cycle_counts)

    if missing:
        tags = ("TOTAL PART COUNT", "OK PART COUNT", "NOT OK PART COUNT")
        if tag_values is None:
            tag_values = _latest_mbd_tag_values(
                db, missing, start_ist, end_ist, tags, plant_code=plant_code
            )
        for machine in missing:
            vals = tag_values.get(machine, {})
            total = _parse_numeric_value(vals.get("TOTAL PART COUNT"))
            good = _parse_numeric_value(vals.get("OK PART COUNT"))
            reject = _parse_numeric_value(vals.get("NOT OK PART COUNT"))

            if total is None and good is not None and reject is not None:
                total = max(0, good + reject)
            if good is None and total is not None:
                good = max(0, total - (reject or 0))
            if reject is None and total is not None and good is not None:
                reject = max(0, total - good)

            total = max(0, int(total or 0))
            good = max(0, int(good or 0))
            reject = max(0, int(reject or 0))
            if total or good or reject:
                out[machine] = {
                    "part_count": total,
                    "good_count": good,
                    "reject_count": reject,
                }
    return out


def _latest_focas_snapshot(
    db, machines, start_ist, end_ist, plant_code=None, inclusive_end=False
):
    """
    Produced parts for FOCAS machines via delta counting from machine_data.

    Fetches rows from 2h before window start so the pre-window baseline is available.
    """
    if not machines:
        return {}
    coll = _collection(db, "machine_data")
    if coll is None:
        return {}

    fetch_start = start_ist - timedelta(hours=2)
    ts_match = _timestamp_match(fetch_start, end_ist)
    match = {
        "$and": [
            ts_match,
            {"machine_name": {"$in": list(machines)}},
        ]
    }
    try:
        docs = list(
            coll.find(
                match,
                {
                    "machine_name": 1,
                    "timestamp": 1,
                    "cycle_start": 1,
                    "total_part_count": 1,
                    "cycle_start_utc": 1,
                    "cycle_stop_utc": 1,
                    "machine_cycle_time": 1,
                    "overall_run_time": 1,
                    "machine_down_time": 1,
                    "_id": 0,
                },
            )
        )
    except Exception as exc:
        log_traceability(f"FOCAS delta query failed: {exc}", plant_code=plant_code)
        return {}

    return _focas_delta_snapshot_from_docs(
        docs, machines, start_ist, end_ist, inclusive_end=inclusive_end
    )


def _mbd_cycle_time_for_window(db, machines, start_ist, end_ist, plant_code=None):
    """
    Average MACHINE CYCLE TIME per machine from Mbd for the window.
    Handles station-sum machines (e.g. KM-BN-01).
    """
    from core.mbd_cycle_utils import (
        finalize_mbd_cycle_time,
        mbd_aggregate_cycle_time_seconds,
        mbd_station_index_from_tag,
        mbd_value_to_seconds,
    )

    if not machines:
        return {}

    coll = _collection(db, "Mbd")
    if coll is None:
        return {}

    tags = [
        "MACHINE CYCLE TIME",
        "MACHINE CYCLE TIME STATION 1",
        "MACHINE CYCLE TIME STATION 2",
        "MACHINE CYCLE TIME STATION 3",
    ]
    ts_match = _timestamp_match(start_ist, end_ist)
    match = {
        "$and": [
            ts_match,
            {"source": {"$in": list(machines)}},
            {"tag": {"$in": tags}},
        ]
    }
    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": {"source": "$source", "tag": "$tag"},
                "value": {"$first": "$value"},
            }
        },
    ]

    ct_map = {}
    try:
        for d in coll.aggregate(pipeline):
            source = d["_id"]["source"]
            tag = d["_id"]["tag"]
            value = d.get("value")
            if source not in ct_map:
                ct_map[source] = {"station_cycle_seconds": {}}
            if tag == "MACHINE CYCLE TIME":
                ct_map[source]["cycle_time"] = mbd_aggregate_cycle_time_seconds(
                    value, plant_code=plant_code
                )
            elif tag.startswith("MACHINE CYCLE TIME STATION"):
                idx = mbd_station_index_from_tag(tag)
                if idx:
                    ct_map[source]["station_cycle_seconds"][idx] = mbd_value_to_seconds(
                        value
                    )
    except Exception as exc:
        log_traceability(f"MBD cycle time query failed: {exc}", plant_code=plant_code)
        return {}

    for source, data in ct_map.items():
        finalize_mbd_cycle_time(source, data)

    return {src: data.get("cycle_time", 0) for src, data in ct_map.items()}


def _focas_cycle_time_for_window(db, machines, start_ist, end_ist, plant_code=None):
    """Latest machine_cycle_time per machine from machine_data for window."""
    if not machines:
        return {}

    coll = _collection(db, "machine_data")
    if coll is None:
        return {}

    ts_match = _timestamp_match(start_ist, end_ist)
    match = {
        "$and": [
            ts_match,
            {"machine_name": {"$in": list(machines)}},
            {"machine_cycle_time": {"$exists": True, "$nin": [None, ""]}},
        ]
    }
    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": "$machine_name",
                "machine_cycle_time": {"$first": "$machine_cycle_time"},
            }
        },
    ]

    result = {}
    try:
        for d in coll.aggregate(pipeline):
            name = d["_id"]
            if name not in machines:
                continue
            ct = structured_doc_cycle_seconds(
                {"machine_cycle_time": d["machine_cycle_time"]},
                plant_code=None,
            )
            if ct > 0:
                result[name] = ct
    except Exception as exc:
        log_traceability(f"FOCAS cycle time query failed: {exc}", plant_code=plant_code)
    return result


def _categorise_live_machines(db, machines):
    """
    Split a list of live (non-FANUC) machines into MBD vs FOCAS.

    Returns (mbd_set, focas_set) — both as sets of canonical machine names.
    Unknown-category machines default to MBD (tag-based Mbd collection).
    """
    mbd = set()
    focas = set()
    machine_set = set(machines)
    for doc in db["PMS_machine_info"].find(
        {"machine_name": {"$in": list(machine_set)}},
        {"machine_name": 1, "category": 1, "_id": 0},
    ):
        name = str(doc.get("machine_name") or "").strip()
        if not name or name not in machine_set:
            continue
        cat = _norm_category(doc.get("category"))
        if cat == "fanuc_focas":
            focas.add(name)
        else:
            # mbd or blank → Mbd tag path
            mbd.add(name)
    # Any machine not found in PMS_machine_info defaults to MBD
    for m in machine_set:
        if m not in mbd and m not in focas:
            mbd.add(m)
    return mbd, focas


def count_each_cycle_map(
    db,
    machines,
    start_ist,
    end_ist,
    plant_code=None,
    mbd_machines=None,
    focas_machines=None,
    mbd_docs=None,
    focas_docs=None,
    quiet=False,
    focas_inclusive_end=False,
    focas_cycle_docs=None,
):
    """
    Per-machine part count + cycle time for non-FANUC machines in a time window.

    MBD machines (category='mbd' or blank):
      - Part count = count of Mbd cycle docs (cycle_start/stop) in window.
      - Cycle time = avg (cycle_stop - cycle_start) seconds per doc.
      - Fallback to tag-snapshot if no cycle docs found.

    FANUC FOCAS machines (category='fanuc_focas'):
      - Part count = count of machine_data cycle docs (cycle_start/stop) in window.
      - Cycle time = avg from machine_cycle_time field or timestamp delta.
      - Fallback to delta of total_part_count series if no cycle docs found.

    Returns dict: {machine_name: {"part_count": int, "cycle_time": int_seconds}}
    """
    if not machines:
        return {}

    if mbd_machines is None or focas_machines is None:
        mbd_machines, focas_machines = _categorise_live_machines(db, machines)

    if not quiet:
        log_traceability(
            f"count_each_cycle_map: window={start_ist}→{end_ist} "
            f"mbd={sorted(mbd_machines)} focas={sorted(focas_machines)}",
            plant_code=plant_code,
        )

    result = {}

    # --- MBD path: cycle-doc counting (primary) + tag-snapshot (fallback) ---
    if mbd_machines:
        mbd_counts = _mbd_counts_for_window(
            db,
            mbd_machines,
            start_ist,
            end_ist,
            plant_code=plant_code,
            cycle_docs=mbd_docs,
        )
        mbd_duration_tags = _latest_mbd_tag_values(
            db,
            mbd_machines,
            start_ist,
            end_ist,
            MBD_DURATION_TAGS,
            plant_code=plant_code,
        )
        # cycle_time already embedded by _mbd_cycle_doc_counts; get tag-based ct
        # only when fallback path was used (no cycle_time key in count_info)
        mbd_ct_tag = {}
        needs_ct = [
            m for m in mbd_machines if mbd_counts.get(m, {}).get("cycle_time", 0) == 0
        ]
        if needs_ct:
            mbd_ct_tag = _mbd_cycle_time_for_window(
                db, needs_ct, start_ist, end_ist, plant_code=plant_code
            )

        for machine in mbd_machines:
            count_info = mbd_counts.get(machine, {})
            total = int(count_info.get("part_count") or 0)
            good = int(count_info.get("good_count") or total)
            reject = int(count_info.get("reject_count") or 0)
            ct = int(count_info.get("cycle_time") or 0)
            if ct == 0:
                ct = mbd_ct_tag.get(machine, 0)
            runtime = int(count_info.get("run_time_seconds") or 0)
            downtime = count_info.get("downtime_seconds")
            tag_runtime, tag_downtime = _mbd_duration_stats(mbd_duration_tags, machine)
            if runtime <= 0 and tag_runtime is not None:
                runtime = tag_runtime
            if downtime is None:
                downtime = tag_downtime
            if total > 0 or ct > 0:
                result[machine] = {
                    "part_count": total,
                    "good_count": good,
                    "reject_count": reject,
                    "cycle_time": ct,
                    "run_time_seconds": runtime,
                    "downtime_seconds": downtime,
                    "runtime_from_cycles": bool(
                        count_info.get("runtime_from_cycles") and runtime > 0
                    ),
                    "source": "Mbd",
                }

    # --- FOCAS path: cycle-doc counting (primary) + delta fallback ---
    if focas_machines:
        # Primary: count cycle docs
        focas_cycle = _focas_cycle_doc_counts(
            db,
            focas_machines,
            start_ist,
            end_ist,
            plant_code=plant_code,
            cycle_docs=focas_cycle_docs,
        )

        # Fallback for machines with no cycle docs: delta of total_part_count
        missing = [m for m in focas_machines if m not in focas_cycle]
        focas_delta = {}
        if missing:
            if focas_docs is not None:
                focas_delta = _focas_delta_snapshot_from_docs(
                    focas_docs,
                    missing,
                    start_ist,
                    end_ist,
                    inclusive_end=focas_inclusive_end,
                )
            else:
                focas_delta = _latest_focas_snapshot(
                    db,
                    missing,
                    start_ist,
                    end_ist,
                    plant_code=plant_code,
                    inclusive_end=focas_inclusive_end,
                )

        for machine in focas_machines:
            if machine in focas_cycle:
                count_info = focas_cycle[machine]
                total = int(count_info.get("part_count") or 0)
                good = int(count_info.get("good_count") or total)
                reject = int(count_info.get("reject_count") or 0)
                ct = int(count_info.get("cycle_time") or 0)
                if total > 0 or ct > 0:
                    result[machine] = {
                        "part_count": total,
                        "good_count": good,
                        "reject_count": reject,
                        "cycle_time": ct,
                        "run_time_seconds": int(
                            count_info.get("run_time_seconds") or 0
                        ),
                        "downtime_seconds": int(
                            count_info.get("downtime_seconds") or 0
                        ),
                        "runtime_from_cycles": bool(
                            count_info.get("runtime_from_cycles")
                        ),
                        "source": "machine_data",
                    }
            else:
                doc = focas_delta.get(machine) or {}
                total = _parse_numeric_value(doc.get("total_part_count"))
                good = _parse_numeric_value(doc.get("ok_part_count"))
                reject = _parse_numeric_value(doc.get("not_ok_part_count"))
                if total is None and good is not None and reject is not None:
                    total = max(0, good + reject)
                if good is None and total is not None:
                    good = max(0, total - (reject or 0))
                if reject is None and total is not None and good is not None:
                    reject = max(0, total - good)
                total = max(0, int(total or 0))
                good = max(0, int(good or 0))
                reject = max(0, int(reject or 0))
                ct = structured_doc_cycle_seconds(doc, plant_code=None)
                run_time = _machine_data_duration_seconds(doc.get("overall_run_time"))
                down_time = _machine_data_duration_seconds(doc.get("machine_down_time"))
                if total > 0 or ct > 0:
                    result[machine] = {
                        "part_count": total,
                        "good_count": good,
                        "reject_count": reject,
                        "cycle_time": ct,
                        "run_time_seconds": run_time,
                        "downtime_seconds": down_time,
                        "runtime_from_cycles": False,
                        "source": "machine_data",
                    }

    for machine in sorted(set(mbd_machines or []) | set(focas_machines or [])):
        item = result.get(machine, {})
        source = "Mbd" if machine in (mbd_machines or []) else "machine_data"

    return result


def _oee_rows_from_counts(
    db,
    cycle_map,
    start_utc,
    end_utc,
    date_value,
    plant_code,
    planned_map=None,
    shift=None,
    total_time_minutes=None,
    unplanned_map=None,
):
    """
    Build report rows using the same OEE formulas as calculate_bulk_oee_metrics_day
    in core.views.machinedashboard.
    """
    machines = list(cycle_map.keys())
    unplanned = unplanned_map
    if unplanned is None and machines:
        try:
            unplanned = get_unplanned_downtime_map(db, machines, start_utc, end_utc)
        except Exception:
            unplanned = {name: 0 for name in machines}
    unplanned = unplanned or {}

    now_utc = datetime.now(IST).astimezone(timezone.utc)
    if total_time_minutes is None:
        total_time_minutes = int((end_utc - start_utc).total_seconds() // 60)

    is_current_window = end_utc > now_utc
    if is_current_window:
        actual_runtime_seconds = max(
            0.0, (min(end_utc, now_utc) - start_utc).total_seconds()
        )
    else:
        actual_runtime_seconds = max(0.0, (total_time_minutes * 60) - 0)

    planned_map = planned_map or {}
    rows = []

    for machine, data in cycle_map.items():
        part_count = int(data.get("part_count") or 0)
        if part_count <= 0:
            continue
        good_count = int(data.get("good_count") or part_count)
        reject_count = int(data.get("reject_count") or max(0, part_count - good_count))
        if good_count + reject_count > part_count:
            reject_count = max(0, part_count - good_count)

        ct_seconds = int(data.get("cycle_time") or 0)

        source_runtime = max(0, int(data.get("run_time_seconds") or 0))
        runtime_is_cycle_sum = bool(data.get("runtime_from_cycles"))
        machine_runtime_seconds = actual_runtime_seconds
        has_source_runtime = False
        if source_runtime > 0:
            if runtime_is_cycle_sum:
                if source_runtime <= actual_runtime_seconds * 1.1:
                    machine_runtime_seconds = min(
                        float(source_runtime), actual_runtime_seconds
                    )
                    has_source_runtime = True
            else:
                scoped_runtime = shift_scoped_machine_runtime_seconds(
                    source_runtime, actual_runtime_seconds
                )
                if scoped_runtime > 0:
                    machine_runtime_seconds = scoped_runtime
                    has_source_runtime = True

        machine_runtime_seconds = max(0.0, machine_runtime_seconds)
        has_source_downtime = data.get("downtime_seconds") is not None
        source_downtime_seconds = max(0, int(data.get("downtime_seconds") or 0))
        if (
            has_source_downtime
            and source_downtime_seconds <= actual_runtime_seconds * 1.1
        ):
            downtime_seconds = min(
                float(source_downtime_seconds), actual_runtime_seconds
            )
        else:
            downtime_seconds = max(0.0, float(unplanned.get(machine) or 0) * 60)
        unplanned_minutes = downtime_seconds / 60.0
        idle_seconds = max(
            0.0,
            actual_runtime_seconds - machine_runtime_seconds - downtime_seconds,
        )

        if has_source_runtime:
            availability = (
                round((machine_runtime_seconds / actual_runtime_seconds) * 100, 2)
                if actual_runtime_seconds > 0
                else 0
            )
        else:
            total_runtime = max(
                0.0, (actual_runtime_seconds / 60.0) - unplanned_minutes
            )
            availability = (
                round((total_runtime / (actual_runtime_seconds / 60.0)) * 100, 2)
                if actual_runtime_seconds > 0
                else 0
            )
        performance = (
            round(((ct_seconds * part_count) / machine_runtime_seconds) * 100, 2)
            if machine_runtime_seconds > 0
            else 0
        )
        quality = round((good_count / part_count) * 100, 2) if part_count else 0

        availability = _clamp_oee_pct(availability)
        performance = _clamp_oee_pct(performance)
        quality = _clamp_oee_pct(quality)
        overall_oee = round((availability * performance * quality) / 10000, 2)

        row = {
            "machine": machine,
            "availability": availability,
            "performance": performance,
            "quality": quality,
            "overall_oee": overall_oee,
            "downtime": unplanned_minutes,
            "planned": planned_map.get(machine, 0) or 0,
            "actual": part_count,
            "total_count": part_count,
            "good_count": good_count,
            "reject_count": reject_count,
            "cycle_time_seconds": ct_seconds,
            "run_time_seconds": (
                int(machine_runtime_seconds) if has_source_runtime else None
            ),
            "downtime_seconds": int(downtime_seconds),
            "idle_time_seconds": int(idle_seconds) if has_source_runtime else None,
            "source_collection": data.get("source") or "unknown",
            "shift": shift,
            "created_at": date_value,
            "date": date_value,
        }
        rows.append(row)

    if rows:
        for row in rows:
            print(
                f"  {row.get('machine')}: source={row.get('source_collection')} "
                f"count={row.get('actual')} oee={row.get('overall_oee')}"
            )
    return rows


def _trace_collection_for_plant(plant_code):
    return "work_order_entry" if plant_code == "034" else "PCB_Trace"


def _daily_rows_from_bulk_oee(oee_results, business_day_start_ist, planned_map=None):
    """Map dashboard daywise OEE rows into daily report response shape."""
    planned_map = planned_map or {}
    date_label = business_day_start_ist.strftime("%Y-%m-%d")
    date_iso = business_day_start_ist.isoformat()
    rows = []
    for result in oee_results or []:
        actual = int(result.get("total_count") or 0)
        has_data = result.get("has_data", actual > 0)
        if not has_data and actual <= 0:
            continue
        rows.append(
            {
                "machine": result["machine"],
                "availability": result.get("availability", 0),
                "performance": result.get("performance", 0),
                "quality": result.get("quality", 0),
                "overall_oee": result.get("overall_oee", 0),
                "downtime": result.get("unplanned_downtime", 0),
                "planned": int(planned_map.get(result["machine"]) or 0),
                "actual": actual,
                "total_count": actual,
                "shift": None,
                "created_at": date_iso,
                "date": date_label,
            }
        )
    return rows


def fetch_live_daily_records(db, machines, start_date, end_date, plant_code):
    """
    One daywise row per non-FANUC machine.

    Window for report date D = [D shift1 start, D+1 shift1 start) from PMS_shift_config.

    MBD:   count Mbd cycle docs (cycle_start/stop) in window — each doc = 1 part.
    FOCAS: count machine_data cycle docs; fall back to total_part_count delta.
    FANUC: handled separately by fetch_fanuc_daily_records (mtlink_data).
    """
    if not machines:
        log_traceability(
            f"Non-FANUC OEE Daily skipped: no live machines, "
            f"date_range={start_date} to {end_date}",
            plant_code=plant_code,
        )
        return []

    rows = []
    mbd_machines, focas_machines = _categorise_live_machines(db, machines)

    for business_day in iter_report_dates(start_date, end_date):
        start_utc, end_utc, start_ist, end_ist, total_minutes = report_day_window_utc(
            business_day, plant_code
        )
        date_label = business_day.strftime("%Y-%m-%d")
        window_label = (
            f"{date_label} {start_ist.strftime('%H:%M')} IST → "
            f"{end_ist.strftime('%Y-%m-%d %H:%M')} IST"
        )
        try:
            planned_map = get_daily_planned_qty_map(
                db, machines or [], date_label, plant_code=plant_code
            )
            # FOCAS: pre-fetch with 2h lead-in for delta baseline
            focas_docs = (
                _fetch_focas_docs_for_range(db, focas_machines, start_ist, end_ist)
                if focas_machines
                else []
            )
            cycle_map = count_each_cycle_map(
                db,
                machines,
                start_ist,
                end_ist,
                plant_code=plant_code,
                mbd_machines=mbd_machines,
                focas_machines=focas_machines,
                focas_docs=focas_docs,
                quiet=True,
                focas_inclusive_end=True,
            )
            unplanned = get_unplanned_downtime_map(db, machines, start_utc, end_utc)
            day_rows = _oee_rows_from_counts(
                db,
                cycle_map,
                start_utc,
                end_utc,
                date_label,
                plant_code,
                planned_map=planned_map,
                total_time_minutes=total_minutes,
                unplanned_map=unplanned,
            )
            rows.extend(day_rows)
            log_traceability(
                f"Non-FANUC OEE Daily window={window_label}, machines={machines}, "
                f"mbd={sorted(mbd_machines)} focas={sorted(focas_machines)} "
                f"records={len(day_rows)}",
                plant_code=plant_code,
            )
        except Exception as exc:
            log_traceability(
                f"Live daily OEE failed for {date_label} ({window_label}): {exc}",
                plant_code=plant_code,
            )
            continue

    log_traceability(
        f"Non-FANUC OEE Daily: source=Mbd/machine_data, machines={machines}, "
        f"date_range={start_date} to {end_date}, records={len(rows)}",
        plant_code=plant_code,
    )
    return rows


def fetch_live_shift_records(
    db, machines, start_date, end_date, plant_code, selected_shift=None
):
    """Shiftwise OEE from Mbd + machine_data for non-FANUC machines.

    MBD:   count Mbd cycle docs per shift window — each doc = 1 part.
    FOCAS: count machine_data cycle docs; fall back to total_part_count delta.
    """
    if not machines:
        log_traceability(
            f"Non-FANUC OEE Shift skipped: no live machines, "
            f"date_range={start_date} to {end_date}",
            plant_code=plant_code,
        )
        return []

    windows = list(
        _iter_report_shift_windows(start_date, end_date, plant_code, selected_shift)
    )
    if not windows:
        return []

    range_start_ist = min(w[2] for w in windows)
    range_end_ist = max(w[3] for w in windows)
    range_start_utc = range_start_ist.astimezone(timezone.utc)
    range_end_utc = range_end_ist.astimezone(timezone.utc)

    mbd_machines, focas_machines = _categorise_live_machines(db, machines)

    mbd_cycle_docs = (
        _fetch_mbd_cycle_docs_for_range(
            db, mbd_machines, range_start_ist, range_end_ist
        )
        if mbd_machines
        else []
    )
    focas_cycle_docs = (
        _fetch_focas_cycle_docs_for_range(
            db, focas_machines, range_start_ist, range_end_ist
        )
        if focas_machines
        else []
    )
    # Pre-fetch FOCAS docs for the full range (includes 2h lead-in for delta baseline)
    focas_docs = (
        _fetch_focas_docs_for_range(db, focas_machines, range_start_ist, range_end_ist)
        if focas_machines
        else []
    )
    downtime_docs = _fetch_unplanned_downtime_docs(
        db, machines, range_start_utc, range_end_utc
    )

    rows = []
    for (
        date_label,
        shift_name,
        start_ist,
        end_ist,
        start_utc,
        end_utc,
        total_minutes,
    ) in windows:
        try:
            cycle_map = count_each_cycle_map(
                db,
                machines,
                start_ist,
                end_ist,
                plant_code=plant_code,
                mbd_machines=mbd_machines,
                focas_machines=focas_machines,
                mbd_docs=mbd_cycle_docs,
                focas_docs=focas_docs,
                quiet=True,
                focas_cycle_docs=focas_cycle_docs,
            )
            unplanned = _unplanned_downtime_map_from_docs(
                downtime_docs, machines, start_utc, end_utc
            )
            rows.extend(
                _oee_rows_from_counts(
                    db,
                    cycle_map,
                    start_utc,
                    end_utc,
                    date_label,
                    plant_code,
                    shift=shift_name,
                    total_time_minutes=total_minutes,
                    unplanned_map=unplanned,
                )
            )
        except Exception as exc:
            log_traceability(
                f"Live shift OEE failed for {date_label} {shift_name}: {exc}",
                plant_code=plant_code,
            )
            continue

    log_traceability(
        f"Non-FANUC OEE Shift: source=Mbd/machine_data, machines={len(machines)}, "
        f"windows={len(windows)}, records={len(rows)}",
        plant_code=plant_code,
    )
    return rows


def _fmt_report_date(value):
    if isinstance(value, datetime):
        if value.tzinfo:
            return value.astimezone(IST).strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return value


def dedupe_daily_rows(rows):
    """Keep first row per date + machine (Fanuc rows should be listed first)."""
    seen = set()
    out = []
    for row in rows:
        key = (
            _fmt_report_date(row.get("date") or row.get("created_at")),
            row.get("machine"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def merge_and_cap(rows, max_rows, include_shift=False):
    """Sort merged Fanuc + live rows (date desc, then shift/machine) and cap."""
    rows.sort(
        key=lambda row: (
            _fmt_report_date(row.get("date") or row.get("created_at")) or "",
            (row.get("shift") or "") if include_shift else "",
            row.get("machine") or "",
        )
    )
    rows.sort(
        key=lambda row: _fmt_report_date(row.get("date") or row.get("created_at"))
        or "",
        reverse=True,
    )
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    return rows, truncated


MAX_ROWS = 5000


def _param(request, key, default=""):
    value = request.POST.get(key) or request.GET.get(key) or default
    return str(value).strip()


def oee_report_source(request):
    """JSON OEE for MBD / Fanuc FOCAS from Mbd + machine_data (08:00–08:00)."""
    plant_code = request.session.get("plant_code")
    if not plant_code:
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"error": "Session expired"}, status=400)
        messages.error(request, "Your session has expired. Please log in again.")
        return redirect("signin")

    yesterday = datetime.now(IST).date() - timedelta(days=1)
    start_date = _param(request, "start_date", yesterday.strftime("%Y-%m-%d"))
    end_date = _param(request, "end_date", start_date)
    selected_machines = request.POST.getlist("machine")
    if not selected_machines:
        legacy_machine = _param(request, "machine")
        selected_machines = [legacy_machine] if legacy_machine else []
    selected_machines = [
        machine.strip() for machine in selected_machines if machine.strip()
    ]
    selected_shift = _param(request, "shift") or None
    report_type = (_param(request, "type", "daily") or "daily").lower()

    try:
        start_d = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_d = datetime.strptime(end_date, "%Y-%m-%d").date()
        if end_d < start_d:
            raise ValueError("End date must be on or after start date.")
    except ValueError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    db = get_db_connection_machine(plant_code)
    _fanuc_machines, live_machines = machines_by_oee_source(
        db, selected_machines or None
    )

    if report_type == "shift":
        rows = fetch_live_shift_records(
            db,
            live_machines,
            start_date,
            end_date,
            plant_code,
            selected_shift=selected_shift,
        )
        total_actual = _sum_actual_counts(rows)
        rows, truncated = merge_and_cap(rows, MAX_ROWS, include_shift=True)
    else:
        rows = fetch_live_daily_records(
            db, live_machines, start_date, end_date, plant_code
        )
        total_actual = _sum_actual_counts(rows)
        rows, truncated = merge_and_cap(rows, MAX_ROWS)

    payload = {
        "oee_results": rows,
        "fanuc_machines": _fanuc_machines,
        "live_machines": live_machines,
        "start_date": start_date,
        "end_date": end_date,
        "total_actual": total_actual,
        "type": report_type,
    }
    if truncated:
        payload["truncated"] = True
        payload["max_rows"] = MAX_ROWS
    return JsonResponse(payload)
