"""Record docs/images/demo.gif from the demo page.

Needs the service running (`docker compose up`) with a real LLM configured: it
processes one synthetic invoice and one invented email with wrong totals (about
0.1 US cents with gpt-4.1-mini). Uses the installed Microsoft Edge, so no browser
download is needed.

The documents must be new to the database, otherwise the page shows the stored
result ("ya procesado"). Start from an empty database or change the documents.

    uv run python -m scripts.record_demo
"""

import argparse
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
INVOICE = ROOT / "evals" / "dataset" / "invoice-007.pdf"
OUT = ROOT / "docs" / "images" / "demo.gif"
VIEWPORT = {"width": 900, "height": 860}

# Invented email with wrong totals: 20.00 + 4.20 is 24.20, not 25.20.
WRONG_TOTAL_EMAIL = """\
De: Talleres Ejemplo S.L. <facturas@example.com>
Asunto: Factura F-2026-0101

FACTURA F-2026-0101 · Fecha: 15/09/2026
Emisor: Talleres Ejemplo S.L. · CIF B12345674

2 x Filtro de aceite · 10,00 € · 20,00 €

Base imponible: 20,00 €
IVA 21 %: 4,20 €
TOTAL: 25,20 €"""


class Recorder:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[tuple[Image.Image, int]] = []

    def shot(self, ms: int) -> None:
        image = Image.open(io.BytesIO(self.page.screenshot()))
        self.frames.append((image.convert("RGB"), ms))

    def save(self, path: Path) -> None:
        # One shared palette keeps colours stable across frames and the file small.
        palette = self.frames[-1][0].quantize(colors=128, method=Image.Quantize.MEDIANCUT)
        images = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f, _ in self.frames]
        path.parent.mkdir(parents=True, exist_ok=True)
        images[0].save(
            path,
            save_all=True,
            append_images=images[1:],
            duration=[ms for _, ms in self.frames],
            loop=0,
            optimize=True,
        )


def wait_for_result(page: Page) -> None:
    page.wait_for_selector(
        "#result:not(.hidden) .status, #result:not(.hidden) .error", timeout=120_000
    )


def record(base_url: str, out: Path) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport=VIEWPORT)
        page.goto(base_url)
        rec = Recorder(page)
        rec.shot(1500)

        # 1) A synthetic PDF invoice -> valid.
        page.set_input_files("#file", str(INVOICE))
        rec.shot(1200)
        page.click("#send")
        rec.shot(900)  # "Leyendo..."
        wait_for_result(page)
        rec.shot(4500)

        # 2) An email with wrong totals -> sent to review, with the reason.
        page.goto(base_url)
        page.fill("#text", WRONG_TOTAL_EMAIL)
        rec.shot(2200)
        page.click("#send")
        rec.shot(900)
        wait_for_result(page)
        rec.shot(5000)

        browser.close()
    rec.save(out)
    print(f"{out} · {len(rec.frames)} frames · {out.stat().st_size / 1024:.0f} KB")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--url", default="http://localhost:8000/")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args()
    record(args.url, args.out)


if __name__ == "__main__":
    main()
