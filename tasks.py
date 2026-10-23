"""Celery tasks for OEE, WIP daily saves, and mail jobs."""

import traceback
from collections import defaultdict
from datetime import date, datetime
from datetime import time as dtime
from datetime import timedelta
from datetime import timezone
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from django.template.loader import render_to_string

from core.mongo_connection import get_db_connection_machine
from core.mtlink_collector import collect_mtlink_data  # noqa: F401 — Celery register
from core.oeetask import get_8am_shift_window_wip
from core.packing_trace_utils import count_packing_trace_fg
from core.plant_registry import PLANT_REGISTRY
from core.utility.log import log_traceability
from core.views.machinedashboard import (
    calculate_bulk_oee_metrics_day,
    get_daily_planned_qty_map,
    get_planned_actual_qty_map,
)

IST = ZoneInfo("Asia/Kolkata")
DAYWISE_SEGMENT_CHOICES = ("day", "night")
DAYWISE_SEGMENT_FULL = "full"


def get_shift_window_8am_ist(business_day):
    """08:00 IST → next day 08:00 IST (same window as machine dashboard)."""
    start_ist = datetime.combine(business_day, dtime(8, 0), tzinfo=IST)
    end_ist = start_ist + timedelta(days=1)
    return (
        start_ist,
        end_ist,
        start_ist.astimezone(timezone.utc),
        end_ist.astimezone(timezone.utc),
    )


def get_daywise_business_day(when=None):
    """Production day 08:00–08:00 IST; at 07:55 job use yesterday."""
    when = when or datetime.now(IST)
    if when.timetz() >= dtime(8, 0, tzinfo=IST):
        return when.date()
    return (when - timedelta(days=1)).date()


def get_daywise_document_date(business_day):
    """
    Stored `date` field: 08:00 IST on the business day (e.g. 21-May 08:00 for day 21).
    Day, night (00:00–07:55 next calendar morning), and full 08:00–08:00 all use this key.
    """
    return datetime.combine(business_day, dtime(8, 0), tzinfo=IST)


def get_daywise_date_label(business_day):
    """YYYY-MM-DD label for queries (e.g. '2026-05-21')."""
    return business_day.strftime("%Y-%m-%d")


def get_daywise_segments(business_day):
    """
    All segments share the same business_date label (e.g. 21st):
    day:   08:00–23:55 on calendar business_day
    night: 00:00–08:00 on calendar business_day+1 (still stored under business_day)
    full:  08:00 business_day → 08:00 next day (via get_shift_window_8am_ist)
    """
    day_start = datetime.combine(business_day, dtime(8, 0), tzinfo=IST)
    day_end = datetime.combine(business_day, dtime(23, 55), tzinfo=IST)
    next_day = business_day + timedelta(days=1)
    night_start = datetime.combine(next_day, dtime(0, 0), tzinfo=IST)
    night_end = datetime.combine(next_day, dtime(8, 0), tzinfo=IST)
    return [
        {
            "segment": "day",
            "start_ist": day_start,
            "end_ist": day_end,
            "start_utc": day_start.astimezone(timezone.utc),
            "end_utc": day_end.astimezone(timezone.utc),
            "minutes": int((day_end - day_start).total_seconds() / 60),
        },
        {
            "segment": "night",
            "start_ist": night_start,
            "end_ist": night_end,
            "start_utc": night_start.astimezone(timezone.utc),
            "end_utc": night_end.astimezone(timezone.utc),
            "minutes": int((night_end - night_start).total_seconds() / 60),
        },
    ]


def _record_date_key(record):
    bd = record.get("business_date")
    if isinstance(bd, str) and len(bd) >= 10:
        return datetime.strptime(bd[:10], "%Y-%m-%d").date()
    df = record.get("date")
    if isinstance(df, datetime):
        return df.astimezone(IST).date() if df.tzinfo else df.date()
    if isinstance(df, str) and len(df) >= 10:
        return datetime.strptime(df[:10], "%Y-%m-%d").date()
    return None


def merge_daywise_oee_records(records):
    """
    Merge day + night segment rows into one daily row per machine for the report UI.
    Legacy rows without segment pass through unchanged.
    """

    legacy = []
    grouped = defaultdict(list)
    for rec in records:
        seg = rec.get("segment")
        if seg in DAYWISE_SEGMENT_CHOICES or seg == DAYWISE_SEGMENT_FULL:
            dk = _record_date_key(rec)
            machine = rec.get("machine")
            if dk and machine:
                grouped[(machine, dk)].append(rec)
            else:
                legacy.append(rec)
        else:
            legacy.append(rec)

    merged = list(legacy)
    for (_machine, _dk), parts in grouped.items():
        full_rows = [p for p in parts if p.get("segment") == DAYWISE_SEGMENT_FULL]
        if full_rows:
            row = dict(full_rows[0])
            row.pop("segment", None)
            merged.append(row)
            continue

        parts = sorted(
            parts,
            key=lambda r: (
                0
                if r.get("segment") == "day"
                else 1 if r.get("segment") == "night" else 2
            ),
        )

        def _has_segment_data(p):
            return (
                float(p.get("runtime_minutes") or 0) > 0
                or int(p.get("total_parts") or 0) > 0
                or int(p.get("actual") or 0) > 0
                or float(p.get("downtime") or 0) > 0
            )

        active_parts = [p for p in parts if _has_segment_data(p)]
        if not active_parts:
            continue
        parts = active_parts

        if len(parts) == 1:
            row = dict(parts[0])
            row.pop("segment", None)
            merged.append(row)
            continue

        total_parts = sum(int(p.get("total_parts") or 0) for p in parts)
        downtime = sum(float(p.get("downtime") or 0) for p in parts)
        planned = max(int(p.get("planned") or 0) for p in parts)
        # Actual = sum of segment OEE counts (not trace row sums — avoids double count)
        actual = total_parts
        runtime_minutes = sum(float(p.get("runtime_minutes") or 0) for p in parts)

        def _wavg(field):
            if runtime_minutes <= 0:
                best = max(
                    parts,
                    key=lambda p: (
                        int(p.get("total_parts") or 0),
                        int(p.get("actual") or 0),
                        float(p.get(field) or 0),
                    ),
                )
                return round(float(best.get(field) or 0), 2)
            return round(
                sum(
                    float(p.get(field) or 0) * float(p.get("runtime_minutes") or 0)
                    for p in parts
                )
                / runtime_minutes,
                2,
            )

        availability = _wavg("availability")
        performance = _wavg("performance")
        quality = _wavg("quality")
        overall_oee = round((availability * performance * quality) / 10000, 2)

        base = dict(parts[0])
        full_start = datetime.combine(_dk, dtime(8, 0), tzinfo=IST)
        full_end = full_start + timedelta(days=1)
        base.update(
            {
                "segment": None,
                "availability": availability,
                "performance": performance,
                "quality": quality,
                "overall_oee": overall_oee,
                "total_parts": total_parts,
                "downtime": downtime,
                "planned": planned,
                "actual": actual,
                "runtime_minutes": runtime_minutes,
                "business_start": full_start,
                "business_end": full_end,
                "data_source": "day+night",
            }
        )
        merged.append(base)

    return merged


