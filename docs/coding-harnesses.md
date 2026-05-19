# Coding harnesses for seer's autofix loop

A research/decision doc for replacing seer's hand-rolled agent loop (`automation/autofix/autofix_agent.py`) with an off-the-shelf coding harness. ROADMAP Phase 2 deliverable.

## Why this decision matters

Seer's current autofix is a bespoke ReAct-style loop: a prompt template, a fixed tool set (`semantic_search`, `comment_thread`, `confidence`, `root_cause`), and ad-hoc orchestration in `AutofixAgent`. It works for *"explain this stacktrace + suggest a single-file change"* but hits a hard ceiling on:

- multi-file refactors
- "fix and verify" loops (no test-run capability)
- non-trivial code generation where the model needs to read 5+ files before proposing a change
- producing a clean diff vs. a verbose markdown explanation

Modern coding harnesses (Claude Code SDK, aider, OpenHands, etc.) have already solved the agent-loop problem with mature tool surfaces, sandboxing, and PR-creation primitives. We should not be building a second one.

The dogfood loop we already have on `sentry.hz.ledoweb.com` (seer reports its own errors to project id=4) is the perfect testbed to compare a new harness against the existing autofix on real bugs.

## Self-host constraints

Any harness we adopt has to fit these:

| Constraint | Source |
|---|---|
| **Provider: Gemini via Vertex ADC** | Already wired: `seer-vertex@kendall-ledo.iam.gserviceaccount.com` + `roles/aiplatform.user`. No Anthropic-on-Vertex Marketplace subscription. No `OPENAI_API_KEY`. |
| **Budget: ~$20/mo Vertex AI total** | `Sentry Seer + Ledoweb Gemini - $20 cap` budget on the kendall-ledo billing account. Realistic autofix volume is ~20-50 invocations/day. |
| **Runs inside a Docker container on a non-cluster VM** | `sentry-self-hosted-seer-1` on the sentry-seer VM. No IDE, no human in the loop, no shell prompt. |
| **Public fork** | `ledoent/seer` is public; no closed-source SDKs or runtime libraries. |
| **License-compatible** | Apache 2.0 / MIT / similarly permissive. No GPL contamination of the fork. |
| **Input shape** | A Sentry event (stacktrace + project metadata) plus optional repo URL. Output is a structured diagnosis + optional patch + optional PR. |
| **Observability** | Has to coexist with langfuse spans + the seer-project DSN that already captures autofix errors. |

## Harness landscape

### Anthropic Claude Code SDK (headless mode)

