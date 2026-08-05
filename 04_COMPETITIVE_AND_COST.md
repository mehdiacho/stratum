# 04 — Competitive landscape and cost model

**All prices retrieved 4 August 2026.** They move fast. Anything acted on commercially should be re-checked against the vendor's own page — sources listed at the end.

---

## Part 1 — Who else does this

Four categories. None of them occupies the same position.

### Category 1 — Transcript summarisers

**Eightify, NoteGPT, Glasp, browser extensions, NotebookLM's YouTube ingestion, "summarise this video" in a chat app.**

| Tool | Price |
|---|---|
| Eightify | 3 summaries/day free, then $9.99/mo |
| NoteGPT | 10 free, then $9.99/mo |
| NotebookLM | Free tier; Plus $7.99/mo (bundled with Google AI Plus, not standalone); Pro $19.99/mo; Ultra $99.99–$200/mo |

**What they do:** fetch captions, summarise, output prose.

**Why they do not solve the problem:** they never look at the picture. On the corpus that matters here — someone building a thing while saying *"and then you just do this bit"* — the transcript contains almost no information and the summary is confidently empty. Reported failure modes cluster exactly where you would expect: heavy accents, poor subtitles, and videos where the subtitles do not carry the content.

They are also human-output tools. Prose, no structure, no provenance, no addressability, nothing an agent can consume without a second pass.

**Verdict:** adjacent, cheap, and blind to the thing this is for.

### Category 2 — Video understanding APIs

**TwelveLabs** is the reference implementation of this category.

| Component | Price |
|---|---|
| Video indexing | $0.042/min, one-time |
| API usage — input video | $0.021/min |
| API usage — output text | $0.0075 / 1k tokens |
| Infrastructure | $0.0015/min |
| Free tier | 10 hours of indexing |

**What it does:** genuine multimodal understanding — indexes video so it can be searched semantically, and answers questions about it.

**Why it is not the same product:** it is built for **retrieval across a library**, not **comprehension of one build**. It answers *"find me the clip where the pump is installed"*. It does not produce an ordered procedure with a materials list and per-claim provenance. If you wanted STRATUM's output from TwelveLabs you would build STRATUM on top of it.

**Cost:** roughly $0.42 to index a 10-minute video before a single question is asked, and around $0.65 for index plus one analysis pass. Roughly **20–30× the modelled hybrid cost** (Part 2).

**Verdict:** a genuine capability, a different job, and expensive for this use.

### Category 3 — Native long-context multimodal models

Feeding the whole video file to a frontier multimodal model and asking for a write-up.

**This is the most honest competitor and the specs should say so.** Video tokenises at roughly 263 tokens per second at default resolution, or around 100 tokens/second at low media resolution. A 10-minute video is therefore ~158k tokens standard or ~60k low-res — which at cheap-model rates is a few cents for a single pass. **It is cost-competitive with STRATUM's hybrid posture.** Any argument for STRATUM based purely on price is wrong.

The real arguments are four, and none of them is money:

| | Native video ingestion | STRATUM |
|---|---|---|
| Frame selection | The provider decides; you cannot see or tune it | Explicit, inspectable, tunable |
| Intermediate artifacts | None — one prose answer | Keyframes, transcript, transitions, all on disk |
| Provenance | Approximate, unverifiable | Every claim tagged to a modality and a timestamp |
| Re-asking a new question | Re-upload, re-pay, re-analyse | Re-query the artifact for free |
| Long video | Degrades, and quietly | Cost and quality scale with information content, not duration |

The fourth line is the strongest. A research corpus gets asked questions repeatedly over months. Paying to re-ingest 40 videos each time you have a new question is both expensive and non-reproducible — you get a different answer each run with no way to see why.

**Verdict:** the real alternative. **Phase 4 must implement it as a comparison mode** and run it against the reference video, honestly, and record the result in the repo. If it wins, that is worth knowing early and cheaply.

### Category 4 — Screen-recording to documentation

**Scribe** (from ~$12/user/mo), **Guidde** (~$23/creator/mo Pro), **Trupeer** (~$40/mo Pro, capped at 20 AI video minutes).

