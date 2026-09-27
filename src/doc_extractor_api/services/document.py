"""Schema of a document extracted by the LLM (invoice, delivery note or order).

Money is Decimal, never float: 0.1 + 0.2 must be exactly 0.3 on an invoice.
Almost every field is optional because the LLM may not find it; missing
required fields are reported by the validation rules, not rejected here.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field

Confidence = Annotated[float, Field(ge=0, le=1)]


class DocumentType(StrEnum):
    INVOICE = "invoice"
    DELIVERY_NOTE = "delivery_note"
    ORDER = "order"


class LineItem(BaseModel):
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class ExtractedDocument(BaseModel):
    document_type: DocumentType
    document_number: str | None = None
    issuer_name: str | None = None
    issuer_tax_id: str | None = None
    issue_date: date | None = None
    currency: str = "EUR"
    lines: list[LineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    vat_amount: Decimal | None = None
    total: Decimal | None = None
    confidence: dict[str, Confidence] = Field(
        default_factory=dict,
        description="How sure the extractor is about each field, from 0 to 1.",
    )
