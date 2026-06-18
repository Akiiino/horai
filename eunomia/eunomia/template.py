"""The schedule template: parse routine.toml into Blocks and answer time queries.

Everything here is pure (no I/O beyond reading the given file, no clock, no
network) so it can be unit-tested directly. A future read-only calendar source
only needs to produce a list[Block] to slot in.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
import tomllib
from dataclasses import dataclass
from typing import Any, cast

_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


@dataclass(frozen=True)
class Block:
    name: str
    start: dt.time
    end: dt.time
    days: frozenset[int]  # weekday ints, Monday=0
    steps: tuple[str, ...] = ()
    anchor: bool = True
    nag_every_min: int = 10
    nag_max: int = 3


def parse_days(spec: Any) -> frozenset[int]:
    items: list[Any]
    if isinstance(spec, list):
        items = cast("list[Any]", spec)
    else:
        s = str(spec).strip().lower()
        if s in ("daily", "every", "everyday", "all"):
            return frozenset(range(7))
        if s == "weekdays":
            return frozenset(range(5))
        if s == "weekends":
            return frozenset({5, 6})
        items = [p for p in s.split(",") if p.strip()]
    out: set[int] = set()
    for item in items:
        key = str(item).strip().lower()[:3]
        if key not in _WEEKDAYS:
            raise ValueError(f"unknown day: {item!r}")
        out.add(_WEEKDAYS[key])
    if not out:
        raise ValueError("a block must be active on at least one day")
    return frozenset(out)


_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def format_days(days: frozenset[int]) -> str:
    """Human-readable inverse of :func:`parse_days`."""
    if days == frozenset(range(7)):
        return "daily"
    if days == frozenset(range(5)):
        return "weekdays"
    if days == frozenset({5, 6}):
        return "weekends"
    return ",".join(_DAY_NAMES[d] for d in sorted(days))


def parse_time(spec: Any) -> dt.time:
    hour, _, minute = str(spec).partition(":")
    return dt.time(int(hour), int(minute or 0))


def parse_block(raw: dict[str, Any]) -> Block:
    nag: dict[str, Any] = raw.get("nag") or {}
    return Block(
        name=str(raw["name"]),
        start=parse_time(raw["start"]),
        end=parse_time(raw["end"]),
        days=parse_days(raw.get("days", "daily")),
        steps=tuple(str(s) for s in raw.get("steps", [])),
        anchor=bool(raw.get("anchor", True)),
        nag_every_min=int(nag.get("every_min", 10)),
        nag_max=int(nag.get("max", 3)),
    )


def parse_blocks(data: dict[str, Any]) -> list[Block]:
    return [parse_block(b) for b in data.get("block", [])]


def load_blocks(path: str | Path) -> list[Block]:
    with open(path, "rb") as f:
        return parse_blocks(tomllib.load(f))


def active_on(block: Block, date: dt.date) -> bool:
    return date.weekday() in block.days


def start_at(block: Block, date: dt.date, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(date, block.start, tzinfo=tz)


def end_at(block: Block, date: dt.date, tz: dt.tzinfo) -> dt.datetime:
    return dt.datetime.combine(date, block.end, tzinfo=tz)


def current_block(blocks: list[Block], when: dt.datetime) -> Block | None:
    """The block whose [start, end) contains ``when`` today.

    If several overlap, the one that started most recently wins.
    """
    here = [
        b
        for b in blocks
        if active_on(b, when.date()) and b.start <= when.time() < b.end
    ]
    return max(here, key=lambda b: b.start, default=None)


def next_block(blocks: list[Block], when: dt.datetime) -> Block | None:
    """The next block to start later today."""
    later = [b for b in blocks if active_on(b, when.date()) and b.start > when.time()]
    return min(later, key=lambda b: b.start, default=None)
