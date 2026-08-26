"""Record real provider responses as replay fixtures.

Run deliberately, never from a test. The fixtures it writes are what FakeClient replays,
so the default suite stays offline and free while still exercising responses a real model
actually produced.

    uv run python -m specguard.llm.record --spec SPEC-001
    uv run python -m specguard.llm.record --all
"""

from __future__ import annotations

import argparse
from pathlib import Path

from specguard.config import get_settings
from specguard.fixtures.generate import load_manifest
from specguard.ingest.extract import PROMPT_NAME, SpecExtraction
from specguard.ingest.pdf import ingest_pdf
from specguard.llm.factory import FIXTURE_DIR, build_client
from specguard.llm.protocol import LLMClient
from specguard.prompts.loader import load_prompt

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
