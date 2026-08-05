# 07 — Open questions

Everything unresolved. Each has an owner and states what it blocks. **Claude Code should not silently decide any of these — surface them.**

---

## Blocking Phase 1

| # | Question | Owner | Note |
|---|---|---|---|
| Q1 | **Which cloud VLM is the Phase 1 reasoner?** | Mehdi | Choose on the three axes in `02_BACKEND_ABSTRACTION.md` — schema reliability, image token cost, delta-vs-description adherence. Test on the reference video before committing. Adapter makes it an afternoon to switch. |
| Q2 | **What is the reference video?** | Mehdi | 60–90 s, visually-led, self-recorded so licensing is not a question. Every test depends on it. |
| Q3 | **Where does the corpus live?** | Mehdi | A folder on this machine, outside the repo, backed up. Videos are large and must never enter git. |
| Q4 | **Does the machine have a usable GPU?** | Mehdi | Decides whether `faster-whisper` runs int8-CPU or float16-CUDA, and whether the local posture is testable at all. |

---

## Technical risks — resolved by measurement, not discussion

| # | Risk | How to settle it |
|---|---|---|
| R1 | **Can a local 8B-class VLM hold a state thread across 80 sequential calls?** The riskiest assumption in the whole design. | Phase 2. Run the reference video under the local reasoner, count schema failures and describe-instead-of-delta failures. Assume no until measured. |
| R2 | **Do the tier-1/tier-2 thresholds generalise?** Tuned on hydroponics footage, they may collapse on a different visual style. | Phase 2 `stratum tune`. Expect per-corpus threshold profiles rather than one global default. |
| R3 | **Does state compaction lose things that matter?** A part set down at 2:00 and picked up at 8:00 must survive compaction. | Build a deliberate test case into the reference video. |
| R4 | **Is 2 fps sampling enough?** A fast hand movement between two samples is invisible. | Compare 1 / 2 / 4 fps on the same video and look at what appears and disappears. |
| R5 | **Do sharpness-selected frames actually help?** Assumed, not verified. | A/B on the reference video, first-in-cluster vs sharpest-in-cluster. |
| R6 | **Does the modality profile change output quality, or only the prompt?** It is a nice idea that might do nothing. | Run one video with the profile forced to each of the four classes and compare artifacts. |

---

## Design questions, deferred honestly

| # | Question | When |
|---|---|---|
| D1 | **Should S6 see more than one prior frame?** Config allows 0–2. Unknown which is right, and it doubles or halves image cost. | Phase 2, empirically |
| D2 | **Should the transcript ever override the picture?** When audio says 20 mm and the fitting reads 25 mm, the current design records a conflict and resolves nothing. That may be correct, or it may be passing the buck. | Phase 2, once real conflicts have been seen |
| D3 | **Is a fourth modality — `stated`, for author-written captions — right?** Argued for in `06_INGESTION_ROADMAP.md`. Adds a value to a deliberately tiny enum. | Decide before schema v1.0 is frozen |
| D4 | **Should failed transitions block synthesis?** Currently no. A run with 20 of 80 failures produces a confidently wrong document. | Add a failure-rate threshold in Phase 2 |
| D5 | **Corpus-level entity reconciliation — automatic or reviewed?** Merging "PVC channel" across five videos may silently merge two different things. | Phase 3 |

---

## Non-technical

| # | Question | Note |
|---|---|---|
| N1 | **The name.** STRATUM is provisional and its trademark status is unchecked. | Only matters if this is ever published or sold. Renaming an internal tool costs nothing. Per `VENTURE_ARCHITECTURE.md`: code names are provisional labels, not branding. |
| N2 | **Open source, commercial, or private?** | Deliberately deferred until the pipeline works — see `../README.md`. Note the dependency: if the URL module is ever built on personal session tokens, the product path closes (`06_INGESTION_ROADMAP.md`). |
| N3 | **Does processing third-party video into a derived document raise a copyright question?** | Almost certainly fine for private research — this is note-taking. Genuinely unsettled if artifacts are published or the tool is sold. Ask a lawyer before, not after. Not a Phase 1 concern. |
| N4 | **How does this feed Track V?** | The artifacts are research inputs for the agriculture equipment specification (`01_AGRICULTURE/TRACK_V_EQUIPMENT_SPEC.md`). Whether they get filed there, referenced, or merged is unresolved and worth deciding once real artifacts exist. |

---

## Assumptions this specification makes, listed so they can be attacked

1. **The information in these videos is genuinely visual.** If it turns out the transcript carries 90% of it, a much simpler tool wins and this design is over-engineered. *Test cheaply: run three videos through a plain transcript summariser first and see what is actually missing.*
2. **State-delta beats independent frame description.** Well-supported by the research literature, unverified on this corpus.
3. **80 keyframes is roughly the right order for 10 minutes.** A guess. Could be 30, could be 200.
4. **Frontier VLMs reliably produce schema-conformant output across 80 sequential calls.** Mostly true today, and the entire pipeline depends on it. Retry and repair logic is not optional.
5. **Cost is not the binding constraint.** True at personal volume. False immediately if this becomes a product.

Assumption 1 is the one worth attacking first, and it costs almost nothing to attack.
