# Changelog

All notable changes to this project are documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Project scaffold: FastAPI app, health endpoint, JSON logging, CI, Docker.
- Extracted document schema (invoice, delivery note, order) with per-field confidence.
- Business validation rules: line amounts, subtotal, total, Spanish tax ID (NIF/NIE/CIF),
  dates, required fields and low confidence; any issue sets `needs_review`.