def _save_daywise_full_summary(
    db,
    plant_code,
    business_day,
    all_machines,
    business_date,
    day_collection,
    daily_planned_map,
    trace_col,
    plan_date_str,
):
    """
    One row per machine for full 08:00–08:00 IST.

    Prefer merging day + night segments (avoids live counters that only hold
    Shift 3 after morning reset). Fall back to direct 08:00–08:00 calc.
    """
    start_ist, end_ist, start_utc, end_utc = get_shift_window_8am_ist(business_day)
    total_minutes = int((end_ist - start_ist).total_seconds() / 60)

    # --- Primary: merge day + night when both segments exist ---
    handled = set()
    for machine in all_machines:
        day_doc = day_collection.find_one(
            {
                "machine": machine,
                "business_date": plan_date_str,
                "segment": "day",
            }
        )
        night_doc = day_collection.find_one(
            {
                "machine": machine,
                "business_date": plan_date_str,
                "segment": "night",
            }
        )
        # Need both halves of the 08:00–08:00 day; night-only ≈ Shift 3
        if not (day_doc and night_doc):
            continue

        parts = [day_doc, night_doc]
        total_parts = sum(int(p.get("total_parts") or 0) for p in parts)
        downtime = sum(float(p.get("downtime") or 0) for p in parts)
        planned_qty = max(int(p.get("planned") or 0) for p in parts)
        if planned_qty <= 0:
            planned_qty = int(daily_planned_map.get(machine) or 0)
        runtime_minutes = sum(float(p.get("runtime_minutes") or 0) for p in parts)

        def _wavg(field, _parts=parts, _runtime=runtime_minutes):
            if _runtime <= 0:
                best = max(
                    _parts,
                    key=lambda p: (
                        int(p.get("total_parts") or 0),
                        float(p.get(field) or 0),
                    ),
                )
                return round(float(best.get(field) or 0), 2)
            return round(
                sum(
                    float(p.get(field) or 0) * float(p.get("runtime_minutes") or 0)
                    for p in _parts
                )
                / _runtime,
                2,
            )

        availability = _wavg("availability")
        performance = _wavg("performance")
        quality = _wavg("quality")
        overall_oee = round((availability * performance * quality) / 10000, 2)

        day_doc_out = {
            "machine": machine,
            "date": business_date,
            "business_date": plan_date_str,
            "segment": DAYWISE_SEGMENT_FULL,
            "availability": availability,
            "performance": performance,
            "quality": quality,
            "overall_oee": overall_oee,
            "total_parts": total_parts,
            "planned": planned_qty,
            "actual": total_parts,
            "downtime": downtime,
            "runtime_minutes": round(runtime_minutes, 2),
            "cycle_time_minutes": day_doc.get("cycle_time_minutes"),
            "cycle_time_seconds": day_doc.get("cycle_time_seconds"),
            "data_source": "day+night",
            "business_start": start_ist,
            "business_end": end_ist,
            "created_at": datetime.now(IST),
        }
        day_collection.update_one(
            {
                "machine": machine,
                "business_date": plan_date_str,
                "segment": DAYWISE_SEGMENT_FULL,
            },
            {"$set": day_doc_out},
            upsert=True,
        )
        handled.add(machine)

    if handled:
        print(
            f"_save_daywise_full_summary: merged day+night for "
            f"{len(handled)} machines (plant {plant_code})"
        )

    remaining = [m for m in all_machines if m not in handled]
    if not remaining:
        return

    # --- Fallback: shiftwise sum and/or direct 08:00–08:00 calc ---
    oee_results = calculate_bulk_oee_metrics_day(
        db=db,
        machines=remaining,
        start_date_obj=start_utc,
        end_date_obj=end_utc,
        total_time_minutes=total_minutes,
        trace_collection=trace_col,
        allow_api_fallback=True,
        use_window_runtime=True,
        plant_code=plant_code,
    )

    # Prefer shiftwise sum (Shift 1+2+3) over live counters that may only hold Shift 3
    shiftwise_by_machine = {}
    shift_date_midnight = datetime.combine(business_day, dtime(0, 0), tzinfo=IST)
    for sdoc in db["PMS_shiftwise_oee"].find(
        {
            "date": shift_date_midnight,
            "shift": {"$in": ["Shift 1", "Shift 2", "Shift 3"]},
            "machine": {"$in": remaining},
        }
    ):
        m = sdoc.get("machine")
        if not m:
            continue
        bucket = shiftwise_by_machine.setdefault(
            m, {"total_parts": 0, "downtime": 0.0, "rows": []}
        )
        bucket["total_parts"] += int(sdoc.get("total_parts") or 0)
        bucket["downtime"] += float(sdoc.get("downtime") or 0)
        bucket["rows"].append(sdoc)

    total_by_machine = {r["machine"]: r.get("total_count", 0) for r in oee_results}
    pa_map = (
        get_planned_actual_qty_map(
            db,
            remaining,
            start_utc,
            end_utc,
            plan_date_str,
            trace_col,
            total_by_machine,
            plant_code=plant_code,
            daily_planned_map=daily_planned_map,
        )
        or {}
    )

    if not pa_map:
        print(
            f"_save_daywise_full_summary: pa_map is None/empty "
            f"for plant {plant_code}"
        )

    for result in oee_results:
        machine = result["machine"]
        pa = pa_map.get(machine, {"planned": 0, "actual": 0})
        planned_qty = int(daily_planned_map.get(machine) or 0)
        if planned_qty <= 0:
            planned_qty = int(pa.get("planned") or 0)

        sw = shiftwise_by_machine.get(machine)
        if sw and sw["total_parts"] > 0:
            actual_qty = sw["total_parts"]
            downtime = sw["downtime"]
            rows = sw["rows"]
            sw_runtime = sum(float(r.get("total_parts") or 0) for r in rows) or 1.0

            def _sw_wavg(field, _rows=rows, _rt=sw_runtime):
                return round(
                    sum(
                        float(r.get(field) or 0) * float(r.get("total_parts") or 0)
                        for r in _rows
                    )
                    / _rt,
                    2,
                )

            availability = _sw_wavg("availability")
            performance = _sw_wavg("performance")
            quality = _sw_wavg("quality")
            overall_oee = round((availability * performance * quality) / 10000, 2)
            data_source = "shiftwise_sum"
            runtime_minutes = round(result.get("runtime_minutes", 0), 2)
        else:
            actual_qty = int(result.get("total_count") or 0)
            if actual_qty <= 0:
                actual_qty = int(pa.get("actual") or 0)
            downtime = result["unplanned_downtime"]
            availability = result["availability"]
            performance = result["performance"]
            quality = result["quality"]
            overall_oee = result["overall_oee"]
            data_source = result.get("data_source", "No Data")
            runtime_minutes = round(result.get("runtime_minutes", 0), 2)

        if (
            not result.get("has_data")
            and actual_qty == 0
            and planned_qty == 0
            and not downtime
        ):
            continue

        day_doc = {
            "machine": machine,
            "date": business_date,
            "business_date": plan_date_str,
            "segment": DAYWISE_SEGMENT_FULL,
            "availability": availability,
            "performance": performance,
            "quality": quality,
            "overall_oee": overall_oee,
            "total_parts": actual_qty,
            "planned": planned_qty,
            "actual": actual_qty,
            "downtime": downtime,
            "runtime_minutes": runtime_minutes,
            "cycle_time_minutes": result["cycle_time_minutes"],
            "cycle_time_seconds": result["cycle_time_seconds"],
            "data_source": data_source,
            "business_start": start_ist,
            "business_end": end_ist,
            "created_at": datetime.now(IST),
        }

        day_collection.update_one(
            {
                "machine": machine,
                "business_date": plan_date_str,
                "segment": DAYWISE_SEGMENT_FULL,
            },
            {"$set": day_doc},
            upsert=True,
        )


