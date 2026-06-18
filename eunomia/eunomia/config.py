"""Runtime configuration, read from the environment.

The bot token can come from (in order): TELEGRAM_BOT_TOKEN, a file named by
EUNOMIA_TOKEN_FILE, or a systemd ``token`` credential ($CREDENTIALS_DIRECTORY).
The chat id (TELEGRAM_CHAT_ID) is optional — if unset, the bot adopts the first
chat that messages it. Neither is needed in dry-run mode. EUNOMIA_WRAPUP (HH:MM,
default 23:00; empty to disable) sets the nightly summary time.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from collections.abc import Mapping
from zoneinfo import ZoneInfo


def _read_token(env: Mapping[str, str]) -> str:
    token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    path = env.get("EUNOMIA_TOKEN_FILE")
    if not path and env.get("CREDENTIALS_DIRECTORY"):
        candidate = os.path.join(env["CREDENTIALS_DIRECTORY"], "token")
        if os.path.exists(candidate):
            path = candidate
    if path:
        with open(path) as f:
            return f.read().strip()
    return ""


def _parse_hhmm(spec: str) -> dt.time | None:
    spec = spec.strip()
    if not spec:
        return None
    hour, _, minute = spec.partition(":")
    return dt.time(int(hour), int(minute or 0))


@dataclass(frozen=True)
class Config:
    token: str
    chat_id: int  # 0 meaning "adopt the first chat that messages me"
    routine_path: str
    db_path: str
    tz: dt.tzinfo
    dry_run: bool
    wrapup: dt.time | None  # nightly summary time; None disables

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        tz_name = env.get("EUNOMIA_TZ")
        tz = ZoneInfo(tz_name) if tz_name else dt.datetime.now().astimezone().tzinfo
        assert tz is not None
        dry_run = env.get("EUNOMIA_DRY_RUN", "").lower() in ("1", "true", "yes")

        token = _read_token(env)
        if not dry_run and not token:
            raise SystemExit(
                "No bot token: set TELEGRAM_BOT_TOKEN or EUNOMIA_TOKEN_FILE "
                + "(or set EUNOMIA_DRY_RUN=1)"
            )

        chat_raw = env.get("TELEGRAM_CHAT_ID", "").strip()
        return cls(
            token=token,
            chat_id=int(chat_raw) if chat_raw else 0,
            routine_path=env.get("EUNOMIA_ROUTINE", "routine.toml"),
            db_path=env.get("EUNOMIA_DB", "eunomia.db"),
            tz=tz,
            dry_run=dry_run,
            wrapup=_parse_hhmm(env.get("EUNOMIA_WRAPUP", "23:00")),
        )
