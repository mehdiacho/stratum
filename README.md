# STRATUM

**Video → structured, agent-ready context.**

A pipeline that takes instructional video — the kind where somebody builds a thing and the camera, not the narration, carries the information — and produces a layered, provenance-tagged record of what actually happened, in a form an AI agent can act on.

**Track:** BLACKWALL / B-SOFT · **Created:** 4 August 2026 · **Status:** Phase 0 complete — runs end to end on mock backends
**Code name is provisional.** Trademark not checked — see `07_OPEN_QUESTIONS.md`.

---

## The one-paragraph version

Genuinely useful starter information increasingly lives in video, and video is the worst possible format to think with. Existing tools read the subtitles and summarise the talking; they are blind to the screen, which is where the information is when somebody is showing you how a thing is built. STRATUM slices the video into a small set of *meaningfully different* stills, transcribes the audio with timestamps, works out whether this video is carried by its audio or its picture, and then walks the stills in order — each one analysed *in the context of the one before it*, so the output is a record of **what changed**, not a pile of independent image descriptions. The result is a document of state transitions with every claim tagged by where it came from: heard, seen, or inferred.

---

## Read in this order

| # | File | What it settles |
|---|---|---|
| — | **`CLAUDE.md`** | **Start here if you are Claude Code.** Operating instructions, conventions, what not to do. |
| 00 | `00_PRODUCT_BRIEF.md` | The problem, the idea, what "done" means, what is explicitly out of scope |
| 01 | `01_ARCHITECTURE.md` | The nine pipeline stages and how data moves between them |
| 02 | `02_BACKEND_ABSTRACTION.md` | The three model postures (hybrid / cloud / local) and the provider interfaces that make them swappable |
| 03 | `03_OUTPUT_SCHEMA.md` | The canonical artifact — what STRATUM actually emits, and why it is shaped for agents rather than readers |
| 04 | `04_COMPETITIVE_AND_COST.md` | Who else does this, what they charge, what a run costs us under each posture |
| 05 | `05_BUILD_PLAN.md` | Phased build. Phase 1 is the walking skeleton. |
| 06 | `06_INGESTION_ROADMAP.md` | Local files now. The URL-download module, deferred, with the research it needs first |
| 07 | `07_OPEN_QUESTIONS.md` | Everything unresolved, with who decides and what it blocks |
| — | `DECISIONS.md` | Where the code diverges from these documents, and why |

---

## Install

Needs Python 3.11+, [`uv`](https://docs.astral.sh/uv/), and **ffmpeg on PATH** — the
pipeline shells out to `ffmpeg` and `ffprobe` directly.

```bash
uv venv && uv pip install -e ".[dev]"
```

The base install is deliberately light. Local model backends are an optional extra
and are the thing that costs you gigabytes:

```bash
uv pip install -e ".[local]"
```

## Usage

```bash
stratum process ~/corpus/nft_build.mp4
```

A folder works too, and is how a saved series arrives:

```bash
stratum process ~/corpus/nft_series/ --order-by name
```

Output lands in `runs/<content-hash>/`: `artifact.json` (canonical), `artifact.md`
(readable), `chunks.jsonl` (agent-ready), and the retained keyframes.

**Write a sidecar.** Next to `nft_build.mp4`, put `nft_build.json`:

```json
{
  "url": "https://...",
  "title": "NFT channel build, part 2",
  "caption": "4x 1.5m PVC channels at 15cm spacing. 400 L/h pump.",
  "uploader": "@somegrower",
  "published_at": "2026-07-19T10:00:00Z"
}
```

Thirty seconds of work, and it is the highest-value input per second of effort in the
whole system — the caption is often the densest text in the finished artifact. Claims
drawn from it are tagged `stated`.

### The other three commands

```bash
stratum inspect ~/corpus/nft_build.mp4    # probe it: fps, rotation, audio, duration
stratum cost                              # what every run actually cost, per stage
stratum config --fingerprints             # effective config after posture + file + env
```

`stratum inspect <run-id>` summarises an existing run instead.

### Configuration

Copy `stratum.example.yaml` to `stratum.yaml` and edit. Posture is a preset over the
three backend slots; anything can be overridden individually, and mixing is expected.

```bash
stratum process video.mp4 --posture hybrid
```

Model identifiers and prices live in config and never in code. Every entry carries a
`verified_on` date, and the CLI warns when the pricing table is more than 90 days stale.
API keys are referenced by environment-variable *name* — the key itself never appears in
config, in a run directory, or in a log line.

### What runs today

Posture `mock` runs the whole pipeline end to end with no network and no money. That is
the Phase 0 milestone and it is met. The real backends —
`faster-whisper`, a local vision encoder, and a cloud VLM — are Phase 1, and the reasoner
is blocked on **Q1** in `07_OPEN_QUESTIONS.md`: the slot is deliberately left unpinned
rather than resolving that question silently.

### Tests

```bash
uv run pytest
```

Integration tests generate their video fixtures with ffmpeg — silent, rotated, variable
frame rate, shorter than one sampling interval, and no shot boundaries at all. The real
60–90 second reference video is **Q2** and still owed.

---

## Decisions already taken — do not relitigate

| Decision | Made | Rationale |
|---|---|---|
| **Local CLI, no UI** | 4 Aug 2026 | The pipeline is the hard part and the reusable part. A UI wraps it later without rework. |
| **Three model postures, all selectable** | 4 Aug 2026 | Hybrid is the default because it works on modest hardware. Cloud and local are first-class alternatives, not future work — the abstraction is built in Phase 1. |
| **Local files only for now** | 4 Aug 2026 | URL download is a separate, deferred module. See `06_INGESTION_ROADMAP.md`. |
| **Personal tool first, commercial question deferred** | 4 Aug 2026 | Auth, billing and multi-tenancy are wasted work if the core isn't good enough. Build the core, use it, then decide. |

---

## The immediate reason this exists

Mehdi's hydroponics research is currently trapped in TikTok and Instagram video — DIY builds, production-scale walkthroughs, systems assembled on camera with no narration worth transcribing. That research feeds Track V (`01_AGRICULTURE/`). STRATUM is the research method in doctrine D2 — *don't re-walk a path someone has already walked* — turned into software.

It is not built for hydroponics. Hydroponics is just the first corpus.

---

*Specification last updated 4 August 2026. Code scaffolded 5 August 2026.*
