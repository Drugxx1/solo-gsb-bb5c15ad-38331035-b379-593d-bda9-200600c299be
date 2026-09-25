"""核心算法单元测试。运行： pytest"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.analysis import Interval, Reading, analyze

CST = timezone(timedelta(hours=8))


def R(hhmm: str, temp: float, tz=CST) -> Reading:
    h, m = map(int, hhmm.split(":"))
    return Reading(datetime(2026, 9, 25, h, m, tzinfo=tz), temp)


def test_basic_interpolation_and_crossing_time():
    # -16 -> -14, 上限 -15：交点在 10:02:30
    readings = [R("10:00", -16.0), R("10:05", -14.0), R("10:10", -16.0)]
    res = analyze(
        readings,
        temperature_limit=-15.0,
        max_sample_interval=timedelta(minutes=10),
        min_alarm_duration=timedelta(0),
        maintenance_windows=[],
    )
    assert len(res.excursion_intervals) == 1
    seg = res.excursion_intervals[0]
    assert seg.start == datetime(2026, 9, 25, 2, 2, 30, tzinfo=timezone.utc)
    assert seg.end == datetime(2026, 9, 25, 2, 7, 30, tzinfo=timezone.utc)
    assert seg.duration_seconds == pytest.approx(300.0)
    assert res.unknown_intervals == []


def test_limit_is_strict_greater_than():
    # 温度恒等于上限 => 不越限
    readings = [R("10:00", -18.0), R("10:05", -18.0)]
    res = analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])
    assert res.excursion_intervals == []
    assert res.alarms == []


def test_gap_marked_unknown_and_not_crossed():
    readings = [
        R("10:00", -14.0),
        R("10:05", -14.0),
        R("10:30", -14.0),  # 与上一条间隔 25 分钟 > 10 分钟
        R("10:35", -14.0),
    ]
    res = analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])
    assert len(res.unknown_intervals) == 1
    u = res.unknown_intervals[0]
    assert u.start == R("10:05", 0).time.astimezone(timezone.utc)
    assert u.end == R("10:30", 0).time.astimezone(timezone.utc)
    # 断档两侧的越限段各自独立，不合并
    assert len(res.excursion_intervals) == 2


def test_maintenance_subtraction_and_min_duration_filter():
    # 10:00-10:10 全程越限（600s）；豁免 10:04-10:06（120s）
    readings = [R("10:00", -14.0), R("10:05", -14.0), R("10:10", -14.0)]
    windows = [
        Interval(
            datetime(2026, 9, 25, 10, 4, tzinfo=CST),
            datetime(2026, 9, 25, 10, 6, tzinfo=CST),
        )
    ]
    res = analyze(
        readings, -18.0, timedelta(minutes=10), timedelta(seconds=200), windows
    )
    assert len(res.excursion_intervals) == 1
    assert res.excursion_intervals[0].duration_seconds == 600.0
    # 切成 240s / 240s 两段，均 >= 200s => 2 次报警
    assert len(res.alarms) == 2
    assert [a.duration_seconds for a in res.alarms] == [240.0, 240.0]

    # 提高门槛到 250s => 无报警
    res2 = analyze(
        readings, -18.0, timedelta(minutes=10), timedelta(seconds=250), windows
    )
    assert res2.alarms == []


def test_overlapping_maintenance_windows_merged():
    readings = [R("10:00", -14.0), R("10:10", -14.0)]
    windows = [
        Interval(datetime(2026, 9, 25, 10, 2, tzinfo=CST),
                 datetime(2026, 9, 25, 10, 5, tzinfo=CST)),
        Interval(datetime(2026, 9, 25, 10, 4, tzinfo=CST),
                 datetime(2026, 9, 25, 10, 8, tzinfo=CST)),
    ]
    res = analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), windows)
    assert len(res.alarms) == 2
    assert [a.duration_seconds for a in res.alarms] == [120.0, 120.0]


def test_non_monotonic_readings_rejected():
    readings = [R("10:05", -14.0), R("10:00", -14.0)]
    with pytest.raises(ValueError, match="严格递增"):
        analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])


def test_equal_timestamps_rejected():
    readings = [R("10:00", -14.0), R("10:00", -15.0)]
    with pytest.raises(ValueError, match="严格递增"):
        analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])


def test_naive_datetime_rejected():
    readings = [
        Reading(datetime(2026, 9, 25, 10, 0), -14.0),
        Reading(datetime(2026, 9, 25, 10, 5), -14.0),
    ]
    with pytest.raises(ValueError, match="时区"):
        analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])


def test_invalid_maintenance_window_rejected():
    readings = [R("10:00", -14.0), R("10:05", -14.0)]
    bad = Interval(
        datetime(2026, 9, 25, 10, 6, tzinfo=CST),
        datetime(2026, 9, 25, 10, 6, tzinfo=CST),
    )
    with pytest.raises(ValueError, match="非法"):
        analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [bad])


def test_too_few_readings_rejected():
    with pytest.raises(ValueError, match="至少需要 2"):
        analyze([R("10:00", -14.0)], -18.0, timedelta(minutes=10), timedelta(0), [])


def test_timezone_normalization():
    # 同一时刻分别用 +08:00 和 UTC 表示，结果一致
    readings = [
        Reading(datetime(2026, 9, 25, 10, 0, tzinfo=CST), -14.0),
        Reading(datetime(2026, 9, 25, 2, 5, tzinfo=timezone.utc), -14.0),
    ]
    res = analyze(readings, -18.0, timedelta(minutes=10), timedelta(0), [])
    assert len(res.excursion_intervals) == 1
    assert res.excursion_intervals[0].duration_seconds == 300.0
