"""OEE calculation helpers and Celery tasks for shift/day metrics."""

import traceback
from datetime import datetime
from datetime import time as dtime
from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from requests.auth import HTTPBasicAuth

from core.mbd_cycle_utils import (finalize_mbd_cycle_time,
                                  mbd_aggregate_cycle_time_seconds,
                                  mbd_station_index_from_tag,
                                  mbd_value_to_seconds)
from core.mongo_connection import get_db_connection_machine
from core.utility.log import log_traceability
from core.views.machinedashboard import (calculate_bulk_oee_metrics_day,
                                         get_daily_planned_qty_map,
                                         get_machine_data_map,
                                         get_planned_actual_qty_map)


def get_8am_shift_window_wip(selected_date):
    """Get 8 AM to next day 8 AM IST window for given date (used for WIP dashboard)"""
    if isinstance(selected_date, str):
        selected_date = datetime.strptime(selected_date, "%Y-%m-%d").date()

    IST = ZoneInfo("Asia/Kolkata")

    # Create 8 AM IST start datetime
    shift_start = datetime.combine(selected_date, dtime(8, 0, 0)).replace(tzinfo=IST)

    # Next day 8 AM IST end datetime
    shift_end = shift_start + timedelta(days=1)

    # Convert IST to UTC for storage
    shift_start_utc = shift_start.astimezone(timezone.utc)
    shift_end_utc = shift_end.astimezone(timezone.utc)

    return shift_start_utc, shift_end_utc, shift_start, shift_end


def get_shift_from_ist_time(current_dt: datetime):
    """
    Return shift name and business date for an IST datetime.

    Shift 1: 08:00–16:30 (business date = calendar date)
    Shift 2: 16:30–01:00 (after midnight still prior calendar business date)
    Shift 3: 01:00–08:00 (business date = prior calendar day; day is 08:00–08:00)
    """
    current_time = current_dt.time()
    calendar_day = current_dt.date()

    # Shift 1: 08:00 – 16:30
    if dtime(8, 0) <= current_time < dtime(16, 30):
        return "Shift 1", calendar_day

    # Shift 2: 16:30 – 24:00
    if current_time >= dtime(16, 30):
        return "Shift 2", calendar_day

    # Shift 2 tail: 00:00 – 01:00 (belongs to yesterday's Shift 2 / business day)
    if current_time < dtime(1, 0):
        return "Shift 2", calendar_day - timedelta(days=1)

    # Shift 3: 01:00 – 08:00 (belongs to business day that started yesterday 08:00)
    if dtime(1, 0) <= current_time < dtime(8, 0):
        return "Shift 3", calendar_day - timedelta(days=1)

    return "Unknown", calendar_day


