"""Occurrences in, VTODOs out — the whole derivation, and it is a pure function.

A block with steps becomes one task *per step*, with the block name carried as a
category rather than a prefix: ``wash face`` reads at a glance on a watch, while
``Morning Routine`` stays available as a tag. A block without steps becomes one
task named after the block.

Steps are nudged one second apart in *both* DTSTART and DUE. That is enough to
fix their order whichever field the client sorts by, and keeps DTSTART from ever
overtaking DUE on a block with more steps than it has room for, while being far
too small to read as a schedule.

Every task carries a DISPLAY alarm at its own start, so each step announces
itself and the phone can stay in the pocket. A second apart, a block's steps
arrive as one burst to be dismissed in order, revealing the next step at a time.
No repeats — that is the part that made the old design a nag.

UIDs are derived, never stored: the same occurrence always hashes to the same
UID, so identity survives restarts without a database. They are scoped as
``eunomia-…@eunomia`` so reconciliation can recognise its own work and leave
everything else on the list alone.
"""

from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import dataclass

import icalendar

from .template import Occurrence

UID_PREFIX = "eunomia-"
UID_SUFFIX = "@eunomia"

#: How far apart consecutive steps of one block are placed, purely to fix their
#: order. Raise it only if a client rounds two steps together and scrambles them.
STAGGER = dt.timedelta(seconds=1)

_PRODID = "-//eunomia//eunomia//EN"


@dataclass(frozen=True)
class Todo:
    uid: str
    summary: str
    start: dt.datetime
    due: dt.datetime
    category: str  # the block name, empty for a block without steps


def make_uid(source_uid: str, recurrence_id: str, step_index: int) -> str:
    """The deterministic UID of one derived task."""
    key = f"{source_uid}\n{recurrence_id}\n{step_index}".encode()
    return f"{UID_PREFIX}{hashlib.sha256(key).hexdigest()[:16]}{UID_SUFFIX}"


def is_ours(uid: str) -> bool:
    """Whether ``uid`` has the shape reconciliation is allowed to delete."""
    return uid.startswith(UID_PREFIX) and uid.endswith(UID_SUFFIX)


def todos_for(occurrence: Occurrence, stagger: dt.timedelta = STAGGER) -> list[Todo]:
    """The tasks an occurrence should put on the list."""
    if not occurrence.steps:
        return [
            Todo(
                uid=make_uid(occurrence.source_uid, occurrence.recurrence_id, 0),
                summary=occurrence.name,
                start=occurrence.start,
                due=occurrence.end,
                category="",
            )
        ]
    return [
        Todo(
            uid=make_uid(occurrence.source_uid, occurrence.recurrence_id, index),
            summary=step,
            start=occurrence.start + index * stagger,
            due=occurrence.end + index * stagger,
            category=occurrence.name,
        )
        for index, step in enumerate(occurrence.steps)
    ]


def render(todo: Todo, dtstamp: dt.datetime) -> str:
    """``todo`` as a complete iCalendar document, ready to PUT."""
    component = icalendar.Todo()
    component.add("uid", todo.uid)
    component.add("dtstamp", dtstamp)
    component.add("summary", todo.summary)
    component.add("dtstart", todo.start)
    component.add("due", todo.due)
    component.add("status", "NEEDS-ACTION")
    if todo.category:
        component.add("categories", [todo.category])

    alarm = icalendar.Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", todo.summary)
    alarm.add("trigger", dt.timedelta(0), parameters={"RELATED": "START"})
    component.add_component(alarm)

    calendar = icalendar.Calendar()
    calendar.add("prodid", _PRODID)
    calendar.add("version", "2.0")
    calendar.add_component(component)
    return calendar.to_ical().decode()
