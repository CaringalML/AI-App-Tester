"""API and storage models.

The field names serialize to camelCase so the React app's `Finding` type in
src/lib/types.ts reads them without a mapping layer.
"""

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Category = Literal["bug", "improvement"]
Severity = Literal["critical", "high", "medium", "low"]
Confidence = Literal["high", "medium", "low"]
# automated: produced by deterministic browser checks, true by construction.
# agent: reported by Claude while exploring, and required to cite evidence ids.
Source = Literal["automated", "agent"]
Kind = Literal[
    "crash",
    "console",
    "network",
    "broken-link",
    "functional",
    "accessibility",
    "performance",
    "seo",
    "ux",
    "content",
]
ScanStatus = Literal["queued", "running", "done", "error"]

SEVERITY_ORDER: dict[str, int] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def utcnow() -> datetime:
    return datetime.now(UTC)


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ScanOptions(ApiModel):
    find_bugs: bool = True
    find_improvements: bool = True
    check_accessibility: bool = True
    max_pages: int = Field(default=5, ge=1, le=10)


class ScanRequest(ApiModel):
    url: str = Field(min_length=3, max_length=2048)
    options: ScanOptions = Field(default_factory=ScanOptions)


class Finding(ApiModel):
    id: str
    title: str
    category: Category
    severity: Severity
    confidence: Confidence
    kind: Kind
    source: Source
    location: str
    selector: str | None = None
    evidence: str
    steps: list[str]
    suggestion: str
    evidence_ids: list[str] = Field(default_factory=list)
    screenshot_key: str | None = None
    screenshot_url: str | None = None


class SuppressedFinding(ApiModel):
    finding: Finding
    reason: str


class ProgressEvent(ApiModel):
    at: datetime
    message: str
    kind: Literal["info", "action", "finding", "warning"] = "info"


class Usage(ApiModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    estimated_cost_usd: float | None = None


class Scan(ApiModel):
    id: str
    target_url: str
    status: ScanStatus = "queued"
    options: ScanOptions
    model: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    progress: list[ProgressEvent] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    suppressed: list[SuppressedFinding] = Field(default_factory=list)
    summary: str | None = None
    visited_urls: list[str] = Field(default_factory=list)
    agent_steps: int = 0
    usage: Usage = Field(default_factory=Usage)
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    # Epoch seconds. DynamoDB's TTL deletes the record after this.
    expires_at: int = 0


class ScanAccepted(ApiModel):
    id: str
    status: ScanStatus
