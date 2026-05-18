# seer — rebuild roadmap

This document captures what the kencove/ledoent fork of `getsentry/seer` needs to become a complete drop-in replacement for the closed-source Sentry "Seer" service, plus the concrete issues found while running it self-hosted at `https://sentry.hz.ledoweb.com`.

## Context

When Sentry moved Seer (their AI debugging / autofix service) behind the Sentry-Cloud paywall, they pulled the ML model artefacts and several proprietary integrations out of the public repo. The remaining open code in `getsentry/seer` works — Sentry self-hosted can still talk to it — but it crashes whenever a feature reaches for one of the removed pieces.

The kencove fork's mission is to **rebuild the missing features** so that a self-host can run a fully functional Seer without depending on Sentry's cloud.

### Where it currently lives

- **Forks:**
  - `kencove/seer` (origin) — branch `feature/explorer-endpoints` (this checkout, 42 commits ahead of `origin/main` as of 2026-05-18)
  - `ledoent/seer` — same content, used to publish images because `dnplkndll` doesn't have push to `kencove/`
  - `doc-sheet/seer` (intermediate upstream — earlier community work this fork chains from)
  - `getsentry/seer` (true upstream)
- **Published image:** `ghcr.io/ledoent/seer:lightweight` (built from `Lightweight.Dockerfile` via `.github/workflows/build-push-ghcr.yml` in `ledoent/seer`)
- **Deployed at:** `sentry-seer-1` Hetzner VM (10.0.0.9), docker-compose stack at `/opt/sentry/docker-compose.override.yml` on that host. Documented in [infra0:deployments/sentry-seer/README.md](https://gitlab.com/ledoent/infra/-/tree/main/deployments/sentry-seer)
- **Self-monitoring DSN:** `https://104c5f182c4cc65e2e861a93443bc2bf@sentry.hz.ledoweb.com/4` (project `seer`, id `4`). Every issue listed below is currently captured here.

## What's missing

### 1. ML model artefacts (proprietary — biggest single gap)

`models/` in the source repo is empty (`.gitignore` + `.keep` placeholders only). Four model paths are referenced from [`src/seer/inference_models.py`](src/seer/inference_models.py) and crash whenever the corresponding feature is enabled:

| Feature flag env | Model path expected | Status |
|---|---|---|
| `SEVERITY_ENABLED` | `models/issue_severity_v0/embeddings` + `models/issue_severity_v0/classifier` | **Missing** — crashes at first event (Sentry seer-project issue #8) |
| `GROUPING_ENABLED` | `models/issue_grouping_v0/embeddings` | **Missing** (untested — will surface as soon as grouping is exercised) |
| `AUTOFIXABILITY_SCORING_ENABLED` | `models/autofixability_v4/embeddings` | **Missing** |
| `ANOMALY_DETECTION_ENABLED` | (no path — bundled in code?) | likely OK, untested |

There are no `wget`/`gs://`/`curl` references in `Dockerfile`, `Lightweight.Dockerfile`, or `scripts/` — the closed Sentry build presumably pulls these from a private GCS bucket at image-build time. We need an open replacement.

**Options, ranked by effort:**

1. **Use HuggingFace open embedding models in place of the proprietary ones.** Severity, grouping, and autofixability scoring are all variations on "embed an error message + classify or nearest-neighbor it." `sentence-transformers/all-MiniLM-L6-v2` (~80 MB) or `BAAI/bge-small-en-v1.5` (~130 MB) give us general-purpose text embeddings. Wrap them in the existing `SeverityInference` / `GroupingLookup` interfaces. The classifier head can be retrained on public CWE/severity-labelled corpora (Vulners DB, NVD CVE summaries, Sentry's own public docs of "what's a severe error").
2. **Disable the model-dependent features at build time** (turn `SEVERITY_ENABLED` etc. into hard `false`) and run seer in autofix-only mode. Cheapest, but loses ~half of seer's value.
3. **Train new models from scratch** using Sentry's public OSS issue tracker as a corpus. Most accurate to original but a research project unto itself.

### 2. Production-mode assertion chain

`src/seer/configuration.py` lines 165–185 (function `do_validation()`) require, in production:
- `SENTRY_DSN` ✅ now wired
- `SENTRY_REGION` ✅ set to `us` in compose
- `GITHUB_APP_ID` ❌ needs GitHub App for autofix PR creation
- `GITHUB_PRIVATE_KEY` ❌ same
- (commented out) `GOOGLE_CLOUD_PROJECT`, `LANGFUSE_*`

Current workaround: VM compose override sets `DEV=1`, which flips `is_production` to `False` and skips the whole chain. That's an escape hatch, not a fix. **Fork-level fix:** soften the assertions to warnings + per-feature gates so a partial config still boots cleanly. Concretely:
```python
if self.is_production:
    if not self.SENTRY_DSN:
        logger.warning("Running production-mode without SENTRY_DSN — self-errors will not be captured")
    if self.AUTOFIX_PR_CREATION_ENABLED and not (self.GITHUB_APP_ID and self.GITHUB_PRIVATE_KEY):
        raise ConfigurationError(...)  # only if we actually need PR creation
```

### 3. GitHub App / GitLab token wiring for autofix PR creation

The kencove fork added GitLab support (the `feat/gitlab-repo-client` branch — visible from `git branch -a`), but neither GitHub App nor GitLab token are documented for self-hosters. Need:
- A minimum-scope GitHub App definition (manifest YAML so anyone can install it on their org with one click)
- Same for GitLab (project-or-group-scoped token + which API permissions)
- Document the install + secret-mounting flow in `README.md`

### 4. Codebase storage backend

`docker-compose.yml` hard-codes `GOOGLE_CLOUD_PROJECT=kencove-prod` + `CODEBASE_GCS_STORAGE_BUCKET=autofix-repositories-local`. Self-hosters need:
- Pluggable storage backend (S3, GCS, local filesystem)
- A `STORAGE_BACKEND=local|s3|gcs` env switch
- Default `local` with a docker volume so seer works out-of-the-box without cloud creds
- For our deploy specifically: a `EUROPE-WEST3` GCS bucket is provisioned (`gs://ledo-sentry-blobs`) but seer's storage is currently still pointed at the kencove-prod bucket — needs unwiring

### 5. LLM client abstraction

Currently seer assumes Anthropic / OpenAI clients are wired. For self-host we want:
- Provider switch via env (`LLM_PROVIDER=anthropic|openai|local-via-ollama|claude-api`)
- Graceful degradation (autofix off if no LLM creds) instead of hard fail
- Documented model recommendation for each provider

### 6. Build-time gotchas to fix in `Lightweight.Dockerfile`

- ~~Skips `entrypoint.sh`, so fresh deploys land with an empty seer-db and the autofix celery-beat task fires `SEER-5: relation "run_state" does not exist` every minute until `flask db upgrade heads` is run manually. **Fixed: add `entrypoint.sh` to the COPY list + set it as `ENTRYPOINT`.**~~
- Currently builds `Lightweight.Dockerfile` linux/amd64 only — add `linux/arm64` so M-series Macs can pull and `docker run` locally for development.
- `torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu` is pinned; should track major torch security releases.
- The fat default `Dockerfile` (`Compose.Dockerfile`) pulls full ML training deps and produces a ~7 GB image. Document explicitly that the lightweight one is the production target.
- Add a `HEALTHCHECK` instruction so docker compose can report `healthy` (current `seer` container shows `Up X hours` without health status because there's no built-in check).

## Current observed issues (snapshot 2026-05-18 18:30 UTC)

From `sentry.hz.ledoweb.com` project `seer` (id=4):

| # | level | times | message | hypothesis |
|---|---|---|---|---|
| 5 | error | 2 | `Signal handler bootup_celery_beat raised` | celery beat fails to bootup — likely related to #6/#7 chain |
| 6 | error | 15 (now stopped) | `Exception in worker process: GITHUB_APP_ID required for production!` | the prod-assert chain, fixed by `DEV=1` env in compose override |
| 7 | error | 2 | `Signal handler bootup_celery_worker raised` | same cause as #6, celery side |
| 8 | error | 1 | `Path /app/models/issue_severity_v0/embeddings not found` | section #1 above — proprietary models missing |

#6 / #7 / #5 are essentially resolved at the *config* level; the *code* should still be made more lenient (section #2 of this roadmap). #8 is the unresolved structural gap (section #1).

## Suggested phased roadmap

### Phase 1 — soften the failure modes (1–2 days)

Goal: kencove seer image runs cleanly out-of-the-box without `DEV=1` and without crashing when a model is missing. Self-hosters can adopt it without first hand-editing env vars.

- Replace `assert`s in `src/seer/configuration.py:do_validation()` with per-feature gates + warnings.
- In `src/seer/inference_models.py`, catch `FileNotFoundError` from `model_path(...)` and disable the corresponding feature with a `logger.warning("severity scoring disabled — model artefact missing at {path}")`.
- Add `HEALTHCHECK` to `Lightweight.Dockerfile`.
- New `ghcr.io/ledoent/seer:lightweight` build picks all this up automatically once merged.

### Phase 2 — open-model replacements (1–3 weeks, research effort)

- Wire `sentence-transformers/all-MiniLM-L6-v2` (or similar) as the embeddings backend for severity + grouping.
- Train a small severity classifier head on public-data ground truth (NVD, OWASP, Sentry's own example issues). Stored as a `.pkl` / safetensors in `models/issue_severity_v0/` and committed via git-lfs OR downloaded by a `scripts/fetch-models.sh` invoked from the Dockerfile.
- Decide on autofixability scoring — simplest: rule-based heuristics (event has stacktrace? known framework? small change-set?). LLM-based scoring is the harder right answer.

### Phase 3 — clean self-host story (1 week, mostly polish)

- Pluggable codebase storage (`STORAGE_BACKEND=local|s3|gcs`).
- Pluggable LLM client (`LLM_PROVIDER=anthropic|openai|ollama`).
- GitHub App manifest YAML in `docs/self-host/` so an org-admin can install with one click.
- GitLab equivalent (project token + scoped permissions documented).
- `docs/self-host/README.md` walking through: install seer image, configure storage, wire LLM, link your VCS, point Sentry at `http://seer:9091`.

### Phase 4 — beyond parity (open-ended)

- Replace closed `LANGFUSE_*` observability hooks with OpenTelemetry traces into our existing OpenObserve stack.
- Add support for self-hosted code review tools (Coderabbit, etc.) as alternative PR-creators.
- Ollama-only mode: seer + a local Qwen / Llama model so autofix runs entirely on a single VM with zero cloud calls.

## Tracking + dogfood

- **Roadmap progress** — keep this file updated; tick off items as PRs land.
- **Issue discovery** — `https://sentry.hz.ledoweb.com/organizations/ledoweb/issues/?project=4` is the canonical "what's currently broken in seer." Triage there before opening GitHub Issues; only escalate things that recur or are user-reported.
- **Rebase cadence** — kencove fork was cut from `getsentry/seer` at an early commit; check upstream quarterly for security patches and merge what's relevant.

## Pointers

- This file: `ROADMAP.md`
- Build target: `Lightweight.Dockerfile`
- CI: `.github/workflows/build-push-ghcr.yml`
- Compose entry on VM: `infra0:deployments/sentry-seer/docker-compose.override.yml` (in the ledoent/infra repo)
- Operator doc: `infra0:deployments/sentry-seer/README.md`
- Self-monitor: `https://sentry.hz.ledoweb.com/organizations/ledoweb/issues/?project=4`
