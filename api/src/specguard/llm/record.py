"""Record real provider responses as replay fixtures.

Run deliberately, never from a test. The fixtures it writes are what FakeClient replays,
so the default suite stays offline and free while still exercising responses a real model
actually produced.

    uv run python -m specguard.llm.record --spec SPEC-001
    uv run python -m specguard.llm.record --all
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from pydantic import BaseModel

from specguard.config import get_settings
from specguard.fixtures.generate import load_manifest
from specguard.ingest.extract import PROMPT_NAME, SpecExtraction
from specguard.ingest.pdf import ingest_pdf
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.protocol import LLMClient, LLMResult
from specguard.prompts.loader import Prompt, load_prompt

SPEC_DIR = Path(__file__).resolve().parents[3].parent / "fixtures" / "specs"


def record_spec(spec_id: str, filename: str, client: LLMClient, out_dir: Path) -> float:
    """Record one extraction response. Returns the call's cost in USD."""
    document = ingest_pdf(SPEC_DIR / "generated" / filename)
    prompt = load_prompt(PROMPT_NAME)
    cache_key = document.source.sha256[:16]

    result = client.generate(
        prompt=prompt, schema=SpecExtraction, document=document.text, cache_key=cache_key
    )
    usage = result.usage

    from specguard.llm.fake import write_fixture

    write_fixture(
        out_dir / f"{PROMPT_NAME}__{cache_key}.json",
        response=result.value,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=usage.cost_usd,
        latency_ms=usage.latency_ms,
        note=(
            f"recorded from {usage.provider}/{usage.model} with {prompt.version} "
            f"for {spec_id} ({filename})"
        ),
    )
    print(
        f"  {spec_id:9s} {filename:42s} {usage.input_tokens:>6}in "
        f"{usage.output_tokens:>6}out  ${usage.cost_usd:.4f}  {usage.latency_ms:>6}ms"
    )
    return usage.cost_usd


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", action="append", help="Spec id, repeatable.")
    parser.add_argument("--all", action="store_true", help="Record every fixture spec.")
    parser.add_argument("--provider", help="Override LLM_PROVIDER for this run.")
    parser.add_argument("--model", help="Override the provider's model for this run.")
    parser.add_argument("--out", type=Path, default=FIXTURE_DIR)
    args = parser.parse_args()

    settings = get_settings()
    if args.provider:
        settings = settings.model_copy(update={"llm_provider": args.provider})
    if args.model:
        key = "openai_model" if settings.llm_provider == "openai" else "anthropic_model"
        settings = settings.model_copy(update={key: args.model})

    if settings.llm_provider == "fake":
        parser.error(
            "LLM_PROVIDER is 'fake', so there is nothing to record. Pass "
            "--provider openai (or anthropic) to make real calls."
        )

    manifest = {entry.spec_id: entry for entry in load_manifest(SPEC_DIR / "manifest.jsonl")}
    wanted = sorted(manifest) if args.all else (args.spec or [])
    if not wanted:
        parser.error("nothing to record: pass --spec SPEC-001 or --all")

    client = build_client(settings)
    args.out.mkdir(parents=True, exist_ok=True)
    print(f"Recording {len(wanted)} spec(s) via {client.provider}/{client.model}")

    total = sum(
        record_spec(spec_id, manifest[spec_id].filename, client, args.out) for spec_id in wanted
    )
    print(f"Recorded {len(wanted)} fixture(s) into {args.out} for ${total:.4f}")


if __name__ == "__main__":
    main()


class RecordingClient:
    """Wraps a real client and writes every response as a replay fixture.

    Lets a whole pipeline be recorded by running it once for real, rather than teaching
    the recorder the shape of each call. Whatever the pipeline asks for is what gets
    recorded, so the fixtures cannot drift from the calls the code actually makes.
    """

    def __init__(
        self,
        inner: LLMClient,
        out_dir: Path,
        *,
        skip_existing: bool = True,
        min_interval_s: float = 0.0,
    ) -> None:
        self._inner = inner
        self._out = out_dir
        self._skip_existing = skip_existing
        self._min_interval_s = min_interval_s
        self._last_call = 0.0
        self.provider = inner.provider
        self.model = inner.model
        self.cost_usd = 0.0
        self.calls = 0
        self.replayed = 0

    def generate[T: BaseModel](
        self,
        *,
        prompt: Prompt,
        schema: type[T],
        document: str,
        cache_key: str,
    ) -> LLMResult[T]:
        """Make the real call, then record it — or replay a fixture already on disk.

        Resumable by default. A long recording run that dies partway (a rate limit, a
        dropped connection) can be restarted without paying again for everything it
        already captured.
        """
        from specguard.llm.fake import FakeClient, write_fixture

        path = self._out / f"{prompt.name}__{cache_key}.json"
        if self._skip_existing and path.exists():
            self.replayed += 1
            return FakeClient(self._out, model=self.model).generate(
                prompt=prompt, schema=schema, document=document, cache_key=cache_key
            )

        if self._min_interval_s:
            # Paced rather than hammered: a token-per-minute limit is not something the
            # SDK's retry can smooth out on its own when every call is thousands of
            # tokens.
            elapsed = time.monotonic() - self._last_call
            if elapsed < self._min_interval_s:
                time.sleep(self._min_interval_s - elapsed)
        self._last_call = time.monotonic()

        result = self._inner.generate(
            prompt=prompt, schema=schema, document=document, cache_key=cache_key
        )

        write_fixture(
            path,
            response=result.value,
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            cost_usd=result.usage.cost_usd,
            latency_ms=result.usage.latency_ms,
            note=f"recorded from {self.provider}/{self.model} with {prompt.version}",
        )
        self.cost_usd += result.usage.cost_usd
        self.calls += 1
        return result
