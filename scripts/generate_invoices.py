"""Generate synthetic invoices, delivery notes and email orders with their expected data.

    uv run python -m scripts.generate_invoices                 # writes evals/dataset/
    uv run python -m scripts.generate_invoices --seed 7 --out some/dir

Everything is fake (Faker, es_ES) and reproducible: the same seed always produces
the same documents. Each document gets a JSON file with the exact data an extractor
should return (the ground truth for tests and evaluation), plus a manifest.json.
"""

import argparse
import json
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from doc_extractor_api.services.document import DocumentType, ExtractedDocument, LineItem
from doc_extractor_api.services.tax_id import NIF_LETTERS, validate_tax_id

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "evals" / "dataset"
DEFAULT_SEED = 1

CENT = Decimal("0.01")
VAT_RATES = (Decimal("0.21"), Decimal("0.10"), Decimal("0.04"))
VAT_WEIGHTS = (8, 1, 1)
FIRST_DATE = date(2025, 1, 1)
LAST_DATE = date(2026, 9, 1)
INVOICE_LAYOUTS = ("classic", "compact", "modern")
MONTHS_ES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

# (description, min price, max price) in euros.
PRODUCTS: tuple[tuple[str, int, int], ...] = (
    ("Filtro de aceite", 6, 25),
    ("Filtro de aire", 8, 30),
    ("Filtro de habitáculo", 9, 28),
    ("Pastillas de freno delanteras", 25, 90),
    ("Discos de freno (par)", 45, 180),
    ("Bujía de encendido", 4, 18),
    ("Batería 12V 70Ah", 80, 160),
    ("Neumático 205/55 R16", 55, 130),
    ("Aceite motor 5W30 (5 L)", 25, 60),
    ("Líquido de frenos DOT4 (1 L)", 6, 15),
    ("Anticongelante (5 L)", 12, 30),
    ("Correa de distribución", 30, 120),
    ("Bomba de agua", 35, 140),
    ("Amortiguador trasero", 40, 110),
    ("Escobillas limpiaparabrisas", 8, 30),
    ("Lámpara H7", 3, 15),
    ("Kit de embrague", 120, 350),
    ("Juego de juntas", 10, 45),
    ("Rodamiento de rueda", 20, 70),
    ("Silentblock", 8, 35),
    ("Guantes de nitrilo (caja 100)", 6, 14),
    ("Papel de taller (rollo)", 10, 25),
    ("Desengrasante (5 L)", 12, 35),
    ("Mano de obra (hora)", 35, 60),
)


@dataclass(frozen=True)
class Party:
    name: str
    tax_id: str
    address: str


@dataclass(frozen=True)
class SyntheticDocument:
    doc_id: str
    filename: str
    layout: str
    document: ExtractedDocument
    issuer: Party
    customer: Party
    vat_rate: Decimal | None = None


