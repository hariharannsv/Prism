"""Helpers for Packing_Trace FG counts (WIP dashboard, Celery tasks)."""


def _packing_trace_date_or_clauses(
    shift_start, shift_end, utc_start=None, utc_end=None
):
    """Build $or clauses: match if created_date OR submitted_at is in range."""
    clauses = [
        {"created_date": {"$gte": shift_start, "$lt": shift_end}},
        {"submitted_at": {"$gte": shift_start, "$lt": shift_end}},
    ]
    if utc_start is not None and utc_end is not None:
        clauses.extend(
            [
                {"created_date": {"$gte": utc_start, "$lt": utc_end}},
                {"submitted_at": {"$gte": utc_start, "$lt": utc_end}},
            ]
        )
    return clauses


def packing_trace_fg_pipeline(shift_start, shift_end, utc_start=None, utc_end=None):
    """
    Count Packing_Trace documents in the 8AM–8AM IST shift window.
    A record counts if created_date OR submitted_at falls in range (not ifNull on one field).
    Optional UTC bounds cover timestamps stored in UTC.
    """
    return [
        {
            "$match": {
                "$or": _packing_trace_date_or_clauses(
                    shift_start, shift_end, utc_start, utc_end
                )
            }
        },
        {"$count": "total"},
    ]


def count_packing_trace_fg(
    packing_trace_collection,
    shift_start,
    shift_end,
    utc_start=None,
    utc_end=None,
):
    """Return number of Packing_Trace records in the shift window."""
    query = {
        "$or": _packing_trace_date_or_clauses(
            shift_start, shift_end, utc_start, utc_end
        )
    }
    return packing_trace_collection.count_documents(query)
