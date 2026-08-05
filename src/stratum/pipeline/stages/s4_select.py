"""S4 — Select. The keyframe reduction.

Three tiers, cheapest first. The ordering matters: tier 2 costs real compute, and
tier 1 removes most of the work before it runs.

  Tier 1  perceptual hash    kills the long static runs at near-zero cost
  Tier 2  embedding distance kills semantic duplicates pHash keeps
  Tier 3  sharpness          picks the readable frame out of each collapsed cluster

Budget control never truncates. Truncation drops the end of the video, which is
usually where the finished result is.
"""

from __future__ import annotations

import math
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from stratum.media.imaging import hamming, perceptual_hash, sharpness
from stratum.pipeline.context import RunContext
from stratum.schema.ids import keyframe_id
from stratum.schema.media import Candidate, Keyframe, SampleRecord, SelectionRecord, VideoMeta

STAGE = "S4"


@dataclass(frozen=True)
class _Frame:
    candidate: Candidate
    path: Path
    forced: bool
    sharpness: float


def _effective_budget(ctx: RunContext, duration_s: float) -> int:
    cfg = ctx.config.pipeline.select
    by_total = cfg.max_keyframes_total
    if duration_s <= 0:
        return by_total
    by_rate = math.ceil(cfg.max_keyframes_per_minute * (duration_s / 60.0))
    return max(1, min(by_total, by_rate))


def _tier1_phash(frames: list[_Frame], threshold: int) -> list[int]:
    """Walk the sequence, comparing against the last RETAINED frame.

    Comparing against the immediately previous candidate instead lets slow drift
    accumulate silently until the scene has completely changed without any single
    step ever crossing the threshold.
    """
    kept: list[int] = []
    anchor = None
    for i, frame in enumerate(frames):
        digest = perceptual_hash(frame.path)
        if frame.forced or anchor is None or hamming(digest, anchor) >= threshold:
            kept.append(i)
            anchor = digest
    return kept


def _tier2_embeddings(
    indices: list[int], vectors: np.ndarray, forced: list[bool], threshold: float
) -> list[int]:
    """Same walk, on cosine similarity. Vectors are L2-normalised, so a dot
    product is the similarity."""
    kept: list[int] = []
    anchor: np.ndarray | None = None
    for position, index in enumerate(indices):
        vector = vectors[position]
        if forced[position] or anchor is None or float(np.dot(vector, anchor)) <= threshold:
            kept.append(index)
            anchor = vector
    return kept


def _search_threshold(
    indices: list[int],
    vectors: np.ndarray,
    forced: list[bool],
    *,
    start: float,
    floor: float,
    budget: int,
    max_iterations: int,
) -> tuple[list[int], float, int]:
    """Binary-search the tier-2 threshold down until the retained set fits.

    Note the direction. The rule is "drop a frame whose similarity to the last
    retained frame EXCEEDS the threshold", so *lowering* the threshold drops more
    and retains fewer. 01_ARCHITECTURE.md says "raise the tier-2 threshold",
    which reads as raising strictness; taken literally against the comparison it
    specifies, it would retain more, not fewer. Implemented as lowering, which is
    what the surrounding paragraph plainly intends. Recorded in DECISIONS.md.
    """
    kept = _tier2_embeddings(indices, vectors, forced, start)
    if len(kept) <= budget:
        return kept, start, 0

    lo, hi = floor, start
    best = _tier2_embeddings(indices, vectors, forced, lo)
    best_threshold = lo
    iterations = 1

    for _ in range(max_iterations):
        mid = (lo + hi) / 2.0
        candidate = _tier2_embeddings(indices, vectors, forced, mid)
        iterations += 1
        if len(candidate) <= budget:
            # Fits. Try to keep more detail by moving back up.
            best, best_threshold = candidate, mid
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-4:
            break

    return best, best_threshold, iterations


def _tier3_sharpest(
    frames: list[_Frame], kept: list[int], *, radius: int, anchors: set[int]
) -> list[int]:
    """Within each collapsed cluster, retain the sharpest rather than the first.

    The search is bounded by `radius` candidates rather than running to the next
    retained frame. An unbounded search across a forty-second static shot would
    happily move the keyframe several seconds away from where it was selected,
    which desynchronises the transcript window aligned to it in S6.

    The first and last frames never move — they are anchors by position, not by
    content.
    """
    if radius <= 0:
        return kept

    result: list[int] = []
    for position, index in enumerate(kept):
        if index in anchors:
            result.append(index)
            continue
        limit = kept[position + 1] if position + 1 < len(kept) else len(frames)
        window_end = min(index + radius + 1, limit)
        window = range(index, max(window_end, index + 1))
        best = max(window, key=lambda i: (frames[i].sharpness, -i))
        result.append(best)

    # A swap can collide with the following cluster's pick; keep order and drop
    # duplicates rather than emitting the same frame twice.
    deduped: list[int] = []
    for index in result:
        if not deduped or index > deduped[-1]:
            deduped.append(index)
    return deduped


def _mean_adjacent_distance(vectors: np.ndarray) -> float | None:
    if vectors.shape[0] < 2:
        return None
    sims = np.einsum("ij,ij->i", vectors[:-1], vectors[1:])
    return float(np.mean(1.0 - sims))