class DatasetGenerator:
    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        self.fake = Faker("es_ES")
        self.fake.seed_instance(seed)

    # --- Building blocks ---------------------------------------------------

    def cif(self) -> str:
        prefix = self.rng.choice("ABBBBG")
        digits = f"{self.rng.randrange(10**7):07d}"
        for control in "0123456789ABCDEFGHIJ":
            if validate_tax_id(prefix + digits + control).is_valid:
                return prefix + digits + control
        raise AssertionError("unreachable: every CIF has a valid control character")

    def nif(self) -> str:
        number = self.rng.randrange(10**7, 10**8)
        return f"{number}{NIF_LETTERS[number % 23]}"

    def party(self) -> Party:
        address = self.fake.address().replace("\n", ", ")
        if self.rng.random() < 0.8:
            return Party(self.fake.company(), self.cif(), address)
        return Party(self.fake.name(), self.nif(), address)

    def issue_date(self) -> date:
        return FIRST_DATE + timedelta(days=self.rng.randrange((LAST_DATE - FIRST_DATE).days + 1))

    def lines(self, *, priced: bool) -> list[LineItem]:
        items = []
        for description, low, high in self.rng.sample(PRODUCTS, self.rng.randint(1, 8)):
            quantity = Decimal(self.rng.randint(1, 12))
            if not priced:
                items.append(LineItem(description=description, quantity=quantity))
                continue
            price = (Decimal(self.rng.randint(low * 100, high * 100)) / 100).quantize(CENT)
            amount = (quantity * price).quantize(CENT, rounding=ROUND_HALF_UP)
            items.append(
                LineItem(
                    description=description, quantity=quantity, unit_price=price, amount=amount
                )
            )
        return items

    # --- Documents ---------------------------------------------------------

    def invoice(self, index: int) -> SyntheticDocument:
        issuer, customer, issued = self.party(), self.party(), self.issue_date()
        number_format = self.rng.choice(("F-{y}-{n:04d}", "{y}/{n:05d}", "FAC{n:06d}"))
        lines = self.lines(priced=True)
        subtotal = sum((line.amount for line in lines if line.amount is not None), Decimal(0))
        rate = self.rng.choices(VAT_RATES, weights=VAT_WEIGHTS)[0]
        vat = (subtotal * rate).quantize(CENT, rounding=ROUND_HALF_UP)
        doc = ExtractedDocument(
            document_type=DocumentType.INVOICE,
            document_number=number_format.format(y=issued.year, n=self.rng.randint(1, 9999)),
            issuer_name=issuer.name,
            issuer_tax_id=issuer.tax_id,
            issue_date=issued,
            lines=lines,
            subtotal=subtotal,
            vat_amount=vat,
            total=subtotal + vat,
        )
        doc_id = f"invoice-{index:03d}"
        layout = INVOICE_LAYOUTS[(index - 1) % len(INVOICE_LAYOUTS)]
        return SyntheticDocument(doc_id, f"{doc_id}.pdf", layout, doc, issuer, customer, rate)

    def delivery_note(self, index: int) -> SyntheticDocument:
        issuer, customer = self.party(), self.party()
        doc = ExtractedDocument(
            document_type=DocumentType.DELIVERY_NOTE,
            document_number=f"ALB-{self.rng.randint(1, 99999):05d}",
            issuer_name=issuer.name,
            issuer_tax_id=issuer.tax_id,
            issue_date=self.issue_date(),
            lines=self.lines(priced=False),
        )
        doc_id = f"delivery-note-{index:03d}"
        return SyntheticDocument(doc_id, f"{doc_id}.pdf", "delivery_note", doc, issuer, customer)

    def order_email(self, index: int) -> SyntheticDocument:
        # In an order the issuer is the customer who sends it; the supplier receives it.
        issuer, supplier = self.party(), self.party()
        with_tax_id = self.rng.random() < 0.5
        doc = ExtractedDocument(
            document_type=DocumentType.ORDER,
            document_number=f"PED-{self.rng.randint(1, 9999):04d}",
            issuer_name=issuer.name,
            issuer_tax_id=issuer.tax_id if with_tax_id else None,
            issue_date=self.issue_date(),
            lines=self.lines(priced=False),
        )
        doc_id = f"order-{index:03d}"
        return SyntheticDocument(doc_id, f"{doc_id}.txt", "email", doc, issuer, supplier)


# --- Formatting ------------------------------------------------------------


def money_es(value: Decimal) -> str:
    """1234.5 -> '1.234,50 €'"""
    text = f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{text} €"


def money_plain(value: Decimal) -> str:
    return f"EUR {value:.2f}"


def date_long(value: date) -> str:
    return f"{value.day} de {MONTHS_ES[value.month - 1]} de {value.year}"


def tax_id_dashed(value: str) -> str:
    return f"{value[:-1]}-{value[-1]}" if value[0].isdigit() else f"{value[0]}-{value[1:]}"


def percent(rate: Decimal) -> str:
    return f"{rate * 100:.0f}"


# --- PDF rendering ---------------------------------------------------------

WIDTH, HEIGHT = A4
LEFT, RIGHT = 50, WIDTH - 50


def _text(c: Canvas, x: float, y: float, text: str, size: int = 10, bold: bool = False) -> None:
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawString(x, y, text)


def _right(c: Canvas, x: float, y: float, text: str, size: int = 10, bold: bool = False) -> None:
    c.setFont("Helvetica-Bold" if bold else "Helvetica", size)
    c.drawRightString(x, y, text)


def _table(
    c: Canvas,
    y: float,
    headers: tuple[str, ...],
    columns: tuple[float, ...],
    rows: list[tuple[str, ...]],
) -> float:
    """Draw a simple table. First column left-aligned, the rest right-aligned."""
    for i, header in enumerate(headers):
        (_text if i == 0 else _right)(c, columns[i], y, header, 9, bold=True)
    c.line(LEFT, y - 5, RIGHT, y - 5)
    y -= 20
    for row in rows:
        for i, cell in enumerate(row):
            (_text if i == 0 else _right)(c, columns[i], y, cell, 9)
        y -= 16
    c.line(LEFT, y + 8, RIGHT, y + 8)
    return y - 10


def _priced_rows(doc: ExtractedDocument, money: Callable[[Decimal], str]) -> list[tuple[str, ...]]:
    return [
        (line.description, str(line.quantity), money(price), money(amount))
        for line in doc.lines
        if (price := line.unit_price) is not None and (amount := line.amount) is not None
    ]


def _totals(c: Canvas, y: float, rows: list[tuple[str, str]]) -> None:
    for i, (label, value) in enumerate(rows):
        bold = i == len(rows) - 1
        _right(c, RIGHT - 110, y, label, 10, bold)
        _right(c, RIGHT, y, value, 10, bold)
        y -= 16


