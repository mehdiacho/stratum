# 01 — Architecture

---

## The shape of it

Nine stages. Each reads from the run directory and writes back to it. Each is independently resumable and independently testable. No stage calls the next one; an orchestrator runs them in order and can be told to start from any stage.

```
  local video file
        │
   S0   ├─ INGEST ─────────── probe metadata, content hash, create run dir
        │
   S1   ├─ AUDIO ──────────── extract 16 kHz mono WAV
        │
   S2   ├─ TRANSCRIBE ─────── timestamped segments  ◄── Transcriber backend
        │
   S3   ├─ SAMPLE ─────────── coarse decode + shot-boundary detection
        │                     (18,000 frames → ~1,200 candidates)
   S4   ├─ SELECT ─────────── 3-tier dedup + sharpness pick  ◄── Embedder backend
        │                     (~1,200 candidates → ~80 keyframes)
   S5   ├─ WEIGH ──────────── modality profile: audio-led / visual-led / balanced
        │
   S6   ├─ TRAVERSE ───────── sequential state-delta analysis  ◄── Reasoner backend
        │                     one call per keyframe, carrying state
   S7   ├─ SYNTHESISE ─────── transitions → procedure, entities, materials
        │
   S8   └─ EMIT ──────────── canonical JSON + Markdown + chunk manifest
```

The three `◄──` points are the only places a model is invoked. Everything else is deterministic. That is deliberate: it keeps cost proportional to three well-understood knobs and makes the expensive parts swappable (`02_BACKEND_ABSTRACTION.md`).

---

## S0 — Ingest

Accept a local file path or a directory. Probe with `ffprobe` for: duration, container fps *and* average real fps (they differ on variable-frame-rate phone footage — trust the average), resolution, rotation metadata, audio stream presence, codec.

Compute a **content hash** (SHA-256 of the file, or of the first and last 8 MB plus size for large files) and use it as the run ID. Same file processed twice resolves to the same run directory and short-circuits.

Create the run directory:

```
runs/<content_hash>/
  manifest.json          run state, stage completion, config snapshot, costs
  audio.wav
  transcript.json
  candidates/            sampled frames, disposable after S4
  keyframes/             retained frames — these are output, not scratch
  transitions.jsonl      one record per keyframe, appended as S6 progresses
  artifact.json          canonical output
  artifact.md            human rendering
  chunks.jsonl           agent-ready chunks
```

**Rotation is a real trap.** Phone video often carries a rotation flag rather than rotated pixels. Frames extracted without honouring it come out sideways, and a vision model given a sideways image will describe a sideways world with complete confidence. Normalise orientation at extraction and assert it in a test.

---

## S1 — Audio extraction

`ffmpeg` to 16 kHz mono 16-bit WAV. Every ASR backend wants this and resampling once up front avoids each backend doing its own.

If there is no audio stream, or the audio is below a loudness threshold with no detectable speech, record `has_speech: false` and skip S2. Silent videos are a real and important case in this corpus — a lot of build footage is hands plus music — and the pipeline must handle them as a normal path, not an error.

---

## S2 — Transcribe

Backend-selected (`Transcriber` protocol). Output is a normalised structure regardless of backend:

```json
{
  "language": "en",
  "duration_s": 612.4,
  "segments": [
    {"id": 0, "start": 0.0, "end": 3.2, "text": "...", "confidence": 0.94}
  ],
  "words": [
    {"start": 0.0, "end": 0.31, "text": "so", "confidence": 0.98}
  ]
}
```

Word-level timestamps are required, not optional — S6 aligns a transcript window to each keyframe, and segment-level granularity is too coarse when keyframes land two seconds apart.

**Music-only detection.** If the transcript comes back as lyrics or near-empty with low confidence across the board, mark `speech_quality: low`. S5 uses this. Do not throw the transcript away — song lyrics over a build video are noise, and knowing they are noise is useful.

---

## S3 — Sample

**Never decode at source frame rate.** This is the single most important efficiency decision in the pipeline and the correction to the obvious approach. A 10-minute 120 fps video is 72,000 frames; the information content is closer to 80.

Two passes:

**a. Uniform coarse decode.** `ffmpeg` at a fixed sampling rate, default **2 fps**, scaled so the long edge is 1024 px, written as JPEG q≈85 to `candidates/`. A 10-minute video yields ~1,200 candidates. Sampling rate is config, not hard-coded — fast-cut content wants 4 fps, a static tripod shot wants 0.5.

