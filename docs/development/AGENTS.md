# Development Documents — Non-Shipping

**Scope:** This `docs/development/` directory and all subfolders contain development working notes only.

## Status

These documents are **not part of any release artifact** (Pi image, wheel, docs site). They may drift, may be deleted at any time, and maintainers are not obliged to keep them current.

## Hard Guidance

No code file, docstring, or shipped doc (including files outside this directory) **must reference** these documents by filename or by quoting them. Treat them as ephemeral planning notes.

## Not Authoritative

* Architecture: see `ARCHITECTURE.md`.
* Lasting decisions: see `docs/adr/`.
* Requirements: see `PRD.md`.
* API: see `docs/api-contract.md` + `api/fixtures/`.

Once a slice lands, its plan notes here are considered outdated.
