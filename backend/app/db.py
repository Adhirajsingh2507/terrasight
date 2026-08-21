"""Supabase-backed data access with a mock-JSON fallback.

If SUPABASE_URL + SUPABASE_KEY are set, reads/writes go to Supabase; otherwise
the API serves backend/mock/*.json so the frontend never blocks on provisioning.
Use the service_role key on the backend (it bypasses RLS for writes).
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from functools import lru_cache

MOCK = Path(__file__).resolve().parent.parent / "mock"

# table -> mock file
_MOCK_FILE = {"tiles": "tiles.json", "rover_path": "path.json",
              "sites": "sites.json", "boundaries": "boundaries.json"}


@lru_cache(maxsize=1)
def _client():
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY")
    if not (url and key):
        return None
    from supabase import create_client  # lazy: only needed when configured
    return create_client(url, key)


def fetch(table: str) -> list[dict]:
    sb = _client()
    if sb is None:
        return json.loads((MOCK / _MOCK_FILE[table]).read_text())
    return sb.table(table).select("*").execute().data


def upsert(table: str, rows: list[dict]) -> int:
    """Backend/rover writes fused terrain. No-op if Supabase not configured."""
    sb = _client()
    if sb is None:
        return 0
    sb.table(table).upsert(rows).execute()
    return len(rows)


# Natural key per table used to make re-running the pipeline idempotent
# (full-map regenerate each run, so writes must replace, not accumulate).
# boundaries has no natural key (see schema.sql) -> handled as delete-all +
# insert in persist() below instead of an on_conflict upsert.
_NATURAL_KEY = {"tiles": "x,y", "sites": "id", "rover_path": "t"}


def persist(out: dict) -> dict[str, int]:
    """Full-map replace of pipeline output into Supabase. Idempotent: re-runs
    upsert natural-key tables in place and replace boundaries (delete-all,
    re-insert) so nothing duplicates or accumulates. No-op returning
    all-zero counts when Supabase isn't configured; never raises.
    """
    counts = {t: 0 for t in _MOCK_FILE}
    sb = _client()
    if sb is None:
        return counts
    for table, key in _NATURAL_KEY.items():
        rows = out.get(table) or []
        if rows:
            sb.table(table).upsert(rows, on_conflict=key).execute()
            counts[table] = len(rows)
    # boundaries: identity pk only, no natural key -> replace wholesale.
    sb.table("boundaries").delete().neq("id", 0).execute()
    rows = out.get("boundaries") or []
    if rows:
        sb.table("boundaries").insert(rows).execute()
        counts["boundaries"] = len(rows)
    return counts