**What they do:** produce exactly the output shape we want — ordered steps, annotated screenshots, clean documentation. Guidde and Trupeer add AI narration and polish.

**Why they cannot do this:** they generate documentation from **recordings they capture themselves**, instrumented so they can see click and keystroke events. The step boundaries come from the event stream, not from analysing pixels. Point any of them at a stranger's video of somebody assembling a grow bench in a shed and there is nothing to hook into.

**Verdict:** closest in output shape, furthest in input capability. Worth studying for *document design* — they have solved the presentation problem well — and irrelevant as competition.

### The research literature

Worth noting because it validates the core mechanism rather than the product: recent work on GUI-agent training pipelines converts tutorial videos into structured knowledge using precisely the pattern specified here — extract keyframes, transcribe with Whisper, then have a vision-language model **infer the action and intent between consecutive states**. Pairwise state-delta annotation is an established technique that works.

It has been applied to screen recordings, for training agents. It has not been productised for arbitrary real-world video, for humans and agents doing research. That is the gap.

---

## Part 2 — Cost model

### Assumptions, stated so they can be argued with

| Assumption | Value |
|---|---|
| Reference video | 10 minutes, 30 fps, 1080p |
| Sampling rate | 2 fps → 1,200 candidate frames |
| Keyframes retained after S4 | 80 |
| Reasoner text input per call | ~900 tokens (running state + transcript window + prompt) |
| Reasoner output per call | ~250 tokens |
| Image tokens per frame | 258 (low resolution) or ~1,032 (standard, 4 tiles) |
| Synthesis pass | ~12k in, ~3k out |
| **Total per video** | ~105k–167k input tokens, ~23k output tokens, 81 images |

### Per-video cost by posture

| Posture | Transcribe | Embed | Reason | **Total** | Per minute |
|---|---|---|---|---|---|
| **LOCAL** — everything on hardware | $0 | $0 | $0 | **$0** | $0 |
| **HYBRID** — local ASR + local embed + cheap cloud VLM, low-res frames | $0 | $0 | ~$0.020 | **~$0.02** | ~$0.002 |
| **HYBRID+** — same, mid-tier VLM, standard-res frames | $0 | $0 | ~$0.107 | **~$0.11** | ~$0.011 |
| **CLOUD** — cloud ASR + cloud embed + mid-tier VLM | ~$0.04 | ~$0.01 | ~$0.107 | **~$0.16** | ~$0.016 |
| **CLOUD premium** — top-tier VLM, standard-res | ~$0.04 | ~$0.01 | ~$0.457 | **~$0.51** | ~$0.051 |

Reasoner figures derived from published per-million-token rates: a cheap tier around $0.10/M in and $0.40/M out; a mid tier around $0.30/M in and $2.50/M out; a top tier around $1.50/M in and $9.00/M out.

**Output tokens dominate at every tier above the cheapest.** At mid-tier, output is 54% of the reasoner bill despite being 12% of the tokens. Practical consequence: **capping `max_output_tokens` and keeping the delta terse is the highest-leverage cost control in the system** — higher than reducing frames.

### Transcription, if bought rather than run locally

| Provider | Batch rate | 10-min video |
|---|---|---|
| AssemblyAI Universal | $0.0037/min | $0.037 |
| Deepgram Nova-3 | $0.0043/min | $0.043 |
| OpenAI Whisper API | $0.006/min | $0.060 |
| AWS Transcribe | $0.024/min | $0.240 |

At these rates cloud ASR is roughly **twice the cost of the entire hybrid run**. That is the clearest possible argument for running transcription locally: it is the stage where open models are closest to parity and the paid option is proportionally most expensive.

### What it actually costs to use

At 20 videos a month averaging 8 minutes:

| Posture | Monthly |
|---|---|
| Local | $0 |
| **Hybrid** | **~$0.32** |
| Cloud | ~$2.60 |
| Cloud premium | ~$8.20 |

**The whole thing costs less per month than one Eightify subscription, and does something none of them can do.** That is the finding. For personal use the cost question is closed before it is opened — which is why `00_PRODUCT_BRIEF.md` puts the acceptance test on usefulness rather than economics.

### Hardware, for the local posture

