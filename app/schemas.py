"""请求与响应的数据模型。"""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReadingIn(BaseModel):
    """带时区的温度读数。"""

    time: datetime = Field(
        ..., description="读数时间，必须携带时区偏移，如 2026-09-25T08:00:00+08:00"
    )
    temperature: float = Field(..., description="温度读数（与上限同单位，如 °C）")

    @field_validator("time")
    @classmethod
    def _time_must_be_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("time 必须携带时区信息（timezone-aware datetime）")
        return v

    @field_validator("temperature")
    @classmethod
    def _temperature_finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("temperature 必须为有限数值")
        return v


class MaintenanceWindowIn(BaseModel):
    """维护豁免时段（该时段内的越限不计入报警）。"""

    start: datetime = Field(..., description="豁免开始时间（含），必须携带时区")
    end: datetime = Field(..., description="豁免结束时间（不含），必须晚于 start")

    @field_validator("start", "end")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("时间字段必须携带时区信息（timezone-aware datetime）")
        return v

    @model_validator(mode="after")
    def _check_range(self) -> "MaintenanceWindowIn":
        if self.end <= self.start:
            raise ValueError("end 必须严格晚于 start")
        return self


class ReviewRequest(BaseModel):
    """冷库温度越限复盘请求。"""

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "readings": [
                {"time": "2026-09-25T08:00:00+08:00", "temperature": -18.0},
                {"time": "2026-09-25T08:05:00+08:00", "temperature": -15.6},
                {"time": "2026-09-25T08:10:00+08:00", "temperature": -14.0},
                {"time": "2026-09-25T08:15:00+08:00", "temperature": -18.2},
            ],
            "temperature_limit": -18.0,
            "max_sample_interval_seconds": 600,
            "min_alarm_duration_seconds": 240,
            "maintenance_windows": [
                {
                    "start": "2026-09-25T08:12:00+08:00",
                    "end": "2026-09-25T08:13:00+08:00",
                }
            ],
        }
    })

    readings: list[ReadingIn] = Field(..., min_length=2, description="同一冷库的温度读数")
    temperature_limit: float = Field(..., description="温度上限（严格大于该值视为越限）")
    max_sample_interval_seconds: float = Field(
        ..., gt=0, description="最大允许采样间隔（秒）；超过则相邻读数间标记为未知"
    )
    min_alarm_duration_seconds: float = Field(
        ..., ge=0, description="最短报警时长（秒）；扣除豁免后达到该时长才计为报警"
    )
    maintenance_windows: list[MaintenanceWindowIn] = Field(
        default_factory=list, description="维护豁免时段，可重叠；豁免内的越限被扣除"
    )

    @field_validator("temperature_limit")
    @classmethod
    def _limit_finite(cls, v: float) -> float:
        if v != v or v in (float("inf"), float("-inf")):
            raise ValueError("temperature_limit 必须为有限数值")
        return v


class IntervalOut(BaseModel):
    """时间区间输出（左闭右开）。"""

    start: datetime = Field(..., description="区间开始时间（含），带时区偏移")
    end: datetime = Field(..., description="区间结束时间（不含），带时区偏移")
    duration_seconds: float = Field(..., description="持续时长（秒）")


class ReviewResponse(BaseModel):
    """复盘结果。时间以首个读数的时区呈现，内部按 UTC 计算。"""

    timezone: str = Field(..., description="输出时间采用的时区（取自首个读数）")
    alarms: list[IntervalOut] = Field(..., description="符合时长条件的报警区间")
    excursion_intervals: list[IntervalOut] = Field(
        ..., description="扣除豁免前，温度高于上限的连续区间"
    )
    unknown_intervals: list[IntervalOut] = Field(
        ..., description="采样断档形成的未知区间（不插值、越限不可跨越）"
    )
