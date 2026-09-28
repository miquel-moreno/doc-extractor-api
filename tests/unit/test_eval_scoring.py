import json
from datetime import date
from decimal import Decimal
from typing import Any

from evals.run import DATASET, estimate_cost, evaluate, load_entries
from evals.scoring import DocumentResult, normalize, score_document, summarize

from doc_extractor_api.adapters.llm import FakeLLMClient

EXPECTED: dict[str, Any] = {
    "document_type": "invoice",
    "document_number": "F-2026-0001",
    "issuer_name": "Suministros Ejemplo S.L.",
    "issuer_tax_id": "B12345674",
    "issue_date": "2026-09-01",
    "lines": [{"description": "Bujía", "quantity": "3", "unit_price": "5.50", "amount": "16.50"}],
    "subtotal": "16.50",
    "vat_amount": "3.47",
    "total": "19.97",
}


def test_equivalent_values_are_equal() -> None:
    assert normalize("total", "57.0") == normalize("total", "57.00")
    assert normalize("issuer_tax_id", "ES-B12345674") == normalize("issuer_tax_id", "B12345674")
    assert normalize("issuer_name", "  Suministros  ejemplo s.l.") == normalize(
        "issuer_name", "Suministros Ejemplo S.L."
    )
    assert normalize("total", None) is None and normalize("issuer_name", "") is None
    assert normalize("total", "lots") != normalize("total", "0")


def test_perfect_extraction_scores_perfect() -> None:
    got = {**EXPECTED, "total": "19.970", "issuer_tax_id": "ESB12345674"}

    score = score_document(EXPECTED, got)

    assert score.perfect
    assert score.wrong_fields == []


def test_wrong_fields_and_lines_are_reported() -> None:
    lines = [{**EXPECTED["lines"][0], "quantity": "4"}]
    got = {**EXPECTED, "issuer_name": "Cliente S.A.", "lines": lines}

    score = score_document(EXPECTED, got)

    assert not score.perfect
    assert score.wrong_fields == ["issuer_name", "lines"]


def test_missing_extraction_scores_nothing() -> None:
    score = score_document(EXPECTED, None)

    assert not score.perfect
    assert not any(score.fields.values())


def result(
    perfect: bool, status: str, doc_type: str = "invoice", seconds: float = 2
) -> DocumentResult:
    fields = score_document(EXPECTED, EXPECTED if perfect else {**EXPECTED, "total": "1"})
    return DocumentResult(
        doc_id="x",
        document_type=doc_type,
        layout="classic",
        score=fields,
        status=status,
        latency_ms=seconds * 1000,
        input_tokens=100,
        output_tokens=20,
        attempts=1,
    )


def test_summary_counts_the_safety_metrics() -> None:
    summary = summarize(
        [
            result(True, "valid"),
            result(True, "needs_review"),  # false alarm
            result(False, "needs_review"),  # error caught
            result(False, "valid", doc_type="order", seconds=6),  # error that got through
        ]
    )

    assert summary["documents"] == 4
    assert (summary["perfect"], summary["perfect_pct"]) == (2, 50.0)
    assert summary["wrong_sent_to_review"] == 1
    assert summary["wrong_marked_valid"] == 1
    assert summary["correct_sent_to_review"] == 1
    assert summary["field_accuracy_pct"]["total"] == 50.0
    assert summary["field_accuracy_pct"]["issuer_name"] == 100.0
    assert summary["by_type"] == {
        "invoice": {"documents": 3, "perfect": 2},
        "order": {"documents": 1, "perfect": 0},
    }
    assert summary["seconds_per_document_mean"] == 3.0
    assert (summary["input_tokens"], summary["output_tokens"]) == (400, 80)


def test_cost_estimate() -> None:
    assert estimate_cost("gpt-4.1-mini-2025-04-14", 1_000_000, 1_000_000, "openai") == 2.0
    assert estimate_cost("qwen2.5:3b", 5000, 5000, "ollama") == 0.0
    assert estimate_cost("unknown-model", 10, 10, "openai") is None


def as_llm_answer(expected: dict[str, Any], **overrides: Any) -> str:
    def num(value: Any) -> float | None:
        return None if value is None else float(Decimal(value))

    data = {
        "document_type": expected["document_type"],
        "document_number": expected["document_number"],
        "issuer_name": expected["issuer_name"],
        "issuer_tax_id": expected["issuer_tax_id"],
        "issue_date": expected["issue_date"],
        "lines": [
            {
                "description": line["description"],
                "quantity": num(line["quantity"]),
                "unit_price": num(line["unit_price"]),
                "amount": num(line["amount"]),
            }
            for line in expected["lines"]
        ],
        "subtotal": num(expected["subtotal"]),
        "vat_amount": num(expected["vat_amount"]),
        "total": num(expected["total"]),
        "confidence": dict.fromkeys(
            [
                "document_number",
                "issuer_name",
                "issuer_tax_id",
                "issue_date",
                "subtotal",
                "vat_amount",
                "total",
            ],
            0.95,
        ),
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


async def test_evaluation_run_on_real_dataset_files_with_a_fake_llm() -> None:
    entries = [e for e in load_entries() if e["id"] in {"invoice-001", "order-001"}]
    answers = []
    for entry in entries:
        expected = json.loads((DATASET / entry["expected"]).read_text(encoding="utf-8"))
        answers.append(as_llm_answer(expected))
    # Break the first one: the issuer name of the customer instead of the issuer.
    answers[0] = answers[0].replace('"issuer_name": "', '"issuer_name": "WRONG ', 1)

    results, details, model = await evaluate(
        entries, FakeLLMClient(answers), today=date(2026, 9, 28)
    )

    assert model == "fake-model"
    assert [r.score.perfect for r in results] == [False, True]
    assert details[0]["wrong_fields"]["issuer_name"]["got"].startswith("WRONG ")
    assert results[1].status == "valid"
