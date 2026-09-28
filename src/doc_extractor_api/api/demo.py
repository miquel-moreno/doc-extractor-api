"""Minimal demo page (the one in the README GIF). It only calls POST /extract."""

from importlib.resources import files

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(include_in_schema=False)

_PAGE = files("doc_extractor_api").joinpath("static/index.html")


@router.get("/")
async def demo_page() -> HTMLResponse:
    return HTMLResponse(_PAGE.read_text(encoding="utf-8"))