def run_daywise_oee(segments=None, business_date_override=None):
    """
    Save daywise OEE per IST segment (stored separately; merged on report read).
      day:   08:00-23:55 — cron 23:55
      night: 00:00-08:00 — cron 07:55
      full:  08:00-08:00 — written after night (or day+night backfill)
    """
    if business_date_override is not None:
        ov = business_date_override
        if isinstance(ov, str):
            business_day = datetime.strptime(ov.strip(), "%Y-%m-%d").date()
        elif isinstance(ov, datetime):
            business_day = ov.date()
        elif isinstance(ov, date):
            business_day = ov
        else:
            raise TypeError(
                "business_date_override must be date, datetime, or YYYY-MM-DD str, "
                f"got {type(ov).__name__}"
            )
    else:
        business_day = get_daywise_business_day()

    if segments is None:
        segments_to_run = DAYWISE_SEGMENT_CHOICES
    else:
        segments_to_run = tuple(segments)
        bad = set(segments_to_run) - set(DAYWISE_SEGMENT_CHOICES)
        if bad:
            raise ValueError(f"Invalid segment(s): {bad}. Use 'day' and/or 'night'.")

    business_date = get_daywise_document_date(business_day)
    business_date_label = get_daywise_date_label(business_day)
    all_segments = get_daywise_segments(business_day)
    segments_list = [s for s in all_segments if s["segment"] in segments_to_run]
    # Full 08:00–08:00 only after night completes (or day+night backfill)
    save_full = "night" in segments_to_run

    print(
        f"Daywise OEE business_day={business_day}, "
        f"segments={[s['segment'] for s in segments_list]}, "
        f"save_full={save_full}"
    )

    for plant_code in settings.PLANT_CODES:
        try:
            print(f"im into {plant_code} database")

            db = get_db_connection_machine(plant_code)

            all_machines = sorted(db["PMS_machine_info"].distinct("machine_name"))
            trace_col = "work_order_entry" if plant_code == "034" else "PCB_Trace"
            plan_date_str = business_day.strftime("%Y-%m-%d")
            day_collection = db["daily_oee_report"]
            daily_planned_map = get_daily_planned_qty_map(
                db, all_machines, plan_date_str, plant_code=plant_code
            )
            if daily_planned_map is None:
                print(f"daily_planned_map is None for plant {plant_code}")
                daily_planned_map = {}
            full_business_segments = get_daywise_segments(business_day)
            full_start_utc = full_business_segments[0]["start_utc"]
            full_end_utc = full_business_segments[-1]["end_utc"]
            full_day_planned_map = get_planned_actual_qty_map(
                db,
                all_machines,
                full_start_utc,
                full_end_utc,
                plan_date_str,
                trace_col,
                {},
                plant_code=plant_code,
                daily_planned_map=daily_planned_map,
            )
            if full_day_planned_map is None:
                print(f"full_day_planned_map is None for plant {plant_code}")
                full_day_planned_map = {}
            if "day" in segments_to_run:
                day_collection.delete_many(
                    {
                        "business_date": business_date_label,
                        "segment": {"$exists": False},
                    }
                )
                day_collection.delete_many(
                    {
                        "date": business_date,
                        "business_date": {"$exists": False},
                        "segment": {"$exists": False},
                    }
                )

            for seg in segments_list:
                oee_results = calculate_bulk_oee_metrics_day(
                    db=db,
                    machines=all_machines,
                    start_date_obj=seg["start_utc"],
                    end_date_obj=seg["end_utc"],
                    total_time_minutes=seg["minutes"],
                    trace_collection=trace_col,
                    allow_api_fallback=True,
                    use_window_runtime=True,
                    plant_code=plant_code,
                )

                total_by_machine = {
                    r["machine"]: r.get("total_count", 0) for r in oee_results
                }
                planned_actual_map = get_planned_actual_qty_map(
                    db,
                    all_machines,
                    seg["start_utc"],
                    seg["end_utc"],
                    plan_date_str,
                    trace_col,
                    total_by_machine,
                    plant_code=plant_code,
                    daily_planned_map=daily_planned_map,
                )
                if planned_actual_map is None:
                    print(f"planned_actual_map is None for plant {plant_code}")
                    planned_actual_map = {}
                for result in oee_results:
                    machine = result["machine"]
                    pa = planned_actual_map.get(machine, {"planned": 0, "actual": 0})

                    full_pa = full_day_planned_map.get(
                        machine, {"planned": 0, "actual": 0}
                    )
                    planned_qty = int(daily_planned_map.get(machine) or 0)
                    if planned_qty <= 0:
                        planned_qty = int(full_pa.get("planned") or 0)
                    if planned_qty <= 0:
                        planned_qty = int(pa.get("planned") or 0)
                    # Actual must match machine dashboard (OEE total_count / MBD·trace path)
                    actual_qty = int(result.get("total_count") or 0)
                    if actual_qty <= 0:
                        actual_qty = int(pa.get("actual") or 0)
                    if actual_qty <= 0:
                        actual_qty = int(full_pa.get("actual") or 0)

                    if (
                        not result.get("has_data")
                        and actual_qty == 0
                        and planned_qty == 0
                        and not result.get("unplanned_downtime")
                    ):
                        continue

                    day_doc = {
                        "machine": machine,
                        "date": business_date,
                        "business_date": business_date_label,
                        "segment": seg["segment"],
                        "availability": result["availability"],
                        "performance": result["performance"],
                        "quality": result["quality"],
                        "overall_oee": result["overall_oee"],
                        "total_parts": actual_qty,
                        "planned": planned_qty,
                        "actual": actual_qty,
                        "downtime": result["unplanned_downtime"],
                        "runtime_minutes": round(result.get("runtime_minutes", 0), 2),
                        "cycle_time_minutes": result["cycle_time_minutes"],
                        "cycle_time_seconds": result["cycle_time_seconds"],
                        "data_source": result.get("data_source", "No Data"),
                        "business_start": seg["start_ist"],
                        "business_end": seg["end_ist"],
                        "created_at": datetime.now(IST),
                    }

                    day_collection.update_one(
                        {
                            "machine": machine,
                            "business_date": business_date_label,
                            "segment": seg["segment"],
                        },
                        {"$set": day_doc},
                        upsert=True,
                    )

            # Full 08:00-08:00 only after night (complete production day)
            if save_full:
                _save_daywise_full_summary(
                    db,
                    plant_code,
                    business_day,
                    all_machines,
                    business_date,
                    day_collection,
                    daily_planned_map,
                    trace_col,
                    plan_date_str,
                )
                print(
                    f"  ✔ Full-day summary (08:00–08:00 IST) saved for plant {plant_code}"
                )

            print(
                f"✔ Daywise OEE saved for plant {plant_code} "
                f"({business_day}, {list(segments_to_run)})"
            )

        except Exception as e:
            print(f"❌ Failed Daywise save for plant {plant_code}: {e}")
            traceback.print_exc()


@shared_task(name="daywise_oee_day")
def save_daywise_oee_day():
    """Day segment 08:00-23:55 IST — schedule 23:55. Full saved later with night."""
    run_daywise_oee(segments=["day"])
    return "Daywise OEE (day segment) saved successfully"


