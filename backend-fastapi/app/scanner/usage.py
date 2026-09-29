from ..config import MODEL_PRICING_PER_MTOK
from ..models import Usage


class UsageTracker:
    def __init__(self, model: str) -> None:
        self.model = model
        self.usage = Usage()

    def add(self, api_usage: object) -> None:
        u = self.usage
        u.requests += 1
        u.input_tokens += getattr(api_usage, "input_tokens", 0) or 0
        u.output_tokens += getattr(api_usage, "output_tokens", 0) or 0
        u.cache_read_input_tokens += getattr(api_usage, "cache_read_input_tokens", 0) or 0
        u.cache_creation_input_tokens += getattr(api_usage, "cache_creation_input_tokens", 0) or 0
        u.estimated_cost_usd = self._cost()

    def _cost(self) -> float | None:
        price = MODEL_PRICING_PER_MTOK.get(self.model)
        if not price:
            return None
        u = self.usage
        total = (
            u.input_tokens * price["input"]
            + u.output_tokens * price["output"]
            + u.cache_read_input_tokens * price["cache_read"]
            + u.cache_creation_input_tokens * price["cache_write"]
        ) / 1_000_000
        return round(total, 4)
