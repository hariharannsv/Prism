"""
Daywise OEE test — reads live trace (PCB_Trace / work_order_entry),
writes ONLY to oee_test. Production daily_oee_report is not modified.

Window (IST): business day D → D 08:00 through (D+1) 08:00 (same as prod save).
"""

from __future__ import annotations

from datetime import datetime
from datetime import time as dtime
from zoneinfo import ZoneInfo

from django.conf import settings

from core.mongo_connection import get_db_connection_machine
from core.oeetask import get_8am_shift_window_wip
from core.tasks import get_daywise_business_day, merge_daywise_oee_records
from core.views.machinedashboard import (
    calculate_bulk_oee_metrics_day,
    get_planned_actual_qty_map,
)

IST = ZoneInfo("Asia/Kolkata")
TEST_COLLECTION = getattr(settings, "OEE_TEST_COLLECTION", "oee_test")

# Re-exports for management commands
__all__ = [
    "TEST_COLLECTION",
    "get_daywise_business_day",
    "get_test_collection_name",
    "is_test_mode_request",
    "merge_daywise_oee_records",
    "run_daywise_oee_test",
    "run_daywise_oee_split_test",
]


def get_test_collection_name() -> str:
    """Return MongoDB collection name used for OEE test saves."""
    return TEST_COLLECTION


def is_test_mode_request(request) -> bool:
    """Return True when request should read from the OEE test collection."""
    if request.GET.get("test") == "1":
        return True
    if request.POST.get("use_test_collection") == "1":
        return True
    return bool(getattr(settings, "OEE_DAYWISE_USE_TEST_COLLECTION", False))


def run_daywise_oee_test(
    business_day=None,
    plant_codes=None,
    dummy=False,
    plant_code=None,
    **_kwargs,
):
    """
    Real OEE from live trace → oee_test (one row per machine, 08:00–08:00 IST).
    daily_oee_report unchanged.
    """
    if dummy:
        return {
            "status": "skipped",
            "reason": "dummy mode not implemented; use --live",
            "collection": TEST_COLLECTION,
        }

    if plant_code and not plant_codes:
        plant_codes = [plant_code]

    business_day = business_day or get_daywise_business_day()
    if isinstance(business_day, str):
        business_day = datetime.strptime(business_day, "%Y-%m-%d").date()

    business_date = datetime.combine(business_day, dtime(0, 0), tzinfo=IST)
    plan_date_str = business_day.strftime("%Y-%m-%d")
    shift_start_utc, shift_end_utc, shift_start_ist, shift_end_ist = (
        get_8am_shift_window_wip(plan_date_str)
    )
    total_time_minutes = int((shift_end_ist - shift_start_ist).total_seconds() / 60)

    plant_codes = plant_codes or settings.PLANT_CODES
    summary = []

    print(
        f"OEE test save: business_day={business_day} → {TEST_COLLECTION} "
        f"(live trace in, daily_oee_report untouched)"
    )
    print(
        f"  window IST {shift_start_ist} → {shift_end_ist} ({total_time_minutes} min)"
    )

    for plant in plant_codes:
        try:
            db = get_db_connection_machine(plant)
            coll = db[TEST_COLLECTION]
            trace_col = "work_order_entry" if plant == "034" else "PCB_Trace"
            all_machines = sorted(db["PMS_machine_info"].distinct("machine_name"))

            coll.delete_many({"date": business_date, "plant_code": plant})

            oee_results = calculate_bulk_oee_metrics_day(
                db=db,
                machines=all_machines,
                start_date_obj=shift_start_utc,
                end_date_obj=shift_end_utc,
                total_time_minutes=total_time_minutes,
                trace_collection=trace_col,
                allow_api_fallback=False,
            )
            total_by_machine = {
                r["machine"]: r.get("total_count", 0) for r in oee_results
            }
            planned_actual_map = get_planned_actual_qty_map(
                db,
                all_machines,
                shift_start_utc,
                shift_end_utc,
                plan_date_str,
                trace_col,
                total_by_machine,
            )

            saved = 0
            for result in oee_results:
                machine = result["machine"]
                pa = planned_actual_map.get(machine, {"planned": 0, "actual": 0})
                doc = {
                    "machine": machine,
                    "date": business_date,
                    "plant_code": plant,
                    "availability": result["availability"],
                    "performance": result["performance"],
                    "quality": result["quality"],
                    "overall_oee": result["overall_oee"],
                    "planned": pa["planned"],
                    "actual": pa["actual"],
                    "total_parts": result["total_count"],
                    "downtime": result["unplanned_downtime"],
                    "runtime_minutes": round(result.get("runtime_minutes", 0), 2),
                    "cycle_time_minutes": result["cycle_time_minutes"],
                    "cycle_time_seconds": result["cycle_time_seconds"],
                    "data_source": result.get("data_source", "No Data"),
                    "business_start": shift_start_ist,
                    "business_end": shift_end_ist,
                    "created_at": datetime.now(IST),
                }
                coll.update_one(
                    {
                        "machine": machine,
                        "date": business_date,
                        "plant_code": plant,
                    },
                    {"$set": doc, "$unset": {"segment": ""}},
                    upsert=True,
                )
                saved += 1

            summary.append(
                {
                    "plant": plant,
                    "status": "ok",
                    "machines_saved": saved,
                    "trace_collection": trace_col,
                }
            )
            print(f"✔ {plant}: {saved} machines saved to {TEST_COLLECTION}")
        except Exception as exc:
            summary.append({"plant": plant, "status": "error", "error": str(exc)})
            print(f"❌ {plant}: {exc}")

    return {
        "business_day": business_day.isoformat(),
        "collection": TEST_COLLECTION,
        "window_ist": f"{shift_start_ist} → {shift_end_ist}",
        "plants": summary,
    }


# Backward-compatible alias for management command / scripts
run_daywise_oee_split_test = run_daywise_oee_test
