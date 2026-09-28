# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Evaluation on the 50-document dataset (`make eval`): per-field accuracy, perfect documents,
  safety metrics (wrong documents marked valid), time and cost. Results for gpt-4.1-mini
  and qwen2.5:3b in `evals/results/`.
- Job queue: `POST /jobs` (202 + job id) and `GET /jobs/{id}`, with a Redis (arq) worker that
  retries with backoff when the LLM provider fails. `docker compose` now runs API, worker,
  PostgreSQL and Redis.

### Fixed
- Invoice lines without unit price or amount now go to review (found by the evaluation).
- The document number is extracted without its label ("Factura nº …").

## [0.1.0] - 2026-09-28

### Added
- Project scaffold: FastAPI app, health endpoint, JSON logging, CI, Docker.
- Extracted document schema (invoice, delivery note, order) with per-field confidence.
- Business validation rules: line amounts, subtotal, total, Spanish tax ID (NIF/NIE/CIF),
  dates, required fields and low confidence; any issue sets `needs_review`.
- Synthetic dataset generator (`scripts/generate_invoices.py`) and a 50-document
  evaluation set: 30 invoices in 3 layouts, 10 delivery notes and 10 email orders,
  each with its expected JSON.
- LLM client for OpenAI and Ollama (OpenAI-compatible Chat Completions) with strict
  structured output, token usage and latency.
- LLM extraction service with one retry on invalid output.
- `documents` table (SQLAlchemy 2 async + Alembic migration) with a unique SHA-256 fingerprint.
- Idempotent processing: fingerprint, deduplicate, extract, validate and store; failed
  extractions are kept for human review.
- REST API: `POST /extract` (PDF or text; 201 new, 200 already processed), `GET /documents/{id}`
  and `GET /reviews` (human review queue), with clear 413/415/422/503 errors.
- Docker image applies database migrations on start; `docker compose up` runs API + PostgreSQL.

### Fixed
- Low confidence on empty fields no longer sends correct documents to review.
- Amounts are always returned with two decimals (cents).

[Unreleased]: https://github.com/miquel-moreno/doc-extractor-api/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/miquel-moreno/doc-extractor-api/releases/tag/v0.1.0
