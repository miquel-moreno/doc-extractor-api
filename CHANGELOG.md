# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

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

### Fixed
- Low confidence on empty fields no longer sends correct documents to review.
