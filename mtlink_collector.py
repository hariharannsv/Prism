"""
FANUC MTLink API → Celery → MongoDB (mtlink_data).

Only collects raw API events. No shift/day/production counts.
Mongo times stored in IST (+05:30). API requests still use UTC.
"""

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests
from celery import shared_task
from django.conf import settings
from pymongo import ASCENDING, UpdateOne
from pymongo.errors import OperationFailure
from requests.auth import HTTPBasicAuth

from core.mongo_connection import get_db_connection_machine

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# MTLink monitoring sources (API path tag without machine suffix)
SOURCE_PARTS_NUM = "PartsNum_path1"
SOURCE_PART_COUNTER = "PartCounter"

DATA_COLLECTION = "mtlink_data"
STATE_COLLECTION = "mtlink_collection_state"

# Small overlap so open events (end=null) get updated when API closes them
OVERLAP_MINUTES = 5


def _utcnow():
    return datetime.now(timezone.utc)


def _now_ist():
    return datetime.now(IST)


def _iso_z(dt):
    """Any aware/naive dt → UTC string for MTLink API params (...Z)."""
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = _parse_iso(dt)
        if dt is None:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _iso_ist(dt):
    """
    Any dt (API UTC or aware) → IST string for Mongo.
    Example: 2026-08-13T11:22:48.500+05:30
    """
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = _parse_iso(dt)
        if dt is None:
            return None
    if dt.tzinfo is None:
        # API values without tz are UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST).isoformat()


def _parse_iso(value):
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo:
            return value
        # Naive: treat as UTC (legacy mtlink_data / API)
        return value.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _event_key(machine, source, start_ist):
    """Deterministic unique id (IST start). Same before/after end changes."""
    return f"{machine}|{source}|{start_ist}"


def _plant_code():
    return getattr(settings, "FANUC_MTLINK_PLANT_CODE", "041")


def _get_db():
    return get_db_connection_machine(_plant_code())


def _ensure_indexes(db):
    """Unique event identity + state lookup. Safe to call every run."""
    data = db[DATA_COLLECTION]
    state = db[STATE_COLLECTION]

    try:
        data.create_index(
            [("event_key", ASCENDING)],
            unique=True,
            name="uniq_mtlink_event_key",
        )
        data.create_index(
            [("machine", ASCENDING), ("source", ASCENDING), ("start", ASCENDING)],
            name="idx_mtlink_machine_source_start",
        )
        state.create_index(
            [("machine", ASCENDING), ("source", ASCENDING)],
            unique=True,
            name="uniq_mtlink_state_machine_source",
        )
    except OperationFailure as exc:
        # Index already exists with different options — log, keep going
        logger.warning("mtlink index ensure: %s", exc)


def _configured_machines(db, only_machine=None):
    """
    Always from PMS_machine_info.machine_name (same as OEE / hourly report).
    """
    machines = sorted(
        m
        for m in db["PMS_machine_info"].distinct("machine_name")
        if m and str(m).strip()
    )

    if only_machine:
        only = str(only_machine).strip()
        matched = [m for m in machines if m == only]
        return matched if matched else [only]
    return machines


def _api_url(machine, source):
    base = settings.MTLINK_API_BASE_URL.rstrip("/")
    # PartsNum_path1_M01 / PartCounter_M01
    tag = f"{source}_{machine}"
    return f"{base}/{machine}/monitorings/{tag}/logs"


def _fetch_logs(machine, source, start_utc, end_utc):
    """Call one MTLink logs API. Returns list or raises."""
    url = _api_url(machine, source)
    timeout = getattr(settings, "MTLINK_API_TIMEOUT", 10)
    params = {
        "start": _iso_z(start_utc),
        "end": _iso_z(end_utc),
    }
    response = requests.get(
        url,
        auth=HTTPBasicAuth(
            settings.MTLINK_API_USERNAME,
            settings.MTLINK_API_PASSWORD,
        ),
        params=params,
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, list) else []


def _get_state(db, machine, source):
    return db[STATE_COLLECTION].find_one({"machine": machine, "source": source})


def _window_for_source(db, machine, source, now_utc):
    """
    Fetch window for machine+source.
    Uses last_processed_start - overlap when state exists.
    Else initial lookback.
    """
    lookback = int(getattr(settings, "FANUC_MTLINK_INITIAL_LOOKBACK_MINUTES", 120))
    state = _get_state(db, machine, source)
    last_start = None
    if state:
        last_start = _parse_iso(state.get("last_processed_start"))

    if last_start:
        start_utc = last_start - timedelta(minutes=OVERLAP_MINUTES)
    else:
        start_utc = now_utc - timedelta(minutes=lookback)

    return start_utc, now_utc


