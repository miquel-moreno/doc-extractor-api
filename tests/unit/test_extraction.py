import json
from datetime import date
from decimal import Decimal
from typing import Any

from doc_extractor_api.adapters.llm import FakeLLMClient
from doc_extractor_api.services.document import DocumentType
from doc_extractor_api.services.extraction import (
    EXTRACTION_SCHEMA,
    SYSTEM_PROMPT,
    extract_document,
)
from tests.factories import llm_answer

INVOICE_TEXT = "FACTURA Nº F-2026-0001 · Suministros Ejemplo S.L. · CIF B12345674 ..."


async def test_valid_answer_is_converted_to_the_domain_model() -> None:
    llm = FakeLLMClient([llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert result.succeeded
    assert result.attempts == 1
    doc = result.document
    assert doc is not None
    assert doc.document_type == DocumentType.INVOICE
    assert doc.issue_date == date(2026, 9, 1)
    assert doc.total == Decimal("44.17")
    assert doc.vat_amount == Decimal("7.67")  # exact, no float error
    assert doc.lines[1].unit_price == Decimal("5.5")
    assert doc.confidence["issuer_tax_id"] == 0.9


async def test_prompt_contains_rules_and_document_text() -> None:
    llm = FakeLLMClient([llm_answer()])

    await extract_document(INVOICE_TEXT, llm)

    system, user = llm.calls[0]
    assert system.role == "system" and system.content == SYSTEM_PROMPT
    assert user.role == "user" and INVOICE_TEXT in user.content


async def test_invalid_answer_is_retried_with_the_errors() -> None:
    bad = llm_answer(issue_date="1 de septiembre")
    llm = FakeLLMClient([bad, llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert result.succeeded
    assert result.attempts == 2
    retry = llm.calls[1]
    assert retry[-2].role == "assistant" and retry[-2].content == bad
    assert retry[-1].role == "user" and "issue_date" in retry[-1].content


async def test_non_json_answer_is_retried() -> None:
    llm = FakeLLMClient(["Sorry, I cannot read this.", llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert result.succeeded
    assert result.attempts == 2


async def test_gives_up_after_max_attempts() -> None:
    llm = FakeLLMClient([llm_answer(total="lots"), llm_answer(total="lots")])

    result = await extract_document(INVOICE_TEXT, llm)

    assert not result.succeeded
    assert result.document is None
    assert result.attempts == 2
    assert result.error is not None and "total" in result.error


async def test_domain_rules_violation_is_also_retried() -> None:
    # Valid JSON for the LLM schema, but a quantity of 0 is rejected by the domain model.
    zero = [{"description": "Filtro", "quantity": 0, "unit_price": None, "amount": None}]
    llm = FakeLLMClient([llm_answer(lines=zero), llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert result.attempts == 2
    assert "quantity" in llm.calls[1][-1].content


async def test_extra_fields_are_rejected() -> None:
    llm = FakeLLMClient([llm_answer(customer_name="X"), llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert result.attempts == 2


async def test_usage_is_added_up_across_attempts() -> None:
    llm = FakeLLMClient(["not json", llm_answer()])

    result = await extract_document(INVOICE_TEXT, llm)

    assert (result.input_tokens, result.output_tokens) == (200, 100)
    assert result.model == "fake-model"


async def test_missing_values_stay_empty() -> None:
    answer = llm_answer(
        document_type="order",
        issuer_tax_id=None,
        subtotal=None,
        vat_amount=None,
        total=None,
        lines=[{"description": "Bujía", "quantity": 4, "unit_price": None, "amount": None}],
    )
    result = await extract_document("Pedido por email", FakeLLMClient([answer]))

    doc = result.document
    assert doc is not None
    assert doc.issuer_tax_id is None and doc.total is None
    assert doc.lines[0].amount is None


def test_schema_is_strict() -> None:
    def objects(node: Any) -> list[dict[str, Any]]:
        found = []
        if isinstance(node, dict):
            if node.get("type") == "object":
                found.append(node)
            for value in node.values():
                found += objects(value)
        elif isinstance(node, list):
            for item in node:
                found += objects(item)
        return found

    found = objects(EXTRACTION_SCHEMA)
    assert len(found) == 3  # document, line item, confidence
    for obj in found:
        assert obj["additionalProperties"] is False
        assert set(obj["required"]) == set(obj["properties"])
    assert "default" not in json.dumps(EXTRACTION_SCHEMA)
