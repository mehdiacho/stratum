# 03 — Output schema

> The output is designed to be **read by an agent**, and only incidentally by a person. Every decision here follows from that.

---

## Four principles

**1. Provenance on every claim.** Each assertion carries `modality` — `heard` · `seen` · `inferred` — plus a confidence and a source timestamp. In prose, *"the channels are 15 cm apart"* and *"the channels look about a hand-width apart"* read the same. To a downstream agent they must not. This is the single most valuable property of the artifact and the thing no competitor produces.

**2. Stable, addressable IDs.** Every entity, transition, claim and step has an ID that survives re-runs and can be cited. This is what makes the multi-video merge possible and what lets an agent say *"per `kf_0031`"* instead of paraphrasing.

**3. Chunk on meaning, not on token count.** Chunks break at transition and step boundaries. A chunk that splits a state change in half is worse than useless — it produces a retrieved fragment describing a world that never existed.

**4. Frames are output, not scratch.** Keyframes stay on disk and are referenced by path. A multimodal agent working with the artifact can pull the actual image when the text is not enough. Deleting them to save space destroys most of the value.

---

## `artifact.json` — canonical

```jsonc
{
  "schema_version": "1.0",
  "run": {
    "id": "<content hash>",
    "created_at": "2026-08-04T19:14:02Z",
    "source": {
      "kind": "local_file",
      "filename": "nft_build.mp4",
      "duration_s": 612.4,
      "fps_avg": 29.97,
      "resolution": [1080, 1920],
      "content_hash": "sha256:..."
    },
    "config_snapshot": { },
    "cost": {
      "total_usd": 0.0184,
      "by_stage": {"transcribe": 0.0, "select": 0.0, "traverse": 0.0171, "synthesise": 0.0013},
      "tokens_in": 96420, "tokens_out": 21300, "images": 81,
      "pricing_table_verified_on": "2026-08-04"
    },
    "stages_completed": ["S0","S1","S2","S3","S4","S5","S6","S7","S8"],
    "warnings": []
  },

  "modality_profile": {
    "class": "visual_led",
    "speech_density_wpm": 41.2,
    "mean_asr_confidence": 0.71,
    "keyframes_per_minute": 7.9,
    "grounding_score": 0.34,
    "note": "Narration is sparse and mostly commentary. Visual evidence dominant.",
    "scope": "whole_video"
  },

  "entities": [
    {
      "id": "ent_channel",
      "label": "PVC grow channel",
      "kind": "material",
      "attributes": {
        "cross_section": {"value": "square, approx 75 mm", "modality": "inferred", "confidence": 0.55},
        "length":        {"value": "1.5 m",                "modality": "heard",    "confidence": 0.9}
      },
      "first_seen_kf": "kf_0012",
      "last_seen_kf": "kf_0074",
      "mentions": ["kf_0012", "kf_0031", "kf_0074"]
    }
  ],

  "transitions": [
    {
      "id": "tr_0031",
      "keyframe": {
        "id": "kf_0031",
        "path": "keyframes/kf_0031.jpg",
        "timestamp_s": 184.2,
        "shot_boundary": false,
        "sharpness": 118.4
      },
      "delta": "A second channel has been placed parallel to the first and joined at the near end with a 90° elbow.",
      "action_inferred": {"value": "joining channels", "confidence": 0.78},
      "entities_added":   ["ent_channel_2", "ent_elbow_1"],
      "entities_changed": [],
      "entities_removed": [],
      "claims": [
        {"id": "cl_0102", "text": "Spacing between channels is stated as 15 cm", "modality": "heard",    "confidence": 0.90, "transcript_span": [181.0, 186.4]},
        {"id": "cl_0103", "text": "The elbow appears to be solvent-welded, not push-fit", "modality": "inferred", "confidence": 0.45}
      ],
      "uncertainty": "Elbow fitting size not visible and not stated.",
      "transcript_window": {"start_s": 181.2, "end_s": 187.2, "text": "...", "weight": "hint"},
      "status": "ok"
    }
  ],

  "procedure": [
    {
      "id": "st_04",
      "ordinal": 4,
      "title": "Join channels into a bench run",
      "detail": "...",
      "derived_from": ["tr_0029", "tr_0031", "tr_0033"],
      "timestamp_range_s": [176.0, 201.5],
      "confidence": 0.72
    }
  ],

  "materials": [
    {"entity": "ent_channel", "quantity": {"value": 4, "modality": "seen", "confidence": 0.85}, "note": "Four visible on the bench at kf_0074"}
  ],

  "open_questions": [
    {"id": "oq_01", "text": "Pump specification is never shown or stated.", "why_it_matters": "Determines head height and flow rate for the whole system.", "related": ["ent_pump"]}
  ],

  "conflicts": [
    {"id": "cf_01", "heard": "he says 20 mm", "seen": "the fitting reads 25 mm", "keyframes": ["kf_0052"], "resolution": "unresolved"}
  ]
}
```