def _save_events(db, machine, source, logs, collected_at_ist):
    """
    Upsert each API event by event_key (machine|source|start_ist).
    start/end/collected_at stored as IST (+05:30).
    Open end=null saved; later closed end → same key updated.
    Returns (inserted, updated, skipped_dup_unchanged).
    """
    if not logs:
        return 0, 0, 0

    ops = []
    keys = []
    for item in logs:
        start_raw = item.get("start")
        if not start_raw:
            continue
        start_ist = _iso_ist(start_raw)
        if not start_ist:
            continue
        end_raw = item.get("end")
        end_ist = _iso_ist(end_raw) if end_raw else None
        key = _event_key(machine, source, start_ist)
        keys.append(key)
        ops.append(
            UpdateOne(
                {"event_key": key},
                {
                    "$set": {
                        "machine": machine,
                        "source": source,
                        "start": start_ist,
                        "end": end_ist,
                        "value": item.get("value"),
                        "collected_at": collected_at_ist,
                        "event_key": key,
                        "tz": "IST",
                    }
                },
                upsert=True,
            )
        )

    if not ops:
        return 0, 0, 0

    # Count existing before write → classify insert vs update for logs
    existing = {
        doc["event_key"]: doc
        for doc in db[DATA_COLLECTION].find(
            {"event_key": {"$in": keys}},
            {"event_key": 1, "end": 1, "value": 1},
        )
    }

    result = db[DATA_COLLECTION].bulk_write(ops, ordered=False)
    inserted = result.upserted_count
    updated = 0
    skipped = 0
    for item in logs:
        start_raw = item.get("start")
        if not start_raw:
            continue
        start_ist = _iso_ist(start_raw)
        if not start_ist:
            continue
        key = _event_key(machine, source, start_ist)
        old = existing.get(key)
        if not old:
            continue
        end_raw = item.get("end")
        end_ist = _iso_ist(end_raw) if end_raw else None
        if old.get("end") != end_ist or old.get("value") != item.get("value"):
            updated += 1
        else:
            skipped += 1

    return inserted, updated, skipped


def _update_state(db, machine, source, logs, collected_at_ist):
    """Remember newest start (IST) for this machine+source (API window hint)."""
    newest = None
    for item in logs:
        start_dt = _parse_iso(item.get("start"))
        if start_dt and (newest is None or start_dt > newest):
            newest = start_dt

    if newest is None:
        db[STATE_COLLECTION].update_one(
            {"machine": machine, "source": source},
            {
                "$set": {
                    "machine": machine,
                    "source": source,
                    "last_successful_collection": collected_at_ist,
                    "tz": "IST",
                }
            },
            upsert=True,
        )
        return

    db[STATE_COLLECTION].update_one(
        {"machine": machine, "source": source},
        {
            "$set": {
                "machine": machine,
                "source": source,
                "last_processed_start": _iso_ist(newest),
                "last_successful_collection": collected_at_ist,
                "tz": "IST",
            }
        },
        upsert=True,
    )


def _collect_one_source(db, machine, source, now_utc):
    """Fetch one source for one machine → save IST → update state."""
    start_utc, end_utc = _window_for_source(db, machine, source, now_utc)
    logs = _fetch_logs(machine, source, start_utc, end_utc)
    api_label = "API1" if source == SOURCE_PARTS_NUM else "API2"
    msg = f"[{machine}] {api_label} fetched: {len(logs)} records"
    print(msg)
    logger.info(msg)

    collected_at_ist = _iso_ist(now_utc)
    inserted, updated, skipped = _save_events(
        db, machine, source, logs, collected_at_ist
    )
    _update_state(db, machine, source, logs, collected_at_ist)

    for line in (
        f"[{machine}] Inserted: {inserted} records",
        f"[{machine}] Updated: {updated} open record",
        f"[{machine}] Duplicates skipped: {skipped}",
    ):
        print(line)
        logger.info(line)
    return {
        "source": source,
        "fetched": len(logs),
        "inserted": inserted,
        "updated": updated,
        "skipped": skipped,
    }


def collect_machine(db, machine, now_utc=None):
    """
    Collect API1 + API2 for one machine.
    Errors on one source logged; other source still runs.
    """
    now_utc = now_utc or _utcnow()
    summary = {"machine": machine, "sources": [], "ok": True, "error": None}

    for source in (SOURCE_PARTS_NUM, SOURCE_PART_COUNTER):
        try:
            summary["sources"].append(_collect_one_source(db, machine, source, now_utc))
        except Exception as exc:
            summary["ok"] = False
            summary["error"] = str(exc)
            logger.exception("[%s] collect failed source=%s: %s", machine, source, exc)

    logger.info("[%s] Collection completed", machine)
    return summary


@shared_task(name="collect_mtlink_data", bind=True)
def collect_mtlink_data(self, machine=None):
    """
    Celery Beat entry: all machines from PMS_machine_info.
    One machine fail → others still run.
    """
    plant = _plant_code()
    db = _get_db()
    _ensure_indexes(db)
    machines = _configured_machines(db, only_machine=machine)
    now_utc = _utcnow()

    start_msg = (
        f"MTLink collect start plant={plant} db={db.name} "
        f"source=PMS_machine_info machines={len(machines)} list={machines}"
    )
    print(start_msg)
    logger.info(start_msg)

    results = []
    for m in machines:
        try:
            results.append(collect_machine(db, m, now_utc=now_utc))
        except Exception as exc:
            err = f"[{m}] machine-level failure: {exc}"
            print(err)
            logger.exception(err)
            results.append(
                {"machine": m, "ok": False, "error": str(exc), "sources": []}
            )

    ok = sum(1 for r in results if r.get("ok"))
    bad = len(results) - ok
    total_docs = db[DATA_COLLECTION].estimated_document_count()
    done_msg = (
        f"MTLink collect done ok={ok} failed={bad} "
        f"db={db.name}.{DATA_COLLECTION} docs≈{total_docs}"
    )
    print(done_msg)
    logger.info(done_msg)
    return {
        "ok": ok,
        "failed": bad,
        "plant": plant,
        "db": db.name,
        "collection": DATA_COLLECTION,
        "machines": machines,
        "doc_count": total_docs,
        "results": results,
    }
