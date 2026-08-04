import datetime as dt
from zoneinfo import ZoneInfo

from eunomia import reconcile, todo
from eunomia.template import Occurrence

TZ = ZoneInfo("Europe/Madrid")
AHEAD = dt.timedelta(hours=48)
BACK = dt.timedelta(hours=12)


def _at(day: int, hour: int, minute: int = 0) -> dt.datetime:
    return dt.datetime(2026, 1, day, hour, minute, tzinfo=TZ)


def _occurrence(uid: str, day: int, hour: int, *steps: str) -> Occurrence:
    return Occurrence(
        source_uid=uid,
        recurrence_id=f"2026-01-{day:02d}T{hour:02d}:00:00+00:00",
        name=uid,
        start=_at(day, hour),
        end=_at(day, hour) + dt.timedelta(minutes=30),
        steps=steps,
    )


MORNING = _occurrence("morning", 5, 7, "wash face", "brush teeth")


def test_in_window_spans_ahead_and_back():
    assert reconcile.in_window(MORNING, _at(3, 7), AHEAD, BACK)  # exactly 48h before
    assert not reconcile.in_window(MORNING, _at(3, 6, 59), AHEAD, BACK)
    assert reconcile.in_window(MORNING, _at(5, 19, 29), AHEAD, BACK)
    assert not reconcile.in_window(MORNING, _at(5, 19, 30), AHEAD, BACK)  # end + 12h


def test_steps_enter_and_leave_together():
    inside = reconcile.desired_todos([MORNING], _at(5, 7), AHEAD, BACK)
    assert len(inside) == 2
    outside = reconcile.desired_todos([MORNING], _at(6, 12), AHEAD, BACK)
    assert outside == {}


def test_plan_never_touches_the_intersection():
    desired = reconcile.desired_todos([MORNING], _at(5, 7), AHEAD, BACK)
    ticked_off, still_open = sorted(desired)
    stale = todo.make_uid("yesterday", "2026-01-04T06:00:00+00:00", 0)

    plan = reconcile.plan(desired, {ticked_off, still_open, stale})

    assert plan.put == ()  # neither is re-PUT, so STATUS:COMPLETED survives
    assert plan.delete == (stale,)


def test_plan_creates_what_is_missing():
    desired = reconcile.desired_todos([MORNING], _at(5, 7), AHEAD, BACK)
    plan = reconcile.plan(desired, set())
    assert {item.uid for item in plan.put} == set(desired)
    assert plan.delete == ()


def test_empty_plan_is_falsy():
    assert not reconcile.plan({}, set())
    assert reconcile.plan({}, {"eunomia-deadbeef@eunomia"})


def test_next_wakeup_is_the_nearest_edge():
    later = _occurrence("evening", 5, 18)
    occurrences = [MORNING, later]

    # before anything is due: the first thing to happen is morning entering
    assert reconcile.next_wakeup(occurrences, _at(3, 0), AHEAD, BACK) == _at(3, 7)
    # once it is in, the next edge is the later block entering
    assert reconcile.next_wakeup(occurrences, _at(3, 7), AHEAD, BACK) == _at(3, 18)
    # once both are in, it is morning leaving, 12h after its end
    assert reconcile.next_wakeup(occurrences, _at(4, 0), AHEAD, BACK) == _at(5, 19, 30)


def test_next_wakeup_is_none_once_every_edge_has_passed():
    assert reconcile.next_wakeup([MORNING], _at(6, 0), AHEAD, BACK) is None
    assert reconcile.next_wakeup([], _at(5, 0), AHEAD, BACK) is None
