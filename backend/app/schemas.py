from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


class ReviewCreate(BaseModel):
    start_url: str
    report_year: int
    base_year: int


class ReviewResponse(BaseModel):
    id: int
    start_url: str
    report_year: int
    base_year: int
    created_at: datetime

    class Config:
        from_attributes = True


class SectionResponse(BaseModel):
    id: int
    review_id: int
    title: str
    url: str
    anchor: Optional[str] = None
    level: int
    is_virtual: bool

    class Config:
        from_attributes = True


class CheckResultResponse(BaseModel):
    id: int
    rule: str
    severity: str  # PASS, WARN, FAIL
    message: str
    evidence_json: Optional[Any] = None

    class Config:
        from_attributes = True


class CheckRunResponse(BaseModel):
    id: int
    mode: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    results: List[CheckResultResponse] = []

    class Config:
        from_attributes = True


class ManualReviewCreate(BaseModel):
    items_checked_json: Optional[dict] = None
    comments: Optional[str] = None
    reviewer: Optional[str] = None


class ManualReviewResponse(BaseModel):
    id: int
    section_id: int
    items_checked_json: Optional[dict] = None
    comments: Optional[str] = None
    reviewer: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True