| Component | Requirement |
|---|---|
| Transcription (`faster-whisper` large-v3, int8) | CPU adequate. Roughly real-time or better on a modern multi-core machine. |
| Embedding (small vision encoder) | CPU adequate at these volumes |
| Local VLM reasoner | **The constraint.** A capable 8B-class open vision model runs in roughly 6 GB VRAM quantised; ~10 GB at full precision. 27B–32B class wants 16–20 GB, so a 24 GB card or a Mac with generous unified memory. |

So the local posture's real question is whether an 8B-class vision model can hold a state-delta thread across 80 sequential calls without drifting. **Assume it cannot until measured.** That is the single riskiest technical assumption in this document, and it is cheap to test.

---

## Part 3 — If this ever becomes a product

Deliberately not decided (see `../README.md`). Recorded so the analysis is not redone from scratch later.

**The wedge, if there is one:** *arbitrary third-party visually-primary video → structured, provenance-tagged, agent-ready record.* Category 1 cannot see. Category 2 does retrieval, not comprehension. Category 3 gives no artifact. Category 4 cannot accept the input.

**Pricing shape it would have to take:** usage-based, because cost scales with minutes and information density, not with seats. Trupeer's model — a monthly fee with a video-minute allowance — is the closest fit in the market and the closest to how the cost is actually incurred. Seat pricing would be wrong here.

**The three honest risks:**

1. **The gap may be empty because it is small.** Most people wanting to understand a video are content with a transcript summary. The people who need frame-level provenance may be a rounding error.
2. **Category 3 erodes it.** Native video understanding gets better and cheaper every quarter. Any moat has to be the artifact, the provenance and the reproducibility — not the extraction.
3. **Input supply is legally awkward.** The videos are on platforms whose terms prohibit downloading. Fine for personal use; a structural problem for a product. See `06_INGESTION_ROADMAP.md`.

Risk 3 is the one that would most likely stop this being sold, and it is worth knowing that now rather than after building a billing system.

---

## Sources

- [TwelveLabs — Pricing](https://www.twelvelabs.io/pricing) · [Pricing calculator](https://www.twelvelabs.io/pricing-calculator)
- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API — Media resolution](https://ai.google.dev/gemini-api/docs/media-resolution) · [Video understanding](https://ai.google.dev/gemini-api/docs/video-understanding)
- [Speech-to-Text APIs in 2026: benchmarks and pricing](https://futureagi.com/blog/speech-to-text-apis-in-2026-benchmarks-pricing-developer-s-decision-guide/) · [Deepgram — Best speech-to-text APIs 2026](https://deepgram.com/learn/best-speech-to-text-apis-2026) · [OpenAI Whisper API pricing 2026](https://diyai.io/ai-tools/speech-to-text/openai-whisper-api-pricing-2026/)
- [NotebookLM pricing 2026](https://felloai.com/notebooklm-pricing/) · [Eightify review and pricing](https://affine.pro/blog/eightify-youtube-summarizer) · [NoteGPT features and limits](https://www.ekamoira.com/blog/notegpt-youtube-summarizer-complete-guide-to-features-limits-better-alternatives-2026)
- [Guidde vs Scribe pricing comparison 2026](https://www.guidde.com/tool-comparison/guidde-vs-scribe-pricing-comparison-2026) · [Guidde vs Trupeer pricing](https://www.trupeer.ai/tools-comparison/guidde-vs-trupeer-pricing)
- [GUIDE: Resolving Domain Bias in GUI Agents through Real-Time Web Video Retrieval and Plug-and-Play Annotation](https://arxiv.org/pdf/2603.26266)
- [Image deduplication: CLIP embeddings vs perceptual hashing](https://msightflow.ai/blog/image-deduplication) · [Video frame sampling for embeddings](https://mixpeek.com/guides/video-frame-sampling-for-embeddings) · [PySceneDetect features](https://www.scenedetect.com/features/)
- [Best local vision language models 2026](https://tinyweights.dev/posts/best-local-vision-language-models-2026/) · [Qwen3-VL 4B vs 8B VRAM guide](https://codersera.com/blog/qwen3-vl-4b-vs-qwen3-vl-8b-benchmarks-vram-guide/)