- **What** — Anthropic's own coding agent, exposable as a SDK / library / subprocess in headless mode. Tool-rich (Read, Edit, Bash, Glob, Grep, WebFetch). Stateful conversation.
- **Provider** — Claude via direct API (`ANTHROPIC_API_KEY`) or Vertex AI (requires Anthropic Marketplace subscribe — we don't have).
- **Self-host fit** — Headless mode is well-supported. Can pipe in a prompt + a working dir + get back a structured response. License: Anthropic SDK terms (proprietary but free to use).
- **Cost/fix (est.)** — $0.10-$0.30 with Sonnet 4.5 for a typical autofix. ~$3/$15 per million tokens.
- **Blocker** — needs a fresh ANTHROPIC_API_KEY (separate from our Vertex setup) or paid Anthropic-on-Vertex Marketplace.

### OpenAI Codex CLI

- **What** — OpenAI's headless coding agent, ships as a CLI binary.
- **Provider** — OpenAI API key only. No Vertex bridge.
- **Self-host fit** — Same shape as Claude Code SDK headless.
- **Cost/fix (est.)** — $0.10-$0.40 with GPT-4.1 / o3-mini.
- **Blocker** — needs OPENAI_API_KEY (we have zero).

### Gemini CLI / Jules

- **What** — Google's official agentic coding CLI; Jules is Google's web/async variant (free tier).
- **Provider** — Native Vertex AI / AI Studio. Works with our existing seer-vertex SA.
- **Self-host fit** — CLI mode is non-interactive, can run inside the container. Maturity is younger than Claude Code SDK but improving fast.
- **Cost/fix (est.)** — $0.01-$0.05 with Gemini 2.5 Flash, $0.05-$0.15 with Pro. Cheapest of the big-three.
- **Caveat** — Tool surface is narrower than Claude Code; multi-file editing works but feedback loops are less sophisticated.

### aider

- **What** — Apache-2.0-licensed coding assistant; runs in pair-programmer or non-interactive (`--message`) mode. Mature (active dev since 2023).
- **Provider** — Provider-agnostic: `aider --model gemini/gemini-2.5-flash` works against our Vertex setup with zero new keys (uses the same ADC). Also supports Anthropic / OpenAI / Ollama / etc.
- **Self-host fit** — Best fit by a wide margin. Pip-installable, runs in a container, requires git init in the working dir. Has built-in `--auto-commits` + `--gitignore` handling. Can be driven entirely from one shell command per autofix invocation.
- **Cost/fix (est.)** — same as the underlying provider; with Gemini Flash, **~$0.005-$0.01/fix**.
- **Risk** — Output is a diff applied to disk + a git commit; seer would need to capture the diff + structured root-cause separately. Slight impedance mismatch with seer's existing structured-output expectations.

### OpenHands (formerly OpenDevin)

- **What** — Most capable open coding agent; runs in a sandboxed VM/container. Apache 2.0.
- **Provider** — Provider-agnostic.
- **Self-host fit** — Heavyweight (own runtime container, own LLM bridge). Overkill for autofix-class tasks; designed for "give me a feature, here's the whole repo."
- **Cost/fix (est.)** — $0.20-$1.50 because of many tool calls per task.
- **When to revisit** — if we ever want seer to actually implement features (not just fix bugs).

### Plandex

- **What** — Plan-then-execute CLI agent (MIT). Different paradigm: builds a multi-step plan before touching code.
- **Provider** — Provider-agnostic.
- **Self-host fit** — Interesting middle ground. Less mature than aider, more lightweight than OpenHands.
- **When to revisit** — if aider's single-shot approach turns out to be too thin for our actual fixes.

### Seer's existing hand-rolled agent (the baseline)

- **What** — `AutofixAgent` in `automation/autofix/autofix_agent.py` with the tools in `automation/autofix/tools/`.
- **Provider** — Gemini via Vertex (we just verified this end-to-end).
- **Self-host fit** — Already wired. Zero migration cost.
- **Cost/fix** — ~$0.02-$0.10/fix on Gemini 2.5 Pro depending on event size.
- **Quality ceiling** — single-file edits, narrow tool surface, no test-run, output coupled to seer's Sentry-side UI.

## Comparison matrix

| Harness | Multi-file edits | Runs tests | Web research | PR creation | Provider flex | License | Maturity | Est. $/fix on Gemini Flash | Integration effort |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| **aider** | ✅ | ✅ | partial | via git | **✅ Gemini-native** | Apache-2.0 | mature | **$0.005-$0.01** | low |
| Claude Code SDK headless | ✅ | ✅ | ✅ | ✅ | Anthropic-only | Anthropic SDK | mature | n/a (needs ANTHROPIC_API_KEY) | low |
| Gemini CLI / Jules | ✅ | ✅ | ✅ | ✅ | Gemini-only | Apache-2.0 | early-mid | $0.01-$0.05 | low |
| OpenAI Codex CLI | ✅ | ✅ | ✅ | ✅ | OpenAI-only | OpenAI SDK | mature | n/a (needs OPENAI_API_KEY) | low |
| OpenHands | ✅ | ✅ | ✅ | ✅ | agnostic | Apache-2.0 | heaviest | $0.20-$1.50 | medium-high |
| Plandex | ✅ | partial | ✅ | via git | agnostic | MIT | early-mid | $0.01-$0.05 | medium |
| **Seer baseline** | weak | ❌ | ❌ | via integration | Gemini (now) | seer's | works but capped | $0.02-$0.10 | zero (already there) |

## Integration shape (the actual code change)

Today's autofix loop lives in:

- `src/seer/automation/autofix/autofix_agent.py` — the `AutofixAgent` class with `run_iteration`, `get_completion`, `share_insights`
- `src/seer/automation/autofix/components/` — `root_cause/`, `coding/`, `solution/` each invoke `AutofixAgent` with a different prompt + tools
- `src/seer/automation/agent/client.py` — `GeminiProvider`, `OpenAiProvider`, `LlmClient.generate_text`, `generate_structured`

To swap to a harness without ripping out the existing scaffolding:

1. **Add a `HarnessProvider` abstraction** alongside `GeminiProvider` etc. in `client.py`. Same `model(...)` factory pattern. The "model" for a harness is a `(harness_binary, llm_provider, llm_model)` tuple.
2. **New file: `src/seer/automation/harness/aider.py`** — wraps `subprocess.run(['aider', '--no-pretty', '--yes', '--auto-commits', '--message=<prompt>', '--model=gemini/gemini-2.5-flash', ...])` in a working directory that's a shallow clone of the target repo (or the cached `seer_repo_archive` we already use for autofix-repo-snapshots).
3. **`AutofixAgent.run_iteration` becomes harness-aware** — when the configured provider is a `HarnessProvider`, delegate the whole loop instead of running the in-process ReAct.
4. **Diff capture** — aider produces a git commit; we read it back via `git diff HEAD~1 HEAD`, attach it to the autofix-step output, and pass to the existing `share_insights` so the Sentry UI gets the same shape it's expecting.
5. **Feature flag** — `AUTOFIX_HARNESS=aider|builtin` env. Default `builtin` until we validate. The feature-flag in `configuration.py` already has the `AUTOFIXABILITY_SCORING_ENABLED` pattern to mimic.
6. **Keep the existing `share_insights`, langfuse spans, and Sentry DSN dogfood path** — the harness wrapper emits the same shape of step outputs.

Net code change estimate: ~300 lines added (harness wrapper + tests + flag) + ~50 lines touched (autofix_agent + configuration.py + the 3 autofix components). No DB migrations. No new env vars beyond `AUTOFIX_HARNESS`.

## Decision matrix

| Criterion | aider | Claude Code SDK | Gemini CLI | Seer baseline |
|---|:---:|:---:|:---:|:---:|
| Works against our existing Vertex ADC | ✅ | ❌ (needs key) | ✅ | ✅ |
| Inside $20/mo budget at projected volume | ✅ | ❌ (no key) | ✅ | ✅ |
| Multi-file edit quality | high | highest | high | low |
| Integration effort | low | low (when keyed) | low | zero |
| License-clean for our fork | ✅ Apache-2.0 | proprietary SDK | ✅ Apache-2.0 | n/a (own code) |
| Mature enough to bet on today | ✅ | ✅ | mid | ✅ |
| Has runs-tests-in-loop primitive | ✅ | ✅ | ✅ | ❌ |
| **Score (10 = perfect fit)** | **9** | 5 (key blocker) | 7 | 4 |

## Recommendation — phased

### Phase 2a — aider behind a feature flag (3-5 days)

- Add `HarnessProvider` + `AiderHarness` wrapper. Feature-flagged via `AUTOFIX_HARNESS=aider`.
- Bake `aider-chat` into `Lightweight.Dockerfile`.
- Pick 5-10 real Sentry issues (the seer project itself + meas-inst/duropc as they spin up) and run both harnesses side-by-side on each. Compare diagnosis quality, patch validity, cost per fix.
- Land as `ledoent/seer` PR alongside this doc.

**Exit criteria:** aider produces at least equivalent diagnoses on the test set, costs ≤ baseline, fits inside Gemini Flash quotas.

### Phase 2b — make aider default if 2a passes (1-2 weeks)

- Flip `AUTOFIX_HARNESS=aider` default in the compose override.
- Move the hand-rolled `AutofixAgent` into a `legacy/` package; keep it runnable behind the flag for one minor version.
- Update ROADMAP §5 (LLM client abstraction) to mark this complete.

### Phase 3 — Claude Code SDK as a second harness (when ANTHROPIC_API_KEY is available)

- Mint an Anthropic API key (separate from Vertex). Drop in `/opt/sentry-extra/secrets/seer.env`.
- Add `ClaudeCodeHarness` wrapper. `AUTOFIX_HARNESS=claude-code`.
- Use for complex multi-file refactors where aider's quality isn't enough.

### Phase 4 — OpenHands for "implement this feature" (deferred)

- Different goal: building features, not fixing bugs. Heavier integration. Revisit when there's actual demand.

## Open questions / risks

| Risk | Mitigation |
|---|---|
| aider's diff format won't round-trip through seer's existing Sentry-side UI (which expects a specific `changes` schema) | Phase 2a benchmark explicitly tests this — output adapter is part of the wrapper. If the impedance is too big, fall back to seer extracting diff hunks manually. |
| Sandboxing — aider runs in the seer container; if a malicious autofix instruction causes it to `rm -rf`, we have no isolation | Run aider in a `subprocess` with a working dir that's a fresh clone of the target repo inside `/tmp/aider-<run_id>/`. Drop privileges to a non-root uid. Limit walltime via `subprocess.TimeoutExpired`. |
| Gemini Flash quality ceiling on coding-heavy autofixes | Configurable — `AUTOFIX_HARNESS=aider` + `AUTOFIX_MODEL=gemini-2.5-pro` for harder issues. Or fall back to Claude Code in Phase 3. |
| Repo-access pattern — aider needs a checked-out repo; seer's existing `CODEBASE_GCS_STORAGE_BUCKET` cache wasn't designed for this | Reuse the `seer_repo_archive` infrastructure (which already exists for cloning) but mount it read-write for aider. Verify `git_ignore` rules in autofix_agent.py:288-310 still apply. |
| Loss of seer's existing `semantic_search` / `confidence` / `root_cause` tool outputs | Phase 2a runs them in PARALLEL with aider for the diagnosis step. Only the patch-generation step switches to aider. |

## Out of scope

- IDE-side tools (Cursor, Windsurf, Continue, Cline) — different consumer surface; not relevant for server-side autofix.
- Code-review tools (Coderabbit, Greptile, Aviator, Gemini Code Assist, Claude Code GHA bot) — sibling research, written separately in `docs/code-review-tools.md` (TODO).
- Model fine-tuning / training our own coding model — out of scope for seer; that's an open-source ML project.
- Replacing seer's other LLM call sites (severity scoring, issue grouping, anomaly detection). These use embeddings + small generations, not agentic loops. The harness swap is autofix-only.

## What ships when this doc is approved

1. **PR #6 (this PR)** — adds `docs/coding-harnesses.md` only. No code changes.
2. **PR #7 (Phase 2a)** — adds `HarnessProvider` + `AiderHarness` + the feature flag. Behind `AUTOFIX_HARNESS=aider` for opt-in. Includes 5-10-issue benchmark results in the PR body.
3. **PR #8 (Phase 2b)** — flips default after benchmark proves aider equivalent-or-better.
4. **ROADMAP.md** — update §5 to mark "LLM client abstraction" partially complete with a link back to this doc and the harness PRs.

## References

- aider docs — https://aider.chat/docs/install.html ; LLM model list — https://aider.chat/docs/llms.html
- OpenHands — https://github.com/All-Hands-AI/OpenHands
- Plandex — https://github.com/plandex-ai/plandex
- Gemini CLI — https://ai.google.dev/gemini-api/docs/cli (CLI mode)
- Anthropic Claude Code SDK — https://docs.anthropic.com/en/docs/build-with-claude/agent-sdk (headless mode)
- Seer's current agent loop — `src/seer/automation/autofix/autofix_agent.py`
- Seer's provider abstractions — `src/seer/automation/agent/client.py` lines 1140-1180 (GeminiProvider), 232-270 (OpenAiProvider)
- ROADMAP §5 "LLM client abstraction" — `ROADMAP.md`