@shared_task(name="daywise_oee_night")
def save_daywise_oee_night():
    """Night segment 00:00-08:00 IST + full 08:00-08:00 — schedule 07:55."""
    run_daywise_oee(segments=["night"])
    return "Daywise OEE (night segment + full summary) saved successfully"


@shared_task(name="daywise_oee")
def save_daywise_oee():
    """Save both segments + full (manual / backfill)."""
    run_daywise_oee()
    return "Daywise OEE saved successfully"


def run_wip_daily(business_date_override=None):
    """
    Save WIP dashboard data for the business day window.

    business_date_override: None (use IST 8 AM cutoff), or datetime.date,
    datetime.datetime, or 'YYYY-MM-DD' string for manual / mgmt command runs.
    """
    if business_date_override is not None:
        ov = business_date_override
        if isinstance(ov, str):
            business_day = datetime.strptime(ov.strip(), "%Y-%m-%d").date()
        elif isinstance(ov, datetime):
            business_day = ov.date()
        elif isinstance(ov, date):
            business_day = ov
        else:
            raise TypeError(
                "business_date_override must be date, datetime, or YYYY-MM-DD str, "
                f"got {type(ov).__name__}"
            )
    else:
        kolkata_now = datetime.now(ZoneInfo("Asia/Kolkata"))
        current_time = kolkata_now.time()

        # Determine the business day (7:57 AM cutoff - before 8 AM means previous day)
        if current_time >= dtime(8, 0):
            business_day = kolkata_now.date()
        else:
            business_day = (kolkata_now - timedelta(days=1)).date()

    print(f"📊 WIP Dashboard: Saving data for business day: {business_day}")

    for plant_code in settings.PLANT_CODES:
        try:
            db = get_db_connection_machine(plant_code)

            # Get shift window for business day (8 AM to next day 8 AM IST)
            shift_start_utc, shift_end_utc, shift_start_ist, shift_end_ist = (
                get_8am_shift_window_wip(business_day.strftime("%Y-%m-%d"))
            )

            # Collections
            trace_collection = db["PCB_Trace"]
            cost_collection = db["PMS_partcosting"]
            pre_packing_collection = db["pre_packing"]
            packing_trace_collection = db["Packing_Trace"]

            # ===== Calculate metrics =====

            # 1. Production data (from PCB_Trace)
            match_stage = {
                "timestamp": {"$gte": shift_start_utc, "$lt": shift_end_utc},
                "flag": {"$in": [0, 1]},
            }

            production_pipeline = [
                {"$match": match_stage},
                {"$sort": {"timestamp": -1}},
                {
                    "$group": {
                        "_id": "$work_order",
                        "part": {"$first": "$part_name"},
                        "work_order_count": {"$first": "$work_order_count"},
                        "rejected_count": {"$first": "$rejected_count"},
                    }
                },
                {
                    "$group": {
                        "_id": "$part",
                        "work_order_count": {"$sum": "$work_order_count"},
                        "rejected_count": {"$sum": "$rejected_count"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "part": "$_id",
                        "work_order_count": 1,
                        "rejected_count": 1,
                        "production_count": {
                            "$subtract": ["$work_order_count", "$rejected_count"]
                        },
                    }
                },
            ]
            production_data = list(trace_collection.aggregate(production_pipeline))

            # Calculate totals
            total_rejected = sum(item["rejected_count"] for item in production_data)
            total_production = sum(item["production_count"] for item in production_data)
            total_part_count = len(production_data)

            # 2. Planned Qty from pre_packing (same as dashboard)
            pre_packing_query = {
                "submitted_at": {"$gte": shift_start_ist, "$lt": shift_end_ist}
            }
            pre_packing_pipeline = [
                {"$match": pre_packing_query},
                {"$group": {"_id": None, "total_qr": {"$sum": 1}}},
            ]
            pre_packing_result = list(
                pre_packing_collection.aggregate(pre_packing_pipeline)
            )
            # Shift-wide pre_packing QR count (matches WIP dashboard "Planned Qty" KPI).
            # Do not reuse name `planned_qty` in the machine loop below — it would overwrite this.
            planned_qty_pre_packing = (
                pre_packing_result[0]["total_qr"] if pre_packing_result else 0
            )

            # 3. Total Production FG from Packing_Trace (must match wipdashboard.py)

            total_fg = count_packing_trace_fg(
                packing_trace_collection,
                shift_start_ist.replace(tzinfo=None),
                shift_end_ist.replace(tzinfo=None),
                utc_start=shift_start_ist.astimezone(dt_timezone.utc),
                utc_end=shift_end_ist.astimezone(dt_timezone.utc),
            )

            # Fetch daily_planning_data for machine_plan_actual
            daily_planning_collection = db["PMS_daily_planning"]
            daily_planning_query = {
                "plant_code": plant_code,
                "plan_date": business_day.strftime("%Y-%m-%d"),
            }
            daily_planning_data = list(
                daily_planning_collection.find(daily_planning_query)
            )

            # 4. Revenue loss from rejections
            rejection_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": "$work_order",
                        "total_rejected": {"$sum": "$rejected_count"},
                        "part": {"$first": "$part_name"},
                    }
                },
            ]
            rejection_data = list(trace_collection.aggregate(rejection_pipeline))

            total_revenue_loss = 0
            for item in rejection_data:
                part = item.get("part")
                total_rejected = item.get("total_rejected", 0)
                cost_doc = cost_collection.find_one({"part_name": part})
                if cost_doc and "partcost" in cost_doc:
                    part_cost = cost_doc["partcost"]
                    total_revenue_loss += total_rejected * part_cost

            # 5. Machine-wise data
            machine_data_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": {"machine_id": "$machine_id"},
                        "parts": {"$addToSet": "$part_name"},
                        "work_orders": {"$addToSet": "$work_order"},
                        "total_records": {"$sum": 1},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "machine_id": "$_id.machine_id",
                        "parts": 1,
                        "work_orders": 1,
                        "total_records": 1,
                        "parts_count": {"$size": "$parts"},
                        "work_orders_count": {"$size": "$work_orders"},
                    }
                },
                {"$sort": {"machine_id": 1}},
            ]
            machine_data = list(trace_collection.aggregate(machine_data_pipeline))

            # 6. Machine description OK qty
            machine_desc_ok_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": "$machine_description",
                        "ok_qty": {"$sum": "$ok_qty"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "machine_description": {"$ifNull": ["$_id", "Unknown"]},
                        "ok_qty": 1,
                    }
                },
                {"$sort": {"ok_qty": -1}},
            ]
            machine_desc_ok_data = list(
                trace_collection.aggregate(machine_desc_ok_pipeline)
            )

            # 7. Parts used data
            parts_used_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": "$part_name",
                        "work_orders": {"$addToSet": "$work_order"},
                    }
                },
                {
                    "$project": {
                        "part_name": "$_id",
                        "work_order_count": {"$size": "$work_orders"},
                        "_id": 0,
                    }
                },
            ]
            parts_used_data = list(trace_collection.aggregate(parts_used_pipeline))

            # 8. Work order progress
            work_order_progress_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": "$work_order",
                        "part": {"$first": "$part_name"},
                        "total_count": {"$first": "$work_order_count"},
                        "rejected_count": {
                            "$sum": {
                                "$cond": [
                                    {"$in": ["$flag", [0, 1]]},
                                    "$rejected_count",
                                    0,
                                ]
                            }
                        },
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "work_order": "$_id",
                        "part": 1,
                        "total_count": 1,
                        "rejected_count": 1,
                        "passed_count": {
                            "$subtract": ["$total_count", "$rejected_count"]
                        },
                    }
                },
            ]
            work_order_progress = list(
                trace_collection.aggregate(work_order_progress_pipeline)
            )

            # 9. Machine plan vs actual data (using already fetched daily_planning_data)
            machine_actual_pipeline = [
                {"$match": match_stage},
                {
                    "$group": {
                        "_id": {
                            "machine_id": "$machine_id",
                            "work_order": "$work_order",
                        },
                        "work_order_count": {"$first": "$work_order_count"},
                    }
                },
                {
                    "$group": {
                        "_id": "$_id.machine_id",
                        "actual_qty": {"$sum": "$work_order_count"},
                    }
                },
                {"$project": {"_id": 0, "machine": "$_id", "actual_qty": 1}},
            ]
            machine_actual_data = list(
                trace_collection.aggregate(machine_actual_pipeline)
            )

            # Build machine plan vs actual
            machine_plan_actual = []

            # First, add machines from daily planning (with planned qty)
            for plan in daily_planning_data:
                machine_name = plan.get("machine", "")
                machine_planned_qty = int(plan.get("output_per_day", 0) or 0)
                if not machine_planned_qty:
                    machine_planned_qty = int(
                        plan.get("schedule_qty") or plan.get("total_qty") or 0
                    )

                # Find actual qty
                actual_qty = 0
                for act in machine_actual_data:
                    if act.get("machine") == machine_name:
                        actual_qty = act.get("actual_qty", 0) or 0
                        break

                machine_plan_actual.append(
                    {
                        "machine": machine_name,
                        "planned_qty": machine_planned_qty,
                        "actual_qty": actual_qty,
                    }
                )

            # Add machines with actual but no plan
            for act in machine_actual_data:
                machine_name = act.get("machine", "")
                if not any(
                    m.get("machine") == machine_name for m in machine_plan_actual
                ):
                    machine_plan_actual.append(
                        {
                            "machine": machine_name,
                            "planned_qty": 0,
                            "actual_qty": act.get("actual_qty", 0) or 0,
                        }
                    )

            # Sort by machine name
            machine_plan_actual = sorted(
                machine_plan_actual, key=lambda x: x.get("machine", "")
            )

            # Create the document to save
            wip_doc = {
                "business_date": business_day.strftime("%Y-%m-%d"),
                "shift_start_ist": shift_start_ist.isoformat(),
                "shift_end_ist": shift_end_ist.isoformat(),
                "created_at": datetime.now(ZoneInfo("Asia/Kolkata")),
                "plant_code": plant_code,
                # Summary metrics
                "total_work_order": total_fg,
                "total_rejected": total_rejected,
                "total_production": total_production,
                "total_part_count": total_part_count,
                "planned_qty": planned_qty_pre_packing,
                "total_fg": total_fg,
                "total_revenue_loss": total_revenue_loss,
                # Detailed data
                "production_data": production_data,
                "machine_data": machine_data,
                "machine_desc_ok_data": machine_desc_ok_data,
                "parts_used_data": parts_used_data,
                "work_order_progress": work_order_progress,
                "machine_plan_actual": machine_plan_actual,
            }

            # Save to collection
            wip_collection = db["PMS_wip_daily"]
            wip_collection.update_one(
                {
                    "business_date": business_day.strftime("%Y-%m-%d"),
                    "plant_code": plant_code,
                },
                {"$set": wip_doc},
                upsert=True,
            )

            print(f"✔ WIP daily saved for plant {plant_code}, date: {business_day}")

        except Exception as e:
            print(f"❌ Failed WIP daily save for plant {plant_code}: {e}")

            traceback.print_exc()

    return f"WIP daily saved for business day: {business_day}"


