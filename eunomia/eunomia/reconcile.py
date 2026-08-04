"""What to change on the list, and when to look again. Pure, no I/O.

The reconciliation is a strict set difference: UIDs that should exist but don't
are created, UIDs that exist but shouldn't are deleted, and the intersection is
*never touched*. That last part is load-bearing — a blind upsert would rewrite
every task on every pass and un-tick everything checked off since the last one.
Because the object holding "done" is the same object deleted when the window
passes, completion is never read, and there is nothing to remember.

``next_wakeup`` is the other half of statelessness: instead of polling, the loop
computes the next instant at which any occurrence enters or leaves the window
and sleeps until exactly then.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass

from .template import Occurrence
from .todo import STAGGER, Todo, todos_for


@dataclass(frozen=True)
class Plan:
    put: tuple[Todo, ...]
    delete: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.put or self.delete)


def in_window(
    occurrence: Occurrence, now: dt.datetime, ahead: dt.timedelta, back: dt.timedelta
) -> bool:
    """Whether an occurrence should have tasks on the list at ``now``.

    Membership is decided per occurrence, so a block's steps appear and vanish
    together rather than drifting apart with their staggered times.
    """
    return occurrence.start - ahead <= now < occurrence.end + back


def desired_todos(
    occurrences: Iterable[Occurrence],
    now: dt.datetime,
    ahead: dt.timedelta,
    back: dt.timedelta,
    stagger: dt.timedelta = STAGGER,
) -> dict[str, Todo]:
    """Every task the list should hold at ``now``, keyed by UID."""
    return {
        todo.uid: todo
        for occurrence in occurrences
        if in_window(occurrence, now, ahead, back)
        for todo in todos_for(occurrence, stagger)
    }


def plan(desired: Mapping[str, Todo], present: Set[str]) -> Plan:
    """The changes taking the list from ``present`` to ``desired``.

    Creations come out in the order they will be worked through, which is
    nothing to the server but makes a dry run readable.
    """
    missing = desired.keys() - present
    return Plan(
        put=tuple(sorted((desired[uid] for uid in missing), key=_when)),
        delete=tuple(sorted(present - desired.keys())),
    )


def _when(todo: Todo) -> tuple[dt.datetime, str]:
    return (todo.start, todo.uid)


def next_wakeup(
    occurrences: Iterable[Occurrence],
    now: dt.datetime,
    ahead: dt.timedelta,
    back: dt.timedelta,
) -> dt.datetime | None:
    """The earliest future instant at which the plan could change.

    That is the nearest window edge — an occurrence's ``start - ahead`` or its
    ``end + back``. ``None`` when no known occurrence has an edge left, in which
    case the caller falls back to its poll interval.
    """
    edges = [
        edge
        for occurrence in occurrences
        for edge in (occurrence.start - ahead, occurrence.end + back)
        if edge > now
    ]
    return min(edges, default=None)