### Notes on specific fields

**`modality`** — exactly three values, no others:

| Value | Means |
|---|---|
| `heard` | Stated in the audio. Traceable to a transcript span. |
| `seen` | Directly visible in a keyframe. |
| `inferred` | Neither stated nor plainly visible; concluded from context. |

An agent consuming this can filter to `heard`+`seen` when it needs facts and include `inferred` when it needs hypotheses. That single distinction is most of the artifact's value.

**`confidence`** — 0–1, from the model. Treat as ordinal, not calibrated. Useful for ranking, not for arithmetic.

**`status`** on a transition — `ok` · `failed` · `retried`. Failed transitions stay in the record with the error. A gap in the record must be visible, not silent.

**`open_questions`** — a first-class output, not an afterthought. In the intended workflow, this is the *handoff*: what the video could not tell you is exactly what gets chased into text sources afterwards. It is the most directly actionable section for a downstream agent.

---

## `artifact.md` — human rendering

Generated from the canonical model by template. Never hand-written, never edited — it is a view.

```markdown
# nft_build.mp4 — 10:12 — visual-led

## What this video shows
...

## Procedure
### 4. Join channels into a bench run  · 2:56–3:21 · confidence 0.72
...
![kf_0031](keyframes/kf_0031.jpg) *3:04 — second channel placed and joined with a 90° elbow*

## Materials
| Item | Qty | How we know |
|---|---|---|
| PVC grow channel, ~75 mm sq, 1.5 m | 4 | seen (0.85), length heard (0.90) |

## Open questions
- Pump specification never shown or stated — determines head height and flow for the whole system.

## Conflicts
- **3:52** — audio says 20 mm; the fitting appears to read 25 mm. Unresolved.
```

Modality is rendered visibly in the human view too. If a person reading it cannot tell measured from guessed, the document is dangerous — someone will buy 20 mm fittings.

---

## `chunks.jsonl` — agent ingestion

One JSON object per line. Each chunk is self-contained: it repeats the context needed to make sense of it standing alone, because that is how it will be retrieved.

```json
{
  "chunk_id": "run_9f3a::st_04",
  "kind": "step",
  "text": "Step 4 of 11 — Join channels into a bench run (2:56–3:21). A second PVC grow channel is placed parallel to the first and joined at the near end with a 90° elbow. Spacing stated as 15 cm [heard, 0.90]. The elbow appears solvent-welded rather than push-fit [inferred, 0.45]. Unresolved: elbow fitting size never shown or stated.",
  "source": {"run_id": "9f3a...", "video": "nft_build.mp4", "timestamp_range_s": [176.0, 201.5]},
  "keyframes": ["keyframes/kf_0031.jpg"],
  "entities": ["ent_channel", "ent_elbow_1"],
  "modalities_present": ["heard", "seen", "inferred"],
  "confidence": 0.72
}
```

**Chunk kinds:** `overview` · `step` · `transition` · `entity` · `open_question` · `conflict`.

**Why this shape:**

- **Modality tags are inline in the text, not only in metadata.** Metadata gets stripped by retrieval layers; the text survives. A model reading the chunk must be able to see that 0.45 without a schema.
- **Timestamps in the text.** Lets an agent cite back to the video.
- **One chunk per semantic unit, no fixed size.** Steps vary from thirty words to three hundred. Padding them to a uniform token count adds noise; splitting a long one mid-step breaks meaning.
- **`keyframes` paths are carried through.** A multimodal agent can escalate from text to image on the chunks where text is not enough.
- **No prose padding.** No "In this section we will explore". Every token is content.

---

## Multi-video merge (Phase 3)

A merged artifact has the same schema with `run.source.kind: "corpus"` and:

- `run.members` — the constituent run IDs in order
- Entity IDs namespaced then reconciled — `ent_channel@run_9f3a` merges into a corpus-level `ent_channel` with a `merged_from` list
- Every claim keeps its originating `run_id`, so provenance survives the merge
- A `contradictions` section for where two videos in a series disagree — which is common and is itself a finding

**Merging is never destructive.** The member artifacts remain on disk, unmodified. The merge is an additional layer, and it can be recomputed with a better reconciliation strategy without reprocessing a single video.

---

## Versioning

`schema_version` is present from v1.0 and checked on read. When it changes, ship a migration in the same commit — never a reader that silently tolerates both shapes. Artifacts are the durable asset; the code that made them is not.