@shared_task()
def save_wip_daily(_):
    """Celery wrapper: run WIP daily save."""

    return run_wip_daily()


TOOL_CALIB_CATEGORY = "tool_calibration"


def _plant_name(plant_code):
    try:

        for p in PLANT_REGISTRY:
            if p["code"] == plant_code:
                return p["name"]
    except Exception:
        log_traceability(
            request=None,
            level=2,
            message=f"Error finding plant name for code {plant_code}: {traceback.print_exc()}",
        )
    return f"Plant {plant_code}"


def _load_mail_config(plant_code):
    """Return (to, cc, bcc, is_active) for tool_calibration config of a plant."""
    db = get_db_connection_machine(plant_code)
    cfg = db["prism_email_config"].find_one({"category": TOOL_CALIB_CATEGORY}) or {}
    return (
        _split_emails(cfg.get("to_emails", "")),
        _split_emails(cfg.get("cc_emails", "")),
        _split_emails(cfg.get("bcc_emails", "")),
        bool(cfg.get("is_active", True)),
    )


def _fetch_tool_status_rows(plant_code, today, due_within_days):
    db = get_db_connection_machine(plant_code)
    tools = list(db["tool_fixture_master"].find().sort("next_calibration_date", 1))

    overdue_rows = []
    due_soon_rows = []

    for t in tools:
        next_cal = t.get("next_calibration_date")
        if not next_cal:
            continue

        next_date = next_cal.date() if hasattr(next_cal, "date") else next_cal
        if not isinstance(next_date, date):
            continue

        balance = t.get("balance_qty", t.get("calibration_qty", 0)) or 0

        row = {
            "tool_number": t.get("tool_number", "-"),
            "type": t.get("type", "-"),
            "part_no": t.get("part_no", "-"),
            "process": t.get("process", "-"),
            "issued_to": t.get("issued_to", "-"),
            "next_calibration_date": next_date.strftime("%d-%m-%Y"),
            "balance_qty": balance,
            "overdue_days": (next_date - today).days,
        }

        # 🔴 OVERDUE → ALL past dates
        if next_date < today or balance <= 0:
            overdue_rows.append(row)

        # 🟢 DUE SOON → only future within range
        elif 0 <= (next_date - today).days <= due_within_days:
            due_soon_rows.append(row)

    return overdue_rows, due_soon_rows


def _fetch_completed_rows(plant_code, start_dt, end_dt):
    """Fetch tool_calibration_history entries within the given IST window."""
    db = get_db_connection_machine(plant_code)
    rows = []
    cursor = (
        db["tool_calibration_history"]
        .find(
            {
                "status": {"$ne": "Rejection"},
                "calibration_date": {"$gte": start_dt, "$lte": end_dt},
            }
        )
        .sort("calibration_date", 1)
    )
    for c in cursor:
        cdate = c.get("calibration_date")
        rows.append(
            {
                "tool_number": c.get("tool_number", "-"),
                "status": c.get("status", "-"),
                "service_by": c.get("service_by", "-"),
                "issued_location": c.get("issued_location", "-"),
                "calibration_date": (
                    cdate.strftime("%d-%m-%Y") if hasattr(cdate, "strftime") else "-"
                ),
            }
        )
    return rows


def _deliver_mail(_, to, cc, bcc, subject, html_body):
    """Send a pre-rendered HTML mail via the PRISM mail API."""
    payload = {
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body": html_body,
        "display_name": "PRISM",
    }
    _send_mail_api(payload)


