import datetime as dt

import pytest

from eunomia import template


def test_parse_days_keywords():
    assert template.parse_days("daily") == frozenset(range(7))
    assert template.parse_days("weekdays") == frozenset(range(5))
    assert template.parse_days("weekends") == frozenset({5, 6})


def test_parse_days_list_and_csv():
    assert template.parse_days("mon,wed,fri") == frozenset({0, 2, 4})
    assert template.parse_days(["Mon", "Tuesday"]) == frozenset({0, 1})


def test_parse_days_rejects_garbage():
    with pytest.raises(ValueError):
        template.parse_days("someday")


def test_parse_block_defaults_and_nag():
    block = template.parse_block(
        {"name": "Study", "start": "18:00", "end": "19:00", "days": "mon,fri"}
    )
    assert block.start == dt.time(18, 0)
    assert block.steps == ()
    assert block.anchor is True
    assert (block.nag_every_min, block.nag_max) == (10, 3)

    custom = template.parse_block(
        {
            "name": "X",
            "start": "9:5",
            "end": "10:00",
            "nag": {"every_min": 5, "max": 1},
        }
    )
    assert custom.start == dt.time(9, 5)
    assert (custom.nag_every_min, custom.nag_max) == (5, 1)


def _blocks():
    raw = {
        "block": [
            {"name": "Morning", "start": "07:00", "end": "08:00", "days": "daily"},
            {"name": "Lunch", "start": "13:00", "end": "14:00", "days": "daily"},
            {"name": "Overlap", "start": "13:30", "end": "14:00", "days": "daily"},
        ]
    }
    return template.parse_blocks(raw)


def test_current_block_picks_latest_overlap():
    blocks = _blocks()
    when = dt.datetime(2026, 6, 18, 13, 40)  # Thursday, inside Lunch and Overlap
    assert template.current_block(blocks, when).name == "Overlap"


def test_current_block_none_when_idle():
    when = dt.datetime(2026, 6, 18, 10, 0)
    assert template.current_block(_blocks(), when) is None


def test_next_block():
    when = dt.datetime(2026, 6, 18, 10, 0)
    assert template.next_block(_blocks(), when).name == "Lunch"


def test_active_on_respects_weekday():
    block = template.parse_block(
        {"name": "Wkday", "start": "09:00", "end": "10:00", "days": "weekdays"}
    )
    assert template.active_on(block, dt.date(2026, 6, 18))  # Thursday
    assert not template.active_on(block, dt.date(2026, 6, 20))  # Saturday
