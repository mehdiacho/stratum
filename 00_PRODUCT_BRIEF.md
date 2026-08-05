# 00 — Product brief

---

## 1. The problem

Starter information — the thing that makes you want to understand a subject, and gives you enough footing to start reading properly — increasingly lives in short-form video. TikTok, Instagram, YouTube. Someone builds a hydroponic system in their garage, or walks a commercial greenhouse, and in eight minutes conveys more usable orientation than a week of search results.

Video is the worst possible format to think with. You cannot search it, quote it, diff it, or hand it to an agent. And the tools that claim to fix this mostly do not:

**They read the subtitles.** Every mainstream video summariser is a transcript summariser wearing a costume. It fetches captions, feeds them to a language model, and returns prose. On a lecture, that works. On a build video where the narration is *"and then you just do this bit"* while the hands do the actual explaining, it returns nothing, confidently.

**They summarise for humans.** The output is paragraphs meant to be read once. It is not structured, not addressable, carries no provenance, and cannot be fed to an agent as context without a second round of cleanup.

**They treat a video as a bag of frames.** Where visual analysis exists at all, each frame is described independently. You get thirty descriptions of a table with progressively more parts on it, and nowhere a statement of what was *added* between frame eleven and frame twelve — which is the only thing you actually wanted.

---

## 2. The idea

Turn a video into a **state-transition record**.

The insight this rests on: an instructional video is not a sequence of images, it is a sequence of *changes*. The thing being built exists in a state; an action occurs; it exists in a new state. Everything worth extracting lives in the delta. So the analysis must be sequential and stateful — every frame is examined *knowing what the previous frame contained* — and the unit of output is a transition, not a description.

Five things follow from that, and they are the whole design:

**a. Reduce to meaningful stills.** Frame rate is a property of the camera, not of the content. A 120 fps video does not contain 120 pieces of information per second. Sample coarsely, detect shot boundaries, then aggressively deduplicate what remains until what is left is a set of stills that each show something the previous one did not.

**b. Transcribe with timestamps.** Not to summarise, but so that every visual moment can be aligned with what was being said at that moment. The transcript is a *witness* to the frames, not a substitute for them.

**c. Work out which modality is carrying the load.** Some videos are audio with wallpaper. Some are silent hands with music over the top. Most are a mix that shifts within a single video. The pipeline must measure this and weight its analysis accordingly, rather than assuming.

**d. Analyse sequentially, with carried state.** Each keyframe is sent for analysis together with a running summary of the state so far and the transcript aligned to its timestamp. The question asked is not *"what is in this image"* but *"given that we were here, what is different now, and what action produced the difference?"*

**e. Tag every claim by where it came from.** `heard` · `seen` · `inferred`. This is the difference between a document an agent can trust and one it cannot. An inference about pipe diameter and a stated measurement look identical in prose and must never look identical in the output.

---

## 3. What "done" looks like

Phase 1 is done when this is true:

```
$ stratum process ~/videos/nft_build.mp4
```

...produces a directory containing a canonical JSON artifact, a readable Markdown rendering, and the retained keyframes on disk — and Mehdi reads the Markdown and finds it a genuinely faster way to understand that build than watching the video again.

That last clause is the actual acceptance test. It is subjective and it is the only one that matters. Everything measurable — keyframe counts, cost per video, runtime — is a proxy.

**Secondary acceptance criteria, all objectively checkable:**

| Criterion | Target |
|---|---|
| Keyframe reduction | A 10-minute 30 fps video (18,000 frames) reduces to under 150 keyframes with nothing important lost |
| Provenance | Every claim in the output carries a modality tag and a source timestamp |
| Resumability | Killing the process mid-run and re-running it resumes rather than restarts |
| Posture switching | The same video runs under all three postures by changing one config value, no code change |
| Cost visibility | Every run reports what it actually cost, per stage |

---

## 4. Explicitly out of scope for Phase 1

Naming these so they do not creep in:

- **Any UI.** No web app, no TUI, no progress dashboards beyond CLI output.
- **URL / social-media downloading.** See `06_INGESTION_ROADMAP.md`. The interface is defined; the implementation is not built.
- **Accounts, billing, multi-tenancy, hosting.** This runs on one machine for one person.
- **Real-time or streaming processing.** Batch only.
- **Speaker diarisation.** Who is speaking rarely matters in this corpus. The hook exists in the transcript schema; nothing populates it.
- **Video generation, editing, clipping.** STRATUM reads video. It never writes video.
- **A retrieval layer.** The artifact is designed to be chunked and embedded by something else. STRATUM does not own the vector store.

---

## 5. The users, in order

1. **Mehdi, on his own research corpus.** The only user for Phase 1 and 2. Every design tension resolves in favour of this user.
2. **Agents Mehdi runs.** The artifact is consumed by AI models doing downstream work — building an equipment specification, comparing two systems, answering questions against a corpus of processed videos. This is why the output is structured and provenance-tagged rather than prose.
3. **Possibly other people, later.** Deliberately not designed for. See `../README.md` on the deferred commercial question.

---

## 6. The series problem

Information is often spread across a series rather than contained in one video, and then continues into text sources that need no video pipeline at all.

**Design consequence:** the atomic unit is a **video**, but the addressable unit is a **corpus**. The pipeline processes videos independently and idempotently; a separate merge stage reconciles a set of them into one document, resolving repeated entities and ordering steps across episode boundaries.

This is why entity IDs must be stable and provenance must record which video a claim came from. Retrofitting that after the fact would be painful; building it in costs almost nothing.

**Phase 1 processes one video.** Phase 3 merges. But the schema supports the merge from day one.

---

## 7. Why this is worth building rather than buying

Detailed comparison in `04_COMPETITIVE_AND_COST.md`. The short version:

- The consumer summarisers are transcript-only and cannot see the thing that matters
- The video-understanding APIs are built for *search over a library*, not *comprehension of one build*, and cost roughly twenty times more per minute
- The step-by-step documentation tools produce almost exactly the output shape we want — but only from screen recordings they capture themselves, with instrumented click events. They cannot process a stranger's video of a greenhouse.

Nobody occupies the intersection: **arbitrary third-party, visually-primary, real-world video → structured, provenance-tagged, agent-ready record.**

Whether that gap is empty because it is valuable and hard, or empty because it is not valuable, is the honest open question. Building it for personal use answers that question cheaply.
