# DECISIONS

How the code diverges from the specification documents, and why.

Per `CLAUDE.md`: decisions that were obvious and reversible were taken and recorded
here with one line of reasoning. Anything that was neither is in
`07_OPEN_QUESTIONS.md` and remains unanswered.

Newest last.

---

## 2026-08-05 — `stated` is a fourth modality (resolves D3)

**Decision:** `Modality` ships v1.0 with four values: `heard` · `seen` · `inferred` · `stated`.

**Why:** Asked and answered by Mehdi before the schema was frozen. `06_INGESTION_ROADMAP.md`
puts `SidecarMetadataSource` in Phase 1, and it is built. The moment a caption reaches
S6, a claim drawn from author-written post text has no honest tag among the three:
`heard` is a falsehood in the one field the whole design rests on, and `inferred` is
worse because it understates a stated fact. `03_OUTPUT_SCHEMA.md` says three; that
document was written before the sidecar existed.

**Enforced, not merely documented.** S6 drops any `stated` claim when no description was
supplied, and any `heard` claim when no transcript window was sent — see the next entry.

---

## 2026-08-05 — Impossible provenance is dropped, not retagged

**Decision:** In S6, a `heard` claim on a keyframe where no transcript was supplied is
discarded, as is a `stated` claim when the video has no description. Counted, warned
about, and reported in the stage stats.

**Why:** Found by a test, not by reasoning. The prompt rendered an empty `<transcript>`
block on a silent video and the model produced a `heard` claim about it. That is not a
mis-tag — there was nothing to hear, so the content is fabricated. Retagging it
`inferred` would keep the fabrication and relabel it. The prompt already forbids this;
a prompt is not a guarantee, so the pipeline enforces it.

---

## 2026-08-05 — The tier-2 budget search LOWERS the threshold

**Decision:** When the retained keyframe set exceeds the budget, S4 binary-searches the
embedding similarity threshold *downward* from the configured value toward
`select.threshold_search_floor`.

**Why:** `01_ARCHITECTURE.md` S4 says "raise the tier-2 threshold and re-run selection".
Read against the comparison it specifies in the same section — *drop a frame whose
similarity to the last retained frame exceeds the threshold* — raising the threshold
drops fewer frames and retains more, which is backwards. The surrounding paragraph
plainly intends "make dedup more aggressive". Implemented as lowering.

**Still true:** nothing is ever truncated. The search adjusts thresholds and re-selects,
and the final threshold is written into `selection.json` so the run is reproducible.

---

## 2026-08-05 — `mock` is a fourth posture

**Decision:** `Posture` has `mock` alongside `hybrid`, `cloud`, `local`, and it is the
default in the shipped config.

**Why:** The Phase 0 milestone in `05_BUILD_PLAN.md` is "runs end to end with all-mock
backends". That needs to be a selectable posture rather than a test-only fixture, or the
milestone is not reachable from the command line. `hybrid` becomes the default the day
Q1 is answered — the preset already exists with its reasoner slot deliberately unpinned.

---

## 2026-08-05 — Backend calls return a result object carrying `Usage`

**Decision:** `Transcriber.transcribe` returns `TranscribeResult`, `Embedder.embed_images`
returns `EmbedResult`, not the bare `Transcript` / `ndarray` in
`02_BACKEND_ABSTRACTION.md`.

**Why:** Cost visibility is a non-negotiable. Threading usage back through a side channel
— a `last_usage` property, a mutable ledger handle passed into the backend — is the thing
that quietly stops working when someone writes the fourth adapter. Making it part of the
return type means an adapter cannot forget.

---

## 2026-08-05 — `Reasoner` has a second method, `synthesise`

**Decision:** The protocol is `analyse(ReasonRequest)` plus `synthesise(SynthesiseRequest)`.

**Why:** S7 is a model call too. The protocol sketch in `02_BACKEND_ABSTRACTION.md` only
described S6. Two explicitly typed methods beat one generic method with a task
discriminator, because each keeps its own schema-enforced response type.

---

## 2026-08-05 — Prompts are rendered by the stage, not by the adapter

