# ADR-0002: Single Repo, API-First, Backend-First

Status: Accepted
Date: 2026-09-22

## Context
Open-source contributors split frontend-only vs backend-only. A physical frontend/backend repo or service split would let tracks move independently but introduces version skew, CORS, double deploys, and Pi install complexity.

## Decision
One repo, one deployable (`muhideen.service`). Logical split enforced by import-linter (§4.3/§4.4 PRD, ARCHITECTURE.md). Backend lands `api/` + OpenAPI + normative fixtures first; frontend builds against `uv run tools/mock_api.py :8001` without DB/JAKIM.

## Rationale
* Independence without skew: contract + fixtures are the sole coupling.
* Pi install stays one artifact; OTA stays one version.
* Ownership (backend: `core/domain/engine/adapters/api`; frontend: `views/static/themes/fixtures`) lets parallel work without toe-stepping.

## Consequences
* Every cross-boundary change must ship contract + fixtures + changelog or CI fails.
* Frontend cannot import `domain/`; backend cannot embed presentation logic — enforced, not conventional.
