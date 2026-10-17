# Stratum: the best way to turn a video into context for a model

Research for Mehdi, 2026-10-17. Read against Stratum's own spec pack in `X:\Ventures\02_AI_BLACKWALL\software\STRATUM` (README, 00 brief, 01 architecture, 02 backends, 04 competitive and cost, 07 open questions, DECISIONS). The `src/` code was not read; the PC dropped off before I got to it.

## Short answer

Your approach is the right one, and it is what independent builders have arrived at too: sample coarsely, find the cuts, throw away near-duplicates, keep a timestamped transcript, and hand the model a small set of meaningfully different stills. Stratum's spec already goes further than any open tool I found, because it asks the model *what changed* between stills rather than *what is in* each one, and it tags every claim as heard, seen, stated or inferred.

So keep building it, but change what you treat as the competition. Since the spec was written (4 August), native video input has become something to **use inside** Stratum rather than something to beat. The parts worth owning are the state-transition record, provenance and the artifact (S5 to S8). The parts not worth owning are vision models and anything beyond the frame selection you already have.

## What exists now

| Option | What it gives you | Fit for Stratum |
|---|---|---|
| **Gemini, native video** | The only major API that takes a video file. 1 frame/s by default (configurable), about 100 tokens/s at low resolution or 300 at high, plus 32 tokens/s audio. Up to 3 h in a 1M context at low res. Clips by start/end offset. | Strong as a backend. A 10-minute video is about 60k tokens at low res, i.e. cents. |
| **Gemini "agentic" video mode** (new) | The model navigates the timeline and loads only the frames, audio and transcript it needs. Google claims up to 88% fewer tokens and about 7% better quality on long videos; not independently checked. Only on recent Flash-class models. | Good for asking a question of a long video. Gives up the inspectable intermediate artifact, so not a replacement for S6. |
| **Claude, OpenAI APIs** | No video input. Images only, so the workaround is exactly your pipeline: frames plus transcript. | Fine as stills-only reasoners. |
| **Twelve Labs Pegasus / Marengo** | Hosted video understanding, about $0.029/min analysed plus indexing. Built for search across a library. | Still a different job, as 04 already says. |
| **Qwen3-VL (open weights, Apache 2.0)** | 2B to 235B. The 8B runs locally, with long video and timestamp grounding claimed. Text-reading scores are for images, not video. | The candidate for the local posture. R1 (holding state over 80 calls) is still unmeasured. |
| **peepshow** (MIT, Node) | ffmpeg scene detection, 8x8 perceptual-hash dedup, optional Tesseract OCR, Whisper transcript, emits JSON or Markdown. | Covers roughly your S0 to S4, less carefully than yours (no rotation handling, no sharpness pick, no embedding tier). |
| **claude-real-video** (MIT) | Scene-aware deduplicated frames plus transcript for Claude, with extra frames at subtitle times. | Same layer as peepshow. |
| **Scribe / Guidde / Trupeer** | Step-by-step docs, but only from recordings they capture with click events. | Unchanged from 04: right output shape, wrong input. |

Nothing I found produces a step-by-step record of how a physical build changes, with per-claim provenance, from someone else's video. The gap the brief describes is still open.

## What to change in your approach

**1. Small changes are the blind spot in S4.** Tier 1 (pHash) and tier 2 (whole-image embedding similarity) both measure the *whole* frame. In a build video the information is often a small change: a fitting added, a clip moved, a hand-sized part. Both tiers will call that a duplicate and drop it. People who built this at scale hit the same thing; their fix was a second, finer channel:
- a coarse grid (say 32x32) where a few cells changing hard keeps the frame regardless of the overall score, and
- a finer comparison for "settled" small-region changes, with a cooldown so one flickering element can't eat the budget.

This is cheap and deterministic, and it belongs in S4 as a tier between pHash and embeddings. Add a test case for it in the reference video next to the one for R3.

**2. Compare against the last few kept frames, not just the last one.** The spec already compares to the last *retained* frame rather than the previous candidate, which is right. Extending it to the last 3 or 4 retained frames stops A-B-A cutting (back and forth between two angles) from re-admitting shots the model has already seen.

**3. Use the clip between keyframes, not only the stills.** R4 asks whether 2 frames/s misses fast hand movements. With Gemini as the reasoner you don't have to choose: send each S6 call the two sharp stills *plus the low-resolution clip between them* (start/end offsets). The stills give fine detail; the clip shows the action that caused the change. At 100 tokens/s the whole video costs about as much as the stills do. That turns "action_inferred" into something closer to "action_seen". Keep the stills-only path for Claude and local models.