**Decision:** `ReasonRequest` carries `system_prompt` and `user_prompt` as text. Adapters
do model plumbing and nothing else.

**Why:** Otherwise every new adapter reimplements prompt assembly and they drift, which
makes the "same video under all three postures" comparison in Phase 2 measure the wrong
thing. Schema repair logic is shared in `backends/structured.py` for the same reason.

---

## 2026-08-05 — Draft models are separate from canonical models

**Decision:** The model returns `TransitionDraft` / `SynthesisDraft`; the pipeline converts
to `Transition` / canonical `Step`, `Material`, `Conflict`, assigning all IDs itself.

**Why:** The model never invents an ID, and the canonical record never depends on the
model getting its bookkeeping right. S7 additionally checks every reference — a step
citing a transition that was never recorded is dropped and listed in
`dropped_references`, because a model inventing transition ids is a signal worth seeing.

---

## 2026-08-05 — Entities named only in synthesis are registered, not left dangling

**Decision:** When S7 produces a material whose entity S6 never observed, the entity is
registered with no mentions rather than the material pointing at a non-existent id.

**Why:** A dangling reference in the canonical artifact is worse than an entity that
honestly reports zero keyframes. It is still recorded in `dropped_references`, and
`artifact.md` renders it as *named in synthesis only*.

---

## 2026-08-05 — S6 running state lives in its own file

**Decision:** `transitions.jsonl` holds canonical `Transition` records only. The carried
state is in `traverse_state.json` next to it.

**Why:** `state_after` is not part of the output schema in `03_OUTPUT_SCHEMA.md`, and
`transitions.jsonl` is meant to be inspectable. On resume, if the two disagree — a kill
between the two writes — S6 restarts rather than continuing from a state that does not
match the record.

---

## 2026-08-05 — State compaction is deterministic and model-free

**Decision:** When `state_after` exceeds `traverse.state_token_budget`, it is compacted by
prepending a roll-call of known entity labels and keeping the most recent detail. No model
call.

**Why:** A compaction that costs a model call makes the expensive stage more expensive
exactly when it is already struggling. The entity roll-call is the mechanism that answers
R3 — a part set down at 2:00 and picked up at 8:00 survives compaction because its label
stays in the roll-call. Every compaction is logged, and compacting on more than a third
of keyframes raises a warning that the budget is wrong.

---

## 2026-08-05 — Frame extraction disables ffmpeg's autorotate

**Decision:** `extract_frames` passes `-noautorotate` and applies its own `transpose`
filter from the probed rotation.

**Why:** Found by the rotation test, which is exactly why that test exists. Modern ffmpeg
auto-applies the display matrix on decode; applying a transpose on top of that rotates
twice and hands the vision model a sideways image — the precise failure `01_ARCHITECTURE.md`
warns about. Doing it explicitly also means the behaviour does not change under us when
ffmpeg's default does.

---

## 2026-08-05 — Optional tails belong in Python, not in the Markdown template

**Decision:** Conflict lines and the author byline are assembled in `render.py`.

**Why:** Jinja's `trim_blocks` eats the newline after a block tag, so a template line
ending in `{% endif %}` silently glues the next section onto the end of a list. It
happened twice. `test_markdown_sections_are_not_run_together` is the guard.

---

## 2026-08-05 — Unknown pricing raises rather than costing zero

**Decision:** `PricingTable.cost_model` raises `ConfigError` for a provider/model with no
entry. There is no zero-rate fallback.

**Why:** A silent zero makes an expensive run look free, which is the failure the cost cap
exists to prevent, arriving by a different route. Add the pricing entry in the same commit
as the adapter, with a `verified_on` date.

---

## 2026-08-05 — Code and specifications share one directory

**Decision:** The repo root is `software/STRATUM/`, the specification documents stay at
that root untouched, and the existing `README.md` gained Install and Usage sections rather
than being replaced.

**Why:** `CLAUDE.md` says the specs stay in the Ventures folder and are referenced, not
duplicated — and this *is* that folder. Moving Mehdi's documents to make room for a code
README would have been a worse trade than appending to the README that already indexes
them. Reversible: the code is confined to `src/`, `tests/`, `prompts/` and `pyproject.toml`.
