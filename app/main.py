"""FastAPI 入口：冷库温度越限复盘 API。"""

from __future__ import annotations

from datetime import timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .analysis import Interval, Reading, analyze
from .schemas import ReviewRequest, ReviewResponse

app = FastAPI(
    title="冷库温度越限复盘 API",
    description=(
        "接收同一冷库的带时区温度读数与阈值配置，"
        "在有效相邻读数间线性插值，计算超温连续区间，"
        "采样断档标记为未知且不跨越；扣除维护豁免时段后按时长筛出报警。"
    ),
    version="1.0.0",
)


@app.exception_handler(ValueError)
def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """业务校验错误（如时间不严格递增、无效时段）统一返回 422。"""
    return JSONResponse(
        status_code=422,
        content={"detail": [{"type": "value_error", "msg": str(exc)}]},
    )


@app.get("/health", tags=["system"], summary="健康检查")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/api/v1/cold-storage/temperature/review",
    response_model=ReviewResponse,
    tags=["review"],
    summary="温度越限复盘",
    responses={422: {"description": "读数时间不严格递增 / 无效时段 / 参数非法"}},
)
def review(req: ReviewRequest) -> ReviewResponse:
    result = analyze(
        readings=[Reading(r.time, r.temperature) for r in req.readings],
        temperature_limit=req.temperature_limit,
        max_sample_interval=timedelta(seconds=req.max_sample_interval_seconds),
        min_alarm_duration=timedelta(seconds=req.min_alarm_duration_seconds),
        maintenance_windows=[
            Interval(w.start, w.end) for w in req.maintenance_windows
        ],
    )

    # 输出统一采用首个读数的时区，保留用户原始时区观感
    out_tz = req.readings[0].time.tzinfo
    if out_tz is None:  # 校验阶段已拒绝，理论不可达
        out_tz = timezone.utc

    def convert(iv: Interval) -> dict:
        start = iv.start.astimezone(out_tz)
        end = iv.end.astimezone(out_tz)
        return {
            "start": start,
            "end": end,
            "duration_seconds": round((end - start).total_seconds(), 6),
        }

    return ReviewResponse(
        timezone=str(out_tz),
        alarms=[convert(iv) for iv in result.alarms],
        excursion_intervals=[convert(iv) for iv in result.excursion_intervals],
        unknown_intervals=[convert(iv) for iv in result.unknown_intervals],
    )