def run_tool_calibration_pending_mail(due_within_days=7):
    """Run only overdue + due-soon tool calibration mails in one call."""
    overdue = run_tool_calibration_overdue_mail()
    due_soon = run_tool_calibration_due_soon_mail(due_within_days=due_within_days)
    return {
        "sent": (
            [f"[overdue] {x}" for x in overdue["sent"]]
            + [f"[due_soon] {x}" for x in due_soon["sent"]]
        ),
        "failed": (
            [f"[overdue] {x}" for x in overdue["failed"]]
            + [f"[due_soon] {x}" for x in due_soon["failed"]]
        ),
    }


def run_tool_calibration_mail(due_within_days=7, completed_within_days=7):
    """Back-compat wrapper that runs overdue + due-soon + completed in sequence."""
    overdue = run_tool_calibration_overdue_mail()
    due_soon = run_tool_calibration_due_soon_mail(due_within_days=due_within_days)
    completed = run_tool_calibration_completed_mail(
        completed_within_days=completed_within_days
    )
    return {
        "sent": (
            [f"[overdue] {x}" for x in overdue["sent"]]
            + [f"[due_soon] {x}" for x in due_soon["sent"]]
            + [f"[completed] {x}" for x in completed["sent"]]
        ),
        "failed": (
            [f"[overdue] {x}" for x in overdue["failed"]]
            + [f"[due_soon] {x}" for x in due_soon["failed"]]
            + [f"[completed] {x}" for x in completed["failed"]]
        ),
    }


REPORT_CATEGORY_TITLES = {
    "report_workorder": "Workorder Report",
    "report_heattreatment": "Heattreatment Report",
    "report_oee_shiftwise": "OEE Shiftwise Report",
    "report_oee_daily": "OEE Daily Report",
    "report_downtime": "Downtime Report",
    "report_wip": "WIP Report",
    "report_packing": "Packing & Assembly Report",
}


def _fmt_mail_value(value):
    if value is None or value == "":
        return "-"
    if hasattr(value, "strftime"):
        try:
            return value.strftime("%d-%m-%Y %H:%M")
        except Exception:
            return str(value)
    if isinstance(value, list):
        if not value:
            return "-"
        return ", ".join(str(v) for v in value[:6])
    return str(value)


def _pick_value(doc, keys, default="-"):
    for key in keys:
        if key in doc and doc.get(key) not in (None, ""):
            return doc.get(key)
    return default


def _today_window_ist():
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    today = now.date()
    start_dt = datetime.combine(today, dtime.min, tzinfo=ist)
    end_dt = datetime.combine(today, dtime.max, tzinfo=ist)
    return today, today.strftime("%Y-%m-%d"), start_dt, end_dt


def _report_workorder_rows(db):
    _, _, start_dt, end_dt = _today_window_ist()
    docs = list(
        db["PCB_Trace"].find(
            {"timestamp": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0},
        )
    )
    headers = [
        ("date", "Date"),
        ("machine_id", "Machine ID"),
        ("process_description", "Process Description"),
        ("work_order", "Work Order"),
        ("wo_qty", "WO Qty"),
        ("ok_qty", "Ok Qty"),
        ("rejected_qty", "Rejected Qty"),
    ]
    rows = []
    for d in docs:
        rows.append(
            {
                "date": _fmt_mail_value(
                    _pick_value(d, ["timestamp", "created_at", "date"])
                )[:10],
                "machine_id": _fmt_mail_value(
                    _pick_value(d, ["machine_id", "machine"])
                ),
                "process_description": _fmt_mail_value(
                    _pick_value(d, ["machine_description", "process_name"])
                ),
                "work_order": _fmt_mail_value(
                    _pick_value(d, ["work_order", "workorder", "workorder_no"])
                ),
                "wo_qty": _fmt_mail_value(
                    _pick_value(d, ["work_order_count", "wo_qty"])
                ),
                "ok_qty": _fmt_mail_value(d.get("ok_qty")),
                "rejected_qty": _fmt_mail_value(
                    _pick_value(d, ["rejected_count", "rejection_qty"])
                ),
            }
        )
    return headers, rows


def _report_heattreatment_rows(db):
    _, _, start_dt, end_dt = _today_window_ist()
    docs = list(
        db["heat_treatment"].find(
            {"start_time": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0},
        )
    )
    headers = [
        ("s_no", "S.No"),
        ("chargeno", "Charge No"),
        ("process_name", "Process"),
        ("batch_no", "Batch No"),
        ("operator_id", "Operator"),
        ("scanned_machine", "Machine"),
        ("start_time", "Start Time"),
        ("end_time", "End Time"),
        ("work_orders", "Work Orders"),
    ]
    rows = []
    for idx, d in enumerate(docs, start=1):
        batch_nos = [
            b.get("batch_no", "") for b in d.get("batch_no", []) if b.get("batch_no")
        ]
        work_orders = [
            wo.get("workorder_no", "")
            for wo in d.get("work_orders", [])
            if wo.get("workorder_no")
        ]
        rows.append(
            {
                "s_no": idx,
                "chargeno": _fmt_mail_value(d.get("chargeno")),
                "process_name": _fmt_mail_value(d.get("process_name")),
                "batch_no": _fmt_mail_value(batch_nos),
                "operator_id": _fmt_mail_value(d.get("operator_id")),
                "scanned_machine": _fmt_mail_value(d.get("scanned_machine")),
                "start_time": _fmt_mail_value(d.get("start_time")),
                "end_time": _fmt_mail_value(d.get("end_time")),
                "work_orders": _fmt_mail_value(work_orders),
            }
        )
    return headers, rows


def _report_oee_shiftwise_rows(db):
    _, _, start_dt, end_dt = _today_window_ist()
    docs = list(
        db["PMS_shiftwise_oee"].find(
            {"created_at": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0},
        )
    )
    headers = [
        ("created_at", "Created At"),
        ("shift", "Shift"),
        ("machine", "Machine"),
        ("overall_oee", "Overall OEE"),
        ("availability", "Availability (%)"),
        ("performance", "Performance (%)"),
        ("quality", "Quality (%)"),
        ("total_count", "Total Count"),
        ("downtime", "Downtime (min)"),
    ]
    rows = []
    for d in docs:
        rows.append(
            {
                "created_at": _fmt_mail_value(d.get("created_at"))[:10],
                "machine": _fmt_mail_value(d.get("machine")),
                "shift": _fmt_mail_value(d.get("shift")),
                "availability": _fmt_mail_value(d.get("availability")),
                "performance": _fmt_mail_value(d.get("performance")),
                "quality": _fmt_mail_value(d.get("quality")),
                "overall_oee": _fmt_mail_value(d.get("overall_oee")),
                "total_count": _fmt_mail_value(
                    _pick_value(d, ["total_parts", "total_count"])
                ),
                "downtime": _fmt_mail_value(d.get("downtime")),
            }
        )
    return headers, rows


def _report_oee_daily_rows(db):
    _, _, start_dt, end_dt = _today_window_ist()
    docs = list(
        db["daily_oee_report"].find(
            {"created_at": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0},
        )
    )
    headers = [
        ("date", "Date"),
        ("machine", "Machine"),
        ("overall_oee", "Overall OEE"),
        ("availability", "Availability (%)"),
        ("performance", "Performance (%)"),
        ("quality", "Quality (%)"),
        ("total_count", "Total Count"),
        ("planned", "Planned"),
        ("actual", "Actual"),
        ("downtime", "Downtime (min)"),
    ]
    rows = []
    for d in docs:
        rows.append(
            {
                "date": _fmt_mail_value(_pick_value(d, ["created_at", "date"]))[:10],
                "machine": _fmt_mail_value(d.get("machine")),
                "overall_oee": _fmt_mail_value(d.get("overall_oee")),
                "availability": _fmt_mail_value(d.get("availability")),
                "performance": _fmt_mail_value(d.get("performance")),
                "quality": _fmt_mail_value(d.get("quality")),
                "total_count": _fmt_mail_value(
                    _pick_value(d, ["total_parts", "total_count"])
                ),
                "planned": _fmt_mail_value(d.get("planned")),
                "actual": _fmt_mail_value(d.get("actual")),
                "downtime": _fmt_mail_value(d.get("downtime")),
            }
        )
    return headers, rows