**b. Shot-boundary detection.** Run **PySceneDetect** over the video (`AdaptiveDetector`, which handles gradual transitions that the plain content detector misses on fades and slow pans). Record boundary timestamps and force-retain a frame just after each boundary, so a cut never gets deduplicated away.

Output: an ordered candidate list, each with timestamp, path, and `is_shot_start`.

---

## S4 — Select

The keyframe reduction. Three tiers, cheapest first — this ordering is the standard practice and it matters, because tier 2 costs real compute and tier 1 removes most of the work before it runs.

**Tier 1 — perceptual hash.** Compute a pHash or dHash per candidate. Walk the sequence; drop any frame whose Hamming distance from the last *retained* frame is below a threshold. Removes the long static runs — the tripod shot where nothing moves for forty seconds — at near-zero cost.

Compare against the last retained frame, not the immediately previous candidate. Comparing to the previous candidate lets slow drift accumulate silently until the scene has completely changed without any single step crossing the threshold.

**Tier 2 — embedding distance.** Embed survivors with a small vision encoder (`Embedder` protocol — SigLIP, CLIP, or Nomic Embed Vision locally). Drop frames whose cosine similarity to the last retained embedding exceeds a threshold. This catches *semantic* duplicates that pHash keeps: same scene from a slightly different angle, camera shake, a hand passing through frame.

**Tier 3 — sharpness selection.** Within each cluster of frames collapsed by tiers 1–2, retain the sharpest rather than the first. Variance of the Laplacian is the standard cheap measure. This matters more than it sounds: a keyframe grabbed mid-pan is motion-blurred and unreadable, and the vision model will hallucinate into the blur.

**Budget control.** Two caps, both config:

- `max_keyframes_per_minute` (default 12)
- `max_keyframes_total` (default 200)

If the retained set exceeds a cap, do not truncate — truncation drops the end of the video, which is usually where the finished result is. Instead raise the tier-2 threshold and re-run selection, binary-searching the threshold until the set fits. Record the final threshold in the manifest so the run is reproducible.

**Force-retain rules, applied after budgeting:** first frame, last frame, every shot boundary. These are never dropped.

---

## S5 — Weigh

Mehdi's "priority context" requirement, made measurable. The pipeline classifies where the information lives and passes that classification into S6's prompting.

Three signals:

| Signal | How | What it indicates |
|---|---|---|
| **Speech density** | Words per minute from the transcript, plus mean ASR confidence | High and confident → probably audio-led |
| **Visual change rate** | Keyframes retained per minute after S4, and the mean embedding distance between retained frames | High → picture is doing work |
| **Grounding** | Sample ~10 keyframes, ask the reasoner to judge whether the aligned transcript window describes what is visible | Low agreement → the audio is decoration, or the visuals are |

Combine into a profile: `audio_led` · `visual_led` · `balanced` · `visual_only` (no usable speech).

**What it changes downstream, concretely:**

| Profile | S6 behaviour |
|---|---|
| `audio_led` | Transcript window widened; frames used to *confirm and locate* what is said; fewer keyframes needed and the S4 budget can be lowered on a re-run |
| `visual_led` | Transcript window narrowed and demoted to a hint; the frame is the primary evidence; keyframe budget raised |
| `balanced` | Both weighted equally; conflicts between them recorded as conflicts rather than silently resolved |
| `visual_only` | Transcript omitted entirely; every claim tagged `seen` or `inferred`, never `heard` |

**The profile can be per-segment, not just per-video.** A single video often opens with two minutes of talking head and then goes silent for the build. Phase 2 computes the profile over a sliding window. Phase 1 computes one profile for the whole video and records the simplification in the manifest.

---

## S6 — Traverse

The core. One reasoner call per keyframe, in order, carrying state.

**What each call receives:**

1. **The current keyframe image**
2. **The running state** — a compact structured summary of the world as of the previous keyframe: entities present, their properties, the last action, open uncertainties. Text, not images.
3. **The previous keyframe image** — configurable, default 1, can be 0 to halve image cost or 2 for difficult content
4. **The aligned transcript window** — words spoken between the previous keyframe's timestamp and this one, plus a configurable lead/lag margin (default ±3 s), weighted per the S5 profile
5. **The video-level context** — title, any user-supplied description, the modality profile

**What it returns** (structured, schema-enforced):

