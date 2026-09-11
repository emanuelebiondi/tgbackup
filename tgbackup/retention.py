"""
===============================================================================
Project      : TGBackup
File         : tgbackup/retention.py
Description  : Data retention engine based on GFS rotation strategy.
Purpose      : Computes which snapshots to keep and which to mark for pruning
               following a Grandfather-Father-Son rotation policy (daily, weekly,
               monthly), automatically protecting base snapshots required by
               active incremental chains.
===============================================================================
"""

import json
from datetime import datetime
from typing import List, Dict, Any, Set


def parse_snapshot_datetime(ts_str: str) -> datetime:
    """
    ---------------------------------------------------------------------------
    Function: parse_snapshot_datetime
    Description:
        Converts a timestamp string (ISO 8601 or YYYYMMDD_HHMMSS) to datetime.
    Input parameters:
        @param ts_str (str)  : Timestamp string.
    Return value:
        @return (datetime)   : Datetime instance.
    ---------------------------------------------------------------------------
    """
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return datetime.strptime(ts_str[:15], "%Y%m%d_%H%M%S")


def select_snapshots_to_prune(
    snapshots: List[Dict[str, Any]],
    keep_daily: int = 7,
    keep_weekly: int = 4,
    keep_monthly: int = 12
) -> List[Dict[str, Any]]:
    """
    ---------------------------------------------------------------------------
    Function: select_snapshots_to_prune
    Description:
        Applies GFS retention policy while strictly preserving:
        1. The latest snapshot under all circumstances.
        2. Base snapshots referenced by active incremental snapshots.
        3. Daily, weekly, and monthly time-window buckets.
    Input parameters:
        @param snapshots (List[Dict[str, Any]]) : List of snapshots.
        @param keep_daily (int)                 : Daily retention count.
        @param keep_weekly (int)                : Weekly retention count.
        @param keep_monthly (int)               : Monthly retention count.
    Return value:
        @return (List[Dict[str, Any]])          : Snapshots selected for pruning.
    ---------------------------------------------------------------------------
    """
    if not snapshots or len(snapshots) <= 1:
        return []

    sorted_snaps = sorted(
        snapshots,
        key=lambda s: parse_snapshot_datetime(s.get("timestamp") or s.get("created_at")),
        reverse=True
    )

    keep_ids: Set[str] = set()
    
    # 1. Always keep the newest snapshot
    keep_ids.add(sorted_snaps[0]["id"])

    # 2. Incremental chain protection: identify referenced base IDs
    base_ids_in_use: Set[str] = set()
    for s in sorted_snaps:
        manifest_raw = s.get("manifest_json")
        if manifest_raw:
            try:
                m = json.loads(manifest_raw) if isinstance(manifest_raw, str) else manifest_raw
                base_id = m.get("base_snapshot_id")
                if base_id:
                    base_ids_in_use.add(base_id)
            except Exception:
                pass

    # 3. Daily bucket
    seen_days: Set[str] = set()
    for s in sorted_snaps:
        dt = parse_snapshot_datetime(s.get("timestamp") or s.get("created_at"))
        day_key = dt.strftime("%Y-%m-%d")
        if day_key not in seen_days and len(seen_days) < keep_daily:
            seen_days.add(day_key)
            keep_ids.add(s["id"])

    # 4. Weekly bucket (ISO calendar)
    seen_weeks: Set[str] = set()
    for s in sorted_snaps:
        dt = parse_snapshot_datetime(s.get("timestamp") or s.get("created_at"))
        iso_year, iso_week, _ = dt.isocalendar()
        week_key = f"{iso_year}-W{iso_week:02d}"
        if week_key not in seen_weeks and len(seen_weeks) < keep_weekly:
            seen_weeks.add(week_key)
            keep_ids.add(s["id"])

    # 5. Monthly bucket (YYYY-MM)
    seen_months: Set[str] = set()
    for s in sorted_snaps:
        dt = parse_snapshot_datetime(s.get("timestamp") or s.get("created_at"))
        month_key = dt.strftime("%Y-%m")
        if month_key not in seen_months and len(seen_months) < keep_monthly:
            seen_months.add(month_key)
            keep_ids.add(s["id"])

    # Ensure referenced base snapshots are also preserved
    for base_id in base_ids_in_use:
        keep_ids.add(base_id)

    to_prune = [s for s in sorted_snaps if s["id"] not in keep_ids]
    return to_prune
