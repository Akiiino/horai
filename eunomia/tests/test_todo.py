import datetime as dt
from zoneinfo import ZoneInfo

import icalendar

from eunomia import todo
from eunomia.template import Occurrence

TZ = ZoneInfo("Europe/Madrid")
STAMP = dt.datetime(2026, 1, 5, 6, 0, tzinfo=dt.timezone.utc)


def _occurrence(*steps: str) -> Occurrence:
    return Occurrence(
        source_uid="morning@test",
        recurrence_id="2026-01-05T06:30:00+00:00",
        name="Morning Routine",
        start=dt.datetime(2026, 1, 5, 7, 30, tzinfo=TZ),
        end=dt.datetime(2026, 1, 5, 7, 50, tzinfo=TZ),
        steps=steps,
    )


def _rendered(item: todo.Todo) -> icalendar.Todo:
    parsed = icalendar.Calendar.from_ical(todo.render(item, STAMP))
    (component,) = [c for c in parsed.subcomponents if c.name == "VTODO"]
    return component


def test_uid_is_deterministic_and_scoped():
    first = todo.make_uid("morning@test", "2026-01-05T06:30:00+00:00", 0)
    again = todo.make_uid("morning@test", "2026-01-05T06:30:00+00:00", 0)
    assert first == again
    assert todo.is_ours(first)
    assert not todo.is_ours("something-else@elsewhere")


def test_uid_separates_steps_and_instances():
    uids = {
        todo.make_uid("morning@test", "2026-01-05T06:30:00+00:00", 0),
        todo.make_uid("morning@test", "2026-01-05T06:30:00+00:00", 1),
        todo.make_uid("morning@test", "2026-01-06T06:30:00+00:00", 0),
        todo.make_uid("lunch@test", "2026-01-05T06:30:00+00:00", 0),
    }
    assert len(uids) == 4


def test_block_without_steps_becomes_one_task():
    (item,) = todo.todos_for(_occurrence())
    assert item.summary == "Morning Routine"
    assert item.category == ""
    assert item.start == dt.datetime(2026, 1, 5, 7, 30, tzinfo=TZ)
    assert item.due == dt.datetime(2026, 1, 5, 7, 50, tzinfo=TZ)


def test_steps_become_staggered_tasks_tagged_with_the_block():
    items = todo.todos_for(_occurrence("wash face", "brush teeth", "shower"))
    assert [i.summary for i in items] == ["wash face", "brush teeth", "shower"]
    assert {i.category for i in items} == {"Morning Routine"}
    # both fields stagger, so either sort order keeps the steps in sequence
    assert [i.start for i in items] == [
        dt.datetime(2026, 1, 5, 7, 30, n, tzinfo=TZ) for n in range(3)
    ]
    assert [i.due for i in items] == [
        dt.datetime(2026, 1, 5, 7, 50, n, tzinfo=TZ) for n in range(3)
    ]
    # the whole block still arrives as one burst at its own time
    assert items[-1].start - items[0].start < dt.timedelta(minutes=1)


def test_stagger_cannot_push_a_start_past_its_due():
    packed = _occurrence(*[f"step {n}" for n in range(40)])
    items = todo.todos_for(packed, stagger=dt.timedelta(minutes=1))
    assert all(i.start < i.due for i in items)


def test_render_shape():
    (item,) = todo.todos_for(_occurrence())
    component = _rendered(item)
    assert str(component["UID"]) == item.uid
    assert str(component["SUMMARY"]) == "Morning Routine"
    assert component.decoded("DTSTART") == item.start
    assert component.decoded("DUE") == item.due
    assert str(component["STATUS"]) == "NEEDS-ACTION"
    assert "CATEGORIES" not in component


def test_render_carries_the_block_name_as_a_category():
    items = todo.todos_for(_occurrence("wash face"))
    component = _rendered(items[0])
    assert component["CATEGORIES"].to_ical().decode() == "Morning Routine"


def _alarms(item: todo.Todo) -> list[icalendar.Alarm]:
    return [c for c in _rendered(item).subcomponents if c.name == "VALARM"]


def test_render_emits_exactly_one_alarm_at_the_start():
    (item,) = todo.todos_for(_occurrence())
    (alarm,) = _alarms(item)
    assert alarm.decoded("TRIGGER") == dt.timedelta(0)
    assert alarm["TRIGGER"].params["RELATED"] == "START"
    assert "REPEAT" not in alarm  # one notification, never a nag


def test_every_step_announces_itself():
    items = todo.todos_for(_occurrence("wash face", "brush teeth", "shower"))
    assert [len(_alarms(i)) for i in items] == [1, 1, 1]
    assert [str(_alarms(i)[0]["DESCRIPTION"]) for i in items] == [
        "wash face",
        "brush teeth",
        "shower",
    ]
