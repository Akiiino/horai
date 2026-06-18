import datetime as dt
import sqlite3
from collections.abc import Iterator

import pytest

from eunomia import store, template

TZ = dt.timezone.utc


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    c = store.connect(":memory:")
    yield c
    c.close()


def _block(
    name: str = "Morning", steps: tuple[str, ...] = ("a", "b")
) -> template.Block:
    return template.Block(
        name=name,
        start=dt.time(7, 0),
        end=dt.time(8, 0),
        days=frozenset(range(7)),
        steps=tuple(steps),
    )


def test_upsert_is_idempotent_and_creates_steps(conn: sqlite3.Connection):
    block = _block()
    date = dt.date(2026, 6, 18)
    start = template.start_at(block, date, TZ)

    iid, created = store.upsert_instance(conn, block, date, start)
    assert created
    assert len(store.steps(conn, iid)) == 2

    iid2, created2 = store.upsert_instance(conn, block, date, start)
    assert iid2 == iid
    assert not created2
    assert len(store.steps(conn, iid)) == 2  # no duplicates


def test_status_transitions_record_response_time(conn: sqlite3.Connection):
    iid, _ = store.upsert_instance(
        conn,
        _block(steps=()),
        dt.date(2026, 6, 18),
        dt.datetime(2026, 6, 18, 7, tzinfo=TZ),
    )
    now = dt.datetime(2026, 6, 18, 7, 30, tzinfo=TZ)
    store.set_status(conn, iid, store.DONE, now=now)
    row = store.get_instance(conn, iid)
    assert row is not None
    assert row["status"] == store.DONE
    assert row["responded_at"] == now.isoformat()


def test_toggle_step_and_first_undone(conn: sqlite3.Connection):
    iid, _ = store.upsert_instance(
        conn,
        _block(steps=("x", "y")),
        dt.date(2026, 6, 18),
        dt.datetime(2026, 6, 18, 7, tzinfo=TZ),
    )
    assert store.first_undone_step(conn, iid) == "x"
    store.toggle_step(conn, iid, 0)
    assert store.first_undone_step(conn, iid) == "y"
    store.toggle_step(conn, iid, 1)
    assert store.first_undone_step(conn, iid) is None
    store.toggle_step(conn, iid, 0)  # untoggle
    assert store.first_undone_step(conn, iid) == "x"


def test_bump_nag(conn: sqlite3.Connection):
    iid, _ = store.upsert_instance(
        conn,
        _block(steps=()),
        dt.date(2026, 6, 18),
        dt.datetime(2026, 6, 18, 7, tzinfo=TZ),
    )
    assert store.bump_nag(conn, iid) == 1
    assert store.bump_nag(conn, iid) == 2


def test_reschedule_resets_pending_and_nag(conn: sqlite3.Connection):
    iid, _ = store.upsert_instance(
        conn,
        _block(steps=()),
        dt.date(2026, 6, 18),
        dt.datetime(2026, 6, 18, 7, tzinfo=TZ),
    )
    store.bump_nag(conn, iid)
    store.set_status(conn, iid, store.MISSED)
    new = dt.datetime(2026, 6, 18, 9, tzinfo=TZ)
    store.reschedule(conn, iid, new)
    row = store.get_instance(conn, iid)
    assert row is not None
    assert row["status"] == store.PENDING
    assert row["nag_count"] == 0
    assert row["scheduled_start"] == new.isoformat()


def test_mark_missed_before_only_past_pending(conn: sqlite3.Connection):
    block = _block(steps=())
    store.upsert_instance(
        conn, block, dt.date(2026, 6, 16), dt.datetime(2026, 6, 16, 7, tzinfo=TZ)
    )
    today_id, _ = store.upsert_instance(
        conn, block, dt.date(2026, 6, 18), dt.datetime(2026, 6, 18, 7, tzinfo=TZ)
    )

    n = store.mark_missed_before(conn, dt.date(2026, 6, 18))
    assert n == 1
    row = store.get_instance(conn, today_id)
    assert row is not None
    assert row["status"] == store.PENDING


def test_adherence_counts(conn: sqlite3.Connection):
    block = _block(steps=())
    a, _ = store.upsert_instance(
        conn, block, dt.date(2026, 6, 16), dt.datetime(2026, 6, 16, 7, tzinfo=TZ)
    )
    b, _ = store.upsert_instance(
        conn, block, dt.date(2026, 6, 17), dt.datetime(2026, 6, 17, 7, tzinfo=TZ)
    )
    store.set_status(conn, a, store.DONE)
    store.set_status(conn, b, store.MISSED)
    rows = store.adherence(conn, dt.date(2026, 6, 1))
    assert dict((r["block_name"], (r["done"], r["total"])) for r in rows) == {
        "Morning": (1, 2)
    }
