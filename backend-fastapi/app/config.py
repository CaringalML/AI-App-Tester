from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Per-million-token prices used for the cost estimate shown after each scan.
# Cache writes use the 5-minute TTL rate (1.25x input). A model missing from
# this table simply reports no cost estimate rather than a wrong one.
MODEL_PRICING_PER_MTOK: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00, "cache_read": 0.50, "cache_write": 6.25},
    "claude-sonnet-5": {"input": 2.00, "output": 10.00, "cache_read": 0.20, "cache_write": 2.50},
}


class Settings(BaseSettings):
    """Every knob comes from the environment, so the same image runs locally and on ECS."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Claude
    anthropic_api_key: str = Field(default="", repr=False)
    # Required only for organization-level API keys that are not scoped to a
    # workspace; the API rejects those without an anthropic-workspace-id header.
    anthropic_workspace_id: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    # Opus 5.5 always thinks; effort is the only dial for depth, latency and cost.
    agent_effort: str = "medium"
    review_effort: str = "medium"

    # Budgets that keep a scan demo-sized and the bill predictable. These are the
    # quick scan's; a thorough scan trades time and cost for coverage.
    max_agent_steps: int = 30
    scan_timeout_seconds: int = 240
    thorough_agent_steps: int = 100
    thorough_timeout_seconds: int = 1800
    max_concurrent_scans: int = 2
    # Thorough scans hold a slot for up to half an hour; capping them keeps a slot
    # free for quick scans, such as a live demo.
    max_thorough_scans: int = 1
    rate_limit_scans: int = 6
    rate_limit_window_seconds: int = 3600
    max_link_checks: int = 25

    # Network safety
    allow_private_targets: bool = False
    cors_origins: str = "http://localhost:5173"

    # Persistence. Unset means in-memory and local disk, which is what you want in dev.
    dynamodb_table: str | None = None
    artifact_bucket: str | None = None
    aws_region: str = "ap-southeast-2"
    scan_ttl_days: int = 7

    public_base_url: str = "http://localhost:8000"
    local_artifact_dir: str = ".artifacts"
    axe_path: str = "vendor/axe.min.js"
    log_level: str = "INFO"

    def budget(self, depth: str) -> tuple[int, int]:
        """(Claude actions, seconds) a scan of this depth may use."""
        if depth == "thorough":
            return self.thorough_agent_steps, self.thorough_timeout_seconds
        return self.max_agent_steps, self.scan_timeout_seconds

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
