# AGENTS.md — operating instructions for STRATUM

You are building STRATUM: a local CLI that turns video into structured, provenance-tagged, agent-ready context.

**Read in this order before writing code:** `README.md` → `00_PRODUCT_BRIEF.md` → `01_ARCHITECTURE.md` → `02_BACKEND_ABSTRACTION.md` → `03_OUTPUT_SCHEMA.md` → `05_BUILD_PLAN.md` → `07_OPEN_QUESTIONS.md`.

---

## Who you are working for

Mehdi. Software is his own field, so you can talk to him as a peer about architecture and trade-offs. He is not a specialist in video processing or ML infrastructure, so **explain domain-specific choices** — why `AdaptiveDetector` over `ContentDetector`, why perceptual hashing before embedding — rather than assuming familiarity.

He values directness. If a specification decision looks wrong to you once you are in the code, say so and argue it. These documents were written before a line existed; contact with reality beats them.

---

## Non-negotiables

These are load-bearing. Changing one is a conversation, not a commit.

1. **Provenance on every claim.** `heard` · `seen` · `inferred`. If a code path can emit a claim without a modality tag, that path is a bug. This is the product.
2. **Sequential state-carrying analysis.** Each keyframe is analysed knowing the previous state. Do not parallelise S6 by chunking the video — the seams create exactly the discontinuities the design exists to remove.
3. **Never decode at source frame rate.** Sample coarsely and deduplicate hard.
4. **Three backend protocols, no leakage.** No pipeline stage imports a provider SDK or branches on which backend is active.
5. **Model names and prices live in config, never in code and never in these specs.** Every entry carries a `verified_on` date.
6. **Resumable and idempotent.** Killing a run mid-way and re-running it resumes. Transcription in particular is protected — it is expensive and almost never needs redoing.
7. **Never truncate to fit a budget.** Adjust thresholds and re-select. Truncation deletes the end of the video, which is where the finished thing is.
8. **Cost is always visible.** Every model call is metered; every run prints a per-stage breakdown; the per-video cap is a hard stop.

---

## Build order

**Schema first, then mocks, then one real backend at a time.** Specifically:

1. Pydantic models for the whole output schema — before any stage exists
2. Run directory, manifest, config loading, cost ledger
3. All three protocols with mock implementations only
4. End-to-end run on mocks producing a valid artifact full of fake content ← **this is the milestone**
5. Then real backends, one at a time, in the order in `05_BUILD_PLAN.md`

Building stages before the schema means fitting them to it afterwards, and provenance fields are exactly what gets quietly dropped when that happens.

---

## Testing

- **`MockReasoner` is mandatory, not a convenience.** Without it every test costs money and needs a network, and the pipeline logic never gets tested properly.
- Integration tests run against the committed reference video (see `07_OPEN_QUESTIONS.md` Q2) with mock backends.
- S0–S5 are deterministic. Assert byte-identical output given the same input and config. That determinism is what makes the model stages debuggable.
- Test the awkward inputs explicitly: **silent video, rotation metadata, variable frame rate, a video shorter than one sampling interval, a video with no shot boundaries at all.** Each of these is real and each will otherwise be discovered in production.

---

## Prompts

- Prompts live in versioned files under `prompts/`, never as string literals. They change more than the code and the changes must be diffable.
- Each prompt file carries a header comment: what it is for, what it must return, and what its known failure mode is.
- **The known failure mode of the S6 prompt is a model that describes the image instead of the change.** Design the prompt against that specifically and test for it.
- Model output is schema-enforced. Parse failures retry with a repair instruction; persistent failures write `status: failed` and the run continues.

---

## Things not to do

- Do not add a UI, a web server, or a hosted anything. Phase 1–3 is a local CLI.
- Do not implement URL or social-media downloading. The interface exists; nothing satisfies it. See `06_INGESTION_ROADMAP.md`.
- Do not add a vector store or retrieval layer. `chunks.jsonl` is the handoff point.
- Do not hand-write `artifact.md`. It is a template rendering of the canonical model.
- Do not let video files, API keys, or run directories enter git. `.gitignore` them in the first commit.
- Do not silently resolve an open question from `07_OPEN_QUESTIONS.md`. Surface it.
- Do not make the base install pull in PyTorch. Local backends are optional extras.

---

## When you hit a decision the specs do not cover

Ask, unless the answer is obvious and reversible. If it is obvious and reversible, decide, do it, and note it in a `DECISIONS.md` in the repo with one line of reasoning. That file is how the specs stay true as the code diverges from them.

---

## Repo conventions

- Python 3.11+, `uv`, `pyproject.toml` with `local` / `cloud` / `dev` extras
- `ruff` for lint and format, `mypy` in strict mode on `src/`
- Type hints everywhere. The schema is the contract and it should be enforced statically as well as at runtime.
- Conventional commits
- `README.md` in the repo covers install and usage. The specification documents stay here in the Ventures folder and are referenced, not duplicated.

---

## The acceptance test

Not test coverage. Not benchmark scores. **Mehdi processes three real hydroponics videos — one narrated walkthrough, one silent build, one mixed — reads the Markdown, and finds it a genuinely faster way to understand those builds than watching them again.**

Everything else is a proxy for that.
