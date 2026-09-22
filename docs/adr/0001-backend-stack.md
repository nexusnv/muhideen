# ADR-0001: FastAPI-sync + Uvicorn Single Worker

Status: Accepted
Date: 2026-09-22

## Context
PRD §4 evaluated Node+Vue, Python sync, Go, and Laravel. Backend RAM is non-decisive (Chromium dominates). The decision is between contract rigidity + contributor familiarity (FastAPI-sync) and minimal weight (Flask+Waitress).

## Decision
FastAPI with `def` sync endpoints, one Uvicorn worker, Pydantic DTOs as the executable §6.3 contract. SQLite on a single writer thread. No async DB code.

## Rationale
* Pydantic + OpenAPI make the frontend/backend contract machine-checked; drift fails CI instead of relying on discipline.
* Strict pyright pairs better with typed DTOs than hand-checked Flask dicts.
* Sync handlers keep SQLite safe; single worker removes multi-writer complexity on Pi.
* Python familiarity maximizes Malaysian volunteer contributions vs Go.

## Consequences
* Heavier deps (pydantic-core wheels) — mitigated by vendored wheels + `uv` lock.
* Uvicorn ASGI ops instead of Waitress WSGI — single-process systemd unit, no extra proxy.
* Future Go appliance stays possible behind the same contract (ADR-0002).
