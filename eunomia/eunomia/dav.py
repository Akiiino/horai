"""The CalDAV network layer — the only part of Eunomia that talks to a server.

Two collections matter: the *routine* calendar (VEVENTs we expand into
Occurrences) and the *todo* task list (the VTODOs we derive from them). Nothing
else is read or written; in particular completion is never fetched, because the
object holding "done" is the same object deleted when its window passes.

Every write is scoped by UID shape (``eunomia-…@eunomia``), so the worst a bug
in the reconciler can do is churn its own tasks — anything else on the list is
invisible to it.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, cast

import caldav
import icalendar
from caldav.elements.base import ValuedBaseElement
from caldav.elements.dav import SyncToken
from caldav.lib import error as dav_error
from caldav.lib.namespace import ns
from caldav import Calendar, Principal

from .config import Config
from .todo import is_ours

_CTAG = "{http://calendarserver.org/ns/}getctag"
_SYNC_TOKEN = ns("D", "sync-token")


class _GetCTag(ValuedBaseElement):
    """calendarserver's ``getctag``: changes whenever the collection does."""

    tag: ClassVar[str | None] = _CTAG


class Dav:
    """A thin wrapper over a ``caldav`` client and its two resolved collections."""

    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._routine: Calendar | None = None
        self._todos: Calendar | None = None

    def connect(self) -> None:
        client = caldav.DAVClient(
            url=self._cfg.caldav_url,
            username=self._cfg.caldav_username,
            password=self._cfg.caldav_password,
        )
        principal = client.principal()  # pyright: ignore[reportUnknownMemberType]
        self._routine = _calendar_by_name(principal, self._cfg.routine_calendar)
        self._todos = _calendar_by_name(principal, self._cfg.todo_calendar)

    # ---- routine (in) ------------------------------------------------------

    def routine_tag(self) -> str:
        """A cheap change-detector for the routine collection, or "" if none.

        ``getctag`` and RFC 6578's ``sync-token`` both change whenever anything
        in a collection does; we ask for both in one PROPFIND and take whichever
        the server offers. An empty answer means the collection has to be
        refetched every pass — correct, just less frugal.
        """
        assert self._routine is not None, "connect() first"
        props = cast(
            "Mapping[str, str | None]",
            self._routine.get_properties([_GetCTag(), SyncToken()]) or {},
        )
        return props.get(_CTAG) or props.get(_SYNC_TOKEN) or ""

    def fetch_routine_ical(self) -> str:
        """The routine calendar's VEVENTs (and their VTIMEZONEs) as one document.

        Merged into a single VCALENDAR so ``template.expand_window`` can hand the
        whole thing to ``recurring_ical_events`` in one pass.
        """
        assert self._routine is not None, "connect() first"
        merged = icalendar.Calendar()
        merged.add("prodid", "-//eunomia//eunomia//EN")
        merged.add("version", "2.0")
        seen_tz: set[str] = set()
        for event in self._routine.events():
            for comp in event.icalendar_instance.subcomponents:
                if comp.name == "VEVENT":
                    merged.add_component(comp)
                elif comp.name == "VTIMEZONE":
                    tzid = str(comp.get("tzid", ""))
                    if tzid and tzid not in seen_tz:
                        seen_tz.add(tzid)
                        merged.add_component(comp)
        return merged.to_ical().decode()

    # ---- todos (out) -------------------------------------------------------

    def list_our_uids(self) -> set[str]:
        """UIDs of our VTODOs currently on the list, completed ones included.

        Completed tasks must be counted as present: forgetting one would have
        the next pass re-create it, wiping the tick that was just made.
        """
        assert self._todos is not None, "connect() first"
        uids: set[str] = set()
        for todo in self._todos.todos(include_completed=True):
            comp = todo.icalendar_component
            if comp is None:
                continue
            uid = str(comp.get("uid", ""))
            if is_ours(uid):
                uids.add(uid)
        return uids

    def put_todo(self, ical: str) -> None:
        """Create the VTODO in ``ical`` (a PUT, keyed by its UID)."""
        assert self._todos is not None, "connect() first"
        self._todos.save_todo(ical=ical)  # pyright: ignore[reportUnknownMemberType]

    def delete_todo(self, uid: str) -> None:
        assert self._todos is not None, "connect() first"
        try:
            self._todos.todo_by_uid(uid).delete()
        except dav_error.NotFoundError:
            pass  # someone else got there first; the end state is what we wanted


def _calendar_by_name(principal: Principal, name: str) -> Calendar:
    """The collection whose *display name* is ``name`` — not its id, which the
    server may set independently, so the failure lists what is actually there."""
    available: list[str] = []
    for cal in principal.calendars():
        if (cal.name or "") == name:
            return cal
        available.append(cal.name or "")
    raise RuntimeError(
        f"no CalDAV collection named {name!r}; this principal has {available}"
    )
