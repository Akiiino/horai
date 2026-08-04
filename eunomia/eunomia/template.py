"""The routine source: expand a CalDAV routine calendar into dated Occurrences.

The routine lives in a RustiCal calendar as VEVENTs. Recurrence is owned by the
calendar and expanded faithfully by ``recurring_ical_events`` (single events fire
once; RRULE ``INTERVAL``/``COUNT``/``UNTIL`` and ``RDATE``/``EXDATE`` are honored,
including several occurrences in one day) — so an Occurrence exists for a moment
iff the calendar produces one there.

An ``Occurrence`` is one resolved instance, carrying *absolute* datetimes: a
window can span midnight, which a time-of-day cannot express. ``expand_window``
is pure (ical text in, list out), so it unit-tests without a server. Steps ride
in the event DESCRIPTION as lines starting ``- `` or ``* ``.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import icalendar
import recurring_ical_events  # pyright: ignore[reportMissingTypeStubs]


@dataclass(frozen=True)
class Occurrence:
    """One resolved, dated instance of a routine block."""

    source_uid: str  # UID of the VEVENT this came from
    recurrence_id: str  # which instance of that VEVENT, as a UTC timestamp
    name: str
    start: dt.datetime
    end: dt.datetime
    steps: tuple[str, ...] = ()


def parse_description(text: str) -> tuple[str, ...]:
    """The checklist steps in an event DESCRIPTION.

    Lines beginning ``- `` or ``* `` are steps; anything else is ignored, so the
    description stays human-editable in any calendar app.
    """
    steps: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line[:2] in ("- ", "* "):
            steps.append(line[2:].strip())
    return tuple(steps)


def _aware(value: dt.datetime, tz: dt.tzinfo) -> dt.datetime:
    """``value`` in ``tz``; a floating (naive) time is read as local to it."""
    return value.replace(tzinfo=tz) if value.tzinfo is None else value.astimezone(tz)


def _occurrence(comp: icalendar.Component, tz: dt.tzinfo) -> Occurrence | None:
    start_field = comp.get("dtstart")
    if start_field is None or not isinstance(start_field.dt, dt.datetime):
        return None  # all-day / malformed events aren't timed routine blocks
    start = _aware(start_field.dt, tz)

    end_field = comp.get("dtend")
    if end_field is not None and isinstance(end_field.dt, dt.datetime):
        end = _aware(end_field.dt, tz)
    else:
        duration = comp.get("duration")
        end = start + duration.dt if duration is not None else start

    rid_field = comp.get("recurrence-id")
    rid = rid_field.dt if rid_field is not None else None
    instance = _aware(rid, tz) if isinstance(rid, dt.datetime) else start

    return Occurrence(
        source_uid=str(comp.get("uid") or ""),
        recurrence_id=instance.astimezone(dt.timezone.utc).isoformat(),
        name=str(comp.get("summary") or "(untitled)"),
        start=start,
        end=end,
        steps=parse_description(str(comp.get("description") or "")),
    )


def expand_window(
    calendar_ical: str, start: dt.datetime, end: dt.datetime, tz: dt.tzinfo
) -> list[Occurrence]:
    """Every routine occurrence overlapping ``[start, end)``, in starting order."""
    if not calendar_ical.strip():
        return []
    cal = icalendar.Calendar.from_ical(calendar_ical)
    expanded = recurring_ical_events.of(cal).between(start, end)
    occurrences = [o for c in expanded if (o := _occurrence(c, tz))]
    return sorted(occurrences, key=lambda o: (o.start, o.name, o.source_uid))