def _invoice_amounts(sd: SyntheticDocument) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    doc = sd.document
    assert doc.subtotal is not None and doc.vat_amount is not None and doc.total is not None
    assert sd.vat_rate is not None
    return doc.subtotal, doc.vat_amount, doc.total, sd.vat_rate


def render_classic(c: Canvas, sd: SyntheticDocument) -> None:
    doc, issuer, customer = sd.document, sd.issuer, sd.customer
    subtotal, vat, total, rate = _invoice_amounts(sd)
    assert doc.issue_date is not None and doc.document_number is not None
    top = HEIGHT - 60
    _text(c, LEFT, top, issuer.name, 14, bold=True)
    _text(c, LEFT, top - 16, f"CIF/NIF: {issuer.tax_id}", 9)
    _text(c, LEFT, top - 28, issuer.address, 9)
    _right(c, RIGHT, top, "FACTURA", 20, bold=True)
    _right(c, RIGHT, top - 20, f"Nº {doc.document_number}")
    _right(c, RIGHT, top - 34, f"Fecha: {doc.issue_date:%d/%m/%Y}")
    _text(c, LEFT, top - 70, "Cliente:", 10, bold=True)
    _text(c, LEFT, top - 84, customer.name, 9)
    _text(c, LEFT, top - 96, f"NIF: {customer.tax_id}", 9)
    _text(c, LEFT, top - 108, customer.address, 9)
    y = _table(
        c,
        top - 150,
        ("Concepto", "Cant.", "Precio", "Importe"),
        (LEFT, 330, 430, RIGHT),
        _priced_rows(doc, money_es),
    )
    _totals(
        c,
        y,
        [
            ("Base imponible", money_es(subtotal)),
            (f"IVA {percent(rate)} %", money_es(vat)),
            ("TOTAL", money_es(total)),
        ],
    )


def render_compact(c: Canvas, sd: SyntheticDocument) -> None:
    doc, issuer, customer = sd.document, sd.issuer, sd.customer
    subtotal, vat, total, rate = _invoice_amounts(sd)
    assert doc.issue_date is not None
    top = HEIGHT - 60
    _text(c, LEFT, top, f"Factura nº {doc.document_number}", 16, bold=True)
    _text(c, LEFT, top - 18, date_long(doc.issue_date), 10)
    _text(c, LEFT, top - 44, f"{issuer.name} · ES{issuer.tax_id}", 9)
    _text(c, LEFT, top - 56, issuer.address, 9)
    _right(c, RIGHT, top - 44, "Facturar a:", 9, bold=True)
    _right(c, RIGHT, top - 56, f"{customer.name} ({customer.tax_id})", 9)
    y = _table(
        c,
        top - 100,
        ("Descripción", "Unidades", "Precio unidad", "Total"),
        (LEFT, 320, 430, RIGHT),
        _priced_rows(doc, money_plain),
    )
    _totals(
        c,
        y,
        [
            ("Subtotal", money_plain(subtotal)),
            (f"Cuota IVA ({percent(rate)}%)", money_plain(vat)),
            ("Total factura", money_plain(total)),
        ],
    )


def render_modern(c: Canvas, sd: SyntheticDocument) -> None:
    doc, issuer, customer = sd.document, sd.issuer, sd.customer
    subtotal, vat, total, rate = _invoice_amounts(sd)
    assert doc.issue_date is not None
    top = HEIGHT - 60
    _text(c, LEFT, top, "Cliente", 9, bold=True)
    _text(c, LEFT, top - 14, customer.name, 11)
    _text(c, LEFT, top - 28, f"NIF/CIF {tax_id_dashed(customer.tax_id)}", 9)
    _right(c, RIGHT, top, "Número de factura", 9, bold=True)
    _right(c, RIGHT, top - 14, str(doc.document_number), 11)
    _right(c, RIGHT, top - 34, "Fecha de emisión", 9, bold=True)
    _right(c, RIGHT, top - 48, doc.issue_date.isoformat(), 11)
    rows = [
        (f"{i:02d} · {line.description}", str(line.quantity), money_es(p), money_es(a))
        for i, line in enumerate(doc.lines, start=1)
        if (p := line.unit_price) is not None and (a := line.amount) is not None
    ]
    y = _table(
        c, top - 100, ("Descripción", "Uds", "P. unit.", "Total"), (LEFT, 330, 430, RIGHT), rows
    )
    _totals(
        c,
        y,
        [
            ("Importe neto", money_es(subtotal)),
            (f"IVA {percent(rate)} %", money_es(vat)),
            ("TOTAL A PAGAR", money_es(total)),
        ],
    )
    footer = (
        f"Emitida por {issuer.name} · CIF/NIF {tax_id_dashed(issuer.tax_id)} · {issuer.address}"
    )
    _text(c, LEFT, 40, footer, 8)


