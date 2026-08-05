# 02 — Backend abstraction and the three postures

---

## The requirement

> *"Hybrid is fastest to achieve now, but all-cloud and all-local should be possible too — ideally choosable from the jump. If I have funding, cloud models are more capable. If I have good hardware and don't want to spend, all-local is fine. Hybrid is for when my local models aren't great and I want to spend only where capability really matters."*

That is a requirement for **three interchangeable postures selected by configuration**, not three codebases. It is cheap if built in Phase 1 and expensive if retrofitted, so it is built in Phase 1.

---

## Three protocols, that is all

There are exactly three points in the pipeline where a model is invoked. Each gets a protocol.

```python
class Transcriber(Protocol):
    def transcribe(self, wav_path: Path, *, language: str | None) -> Transcript: ...
    @property
    def cost_model(self) -> CostModel: ...

class Embedder(Protocol):
    def embed_images(self, paths: Sequence[Path]) -> np.ndarray: ...   # (n, d), L2-normalised
    @property
    def cost_model(self) -> CostModel: ...

class Reasoner(Protocol):
    def analyse(self, req: ReasonRequest) -> ReasonResponse: ...       # schema-enforced
    @property
    def cost_model(self) -> CostModel: ...
```

Every implementation returns the same normalised types. **No pipeline stage may import a provider SDK or branch on which backend is active.** If a stage needs to know, the abstraction is wrong — fix the abstraction.

---

## The postures

| | **HYBRID** (default) | **CLOUD** | **LOCAL** |
|---|---|---|---|
| Transcriber | `faster-whisper`, local | Deepgram / AssemblyAI / OpenAI | `faster-whisper`, local |
| Embedder | Local vision encoder | Provider embedding API | Local vision encoder |
| Reasoner | **Cloud VLM** | Cloud VLM (larger model) | Local VLM via Ollama / vLLM |
| Needs GPU | No — CPU whisper is adequate | No | Yes, meaningfully |
| Needs API key | Yes, one | Yes | No |
| Cost per 10-min video | **~$0.02** | ~$0.10–0.35 | ~$0 |
| Privacy | Frames leave the machine | Everything leaves | Nothing leaves |
| Quality ceiling | High where it matters | Highest | Depends on hardware |

Costs are modelled in `04_COMPETITIVE_AND_COST.md` with assumptions stated. Treat as estimates.

**Why hybrid is the default.** The three stages have very different capability requirements. Transcription is a solved problem that open models do about as well as paid APIs. Embedding for deduplication needs relative distances, not semantic brilliance — a small local encoder is fine. Sequential state-delta reasoning over an image with carried context is the hard part, and it is where the gap between a frontier model and a 8B local model is widest. So: spend nothing on the first two, spend on the third.

That is not a compromise, it is the correct allocation. Hybrid should stay the default even with unlimited budget, with the reasoner swapped upward.

---

## Configuration

A single YAML file. Posture is a preset; anything can be overridden individually.

```yaml
posture: hybrid          # hybrid | cloud | local

backends:
  transcriber:
    provider: faster-whisper
    model: large-v3
    device: auto          # auto | cpu | cuda
    compute_type: int8    # int8 on CPU, float16 on GPU
  embedder:
    provider: local-siglip
    model: <pinned model id>
    batch_size: 32
  reasoner:
    provider: gemini
    model: <pinned model id>
    max_output_tokens: 800
    temperature: 0.2

pipeline:
  sample_fps: 2.0
  max_keyframes_per_minute: 12
  max_keyframes_total: 200
  phash_threshold: 8              # Hamming distance
  embed_similarity_threshold: 0.94
  prior_frames_in_context: 1
  transcript_window_margin_s: 3.0
  state_token_budget: 1200

limits:
  max_cost_usd_per_video: 1.00    # hard stop, not a warning
  max_wall_time_s: 3600
```

**Mixing is explicitly supported and expected.** Local transcription with a cloud reasoner and a cloud embedder is a valid configuration. The `posture` key only sets defaults.

---

## Implementations to build

### Phase 1 — one per protocol, enough to run end to end

| Protocol | Implementation | Why first |
|---|---|---|
| Transcriber | `faster-whisper` | Runs on CPU at acceptable speed, word timestamps, no key needed, free |
| Embedder | Local SigLIP or CLIP via `open_clip` / `sentence-transformers` | Small, fast, CPU-viable |
| Reasoner | One cloud VLM behind an adapter | The one thing worth paying for |

Plus a **`MockReasoner`** returning canned structured responses. Non-negotiable: without it every test costs money and requires a network, and the pipeline logic never gets tested properly.

### Phase 2 — fill out the matrix

| Protocol | Add |
|---|---|
| Transcriber | Cloud ASR adapter (Deepgram or AssemblyAI — cheapest per minute, see cost doc) |
| Embedder | Cloud embedding adapter |
| Reasoner | Second cloud provider (avoids single-vendor lock and lets output quality be compared on the same video) |
| Reasoner | Local VLM via Ollama's OpenAI-compatible endpoint |

**The local VLM is the highest-risk implementation and should be built with low expectations.** Sequential state-carrying analysis is exactly the workload small models are worst at — they lose the thread, restate what is in the image instead of what changed, and drift out of the output schema. Ship it, measure it honestly on the same reference video as the cloud reasoner, and publish the comparison in the repo rather than assuming parity.

---

## Model identifiers are configuration, never code

Model names change and get deprecated on a timescale of months. Every model identifier lives in the config file with a comment recording when it was last verified. No model name is ever hard-coded, and no model name is written into these specification documents — the specs say *"a cloud VLM"*, and the config says which one, today.

The same applies to the pricing table used for cost accounting: it is a data file with a `verified_on` date, and the CLI warns when it is more than 90 days stale.

---

## Choosing the reasoner — the axis that actually matters

Not raw benchmark score. Three properties, in order:

1. **Structured-output reliability.** The pipeline depends on schema-conformant JSON on every one of eighty sequential calls. A model that is marginally smarter but fails schema one call in twenty is worse than a duller one that never does.
2. **Image cost.** Per-image token cost dominates the bill, because there is one image per call and eighty calls per video. A model that tokenises images cheaply at low resolution beats a model with a lower text rate.
3. **Instruction adherence on the delta question.** The characteristic failure is a model that describes the whole image instead of the change, no matter how the prompt is worded. This is measurable on the reference video and should be measured before committing.

Build the adapter so switching costs an afternoon, then test all three properties on real content rather than reasoning about them.

---

## Cost enforcement

`max_cost_usd_per_video` is a **hard stop**. The orchestrator tracks spend as it goes and halts the run when the cap is hit, writing a partial artifact with everything completed so far.

This exists because the failure mode of a sequential per-frame pipeline is a badly-tuned threshold retaining 900 keyframes instead of 80, at eleven times the expected cost, discovered afterwards. The cap turns that from a bill into a log line.
