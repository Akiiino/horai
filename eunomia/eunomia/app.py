"""The daemon: fetch, expand, diff, apply, sleep until the next window edge.

That loop is the whole program. Eunomia keeps nothing between passes but a cached
copy of the routine (guarded by the collection's change tag) — no completion log,
no database, no memory of what it did yesterday. The list is derived afresh every
time, and the strict set difference in :mod:`.reconcile` means anything already
in the right place is left exactly as the phone last wrote it.

Failure is deliberately loud rather than clever: an unreachable server ends the
process and systemd restarts it. Nothing is lost by that, because today's tasks
are already on the phone and still tick off without us.
"""

from __future__ import annotations

import datetime as dt
import logging
import time

from . import reconcile, template
from .config import Config
from .dav import Dav
from .todo import render

log = logging.getLogger("eunomia")

# How far past the window's leading edge to expand. The loop has to see the
# occurrences that have *not* entered the window yet, or it could not know when
# the next one will; a week is far longer than any nap it takes between passes.
_LOOKAHEAD = dt.timedelta(days=7)


class Eunomia:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._dav = Dav(cfg)
        self._routine_tag = ""
        self._routine_ical: str | None = None

    def run(self) -> None:
        self._dav.connect()
        while True:
            now = dt.datetime.now(self._cfg.tz)
            occurrences = self._reconcile(now)
            if self._cfg.dry_run:
                return  # a dry run is one pass, so its plan can be read and compared
            self._nap(now, occurrences)

    def _reconcile(self, now: dt.datetime) -> list[template.Occurrence]:
        cfg = self._cfg
        occurrences = template.expand_window(
            self._routine(),
            now - cfg.back,
            now + cfg.ahead + _LOOKAHEAD,
            cfg.tz,
        )
        desired = reconcile.desired_todos(
            occurrences, now, cfg.ahead, cfg.back, cfg.stagger
        )
        self._apply(reconcile.plan(desired, self._dav.list_our_uids()))
        return occurrences

    def _routine(self) -> str:
        """The routine calendar, refetched only when the server says it moved."""
        tag = self._dav.routine_tag()
        if self._routine_ical is None or not tag or tag != self._routine_tag:
            self._routine_ical = self._dav.fetch_routine_ical()
            self._routine_tag = tag
        return self._routine_ical

    def _apply(self, plan: reconcile.Plan) -> None:
        if not plan:
            log.info("list is up to date")
            return
        prefix = "would " if self._cfg.dry_run else ""
        stamp = dt.datetime.now(dt.timezone.utc)
        for todo in plan.put:
            log.info(
                "%sPUT %s — %s (due %s)",
                prefix,
                todo.uid,
                todo.summary,
                todo.due.isoformat(timespec="minutes"),
            )
            if not self._cfg.dry_run:
                self._dav.put_todo(render(todo, stamp))
        for uid in plan.delete:
            log.info("%sDELETE %s", prefix, uid)
            if not self._cfg.dry_run:
                self._dav.delete_todo(uid)

    def _nap(self, now: dt.datetime, occurrences: list[template.Occurrence]) -> None:
        """Sleep until the next window edge, or the poll interval, whichever is first."""
        cfg = self._cfg
        deadline = now + cfg.poll
        edge = reconcile.next_wakeup(occurrences, now, cfg.ahead, cfg.back)
        if edge is not None and edge < deadline:
            deadline = edge
        log.debug("sleeping until %s", deadline.isoformat(timespec="seconds"))
        remaining = (deadline - dt.datetime.now(cfg.tz)).total_seconds()
        time.sleep(max(1.0, remaining))