def _report_downtime_rows(db):
    _, today_str, _, _ = _today_window_ist()
    docs = list(db["PMS_unplanneddowntime"].find({"date": today_str}, {"_id": 0}))
    headers = [
        ("date", "Date"),
        ("machine_name", "Machine Name"),
        ("downtime_mins_hrs", "Downtime Min/Hrs"),
        ("downtime_reason", "Downtime Reason"),
    ]
    rows = []
    for d in docs:
        entries = d.get("unplanned_downtime", [])
        total_minutes = (
            sum(int(x.get("minutes", 0) or 0) for x in entries)
            if isinstance(entries, list)
            else 0
        )
        reasons = []
        if isinstance(entries, list):
            for x in entries:
                text = x.get("short_text") or x.get("Text_for_Code") or "-"
                mins = x.get("minutes", 0)
                reasons.append(f"{text}: {mins} mins")
        mins_hrs = f"{round(total_minutes / 60, 2)} hrs"
        rows.append(
            {
                "date": _fmt_mail_value(d.get("date")),
                "machine_name": _fmt_mail_value(
                    _pick_value(d, ["process_name", "machine_name"])
                ),
                "downtime_mins_hrs": mins_hrs,
                "downtime_reason": _fmt_mail_value(reasons),
            }
        )
    return headers, rows


def _report_wip_rows(db):
    _, today_str, _, _ = _today_window_ist()
    docs = list(db["PMS_wip_daily"].find({"business_date": today_str}, {"_id": 0}))
    part_to_process = {}
    for proj in db["PMS_project"].find({}, {"_id": 0, "project_name": 1, "partnos": 1}):
        process_name = proj.get("project_name", "")
        for p in proj.get("partnos", []):
            part_no = str(p.get("part_number", "")).strip().upper()
            if part_no:
                part_to_process[part_no] = process_name

    part_to_machines = {}
    for dp in db["PMS_daily_planning"].find(
        {"business_date": today_str}, {"_id": 0, "part_no": 1, "machine": 1}
    ):
        part_no = str(dp.get("part_no", "")).strip().upper()
        machine = str(dp.get("machine", "")).strip()
        if part_no and machine:
            part_to_machines.setdefault(part_no, set()).add(machine)

    headers = [
        ("date", "Date"),
        ("part", "Part Name"),
        ("process_name", "Process Name"),
        ("planned_qty_fg", "Planned Qty FG"),
        ("production_qty_fg", "Production Qty FG"),
        ("production_qty_wip", "Production Qty WIP In"),
        ("planned_qty_machines", "Planned Qty (Machines)"),
        ("rework_qty", "Rework Qty"),
        ("rejection_qty", "Rejection Qty"),
    ]
    rows = []
    for d in docs:
        machine_plan_actual = d.get("machine_plan_actual", [])
        machine_data = d.get("machine_data", [])
        parts_used_data = d.get("parts_used_data", [])
        all_parts = set()
        for md in machine_data:
            for part in md.get("parts", []):
                part_u = str(part).strip().upper()
                if part_u:
                    all_parts.add(part_u)
        for pud in parts_used_data:
            part_u = str(pud.get("part_name", "")).strip().upper()
            if part_u:
                all_parts.add(part_u)
        if not all_parts:
            all_parts.add("")

        for part_u in all_parts:
            part_machines = part_to_machines.get(part_u, set())
            planned_qty_machines = 0
            for mpa in machine_plan_actual:
                machine_id = mpa.get("machine", "")
                if machine_id in part_machines:
                    planned_qty_machines += int(mpa.get("plan", 0) or 0)
            rows.append(
                {
                    "date": _fmt_mail_value(d.get("business_date")),
                    "part": part_u or "-",
                    "process_name": _fmt_mail_value(part_to_process.get(part_u, "")),
                    "planned_qty_fg": _fmt_mail_value(d.get("planned_qty", 0)),
                    "production_qty_fg": _fmt_mail_value(d.get("total_fg", 0)),
                    "production_qty_wip": _fmt_mail_value(d.get("total_production", 0)),
                    "planned_qty_machines": _fmt_mail_value(planned_qty_machines),
                    "rework_qty": _fmt_mail_value(d.get("rework_qty", 0)),
                    "rejection_qty": _fmt_mail_value(d.get("total_rejected", 0)),
                }
            )
    return headers, rows


def _report_packing_rows(db):
    _, _, start_dt, end_dt = _today_window_ist()
    docs = list(
        db["assembly_process"].find(
            {"createddate": {"$gte": start_dt, "$lte": end_dt}},
            {"_id": 0},
        )
    )
    headers = [
        ("date", "Date"),
        ("shift", "Shift"),
        ("operator", "Operator"),
        ("machine", "Machine"),
        ("assembled_work_order", "Assembled Work Order"),
        ("assembly_process", "Assembly Process"),
        ("subprocess_wo", "Subprocess WO"),
    ]
    rows = []
    for d in docs:
        subprocess_vals = list((d.get("subprocess_data") or {}).values())
        rows.append(
            {
                "date": _fmt_mail_value(d.get("createddate"))[:10],
                "shift": _fmt_mail_value(d.get("shift")),
                "operator": _fmt_mail_value(d.get("operator_id")),
                "machine": _fmt_mail_value(d.get("machine_id")),
                "assembled_work_order": _fmt_mail_value(d.get("work_order")),
                "assembly_process": _fmt_mail_value(d.get("process_name")),
                "subprocess_wo": _fmt_mail_value(subprocess_vals),
            }
        )
    return headers, rows


REPORT_CATEGORY_FETCHERS = {
    "report_workorder": _report_workorder_rows,
    "report_heattreatment": _report_heattreatment_rows,
    "report_oee_shiftwise": _report_oee_shiftwise_rows,
    "report_oee_daily": _report_oee_daily_rows,
    "report_downtime": _report_downtime_rows,
    "report_wip": _report_wip_rows,
    "report_packing": _report_packing_rows,
}


def _to_table_matrix(headers, rows):
    keys = [k for k, _ in headers]
    matrix = []
    for row in rows:
        matrix.append([row.get(key, "-") for key in keys])
    return matrix