def calculate_bulk_oee_metrics(
    db,
    machines,
    start_date_obj,
    end_date_obj,
    total_time_minutes,
    plant_code,
):
    """Compute OEE metrics for many machines in one time window."""

    plant_production_hour = 22.5
    IST = timezone(timedelta(hours=5, minutes=30))

    shift_start = start_date_obj.replace(
        hour=8, minute=0, second=0, microsecond=0, tzinfo=timezone.utc
    )
    shift_end = shift_start + timedelta(days=1)

    cost_data = cache.get_or_set(
        "cost_data",
        lambda: {
            doc["machine_name"]: float(doc["cost"]) for doc in db["PMS_costing"].find()
        },
        60,
    )
    part_cost_data = cache.get_or_set(
        "part_cost_data",
        lambda: {
            doc["machine_name"]: float(doc.get("partcost", 0))
            for doc in db["PMS_partcosting"].find()
        },
        60,
    )

    unplanned_pipeline = [
        {
            "$match": {
                "process_name": {"$in": machines},
                "createdat": {"$gte": start_date_obj, "$lt": end_date_obj},
            }
        },
        {"$unwind": "$unplanned_downtime"},
        {
            "$group": {
                "_id": {
                    "process": "$process_name",
                    "date": {
                        "$dateToString": {"format": "%Y-%m-%d", "date": "$createdat"}
                    },
                },
                "total": {"$sum": {"$toInt": "$unplanned_downtime.minutes"}},
            }
        },
    ]

    unplanned_data_by_date = {}
    for d in db["PMS_unplanneddowntime"].aggregate(unplanned_pipeline):
        process = d["_id"]["process"]
        date_str = d["_id"]["date"]
        unplanned_data_by_date.setdefault(process, {})[date_str] = d["total"]

    rejected_pipeline = [
        {
            "$match": {
                "timestamp": {"$gte": start_date_obj, "$lt": end_date_obj},
                "machine_id": {"$in": machines},
            }
        },
        {
            "$group": {
                "_id": "$machine_id",
                "total_rejected": {"$sum": "$rejected_count"},
            }
        },
    ]
    rejected_data = {
        d["_id"]: d["total_rejected"]
        for d in db["PCB_Trace"].aggregate(rejected_pipeline)
    }

    trace_pipeline = [
        {
            "$match": {
                "timestamp": {"$gte": start_date_obj, "$lt": end_date_obj},
                "flag": 0,
                "machine_id": {"$in": machines},
            }
        },
        {"$group": {"_id": "$machine_id", "total": {"$sum": "$work_order_count"}}},
    ]
    trace_data = {
        d["_id"]: d["total"] for d in db["PCB_Trace"].aggregate(trace_pipeline)
    }

    mbd_pipeline = [
        {
            "$match": {
                "timestamp": {"$gte": shift_start, "$lt": shift_end},
                "source": {"$in": machines},
                "tag": {
                    "$in": [
                        "TOTAL PART COUNT",
                        "MACHINE CYCLE TIME",
                        "MACHINE CYCLE TIME STATION 1",
                        "MACHINE CYCLE TIME STATION 2",
                        "MACHINE CYCLE TIME STATION 3",
                    ]
                },
            }
        },
        {"$sort": {"timestamp": -1}},
        {
            "$group": {
                "_id": {"source": "$source", "tag": "$tag"},
                "value": {"$first": "$value"},
                "timestamp": {"$first": "$timestamp"},
            }
        },
    ]
    mbd_part_count_map = {}

    for d in db["Mbd"].aggregate(mbd_pipeline):
        source = d["_id"]["source"]
        tag = d["_id"]["tag"]
        value = d.get("value")

        if source not in mbd_part_count_map:
            mbd_part_count_map[source] = {"station_cycle_seconds": {}}

        if tag == "TOTAL PART COUNT":
            try:
                mbd_part_count_map[source]["part_count"] = int(value) if value else 0
            except (ValueError, TypeError):
                mbd_part_count_map[source]["part_count"] = 0

        elif tag == "MACHINE CYCLE TIME":
            part_count = mbd_part_count_map[source].get("part_count", 0)
            mbd_part_count_map[source]["cycle_time"] = mbd_aggregate_cycle_time_seconds(
                value, part_count
            )

        elif tag.startswith("MACHINE CYCLE TIME STATION"):
            station_idx = mbd_station_index_from_tag(tag)
            if station_idx:
                mbd_part_count_map[source]["station_cycle_seconds"][station_idx] = (
                    mbd_value_to_seconds(value)
                )

    for source, data in mbd_part_count_map.items():
        finalize_mbd_cycle_time(source, data)

    machine_data_map = get_machine_data_map(db, machines, shift_start, shift_end)

    cell_docs = cache.get_or_set(
        "cell_docs",
        lambda: list(
            db["PMS_oee_cell"].find(
                {"processes.process.createdAt": {"$gte": shift_start, "$lt": shift_end}}
            )
        ),
        60,
    )

    result = []

    # Determine local "today" in IST for live-data decision
    local_now = datetime.now(IST)

    for machine in machines:
        unplanned_minutes = sum(unplanned_data_by_date.get(machine, {}).values())
        rejected_count = rejected_data.get(machine, 0)
        total_part_count = 0

        ct_minutes = 0.0
        ct_seconds = 0
        api_data_used = False
        logs = []

        machine_doc = machine_data_map.get(machine) or {}
        mbd_machine_data = mbd_part_count_map.get(machine, {})

        if "part_count" in machine_doc:
            total_part_count = machine_doc["part_count"]
            if "rejected_count" in machine_doc:
                rejected_count = machine_doc["rejected_count"]
            print(f"📊 Using machine_data part count for {machine}: {total_part_count}")
        elif "part_count" in mbd_machine_data:
            total_part_count = mbd_machine_data["part_count"]
            print(f"📊 Using MBD part count for {machine}: {total_part_count}")

        if machine_doc.get("cycle_time", 0) > 0:
            ct_seconds = machine_doc["cycle_time"]
            ct_minutes = round(ct_seconds / 60, 2)
            print(f"⏱ Using machine_data cycle time for {machine}: {ct_seconds}s")
        elif "cycle_time" in mbd_machine_data:
            ct_seconds = mbd_machine_data["cycle_time"]
            ct_minutes = round(ct_seconds / 60, 2)
            print(f"⏱ Using MBD cycle time for {machine}: {ct_seconds} seconds")

        if (
            local_now - start_date_obj.astimezone(IST)
        ).total_seconds() < 172800:  # 48 hours
            api_url1 = f"{settings.MTLINK_API_BASE_URL}/{machine}/monitorings/PartsNum_path1_{machine}/logs"
            api_url2 = f"{settings.MTLINK_API_BASE_URL}/{machine}/monitorings/PartCounter_{machine}/logs"
            params = {
                "start": start_date_obj.isoformat().replace("+00:00", "Z"),
                "end": end_date_obj.isoformat().replace("+00:00", "Z"),
            }

            for api_url in [api_url1, api_url2]:
                try:
                    response = requests.get(
                        api_url,
                        auth=HTTPBasicAuth("OTadmin", "OTadmin@2025"),
                        params=params,
                        timeout=10,  # Increased timeout slightly
                    )

                    response.raise_for_status()

                    logs_json = response.json()
                    temp_logs = logs_json if isinstance(logs_json, list) else []
                    print(f"{machine} -> API Logs Count: {len(temp_logs)}")
                    if temp_logs:
                        logs = temp_logs
                        print(f"✅ {machine} using {api_url}")
                        break

                except Exception as e:
                    print(f"⚠️ API error for {machine}: {e}")
                    log_traceability(
                        request=None,
                        plant_code=plant_code,
                        level=2,
                        message=f"API error for {machine}: {str(e)}",
                    )
                logs = []

            # Calculate part count from API logs
            if logs:
                processed = []
                for log in logs:
                    ts = log.get("end") or log.get("timestamp") or log.get("start")
                    if not ts:
                        continue
                    try:
                        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        log_traceability(
                            request=None,
                            plant_code=plant_code,
                            level=2,
                            message=f"Error parsing timestamp from log for {machine}:"
                            f"{str(traceback.format_exc())}",
                        )

                    if not (start_date_obj <= ts_dt < end_date_obj):
                        continue

                    processed.append(
                        {
                            "timestamp": ts_dt.isoformat(),
                            "value": log.get("value", None),
                        }
                    )

                if processed:
                    sorted_logs = sorted(
                        processed, key=lambda x: x.get("timestamp", "")
                    )
                    total_count = 0
                    baseline = None
                    prev_value = None

                    for log in sorted_logs:
                        raw = log.get("value", None)
                        if raw is None:
                            continue

                        try:
                            if isinstance(raw, (int, float)):
                                current_value = int(raw)
                            else:
                                s = str(raw).strip()
                                if s == "":
                                    continue
                                if "." in s:
                                    current_value = int(float(s))
                                else:
                                    current_value = int(s)
                        except (ValueError, TypeError):
                            continue

                        if baseline is None:
                            baseline = current_value
                            prev_value = current_value
                            continue

                        if current_value < prev_value:
                            # Reset detected: add increment from baseline to reset point
                            total_count += prev_value - baseline
                            baseline = 0
                            prev_value = current_value
                        else:
                            prev_value = current_value

                    if baseline is not None and prev_value is not None:
                        total_count += prev_value - baseline

                    total_part_count = total_count
                    api_data_used = True
                    print(
                        f"📊 Calculated API part count for {machine}: {total_part_count}"
                    )

            # Calculate cycle time from API logs if needed
            if not ct_seconds and logs:
                valid_logs = []
                for log in logs:
                    ts = log.get("end") or log.get("timestamp")
                    if not ts:
                        continue
                    try:
                        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    except Exception:
                        log_traceability(
                            request=None,
                            plant_code=plant_code,
                            level=2,
                            message=f"Error parsing timestamp for {machine}:"
                            f"{str(traceback.format_exc())}",
                        )
                        continue

                    if start_date_obj <= ts_dt < end_date_obj:
                        valid_logs.append(log)

                valid_logs_sorted = sorted(
                    valid_logs, key=lambda x: x.get("timestamp", "")
                )

                cycle_times = []
                for log in valid_logs_sorted:
                    start_ts = log.get("start")
                    end_ts = log.get("end")
                    if not start_ts or not end_ts:
                        continue
                    try:
                        start_dt = datetime.fromisoformat(
                            start_ts.replace("Z", "+00:00")
                        )
                        end_dt = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
                        cycle_time_sec = (end_dt - start_dt).total_seconds()
                        if cycle_time_sec <= 3:
                            cycle_time_sec *= 60
                        if 1 <= cycle_time_sec <= 3600:
                            cycle_times.append(cycle_time_sec)
                    except Exception:
                        log_traceability(
                            request=None,
                            plant_code=plant_code,
                            level=2,
                            message=f"Error parsing timestamps for {machine}:"
                            f" {str(traceback.format_exc())}",
                        )

                if cycle_times:
                    avg_cycle_time = sum(cycle_times) / len(cycle_times)
                    ct_seconds = int(avg_cycle_time)
                    ct_minutes = round(ct_seconds / 60, 2)
                    api_data_used = True
                    print(
                        f"⏱ Calculated API cycle time for {machine}: {ct_seconds} seconds"
                    )

        # **END NEW API FETCH BLOCK**
        if not total_part_count:
            # Get the part count from mbd_part_count_map if available
            mbd_machine_data = mbd_part_count_map.get(machine, {})
            mbd_part_count = (
                mbd_machine_data.get("part_count", 0)
                if isinstance(mbd_machine_data, dict)
                else 0
            )
            total_part_count = mbd_part_count or trace_data.get(machine, 0)

        if not ct_seconds:
            for doc in cell_docs:
                for cell in doc.get("processes", []):
                    for p in cell.get("process", []):
                        if p.get("processName") == machine:
                            ct_str = p.get("cycleTime")
                            if ct_str:
                                try:
                                    m, s = map(int, ct_str.split(":"))
                                    ct_seconds = m * 60 + s
                                    ct_minutes = round(ct_seconds / 60, 2)
                                    break
                                except Exception:
                                    log_traceability(
                                        request=None,
                                        plant_code=plant_code,
                                        level=2,
                                        message=f"Error parsing cycle time for {machine} from cell data: "
                                        f"{str(traceback.format_exc())}",
                                    )
                    if ct_seconds:
                        break
                if ct_seconds:
                    break

        if ct_seconds <= 0:
            ct_seconds = 1
            ct_minutes = round(ct_seconds / 60, 2)

        good_count = max(0, total_part_count - rejected_count)
        total_downtime = unplanned_minutes
        total_runtime = max(0, total_time_minutes - total_downtime)
        planned_machine_time = total_time_minutes

        one_hour = 60 * 60
        output_qty_perhr = (one_hour / ct_seconds) if ct_seconds > 0 else 0

        perday_plannedqty = plant_production_hour * output_qty_perhr

        availability = (
            round((total_runtime / planned_machine_time) * 100, 2)
            if planned_machine_time
            else 0
        )

        performance = (
            round((total_part_count / perday_plannedqty) * 100, 2)
            if perday_plannedqty and total_part_count
            else 0
        )
        quality = (
            round((good_count / total_part_count) * 100, 2) if total_part_count else 0
        )
        overall_oee = round((availability * performance * quality) / 10000, 2)

        downtime_cost = round((total_downtime / 60) * cost_data.get(machine, 0), 2)
        rejection_loss = round(rejected_count * part_cost_data.get(machine, 0), 2)

        result.append(
            {
                "machine": machine,
                "unplanned_downtime": unplanned_minutes,
                "availability": availability,
                "performance": performance,
                "quality": quality,
                "overall_oee": overall_oee,
                "total_count": total_part_count,
                "rejected_count": rejected_count,
                "ideal_runtime": ct_seconds * total_part_count,
                "runtime_minutes": total_runtime,
                "downtime_cost": downtime_cost,
                "revenue_loss_rejection": rejection_loss,
                "cycle_time_minutes": ct_minutes,
                "cycle_time_seconds": ct_seconds,
                "output_qty_perhr": output_qty_perhr,
                "data_source": "API" if api_data_used else "MBD/Trace",
            }
        )

    return result


