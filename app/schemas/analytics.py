from datetime import date

from pydantic import BaseModel


class AnalyticsSummary(BaseModel):
    messages_sent: int
    delivered: int
    read: int
    failed: int
    replies: int
    leads: int
    conversion_rate: float


class TimeSeriesPoint(BaseModel):
    day: date
    sent: int
    delivered: int
    read: int
    replies: int


class FunnelStage(BaseModel):
    name: str
    value: int
