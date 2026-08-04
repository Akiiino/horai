"""Runtime configuration, read from the environment.

Eunomia is a stateless adapter between two CalDAV collections, so everything it
needs is a URL, a credential and two collection names. The CalDAV password comes
from (in order): EUNOMIA_CALDAV_PASSWORD, a file named by
EUNOMIA_CALDAV_PASSWORD_FILE, or a systemd ``caldav-password`` credential under
$CREDENTIALS_DIRECTORY. It is required even in dry-run mode, which still reads
the routine — it only withholds the writes.

Durations (EUNOMIA_WINDOW_AHEAD, EUNOMIA_WINDOW_BACK, EUNOMIA_STEP_STAGGER,
EUNOMIA_POLL) are written as a number with an optional ``s``/``m``/``h``/``d``
suffix; a bare number is seconds.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass
from collections.abc import Mapping
from zoneinfo import ZoneInfo


def _read_password(env: Mapping[str, str]) -> str:
    """The CalDAV password, inline, from a file, or from a systemd credential."""
    inline = env.get("EUNOMIA_CALDAV_PASSWORD", "").strip()
    if inline:
        return inline
    path = env.get("EUNOMIA_CALDAV_PASSWORD_FILE")
    if not path and env.get("CREDENTIALS_DIRECTORY"):
        candidate = os.path.join(env["CREDENTIALS_DIRECTORY"], "caldav-password")
        if os.path.exists(candidate):
            path = candidate
    if path:
        with open(path) as f:
            return f.read().strip()
    return ""


_DURATION_RE = re.compile(r"\A(\d+)([smhd]?)\Z", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(spec: str, var: str) -> dt.timedelta:
    match = _DURATION_RE.match(spec.strip())
    if match is None:
        raise SystemExit(
            f"{var}: expected a duration like 48h, 90m or 3600; got {spec!r}"
        )
    return dt.timedelta(seconds=int(match[1]) * _UNIT_SECONDS[match[2].lower()])


@dataclass(frozen=True)
class Config:
    caldav_url: str
    caldav_username: str
    caldav_password: str
    routine_calendar: str  # CalDAV calendar holding the routine VEVENTs
    todo_calendar: str  # CalDAV task list the VTODOs are reconciled into
    ahead: dt.timedelta  # how long before an occurrence starts its tasks appear
    back: dt.timedelta  # how long after it ends they are removed
    stagger: dt.timedelta  # how far apart a block's steps are placed, to order them
    poll: dt.timedelta  # longest nap between checks for routine edits
    tz: dt.tzinfo
    dry_run: bool  # print one pass's plan and exit, changing nothing

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        tz_name = env.get("EUNOMIA_TZ")
        tz = ZoneInfo(tz_name) if tz_name else dt.datetime.now().astimezone().tzinfo
        assert tz is not None

        caldav_url = env.get("EUNOMIA_CALDAV_URL", "").strip()
        caldav_username = env.get("EUNOMIA_CALDAV_USER", "").strip()
        caldav_password = _read_password(env)
        if not (caldav_url and caldav_username and caldav_password):
            raise SystemExit(
                "CalDAV is required: set EUNOMIA_CALDAV_URL, EUNOMIA_CALDAV_USER, "
                + "and EUNOMIA_CALDAV_PASSWORD (or EUNOMIA_CALDAV_PASSWORD_FILE)"
            )

        return cls(
            caldav_url=caldav_url,
            caldav_username=caldav_username,
            caldav_password=caldav_password,
            routine_calendar=env.get("EUNOMIA_CALDAV_ROUTINE", "Routine"),
            todo_calendar=env.get("EUNOMIA_CALDAV_TODOS", "Eunomia"),
            ahead=parse_duration(
                env.get("EUNOMIA_WINDOW_AHEAD", "48h"), "EUNOMIA_WINDOW_AHEAD"
            ),
            back=parse_duration(
                env.get("EUNOMIA_WINDOW_BACK", "12h"), "EUNOMIA_WINDOW_BACK"
            ),
            stagger=parse_duration(
                env.get("EUNOMIA_STEP_STAGGER", "1s"), "EUNOMIA_STEP_STAGGER"
            ),
            poll=parse_duration(env.get("EUNOMIA_POLL", "5m"), "EUNOMIA_POLL"),
            tz=tz,
            dry_run=env.get("EUNOMIA_DRY_RUN", "").lower() in ("1", "true", "yes"),
        )
