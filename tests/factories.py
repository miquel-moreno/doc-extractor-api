"""Shared test data."""

import json
from typing import Any

CONFIDENCE = {
    "document_number": 0.99,
    "issuer_name": 0.95,
    "issuer_tax_id": 0.9,
    "issue_date": 0.97,
    "subtotal": 0.9,
    "vat_amount": 0.9,
    "total": 0.95,
}


def llm_answer(**overrides: Any) -> str:
    data: dict[str, Any] = {
        "document_type": "invoice",
        "document_number": "F-2026-0001",
        "issuer_name": "Suministros Ejemplo S.L.",
        "issuer_tax_id": "B12345674",
        "issue_date": "2026-09-01",
        "lines": [
            {"description": "Filtro de aceite", "quantity": 2, "unit_price": 10.0, "amount": 20.0},
            {"description": "Bujía", "quantity": 3, "unit_price": 5.5, "amount": 16.5},
        ],
        "subtotal": 36.5,
        "vat_amount": 7.67,
        "total": 44.17,
        "confidence": CONFIDENCE,
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)
