"""冷库温度越限复盘核心算法。

输入均为时区感知 (timezone-aware) 的 ``datetime``（内部统一换算为 UTC），
输出区间同样为时区感知时间，便于直接序列化。

规则概览：
* 相邻读数时间差 ``> max_sample_interval`` 时，该区间标记为"未知"，
  越限区间不得跨越未知段；
* 相邻读数时间差 ``<= max_sample_interval`` 时，在两点间做线性插值，
  精确求解温度与上限相等的穿越时刻，切出温度严格高于上限的连续段；
* 维护豁免时段（可重叠/相邻）合并后，从越限连续段中扣除；
* 扣除后每段持续时长 ``>= min_alarm_duration`` 才计为一次报警。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


@dataclass(frozen=True)
class Reading:
    """一次带时区的温度读数。"""

    time: datetime
    temperature: float


@dataclass(frozen=True)
class Interval:
    """左闭右开区间 ``[start, end)``。"""

    start: datetime
    end: datetime

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def duration_seconds(self) -> float:
        return self.duration.total_seconds()


@dataclass(frozen=True)
class ReviewResult:
    """复盘结果（全部为 UTC 时间）。

    * ``unknown_intervals``: 采样断档形成的未知区间（不做插值、不可跨越）；
    * ``excursion_intervals``: 扣除豁免前，温度严格高于上限的连续区间；
    * ``alarms``: 扣除豁免后，时长不短于最短报警时长的报警段。
    """

    unknown_intervals: list[Interval]
    excursion_intervals: list[Interval]
    alarms: list[Interval]


def _to_utc(value: datetime, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} 必须携带时区信息（timezone-aware datetime）")
    return value.astimezone(timezone.utc)


def _merge_intervals(intervals: list[Interval]) -> list[Interval]:
    """把按起点有序、可能重叠/相邻的区间合并。"""
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda iv: iv.start)
    merged: list[Interval] = []
    cur_start, cur_end = ordered[0].start, ordered[0].end
    for iv in ordered[1:]:
        if iv.start <= cur_end:
            if iv.end > cur_end:
                cur_end = iv.end
        else:
            merged.append(Interval(cur_start, cur_end))
            cur_start, cur_end = iv.start, iv.end
    merged.append(Interval(cur_start, cur_end))
    return merged


def _excursion_segment(
    t0: datetime,
    v0: float,
    t1: datetime,
    v1: float,
    limit: float,
) -> Interval | None:
    """在单次有效采样间隔 ``[t0, t1)`` 内求温度严格高于 ``limit`` 的子区间。

    温度按线性插值处理；上限交点温度恰好等于上限（严格大于的边界），
    因此区间端点取交点而不影响持续时长（单点时长为 0）。
    """
    span = (t1 - t0).total_seconds()
    if span <= 0:
        return None  # 调用方已保证时间严格递增，这里仅作防御

    over0 = v0 > limit
    over1 = v1 > limit

    if over0 and over1:
        return Interval(t0, t1)
    if not over0 and not over1:
        # 两端均未越限，线性插值为直线，中间不可能高于上限
        return None

    # 一端越限、一端未越限：求唯一交点 t_cross = t0 + f * (t1 - t0)
    f = (limit - v0) / (v1 - v0)
    # 数值上钳制到 (0, 1)
    f = min(1.0, max(0.0, f))
    t_cross = t0 + timedelta(seconds=span * f)
    return Interval(t0, t_cross) if over0 else Interval(t_cross, t1)


def _subtract_windows(
    segment: Interval, windows: list[Interval]
) -> list[Interval]:
    """从 ``segment`` 中扣除若干已合并、互不重叠的豁免窗口。"""
    pieces: list[Interval] = []
    cursor = segment.start
    for win in windows:
        if win.end <= cursor:
            continue
        if win.start >= segment.end:
            break
        cut_start = max(win.start, cursor)
        if cut_start > cursor:
            pieces.append(Interval(cursor, cut_start))
        cursor = max(cursor, win.end)
        if cursor >= segment.end:
            break
    if cursor < segment.end:
        pieces.append(Interval(cursor, segment.end))
    return [p for p in pieces if p.end > p.start]


def analyze(
    readings: list[Reading],
    temperature_limit: float,
    max_sample_interval: timedelta,
    min_alarm_duration: timedelta,
    maintenance_windows: list[Interval],
) -> ReviewResult:
    """执行温度越限复盘。

    :raises ValueError: 读数不足、时间不严格递增、参数非法或豁免时段非法。
    """
    if max_sample_interval.total_seconds() <= 0:
        raise ValueError("max_sample_interval 必须为正数")
    if min_alarm_duration.total_seconds() < 0:
        raise ValueError("min_alarm_duration 不能为负数")

    if len(readings) < 2:
        raise ValueError("至少需要 2 个温度读数才能进行复盘")

    points: list[tuple[datetime, float]] = []
    prev: datetime | None = None
    for idx, r in enumerate(readings):
        t = _to_utc(r.time, f"readings[{idx}].time")
        v = float(r.temperature)
        if v != v:  # NaN
            raise ValueError(f"readings[{idx}].temperature 不能为 NaN")
        if prev is not None and t <= prev:
            raise ValueError("读数时间必须严格递增（不允许相等或乱序）")
        points.append((t, v))
        prev = t

    norm_windows: list[Interval] = []
    for idx, w in enumerate(maintenance_windows):
        ws = _to_utc(w.start, f"maintenance_windows[{idx}].start")
        we = _to_utc(w.end, f"maintenance_windows[{idx}].end")
        if we <= ws:
            raise ValueError(
                f"maintenance_windows[{idx}] 非法：结束时间必须晚于开始时间"
            )
        norm_windows.append(Interval(ws, we))
    merged_windows = _merge_intervals(norm_windows)

    max_gap = max_sample_interval.total_seconds()
    unknown: list[Interval] = []
    excursions: list[Interval] = []

    for (t0, v0), (t1, v1) in zip(points, points[1:]):
        gap = (t1 - t0).total_seconds()
        if gap > max_gap:
            # 采样断档：标记未知，不插值、不可跨越
            unknown.append(Interval(t0, t1))
            continue
        seg = _excursion_segment(t0, v0, t1, v1, float(temperature_limit))
        if seg is not None and seg.end > seg.start:
            excursions.append(seg)

    # 同一温度越限过程可能横跨多个采样间隔，合并为连续区间。
    # 由于未知段从不加入 excursions，越限区间天然不会跨越断档。
    merged_excursions = _merge_intervals(excursions)
    unknown_merged = _merge_intervals(unknown)

    alarms: list[Interval] = []
    for segment in merged_excursions:
        for piece in _subtract_windows(segment, merged_windows):
            if piece.duration_seconds >= min_alarm_duration.total_seconds():
                alarms.append(piece)

    return ReviewResult(
        unknown_intervals=unknown_merged,
        excursion_intervals=merged_excursions,
        alarms=alarms,
    )
