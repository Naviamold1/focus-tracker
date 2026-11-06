"""Jev (TypeSafe System One) backend. The only module that knows about typesafe_sdk.

The SDK is imported lazily: without it, or without TYPESAFE_API_KEY, `make_jev_backend` returns
None and the classifier runs local-only.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Protocol, cast

from focus_tracker.schema import Category

log = logging.getLogger(__name__)

CATEGORIES: dict[str, str] = {
    "productive": "Genuinely advances the stated goal",
    "neutral": "Utilities, logistics, or rest; neither advances nor derails",
    "distraction": "Entertainment or sidetrack; working against the stated goal",
}
INSTRUCTIONS = (
    "Classify the surface the user is looking at, judged against their declared goal. "
    "The same site can be productive or a distraction depending on the goal and page title."
)


@dataclass(frozen=True, slots=True)
class JevResult:
    category: Category
    confidence: float


class JevBackend(Protocol):
    def ask(self, state: str) -> JevResult:
        """Blocking. Raises on total failure (after retries)."""
        ...


class TypeSafeJev:
    """Sync TypeSafeClient used from worker threads."""

    def __init__(self, model: str = "jev-latest", timeout_s: float = 10.0) -> None:
        from typesafe_sdk import Choice, RetryPolicy, TypeSafeClient

        # The SDK reads TYPESAFE_API_KEY itself; the key is never stored or logged here.
        # max_retries=1 means 2 attempts; timeout is the total retry budget per call.
        self._client = TypeSafeClient(
            model=model,
            retry=RetryPolicy(
                max_retries=1, backoff_initial=0.5, backoff_max=2.0, timeout=timeout_s
            ),
        )
        self._question = Choice(instructions=INSTRUCTIONS, criteria=CATEGORIES)

    def ask(self, state: str) -> JevResult:
        response = self._client.system_one(state, {"category": self._question})
        answer = response.choices["category"]
        return JevResult(
            category=cast(Category, answer.choice), confidence=float(answer.confidence)
        )


def make_jev_backend(
    model: str = "jev-latest", timeout_s: float = 10.0
) -> JevBackend | None:
    """Real backend, or None when the SDK or API key is unavailable."""
    if not os.environ.get("TYPESAFE_API_KEY"):
        log.info("TYPESAFE_API_KEY not set; classifier runs local-only")
        return None
    try:
        return TypeSafeJev(model, timeout_s)
    except ImportError:
        log.warning("typesafe-sdk not installed; classifier runs local-only")
    except Exception as exc:  # noqa: BLE001 - an invalid key raises TypeSafeError at client creation
        log.warning(
            "Jev client unavailable (%s); classifier runs local-only",
            type(exc).__name__,
        )
    return None
