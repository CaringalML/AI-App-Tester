"""The evidence ledger.

Everything the browser sees, and every action the agent takes, becomes an
Observation with a stable id (obs-12, act-4). Findings must cite these ids.
That is the core of how false positives are handled: a claim with nothing
behind it in this ledger is visibly marked as ungrounded instead of being
presented with the same authority as a crash the browser actually recorded.
"""

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    id: str
    kind: str
    page: str
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def brief(self, limit: int = 220) -> str:
        text = self.text if len(self.text) <= limit else self.text[: limit - 1] + "…"
        return f"{self.id} [{self.kind}] {text}"


class ObservationLog:
    def __init__(self) -> None:
        self._items: list[Observation] = []
        self._by_id: dict[str, Observation] = {}
        self._counters = {"obs": 0, "act": 0}

    def add(self, kind: str, page: str, text: str, **data: Any) -> Observation:
        prefix = "act" if kind == "action" else "obs"
        self._counters[prefix] += 1
        observation = Observation(f"{prefix}-{self._counters[prefix]}", kind, page, text, data)
        self._items.append(observation)
        self._by_id[observation.id] = observation
        return observation

    @classmethod
    def of(cls, items: list[Observation]) -> "ObservationLog":
        """A view over existing observations, ids unchanged, for building findings from a slice."""
        view = cls()
        for item in items:
            view._items.append(item)
            view._by_id[item.id] = item
        return view

    def __len__(self) -> int:
        return len(self._items)

    def since(self, index: int) -> list[Observation]:
        return self._items[index:]

    def of_kind(self, *kinds: str) -> list[Observation]:
        return [item for item in self._items if item.kind in kinds]

    def get(self, observation_id: str) -> Observation | None:
        return self._by_id.get(observation_id)

    def __contains__(self, observation_id: str) -> bool:
        return observation_id in self._by_id
