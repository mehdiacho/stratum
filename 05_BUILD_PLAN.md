# 05 — Build plan

Four phases. Each ends with something usable. **Do not start a phase before the previous one has been used on real video.**

---

## Phase 0 — Foundations

**Goal:** the skeleton exists and the boring decisions are made once.

- Repo scaffolded with `uv`. `pyproject.toml` with optional extras (`local`, `cloud`, `dev`) so the base install is light.
- `typer` CLI: `stratum process` · `stratum inspect` · `stratum cost` · `stratum config`.
- Config loading — YAML, env-var overrides for keys, pydantic-validated, snapshot written into each run.
- Run directory layout and `manifest.json` with per-stage completion and config hashing.
- The three protocols defined (`Transcriber`, `Embedder`, `Reasoner`) with **only the mocks implemented**.
- Pydantic models for the entire output schema in `03_OUTPUT_SCHEMA.md`. **Write the schema before any stage.** Building the stages first means fitting them to a schema afterwards, which is how provenance fields quietly get dropped.
- Cost ledger: SQLite table, pricing table as a dated data file, `stratum cost` prints a breakdown.
- **A short reference video committed to the repo** — 60–90 seconds, visually-led, self-recorded so there is no licensing question. Every integration test runs against it.

**Done when:** `stratum process reference.mp4` runs end to end with all-mock backends and emits a valid, schema-conformant artifact full of fake content.

That is the important milestone. Everything after it is filling in real implementations behind interfaces that already work.

---

## Phase 1 — The walking skeleton

**Goal:** one real video, one real artifact, hybrid posture.

| Stage | Build |
|---|---|
| S0 Ingest | `ffprobe` metadata, content hash, run dir. **Handle rotation metadata and variable frame rate.** |
| S1 Audio | `ffmpeg` → 16 kHz mono WAV. Silent-video path is a normal path. |
| S2 Transcribe | `faster-whisper` with word timestamps |
| S3 Sample | Uniform 2 fps decode + PySceneDetect `AdaptiveDetector` boundaries |
| S4 Select | pHash → embedding → sharpness. Budget capping by threshold search, not truncation. |
| S5 Weigh | Speech density + change rate. **Skip the grounding check in Phase 1** — it costs model calls; use the two cheap signals. |
| S6 Traverse | Sequential state-delta, one cloud VLM adapter, schema-enforced output, `transitions.jsonl` |
| S7 Synthesise | Procedure, entities, materials, open questions, conflicts |
| S8 Emit | JSON + Markdown + chunks |

**Prompt engineering is the real work in this phase, not the plumbing.** Budget most of the time for S6. The characteristic failure is a model that describes the image instead of the change; expect several rounds of prompt revision, and keep the prompts in versioned files rather than string literals so revisions are diffable.

**Done when:** Mehdi processes three real hydroponics videos of different character — one narrated walkthrough, one silent build, one mixed — reads the Markdown, and says it is faster than rewatching.

**Do not proceed until that is true.** If the output is not useful at three videos, more features will not make it useful at thirty.

---

## Phase 2 — Fill the matrix and tune

**Goal:** all three postures work; quality is measured rather than assumed.

- Cloud ASR adapter (cheapest per minute)
- Cloud embedding adapter
- Second cloud reasoner adapter — provider independence, and side-by-side comparison
- **Local VLM reasoner** via Ollama's OpenAI-compatible endpoint. Measure honestly on the reference video against the cloud reasoner: schema conformance rate, state-drift, delta-vs-description failures. **Publish the comparison in the repo.**
- Per-segment modality profiling (sliding window rather than one profile per video)
- The S5 grounding check, now that a reasoner is cheap to call
- `stratum tune` — sweep keyframe thresholds on one video and report the retained count and cost at each setting, so tuning is empirical
- Retry a single failed transition without re-running the pipeline

**Done when:** the same video runs under all three postures via one config change, and there is a written, evidenced answer to *"is the local posture good enough?"*

---

## Phase 3 — Series and corpus

**Goal:** the unit of understanding becomes a corpus, not a video.

- `stratum merge <run_ids...>` producing a corpus artifact
- Entity reconciliation across videos, non-destructive, recomputable
- Contradiction detection between videos in a series
- Explicit ordering (user-supplied) and inferred ordering (from filenames, timestamps, content)
- Corpus-level open questions — de-duplicated across members, which is where the real research agenda comes out
- `stratum ask` — a question answered against the local artifact set, no re-ingestion

**Done when:** a five-part series produces one document more useful than its five parts.

---

## Phase 4 — Comparison and honesty

**Goal:** find out whether the whole design was necessary.

- **Native-video comparison mode:** send the whole file to a long-context multimodal model, ask for the same artifact shape, and compare against the pipeline output on cost, completeness and provenance quality. Record the result in the repo whichever way it goes.
- Quality harness: a small set of videos with hand-written ground truth for materials and steps, so prompt and threshold changes can be measured rather than eyeballed.
- Cost regression check — flag when a change makes a run materially more expensive.

**This phase exists because the specification could be wrong,** and finding that out at phase 4 for the price of a few API calls is much better than never asking.

---

## Deliberately not planned

Not in any phase. If they become wanted, they are new work with new decisions:

- Web UI, TUI, or hosted service
- URL / social download implementation (see `06_INGESTION_ROADMAP.md`)
- Accounts, billing, multi-tenancy
- A vector store or retrieval layer — `chunks.jsonl` is the handoff point, and whatever consumes it owns the index
- Real-time processing
- Video output of any kind

---

## Ordering rules

1. **Schema before stages.** Always.
2. **Mocks before real backends.** A pipeline that can only be tested by spending money will not be tested.
3. **Real video before more features.** Every phase gate is "did it help", never "did it build".
4. **Prompts are versioned files, not literals.** They change more than the code does.
5. **Never truncate to fit a budget.** Adjust thresholds and re-select. Truncation silently deletes the end of the video, which is where the finished thing is.
