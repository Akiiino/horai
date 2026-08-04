import datetime as dt
from zoneinfo import ZoneInfo

from eunomia import template

TZ = ZoneInfo("Europe/Madrid")

MORNING = """BEGIN:VEVENT
UID:morning@test
SUMMARY:Morning Routine
DTSTART;TZID=Europe/Madrid:20260101T073000
DTEND;TZID=Europe/Madrid:20260101T075000
RRULE:FREQ=DAILY
DESCRIPTION:- wash face\\n- brush teeth\\nnot a step
END:VEVENT
"""

# 23:30 to 00:15 the next day — a window a time-of-day could not express.
WIND_DOWN = """BEGIN:VEVENT
UID:winddown@test
SUMMARY:Wind Down
DTSTART;TZID=Europe/Madrid:20260101T233000
DTEND;TZID=Europe/Madrid:20260102T001500
RRULE:FREQ=DAILY
END:VEVENT
"""

SKIPPED = """BEGIN:VEVENT
UID:lunch@test
SUMMARY:Lunch
DTSTART;TZID=Europe/Madrid:20260101T130000
DTEND;TZID=Europe/Madrid:20260101T134500
RRULE:FREQ=DAILY
EXDATE;TZID=Europe/Madrid:20260106T130000
END:VEVENT
"""

ALL_DAY = """BEGIN:VEVENT
UID:holiday@test
SUMMARY:Holiday
DTSTART;VALUE=DATE:20260105
DTEND;VALUE=DATE:20260106
END:VEVENT
"""


def _cal(*events: str) -> str:
    body = "".join(events)
    return f"BEGIN:VCALENDAR\nVERSION:2.0\nPRODID:-//test//EN\n{body}END:VCALENDAR\n"


def _at(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0
) -> dt.datetime:
    return dt.datetime(year, month, day, hour, minute, tzinfo=TZ)


def test_parse_description_takes_only_step_lines():
    assert template.parse_description("- one\n* two\nprose\n") == ("one", "two")
    assert template.parse_description("") == ()


def test_expand_window_returns_absolute_datetimes():
    found = template.expand_window(_cal(MORNING), _at(2026, 1, 5), _at(2026, 1, 6), TZ)
    assert len(found) == 1
    occurrence = found[0]
    assert occurrence.name == "Morning Routine"
    assert occurrence.source_uid == "morning@test"
    assert occurrence.start == _at(2026, 1, 5, 7, 30)
    assert occurrence.end == _at(2026, 1, 5, 7, 50)
    assert occurrence.steps == ("wash face", "brush teeth")


def test_expand_window_spanning_midnight():
    found = template.expand_window(
        _cal(WIND_DOWN), _at(2026, 1, 5, 23, 45), _at(2026, 1, 6, 0, 5), TZ
    )
    assert len(found) == 1
    assert found[0].start == _at(2026, 1, 5, 23, 30)
    assert found[0].end == _at(2026, 1, 6, 0, 15)


def test_expand_window_honours_exdate():
    found = template.expand_window(_cal(SKIPPED), _at(2026, 1, 5), _at(2026, 1, 8), TZ)
    assert [o.start.date() for o in found] == [dt.date(2026, 1, 5), dt.date(2026, 1, 7)]


def test_expand_window_skips_all_day_events():
    found = template.expand_window(_cal(ALL_DAY), _at(2026, 1, 5), _at(2026, 1, 6), TZ)
    assert found == []


def test_expand_window_sorts_by_start_and_includes_overlaps():
    found = template.expand_window(
        _cal(MORNING, SKIPPED, WIND_DOWN), _at(2026, 1, 5), _at(2026, 1, 6), TZ
    )
    # the first Wind Down began the evening before and runs into the window
    assert [(o.name, o.start) for o in found] == [
        ("Wind Down", _at(2026, 1, 4, 23, 30)),
        ("Morning Routine", _at(2026, 1, 5, 7, 30)),
        ("Lunch", _at(2026, 1, 5, 13, 0)),
        ("Wind Down", _at(2026, 1, 5, 23, 30)),
    ]


def test_recurrence_id_distinguishes_instances():
    found = template.expand_window(_cal(MORNING), _at(2026, 1, 5), _at(2026, 1, 7), TZ)
    assert len({o.recurrence_id for o in found}) == 2


def test_expand_window_on_empty_calendar():
    assert template.expand_window("", _at(2026, 1, 5), _at(2026, 1, 6), TZ) == []