@shared_task(name="shiftwiseoee", bind=True)
def save_shiftwise_oee(self, shift=None):
    """
    Background task: Save OEE data shiftwise for ALL configured plants automatically.
    """
    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)

    # If shift is provided, use it; otherwise auto-detect
    if shift:
        current_shift = shift
        # Overnight saves (before 08:00) belong to previous business day (08:00–08:00)
        if current_shift in ("Shift 2", "Shift 3") and now.time() < dtime(8, 0):
            today_date = (now - timedelta(days=1)).date()
        else:
            today_date = now.date()
    else:
        current_shift, today_date = get_shift_from_ist_time(now)

    # Business date at midnight IST (matches report filters)
    business_date = datetime.combine(today_date, dtime(0, 0), tzinfo=ist)
    print(
        f"DEBUG: Saving shiftwise OEE for {current_shift} on business date: {business_date.date()}"
    )

    # Loop through plant codes from settings
    for plant_code in settings.PLANT_CODES:
        try:
            db = get_db_connection_machine(plant_code)

            # Exact shift windows (IST):
            # Shift 1: 08:00–16:30 | Shift 2: 16:30–01:00 | Shift 3: 01:00–08:00
            if current_shift == "Shift 1":
                start_ist = datetime.combine(today_date, dtime(8, 0, 0), tzinfo=ist)
                end_ist = datetime.combine(today_date, dtime(16, 30, 0), tzinfo=ist)
                start_date_obj = start_ist.astimezone(timezone.utc)
                end_date_obj = end_ist.astimezone(timezone.utc)
                total_time_minutes = (end_ist - start_ist).total_seconds() / 60

            elif current_shift == "Shift 2":
                start_ist = datetime.combine(today_date, dtime(16, 30, 0), tzinfo=ist)
                end_ist = datetime.combine(
                    today_date + timedelta(days=1), dtime(1, 0, 0), tzinfo=ist
                )
                start_date_obj = start_ist.astimezone(timezone.utc)
                end_date_obj = end_ist.astimezone(timezone.utc)
                total_time_minutes = (end_ist - start_ist).total_seconds() / 60

            elif current_shift == "Shift 3":
                # Early morning after business_date (e.g. business 21st → 01:00–08:00 on 22nd)
                start_ist = datetime.combine(
                    today_date + timedelta(days=1), dtime(1, 0, 0), tzinfo=ist
                )
                end_ist = datetime.combine(
                    today_date + timedelta(days=1), dtime(8, 0, 0), tzinfo=ist
                )
                start_date_obj = start_ist.astimezone(timezone.utc)
                end_date_obj = end_ist.astimezone(timezone.utc)
                total_time_minutes = (end_ist - start_ist).total_seconds() / 60
            else:
                print(f"Unknown shift: {current_shift}")
                continue

            all_machines = sorted(db["PMS_machine_info"].distinct("machine_name"))

            # Fetch last shift's part count
            previous_shift_part_count = {machine: 0 for machine in all_machines}

            # Get part count from previous shift
            previous_shift_start = start_date_obj - timedelta(days=1)
            previous_shift_end = start_date_obj
            shiftwise_collection = db["PMS_shiftwise_oee"]

            for machine in all_machines:
                previous_shift_data = shiftwise_collection.find_one(
                    {
                        "machine": machine,
                        "date": {
                            "$gte": previous_shift_start,
                            "$lt": previous_shift_end,
                        },
                        "shift": {"$ne": current_shift},
                    }
                )

                if previous_shift_data:
                    previous_shift_part_count[machine] = previous_shift_data.get(
                        "total_parts", 0
                    )

            # Process the current shift OEE data
            oee_results = calculate_bulk_oee_metrics_day(
                db,
                all_machines,
                start_date_obj,
                end_date_obj,
                total_time_minutes,
                plant_code,
            )

            shift_collection = db["PMS_shiftwise_oee"]

            for result in oee_results:
                machine = result["machine"]

                shift_doc = {
                    "machine": machine,
                    "date": business_date,
                    "shift": current_shift,
                    "availability": result["availability"],
                    "performance": result["performance"],
                    "quality": result["quality"],
                    "overall_oee": result["overall_oee"],
                    "total_parts": result["total_count"],
                    "downtime": result["unplanned_downtime"],
                    "created_at": datetime.now(ist),
                    "cycle_time_minutes": result["cycle_time_minutes"],
                    "cycle_time_seconds": result["cycle_time_seconds"],
                }

                shift_collection.update_one(
                    {
                        "machine": machine,
                        "date": business_date,
                        "shift": current_shift,
                    },
                    {"$set": shift_doc},
                    upsert=True,
                )
                print(
                    f"✅ Saved OEE for {machine} on {business_date.date()} ({current_shift})"
                )

                # Reset part count and cycle time at shift end for all machines
                db["PMS_machine_info"].update_one(
                    {"machine_name": machine},
                    {"$set": {"part_count": 0, "cycle_time": 0}},
                )

        except Exception as e:
            print(f"❌ Failed for plant {plant_code}: {e}")

    return f"Shiftwise OEE saved for all plants - Shift: {current_shift}"