```json
{
  "keyframe_id": "kf_0031",
  "timestamp_s": 184.2,
  "delta": "A second length of PVC channel has been placed parallel to the first and joined at the near end with a 90° elbow.",
  "action_inferred": "joining channels",
  "entities_added":   [{"id": "ent_channel_2", "label": "PVC grow channel", "attrs": {"approx_length": "1.5 m"}, "modality": "seen", "confidence": 0.8}],
  "entities_changed": [],
  "entities_removed": [],
  "claims": [
    {"text": "Channels are spaced roughly one hand-width apart", "modality": "inferred", "confidence": 0.5},
    {"text": "He says the spacing is 15 cm",                     "modality": "heard",    "confidence": 0.9}
  ],
  "uncertainty": "The elbow fitting size is not visible and is not stated.",
  "state_after": "<updated running state>"
}
```

Appended to `transitions.jsonl` as it goes, so a crash at keyframe 60 of 80 loses nothing.

**Context growth is the cost risk.** The running state must be *replaced* each turn, not accumulated — the model returns the new state, and the new state is what the next call receives. If `state_after` exceeds a token budget, compact it: collapse stable entities into a summary line and keep detail only on what is actively changing. Log every compaction; if compaction is firing constantly, the budget is wrong.

**Why not just send the whole video to a long-context multimodal model.** It is a legitimate alternative and it should exist as a comparison mode in Phase 4. It is not the default because it gives up the four things this design is for: control over which frames get looked at, an inspectable intermediate artifact, per-claim provenance back to a specific timestamp, and cost that scales with information content rather than duration.

---

## S7 — Synthesise

One or two reasoner calls over the full transition record. Produces:

- **Procedure** — ordered steps with the timestamp range each was derived from
- **Entities** — deduplicated across the video, with every mention linked back
- **Materials and tools** — what appears, quantities and dimensions where stated or confidently inferred, each tagged by modality
- **Open questions** — things the video does not answer. This is a first-class output. A build video that never shows the pump specification should produce *"pump specification never shown or stated"*, because that is exactly the thread that gets chased into text sources afterwards.
- **Conflicts** — where the audio and the picture disagreed

---

## S8 — Emit

Three artifacts. Full specification in `03_OUTPUT_SCHEMA.md`.

- `artifact.json` — canonical, everything, stable IDs
- `artifact.md` — human rendering with inline keyframe references
- `chunks.jsonl` — semantic chunks for embedding, chunked on transition and step boundaries rather than fixed token windows

---

## Cross-cutting

**Resumability.** `manifest.json` records per-stage completion with a hash of that stage's config. Re-running skips completed stages whose config is unchanged; changing the keyframe threshold invalidates S4 onward but not S0–S2. Transcription is expensive to redo and almost never needs redoing — protect it.

**Cost accounting.** Every backend call records tokens in, tokens out, images, wall time, and computed cost against a pricing table in config. The manifest carries a running total and every run prints a per-stage cost breakdown. Cost that is not visible is cost that is not controlled.

**Determinism.** Everything outside the three model calls must be deterministic and seeded. Given the same video and config, S0–S5 produce byte-identical output. This is what makes the model stages debuggable.

**Failure policy.** A model call that fails after retries writes a transition record with `status: failed` and the pipeline continues. One bad keyframe must not destroy a run. Failed transitions are listed in the artifact and can be retried selectively.

**Concurrency.** S6 is sequential by nature — that is the point of it. Parallelism belongs in S3 (frame extraction) and S4 (embedding), which are embarrassingly parallel. Do not be tempted to parallelise S6 by chunking the video; the seams produce exactly the discontinuities the design exists to avoid.

---

## Stack

| Concern | Choice | Note |
|---|---|---|
| Language | Python 3.11+ | Every relevant library is here |
| Deps / env | `uv` | Fast, lockfile, no ceremony |
| CLI | `typer` | |
| Video / audio | `ffmpeg` + `ffprobe` via `subprocess` | Direct calls, not a wrapper library — wrappers lag ffmpeg and obscure errors |
| Shot detection | `PySceneDetect` (`AdaptiveDetector`) | |
| Image ops | `opencv-python-headless`, `imagehash`, `Pillow` | pHash and Laplacian variance |
| Schemas | `pydantic` v2 | Schema-enforced model output, and the JSON artifact is generated from the models — never hand-written |
| Run state | SQLite via stdlib `sqlite3` | Manifest is JSON for readability; the cache and cost ledger are SQLite |
| Rendering | `jinja2` | Markdown from the canonical model |
| Testing | `pytest` | Plus a short reference video committed to the repo for integration tests |

Backend-specific dependencies (`faster-whisper`, provider SDKs, `torch`) are **optional extras**, not base requirements. Installing STRATUM to run in cloud posture must not drag in PyTorch.