**4. Keep doing the transcript join yourself.** S6 already gives each keyframe its own transcript window. That matters: builders who let the model match transcript to frames by timestamp found it drifting on long videos. Also clamp Whisper timestamps to the video's duration; on music-only audio it can produce segments that land past the end.

**5. Don't build the local reasoner early.** It is the riskiest implementation (R1) and gives nothing your hybrid posture lacks. Leave it for after the cloud path works on real videos.

## The two cheap tests to run first

Both are already in your spec. They should come *before* more pipeline work, because either one could change what you build.

1. **Transcript-only baseline** (assumption 1 in 07). Run three real videos through a plain transcript summary and note what's missing. If little is missing, Stratum is over-built for this corpus.
2. **Native-video baseline** (the Phase 4 comparison mode, moved forward). Give Gemini the whole reference video once, with Stratum's output schema in the prompt, and compare the result to a Stratum run. If native wins on quality, Stratum becomes a thin layer: the schema, the provenance checks and the artifact around one native call. If it loses on small details or provenance, which I expect, you have the evidence for the pipeline.

Both need Q2, the 60 to 90 second reference video, which is still owed and blocks every real test.

## Build versus adapt, stage by stage

| Stage | Verdict |
|---|---|
| S0 to S3: ingest, audio, transcribe, sample | Keep yours. It's built, and handles rotation, variable frame rate and ffmpeg 9 better than the open tools. Use faster-whisper; don't buy transcription. |
| S4: select | Keep, and add the small-region tier and the last-N comparison above. |
| S5: weigh | Keep, but treat it as unproven (R6) until a forced-profile test shows it changes output. |
| S6: traverse | This is Stratum. Own it. Add the clip-between-stills mode for Gemini. |
| S7, S8: synthesise, emit | Own them. The artifact and provenance are the part native video can't give you. |
| Vision and speech models | Adapt. Never train or host your own beyond optional local Whisper and Qwen3-VL. |
| URL ingestion | Still defer, for the terms-of-service reasons in 06. |

## Answer to Q1 (which cloud reasoner)

Gemini Flash-class first. It is the only option that can take both stills and clips, it prices images cheaply at low resolution, and it supports schema-enforced JSON. Add Claude as the second reasoner for the comparison 02 asks for. Pin the exact model in `stratum.yaml` with a `verified_on` date, as the spec requires; no model name goes in the code. This is my recommendation; Q1 is yours to decide.

## One thing to check

The Ventures README says Stratum is "being rebuilt on a React stack", but its own README and brief say "local CLI, no UI". If a UI rebuild is underway somewhere, it isn't in this folder.

## Sources

- [Gemini API: video understanding](https://ai.google.dev/gemini-api/docs/interactions/video-understanding) · [media resolution](https://ai.google.dev/gemini-api/docs/media-resolution) · [tokens](https://ai.google.dev/gemini-api/docs/tokens)
- [Gemini agentic video mode (VP Land write-up)](https://www.vp-land.com/stories/gemini-apis-agentic-video-mode-navigates-long-footage-instead-of-sampling-the-whole-clip)
- [Frame selection is the whole game: notes from making LLMs watch video](https://leoaido.com/how-llms-watch-video/)
- [claude-real-video](https://github.com/HUANGCHIHHUNGLeo/claude-real-video) · [peepshow](https://www.peepshow.dev/)
- [Twelve Labs pricing calculator](https://www.twelvelabs.io/pricing-calculator)
- [Qwen3-VL technical report coverage](https://www.unite.ai/alibaba-releases-qwen3-vl-technical-report-detailing-two-hour-video-analysis/) · [Qwen3-VL-8B model page](https://friendli.ai/models/Qwen/Qwen3-VL-8B-Instruct)
- [Claude video input feature request](https://github.com/anthropics/claude-code/issues/12676) · [OpenAI video input feature request](https://github.com/openai/openai-node/issues/1778) · [OpenAI cookbook: video via frames](https://developers.openai.com/cookbook/examples/gpt_with_vision_for_video_understanding)
- [Extracting code from programming tutorial videos](https://csaws.cs.technion.ac.il/~yahave/blog/video-code.html)