def run_report_category_mail(category):
    """Send configured report mail for a single report category across all plants."""
    if category not in REPORT_CATEGORY_TITLES:
        raise ValueError(f"Unsupported report category: {category}")

    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    report_title = REPORT_CATEGORY_TITLES[category]

    sent, failed = [], []
    for plant_code in settings.PLANT_CODES:
        try:
            db = get_db_connection_machine(plant_code)
            cfg = db["prism_email_config"].find_one({"category": category}) or {}
            is_active = bool(cfg.get("is_active", True))
            to = _split_emails(cfg.get("to_emails", ""))
            cc = _split_emails(cfg.get("cc_emails", ""))
            bcc = _split_emails(cfg.get("bcc_emails", ""))

            if not is_active:
                failed.append(f"{plant_code}: skipped, config inactive")
                continue
            if not (to or cc or bcc):
                failed.append(f"{plant_code}: skipped, no recipients")
                continue

            fetcher = REPORT_CATEGORY_FETCHERS.get(category)
            headers, rows = fetcher(db) if fetcher else ([], [])
            matrix = _to_table_matrix(headers, rows)
            context = {
                "plant_code": plant_code,
                "plant_name": _plant_name(plant_code),
                "report_title": report_title,
                "category": category,
                "report_date": today.strftime("%d-%m-%Y"),
                "headers": headers,
                "rows": matrix,
                "count": len(rows),
            }
            html_body = render_to_string("emails/report_digest_table.html", context)
            subject = (
                f"[PRISM] {report_title} - "
                f"{_plant_name(plant_code)} ({plant_code}) - "
                f"{today.strftime('%d-%m-%Y')}"
            )
            _deliver_mail(plant_code, to, cc, bcc, subject, html_body)
            sent.append(f"{plant_code}: sent ({len(rows)} rows)")
        except Exception as exc:
            failed.append(f"{plant_code}: {exc}")

    return {"sent": sent, "failed": failed}


def run_all_report_mails(categories=None):
    """Run configured report mails for multiple categories."""
    selected = categories or list(REPORT_CATEGORY_TITLES.keys())
    sent = []
    failed = []
    for category in selected:
        result = run_report_category_mail(category)
        sent.extend([f"[{category}] {msg}" for msg in result.get("sent", [])])
        failed.extend([f"[{category}] {msg}" for msg in result.get("failed", [])])
    return {"sent": sent, "failed": failed}


def _split_emails(raw):
    if not raw:
        return []
    return [e.strip() for e in str(raw).replace("\n", ",").split(",") if e.strip()]


def _send_mail_api(payload):
    url = settings.MAIL_API_URL
    token = settings.MAIL_API_TOKEN
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Secret {token}"
    resp = requests.post(url, json=payload, headers=headers, timeout=25)
    if resp.status_code >= 400:
        raise RuntimeError(f"Mail API failed ({resp.status_code}): {resp.text[:400]}")


@shared_task
def save_data_task():
    """Insert a test document into MongoDB via Celery."""
    db = get_db_connection_machine("041")
    collection = db["test_data"]

    data = {"message": "Celery data", "timestamp": datetime.now()}

    collection.insert_one(data)


@shared_task()
def run_tool_calibration_overdue_mail():
    """Send overdue tool-calibration reminder mails per plant."""
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    sent, failed = [], []

    for plant_code in settings.PLANT_CODES:
        try:
            to, cc, bcc, is_active = _load_mail_config(plant_code)

            if not is_active:
                failed.append(f"{plant_code}: skipped, config inactive")
                continue

            if not (to or cc or bcc):
                failed.append(f"{plant_code}: skipped, no recipients")
                continue

            overdue_rows, _ = _fetch_tool_status_rows(plant_code, today, 7)

            # ✅ FIX: do NOT skip when empty
            if not overdue_rows:
                overdue_rows = []

            context = {
                "plant_code": plant_code,
                "plant_name": _plant_name(plant_code),
                "report_date": today.strftime("%d-%m-%Y"),
                "rows": overdue_rows,
                "count": len(overdue_rows),
            }

            html_body = render_to_string(
                "emails/tool_calibration_overdue.html", context
            )

            subject = (
                f"[PRISM] Overdue Tool Calibrations - {_plant_name(plant_code)} "
                f"({plant_code}) - {today.strftime('%d-%m-%Y')}"
            )

            _deliver_mail(plant_code, to, cc, bcc, subject, html_body)

            sent.append(f"{plant_code}: overdue={len(overdue_rows)}")

        except Exception as exc:
            failed.append(f"{plant_code}: {exc}")

    return {"sent": sent, "failed": failed}


@shared_task()
def run_tool_calibration_due_soon_mail(due_within_days=7):
    """Send due-soon tool calibration mail digest per plant."""
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    sent, failed = [], []

    for plant_code in settings.PLANT_CODES:
        try:
            to, cc, bcc, is_active = _load_mail_config(plant_code)
            if not is_active:
                failed.append(f"{plant_code}: skipped, config inactive")
                continue
            if not (to or cc or bcc):
                failed.append(f"{plant_code}: skipped, no recipients")
                continue

            _, due_soon_rows = _fetch_tool_status_rows(
                plant_code, today, due_within_days
            )
            if not due_soon_rows:
                failed.append(f"{plant_code}: skipped, no due-soon tools")
                continue

            context = {
                "plant_code": plant_code,
                "plant_name": _plant_name(plant_code),
                "report_date": today.strftime("%d-%m-%Y"),
                "days_ahead": due_within_days,
                "rows": due_soon_rows,
                "count": len(due_soon_rows),
            }
            html_body = render_to_string(
                "emails/tool_calibration_due_soon.html", context
            )
            subject = (
                f"[PRISM] Tool Calibrations Due Within {due_within_days} Days - "
                f"{_plant_name(plant_code)} ({plant_code}) - "
                f"{today.strftime('%d-%m-%Y')}"
            )
            _deliver_mail(plant_code, to, cc, bcc, subject, html_body)
            sent.append(f"{plant_code}: due_soon={len(due_soon_rows)}")
        except Exception as exc:
            failed.append(f"{plant_code}: {exc}")

    return {"sent": sent, "failed": failed}


@shared_task()
def run_tool_calibration_completed_mail(completed_within_days=7):
    """Completed tool calibration mail digest for the last N days per plant."""
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    days = max(int(completed_within_days or 7), 1)
    start_date = today - timedelta(days=days)
    end_date = today

    start_dt = datetime.combine(start_date, dtime.min, tzinfo=ist)
    end_dt = datetime.combine(end_date, dtime.max, tzinfo=ist)

    sent, failed = [], []
    for plant_code in settings.PLANT_CODES:
        try:
            to, cc, bcc, is_active = _load_mail_config(plant_code)
            if not is_active:
                failed.append(f"{plant_code}: skipped, config inactive")
                continue
            if not (to or cc or bcc):
                failed.append(f"{plant_code}: skipped, no recipients")
                continue

            completed_rows = _fetch_completed_rows(plant_code, start_dt, end_dt)
            if not completed_rows:
                failed.append(f"{plant_code}: skipped, no completed records")
                continue

            context = {
                "plant_code": plant_code,
                "plant_name": _plant_name(plant_code),
                "start": start_date,
                "end": end_date,
                "start_str": start_date.strftime("%d %b %Y"),
                "end_str": end_date.strftime("%d %b %Y"),
                "rows": completed_rows,
                "count": len(completed_rows),
            }
            html_body = render_to_string(
                "emails/tool_calibration_completed.html", context
            )
            subject = (
                f"[PRISM] Weekly Completed Tool Calibrations - "
                f"{_plant_name(plant_code)} ({plant_code}) - "
                f"{start_date.strftime('%d %b')} to "
                f"{end_date.strftime('%d %b %Y')}"
            )
            _deliver_mail(plant_code, to, cc, bcc, subject, html_body)
            sent.append(f"{plant_code}: completed={len(completed_rows)}")
        except Exception as exc:
            failed.append(f"{plant_code}: {exc}")

    return {"sent": sent, "failed": failed}