@shared_task(name="daywise_oee_old", bind=True)
def save_daywise_oee_old(self):
    """
    DEPRECATED: use core.tasks.save_daywise_oee_day / save_daywise_oee_night.
    """
    kolkata_now = datetime.now(ZoneInfo("Asia/Kolkata"))
    current_time = kolkata_now.time()

    if current_time >= dtime(8, 0):
        business_day = kolkata_now.date()
    else:
        business_day = (kolkata_now - timedelta(days=1)).date()

    start_ist = datetime.combine(
        business_day,
        dtime(8, 0),
        tzinfo=ZoneInfo("Asia/Kolkata"),
    )

    end_ist = datetime.combine(
        business_day + timedelta(days=1),
        dtime(7, 57),
        tzinfo=ZoneInfo("Asia/Kolkata"),
    )

    save_date = datetime.combine(
        business_day,
        dtime(0, 0),
        tzinfo=ZoneInfo("Asia/Kolkata"),
    )

    start_date_obj = start_ist.astimezone(timezone.utc)
    end_date_obj = end_ist.astimezone(timezone.utc)

    total_time_minutes = int((end_ist - start_ist).total_seconds() / 60)

    for plant_code in settings.PLANT_CODES:
        try:
            db = get_db_connection_machine(plant_code)
            all_machines = sorted(db["PMS_machine_info"].distinct("machine_name"))
            trace_col = "work_order_entry" if plant_code == "034" else "PCB_Trace"

            oee_results = calculate_bulk_oee_metrics_day(
                db=db,
                machines=all_machines,
                start_date_obj=start_date_obj,
                end_date_obj=end_date_obj,
                total_time_minutes=total_time_minutes,
                trace_collection=trace_col,
            )

            plan_date_str = business_day.strftime("%Y-%m-%d")
            total_by_machine = {
                r["machine"]: r.get("total_count", 0) for r in oee_results
            }
            daily_planned_map = get_daily_planned_qty_map(
                db, all_machines, plan_date_str, plant_code=plant_code
            )
            planned_actual_map = get_planned_actual_qty_map(
                db,
                all_machines,
                start_date_obj,
                end_date_obj,
                plan_date_str,
                trace_col,
                total_by_machine,
                plant_code=plant_code,
                daily_planned_map=daily_planned_map,
            )

            day_collection = db["daily_oee_report"]

            for result in oee_results:
                machine = result["machine"]
                pa = planned_actual_map.get(machine, {"planned": 0, "actual": 0})
                planned_qty = int(daily_planned_map.get(machine) or 0)
                if planned_qty <= 0:
                    planned_qty = int(pa.get("planned") or 0)
                actual_qty = int(result.get("total_count") or 0)
                if actual_qty <= 0:
                    actual_qty = int(pa.get("actual") or 0)

                day_doc = {
                    "machine": machine,
                    "date": save_date,
                    "availability": result["availability"],
                    "performance": result["performance"],
                    "quality": result["quality"],
                    "overall_oee": result["overall_oee"],
                    "total_parts": actual_qty,
                    "planned": planned_qty,
                    "actual": actual_qty,
                    "downtime": result["unplanned_downtime"],
                    "created_at": datetime.now(ZoneInfo("Asia/Kolkata")),
                    "cycle_time_minutes": result["cycle_time_minutes"],
                    "cycle_time_seconds": result["cycle_time_seconds"],
                    "data_source": result.get("data_source", "Unknown"),
                }

                day_collection.update_one(
                    {
                        "machine": machine,
                        "date": save_date,  # ⚠ FIXED (you had business_date before)
                    },
                    {"$set": day_doc},
                    upsert=True,
                )

            print(f"✔ Daywise OEE saved for plant {plant_code}")

        except Exception as e:
            print(f"❌ Failed Daywise save for plant {plant_code}: {e}")

    return f"Daywise OEE saved for date: {save_date}"


