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
# Where a running scan is, for the progress bar. Ordered as they happen.
ScanStage = Literal[
    "queued", "checking", "loading", "crawling", "exploring", "reviewing", "done", "error"
]

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
    # quick: demo-sized, about two minutes. thorough: up to 30 minutes and many more
    # actions, for coverage. Budgets are in Settings.budget().
    depth: Literal["quick", "thorough"] = "quick"


class ScanRequest(ApiModel):
    url: str = Field(min_length=3, max_length=2048)
    options: ScanOptions = Field(default_factory=ScanOptions)


class StyleSource(ApiModel):
    """Where one of an element's colours is set, as DevTools' Styles pane shows it."""

    name: str  # "color" or "background-color"
    value: str  # as written in the CSS, e.g. "#a48d78" or "var(--gold)"
    rule: str  # the rule's selector, e.g. ".text-gold"
    source: str  # "index-8f2a.css:1", "<style> in the page, line 40" or "inline style"
    url: str | None = None
    inherited: bool = False  # set on an ancestor and inherited, as color is
    element: str | None = None  # the ancestor that paints a background, e.g. "section.bg-sand"


class InspectTarget(ApiModel):
    """One element to find with right-click > Inspect, or by pasting `selector` in DevTools."""

    selector: str
    html: str
    text: str | None = None
    styles: list[StyleSource] = Field(default_factory=list)


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
    # A Playwright regression test for this finding, assembled from the recorded
    # run (see scanner/playwright_export.py). It asserts the fixed behaviour.
    playwright_test: str | None = None
    # fails-now: checked against the page when reported, and the assertion fails
    # there, so the test catches the issue. passes-now: the assertion already
    # holds, so the test may not catch it. unverified: could not be checked.
    test_status: Literal["fails-now", "passes-now", "unverified"] | None = None
    test_note: str | None = None
    test_body: list[str] = Field(default_factory=list)
    # The elements involved, ready to find in DevTools (accessibility findings).
    inspect: list[InspectTarget] = Field(default_factory=list)


class SuppressedFinding(ApiModel):
    finding: Finding
    reason: str


class ProgressEvent(ApiModel):
    at: datetime
    message: str
    kind: Literal["info", "action", "finding", "warning"] = "info"


StepKind = Literal[
    "stage",
    "visit",
    "click",
    "type",
    "select",
    "press",
    "navigate",
    "back",
    "look",
    "read",
    "finding",
]


class Box(ApiModel):
    """Element position as fractions of the viewport, so the UI can outline it at any size."""

    x: float
    y: float
    w: float
    h: float


class TimelineStep(ApiModel):
    """One entry in the Cypress-style command log, with the page as it looked at that moment."""

    index: int
    at: datetime
    kind: StepKind
    label: str
    why: str | None = None
    url: str | None = None
    status: Literal["ok", "failed", "info", "warning", "finding"] = "ok"
    # Which part of the run this belongs to, so the UI can group steps into phases.
    phase: Literal["prepare", "explore", "review"] | None = None
    action_id: str | None = None
    finding_id: str | None = None
    signals: int = 0
    box: Box | None = None
    screenshot_key: str | None = None
    before_key: str | None = None
    screenshot_url: str | None = None
    before_url: str | None = None
    # The Playwright line this step would be in a test, e.g. a getByRole click.
    code: str | None = None


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
    stage: ScanStage = "queued"
    # 0.0 to 1.0, only ever increasing. Exploration moves it with each action
    # against the step budget; the other stages have fixed weights.
    completion: float = 0.0
    agent_budget: int = 0
    timeline: list[TimelineStep] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    suppressed: list[SuppressedFinding] = Field(default_factory=list)
    summary: str | None = None
    visited_urls: list[str] = Field(default_factory=list)
    agent_steps: int = 0
    usage: Usage = Field(default_factory=Usage)
    notes: list[str] = Field(default_factory=list)
    error: str | None = None
    # sha256 of the owner token handed to whoever started the scan. Deleting a
    # scan requires the token; only its hash is stored. Never sent to clients.
    owner_token_hash: str | None = None
    # Epoch seconds. DynamoDB's TTL deletes the record after this.
    expires_at: int = 0


class ScanAccepted(ApiModel):
    id: str
    status: ScanStatus
    # Returned once, at creation. The browser keeps it to prove ownership on delete.
    owner_token: str


class ScanSummary(ApiModel):
    """One row of the history sidebar: enough to recognise a run without loading it."""

    id: str
    target_url: str
    status: ScanStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    bugs: int = 0
    improvements: int = 0
    thumbnail_url: str | None = None
    error: str | None = None
