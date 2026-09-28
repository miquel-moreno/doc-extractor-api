"""Evaluation on the 50 synthetic documents (`make eval`).

Runs against a REAL LLM, so it is local only and never part of CI. Uses the
provider and model from .env unless given here:

    uv run python -m evals.run                                 # .env settings
    uv run python -m evals.run --provider openai --model gpt-4.1-mini
    uv run python -m evals.run --provider ollama --model qwen2.5:3b --limit 5

Results go to evals/results/<UTC date and time>_<model>.json (one file per run).
"""

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from doc_extractor_api.adapters.llm import LLMClient, LLMError, build_llm_client
from doc_extractor_api.adapters.pdf import pdf_to_text
from doc_extractor_api.core.config import Settings
from doc_extractor_api.services.extraction import extract_document
from doc_extractor_api.services.validation import validate_document
from evals.scoring import DocumentResult, score_document, summarize

DATASET = Path(__file__).parent / "dataset"
RESULTS = Path(__file__).parent / "results"

# USD per 1M tokens (input, output). Checked on developers.openai.com/api/docs/pricing
# on 2026-09-28. Local models cost 0. Unknown models: cost not reported.
PRICES_PER_MILLION = {"gpt-4.1-mini": (0.40, 1.60), "gpt-4.1-nano": (0.10, 0.40)}


def load_entries(limit: int | None = None) -> list[dict[str, Any]]:
    manifest = json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = manifest["documents"]
    return entries[:limit] if limit else entries


def read_document(entry: dict[str, Any]) -> str:
    path = DATASET / entry["file"]
    if path.suffix == ".pdf":
        return pdf_to_text(path.read_bytes())
    return path.read_text(encoding="utf-8")


async def evaluate(
    entries: list[dict[str, Any]], llm: LLMClient, *, today: date, verbose: bool = False
) -> tuple[list[DocumentResult], list[dict[str, Any]], str]:
    results: list[DocumentResult] = []
    details: list[dict[str, Any]] = []
    model = ""
    for i, entry in enumerate(entries, start=1):
        expected = json.loads((DATASET / entry["expected"]).read_text(encoding="utf-8"))
        extraction = await extract_document(read_document(entry), llm)
        model = extraction.model or model
        got = extraction.document.model_dump(mode="json") if extraction.document else None
        if extraction.document is not None:
            report = validate_document(extraction.document, today=today)
            status, issues = report.status.value, [issue.code.value for issue in report.issues]
        else:
            status, issues = "needs_review", ["extraction_failed"]
        score = score_document(expected, got)
        result = DocumentResult(
            doc_id=entry["id"],
            document_type=entry["document_type"],
            layout=entry["layout"],
            score=score,
            status=status,
            latency_ms=extraction.latency_ms,
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            attempts=extraction.attempts,
            extraction_failed=extraction.document is None,
            issues=issues,
        )
        results.append(result)
        details.append(
            {
                "id": entry["id"],
                "perfect": score.perfect,
                "status": status,
                "issues": issues,
                "wrong_fields": {
                    name: {"expected": expected.get(name), "got": (got or {}).get(name)}
                    for name in score.wrong_fields
                },
                "seconds": round(extraction.latency_ms / 1000, 2),
                "attempts": extraction.attempts,
            }
        )
        if verbose:
            mark = "OK " if score.perfect else "ERR"
            wrong = ", ".join(score.wrong_fields)
            print(
                f"[{i:2d}/{len(entries)}] {mark} {entry['id']:<18} {status:<12} "
                f"{extraction.latency_ms / 1000:5.1f}s {wrong}",
                flush=True,
            )
    return results, details, model


def estimate_cost(model: str, input_tokens: int, output_tokens: int, provider: str) -> float | None:
    if provider == "ollama":
        return 0.0
    for name, (price_in, price_out) in PRICES_PER_MILLION.items():
        if model == name or model.startswith(f"{name}-"):
            return round((input_tokens * price_in + output_tokens * price_out) / 1_000_000, 4)
    return None


def print_summary(summary: dict[str, Any]) -> None:
    n = summary["documents"]
    print("\n=== Resultado ===")
    print(f"Documentos perfectos: {summary['perfect']}/{n} ({summary['perfect_pct']} %)")
    for name, pct in summary["field_accuracy_pct"].items():
        print(f"  {name:<16} {pct:5.1f} %")
    print(
        f"Documentos con algún error: {summary['wrong_documents']} → "
        f"a revisión: {summary['wrong_sent_to_review']} · "
        f"marcados como válidos (colados): {summary['wrong_marked_valid']}"
    )
    print(f"Correctos enviados a revisión (falsas alarmas): {summary['correct_sent_to_review']}")
    print(
        f"Tiempo por documento: media {summary['seconds_per_document_mean']} s · "
        f"mediana {summary['seconds_per_document_median']} s"
    )
    print(f"Tokens: {summary['input_tokens']} entrada · {summary['output_tokens']} salida")
    cost = summary.get("cost_usd")
    print(f"Coste estimado: {'desconocido' if cost is None else f'${cost}'}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate extraction on the synthetic dataset.")
    parser.add_argument("--provider", choices=["openai", "ollama"])
    parser.add_argument("--model")
    parser.add_argument("--limit", type=int, help="only the first N documents")
    args = parser.parse_args()

    if args.provider:
        os.environ["LLM_PROVIDER"] = args.provider
    if args.model:
        os.environ["LLM_MODEL"] = args.model
    settings = Settings()
    entries = load_entries(args.limit)
    print(f"Evaluando {len(entries)} documentos con {settings.llm_provider} / {settings.llm_model}")

    try:
        llm = build_llm_client(settings)
        results, details, model = await evaluate(entries, llm, today=date.today(), verbose=True)
    except LLMError as exc:
        print(f"Error del proveedor de IA: {exc}", file=sys.stderr)
        return 1

    summary = summarize(results)
    summary["cost_usd"] = estimate_cost(
        model, summary["input_tokens"], summary["output_tokens"], settings.llm_provider
    )
    print_summary(summary)

    RESULTS.mkdir(exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "-", model or settings.llm_model)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H%MZ")
    out = RESULTS / f"{stamp}_{safe_model}.json"
    payload = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "provider": settings.llm_provider,
        "model": model,
        "dataset_seed": json.loads((DATASET / "manifest.json").read_text(encoding="utf-8"))["seed"],
        "limit": args.limit,
        "summary": summary,
        "documents": details,
        "scores": [asdict(r) for r in results],
    }
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"\nGuardado en {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