def run(ctx: RunContext) -> dict[str, Any]:
    cfg = ctx.config.pipeline.select
    meta = ctx.read_model(ctx.rundir.video_meta, VideoMeta)
    sample = ctx.read_model(ctx.rundir.sample_record, SampleRecord)

    ctx.rundir.clear_keyframes()

    if not sample.candidates:
        record = SelectionRecord(
            keyframes=[],
            candidates_in=0,
            phash_threshold=cfg.phash_threshold,
            embed_similarity_threshold=cfg.embed_similarity_threshold,
            note="no candidates to select from",
        )
        ctx.write_model(ctx.rundir.selection_record, record)
        ctx.warn("S4 had no candidates; the artifact will contain no transitions")
        return {"keyframes": 0, "candidates_in": 0}

    last_index = len(sample.candidates) - 1
    frames = [
        _Frame(
            candidate=candidate,
            path=ctx.rundir.root / candidate.path,
            forced=candidate.is_shot_start or i == 0 or i == last_index,
            sharpness=sharpness(ctx.rundir.root / candidate.path),
        )
        for i, candidate in enumerate(sample.candidates)
    ]
    anchors = {0, last_index}

    # -- tier 1 ------------------------------------------------------------
    kept = _tier1_phash(frames, cfg.phash_threshold)
    after_phash = len(kept)

    # -- tier 2 ------------------------------------------------------------
    budget = _effective_budget(ctx, meta.duration_s)
    threshold = cfg.embed_similarity_threshold
    iterations = 0
    embed_cost = 0.0
    vectors = np.zeros((0, 0), dtype=np.float32)

    if cfg.use_embedding_tier and len(kept) > 1:
        embedder = ctx.embedder
        result = embedder.embed_images([frames[i].path for i in kept])
        vectors = result.vectors
        embed_cost = ctx.meter(result.usage, embedder.cost_model, unit="embeddings")

        forced_flags = [frames[i].forced for i in kept]
        survivors, threshold, iterations = _search_threshold(
            kept,
            vectors,
            forced_flags,
            start=cfg.embed_similarity_threshold,
            floor=cfg.threshold_search_floor,
            budget=budget,
            max_iterations=cfg.threshold_search_max_iterations,
        )
        position_of = {index: position for position, index in enumerate(kept)}
        vectors = vectors[[position_of[i] for i in survivors]] if survivors else vectors[:0]
        kept = survivors

    after_embedding = len(kept)

    # -- tier 3 ------------------------------------------------------------
    if cfg.use_sharpness_tier:
        kept = _tier3_sharpest(frames, kept, radius=cfg.sharpness_cluster_radius, anchors=anchors)

    # -- force-retain, applied after budgeting -----------------------------
    forced_indices = {i for i, frame in enumerate(frames) if frame.forced}
    final = sorted(set(kept) | forced_indices)

    budget_applied = iterations > 0
    if len(final) > budget:
        overflow = len(final) - budget
        forced_count = len(forced_indices)
        ctx.warn(
            f"retained {len(final)} keyframes against a budget of {budget} "
            f"({forced_count} are force-retained first/last/shot-boundary frames and are "
            f"never dropped). {overflow} over. Nothing was truncated — lower "
            "select.threshold_search_floor or sample_fps if this is too many."
        )

    # -- materialise -------------------------------------------------------
    keyframes: list[Keyframe] = []
    for order, index in enumerate(final):
        frame = frames[index]
        kf_id = keyframe_id(order)
        dest = ctx.rundir.keyframes / f"{kf_id}.jpg"
        shutil.copy2(frame.path, dest)
        next_index = final[order + 1] if order + 1 < len(final) else len(frames)
        keyframes.append(
            Keyframe(
                id=kf_id,
                index=order,
                path=ctx.rundir.relative(dest),
                timestamp_s=frame.candidate.timestamp_s,
                shot_boundary=frame.candidate.is_shot_start,
                sharpness=round(frame.sharpness, 3),
                forced=frame.forced,
                represents_candidates=list(range(index, next_index)),
            )
        )

    mean_distance = _mean_adjacent_distance(vectors) if vectors.size else None

    record = SelectionRecord(
        keyframes=keyframes,
        candidates_in=len(sample.candidates),
        after_phash=after_phash,
        after_embedding=after_embedding,
        phash_threshold=cfg.phash_threshold,
        embed_similarity_threshold=round(threshold, 6),
        embed_threshold_search_iterations=iterations,
        budget_applied=budget_applied,
        effective_budget=budget,
        mean_retained_distance=mean_distance,
        note=None,
    )
    ctx.write_model(ctx.rundir.selection_record, record)

    if not ctx.config.pipeline.emit.keep_candidates:
        # Candidates are disposable after S4. Keyframes never are.
        ctx.rundir.clear_candidates()

    ctx.info(
        f"selected {len(keyframes)} keyframes from {len(sample.candidates)} candidates "
        f"(phash -> {after_phash}, embedding -> {after_embedding}, budget {budget})"
    )

    return {
        "candidates_in": len(sample.candidates),
        "after_phash": after_phash,
        "after_embedding": after_embedding,
        "keyframes": len(keyframes),
        "effective_budget": budget,
        "embed_similarity_threshold": round(threshold, 6),
        "threshold_search_iterations": iterations,
        "mean_retained_distance": mean_distance,
        "cost_usd": embed_cost,
    }
