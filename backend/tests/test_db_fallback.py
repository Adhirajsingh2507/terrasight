"""TerraSight mock-fallback regression test (db.py).

Locks the fallback path: with Supabase unconfigured, fetch() must serve
backend/mock/*.json verbatim and upsert()/persist() must be a safe no-op —
never raise, never write. Also guards that _MOCK_FILE covers every table
main.py serves, so a new endpoint can't silently ship without a mock fallback.

Runnable two ways:
    python backend/tests/test_db_fallback.py   # plain asserts
    pytest backend/tests                        # collected as test_*
"""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app import db, main                                   # noqa: E402


def _unconfigured_env():
    """Snapshot current env, strip Supabase vars, clear the lru_cache.

    Returns the saved env so the caller can restore it exactly.
    """
    saved = dict(os.environ)
    os.environ.pop("SUPABASE_URL", None)
    os.environ.pop("SUPABASE_KEY", None)
    db._client.cache_clear()
    return saved


def _restore_env(saved):
    os.environ.clear()
    os.environ.update(saved)
    db._client.cache_clear()


def test_client_none_when_unconfigured():
    saved = _unconfigured_env()
    try:
        assert db._client() is None
    finally:
        _restore_env(saved)


def test_fetch_returns_mock_for_every_table():
    saved = _unconfigured_env()
    try:
        for table, filename in db._MOCK_FILE.items():
            rows = db.fetch(table)
            expected = json.loads((db.MOCK / filename).read_text())
            assert isinstance(rows, list) and rows, f"{table}: expected non-empty list"
            assert all(isinstance(r, dict) for r in rows), f"{table}: rows must be objects"
            assert rows == expected, f"{table}: fetch() drifted from mock/{filename}"
    finally:
        _restore_env(saved)


def test_upsert_is_noop_when_unconfigured():
    saved = _unconfigured_env()
    try:
        before = json.loads((db.MOCK / "tiles.json").read_text())
        result = db.upsert("tiles", [{"x": 0, "y": 0}])
        assert result == 0
        after = json.loads((db.MOCK / "tiles.json").read_text())
        assert after == before, "upsert() must never write the mock file"
    finally:
        _restore_env(saved)


def test_persist_is_noop_when_unconfigured():
    """db.persist must be a safe no-op when Supabase isn't configured: never
    raise, return all-zero counts, and never touch the mock/*.json files
    (persist is the Supabase write path, distinct from --write's mock path).
    The live Supabase path (upsert/delete/insert against a real project)
    can't be exercised here — no creds in CI/dev; this only locks the
    fallback contract.
    """
    saved = _unconfigured_env()
    try:
        before = {f: (db.MOCK / f).read_text() for f in db._MOCK_FILE.values()}
        out = {"tiles": [{"x": 0, "y": 0}], "sites": [{"id": "S1"}],
               "rover_path": [{"t": 0}], "boundaries": [{"type": "crater"}]}
        counts = db.persist(out)
        assert counts == {"tiles": 0, "sites": 0, "rover_path": 0, "boundaries": 0}
        for fname, text in before.items():
            assert (db.MOCK / fname).read_text() == text, f"persist() must never write mock/{fname}"
    finally:
        _restore_env(saved)


def test_mock_file_covers_every_served_table():
    """_MOCK_FILE must match exactly the tables main.py fetches — a new
    endpoint can't silently lack a mock fallback."""
    import inspect
    served = set()
    for _name, fn in inspect.getmembers(main, inspect.isfunction):
        if fn.__module__ != main.__name__:
            continue
        src = inspect.getsource(fn)
        for table in ("tiles", "rover_path", "sites", "boundaries"):
            if f'db.fetch("{table}")' in src:
                served.add(table)
    assert served, "no db.fetch(...) calls found in app.main — test is broken"
    assert served == set(db._MOCK_FILE), (
        f"main.py serves {served} but db._MOCK_FILE covers {set(db._MOCK_FILE)}"
    )


if __name__ == "__main__":
    test_client_none_when_unconfigured()
    test_fetch_returns_mock_for_every_table()
    test_upsert_is_noop_when_unconfigured()
    test_persist_is_noop_when_unconfigured()
    test_mock_file_covers_every_served_table()
    print("db fallback ok")
