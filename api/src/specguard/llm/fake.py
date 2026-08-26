"""FakeClient: replays recorded responses. Every unit test runs on this.

No test in the default suite may reach a live API, so the fake is not a convenience —
it is what makes the suite deterministic, free and runnable offline. Recorded fixtures
also double as documentation of what the model is expected to return.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ValidationError

from specguard.llm.protocol import LLMError, LLMResult
from specguard.models.rule import LlmUsage
from specguard.prompts.loader import Prompt


class MissingFixtureError(LLMError):
    """No recorded response for this call.

    Raised loudly rather than returning a plausible default: a fake that invents an
    answer turns a missing fixture into a passing test.
    """


class FakeClient:
    """Replays ``<prompt>__<cache_key>.json`` from a fixture directory."""

    provider = "fake"

    def __init__(self, fixture_dir: Path, model: str = "fake-model") -> None:
        self.model = model
        self._dir = fixture_dir
        self.calls: list[tuple[str, str]] = []

    def fixture_path(self, prompt_name: str, cache_key: str) -> Path:
        return self._dir / f"{prompt_name}__{cache_key}.json"

    def generate[T: BaseModel](
        self,
        *,
        prompt: Prompt,
        schema: type[T],
        document: str,
        cache_key: str,
    ) -> LLMResult[T]:
        """Return the recorded response, validated against the caller's schema."""
        del document  # Recorded responses are keyed by cache_key, not by content.
        self.calls.append((prompt.name, cache_key))

        path = self.fixture_path(prompt.name, cache_key)
        if not path.exists():
            raise MissingFixtureError(
                f"no recorded response at {path}; record one with "
                f"`python -m specguard.llm.record --prompt {prompt.name} --key {cache_key}`"
            )

        payload = json.loads(path.read_text(encoding="utf-8"))
        try:
            value = schema.model_validate(payload["response"])
        except ValidationError as error:
            # A fixture that no longer fits the schema is a real failure: it means the
            # model contract changed and the recording is stale.
            raise LLMError(f"recorded response at {path} does not fit {schema.__name__}") from error

        usage = LlmUsage(
            provider=self.provider,
            model=self.model,
            prompt_version=prompt.version,
            input_tokens=payload.get("input_tokens", 0),
            output_tokens=payload.get("output_tokens", 0),
            cost_usd=0.0,
            latency_ms=0,
        )
        return LLMResult(value=value, usage=usage)


def write_fixture(
    path: Path,
    *,
    response: BaseModel,
    input_tokens: int = 0,
    output_tokens: int = 0,
    note: str = "",
) -> None:
    """Write a replay fixture, used by hand-authoring and by the recorder alike."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "note": note,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "response": response.model_dump(mode="json"),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