def render_delivery_note(c: Canvas, sd: SyntheticDocument) -> None:
    doc, issuer, customer = sd.document, sd.issuer, sd.customer
    assert doc.issue_date is not None
    top = HEIGHT - 60
    _text(c, LEFT, top, issuer.name, 14, bold=True)
    _text(c, LEFT, top - 16, f"NIF: {issuer.tax_id} · {issuer.address}", 9)
    _right(c, RIGHT, top - 50, "ALBARÁN DE ENTREGA", 16, bold=True)
    _right(c, RIGHT, top - 68, f"Nº {doc.document_number} · {doc.issue_date:%d/%m/%Y}")
    _text(c, LEFT, top - 100, "Entregar a:", 10, bold=True)
    _text(c, LEFT, top - 114, customer.name, 9)
    _text(c, LEFT, top - 126, customer.address, 9)
    rows = [(line.description, str(line.quantity)) for line in doc.lines]
    y = _table(c, top - 170, ("Descripción", "Cantidad"), (LEFT, RIGHT), rows)
    _text(c, LEFT, y - 40, "Recibí conforme: ____________________", 10)


RENDERERS: dict[str, Callable[[Canvas, SyntheticDocument], None]] = {
    "classic": render_classic,
    "compact": render_compact,
    "modern": render_modern,
    "delivery_note": render_delivery_note,
}


def render_pdf(sd: SyntheticDocument, path: Path) -> None:
    # invariant=1 removes timestamps and random ids, so the same seed gives identical bytes.
    c = Canvas(str(path), pagesize=A4, invariant=1)
    RENDERERS[sd.layout](c, sd)
    c.showPage()
    c.save()


# --- Email rendering -------------------------------------------------------

ORDER_LINE_FORMATS = ("- {q} x {d}", "- {d}: {q} uds.", "- {q} unidades de {d}")


def render_email(sd: SyntheticDocument, fake: Faker, rng: random.Random) -> str:
    doc, issuer = sd.document, sd.issuer
    assert doc.issue_date is not None
    contact = fake.first_name()
    line_format = rng.choice(ORDER_LINE_FORMATS)
    lines = [line_format.format(q=line.quantity, d=line.description) for line in doc.lines]
    signature = [contact, issuer.name]
    if doc.issuer_tax_id:
        signature.append(f"CIF: {doc.issuer_tax_id}")
    signature.append(issuer.address)
    return "\n".join(
        [
            f"De: {contact} <{fake.safe_email()}>",
            f"Para: {sd.customer.name} <pedidos@example.com>",
            f"Fecha: {date_long(doc.issue_date)}",
            f"Asunto: Pedido {doc.document_number}",
            "",
            "Hola,",
            "",
            f"Os paso el pedido {doc.document_number}:",
            "",
            *lines,
            "",
            "Por favor, confirmadme el plazo de entrega.",
            "",
            "Un saludo,",
            *signature,
            "",
        ]
    )


# --- Dataset ---------------------------------------------------------------


def generate_dataset(
    out_dir: Path,
    *,
    seed: int = DEFAULT_SEED,
    invoices: int = 30,
    delivery_notes: int = 10,
    orders: int = 10,
) -> list[SyntheticDocument]:
    generator = DatasetGenerator(seed)
    documents = [
        *(generator.invoice(i) for i in range(1, invoices + 1)),
        *(generator.delivery_note(i) for i in range(1, delivery_notes + 1)),
        *(generator.order_email(i) for i in range(1, orders + 1)),
    ]

    out_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("invoice-*", "delivery-note-*", "order-*", "manifest.json"):
        for stale in out_dir.glob(pattern):
            stale.unlink()

    manifest = []
    for sd in documents:
        source = out_dir / sd.filename
        if sd.layout == "email":
            text = render_email(sd, generator.fake, generator.rng)
            source.write_text(text, encoding="utf-8", newline="\n")
        else:
            render_pdf(sd, source)
        expected = sd.document.model_dump(mode="json", exclude={"confidence"})
        _write_json(out_dir / f"{sd.doc_id}.json", expected)
        manifest.append(
            {
                "id": sd.doc_id,
                "file": sd.filename,
                "expected": f"{sd.doc_id}.json",
                "document_type": sd.document.document_type.value,
                "layout": sd.layout,
            }
        )
    _write_json(out_dir / "manifest.json", {"seed": seed, "documents": manifest})
    return documents


def _write_json(path: Path, data: object) -> None:
    # Always LF, so Windows and Linux produce byte-identical files.
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    documents = generate_dataset(args.out, seed=args.seed)
    print(f"Generated {len(documents)} documents in {args.out}")


if __name__ == "__main__":
    main()