@shared_task(name="save_wip_daily", bind=True)
def save_wip_daily(self):
    """
    Background task: Save WIP dashboard data daily at 7:57 AM.
    Saves data from today 8 AM to next day 7:57 AM (previous business day).
    """
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
            total_work_order = sum(item["work_order_count"] for item in production_data)
            total_rejected = sum(item["rejected_count"] for item in production_data)
            total_production = sum(item["production_count"] for item in production_data)
            total_part_count = len(production_data)

            # 2. Planned Qty from pre_packing
            pre_packing_pipeline = [
                {
                    "$match": {
                        "submitted_at": {"$gte": shift_start_ist, "$lt": shift_end_ist}
                    }
                },
                {"$group": {"_id": None, "total_qr": {"$sum": 1}}},
            ]
            pre_packing_result = list(
                pre_packing_collection.aggregate(pre_packing_pipeline)
            )
            planned_qty_pre_packing = (
                pre_packing_result[0]["total_qr"] if pre_packing_result else 0
            )

            # 3. Total Production FG — sum packing report Items for shift window (8 AM to 8 AM IST)
            from core.views.asspack import count_packing_fg_items

            raw_packing_docs = list(packing_trace_collection.find({}, {"_id": 0}))
            fg_start = shift_start_ist.replace(tzinfo=None)
            fg_end = shift_end_ist.replace(tzinfo=None)
            total_fg = count_packing_fg_items(raw_packing_docs, fg_start, fg_end)

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

            # 9. Machine plan vs actual data
            daily_planning_collection = db["PMS_daily_planning"]

            daily_planning_query = {
                "plant_code": plant_code,
                "plan_date": business_day.strftime("%Y-%m-%d"),
            }

            daily_planning_data = list(
                daily_planning_collection.find(daily_planning_query)
            )

            # Get machine actual qty from trace
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
                "total_work_order": total_work_order,
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
