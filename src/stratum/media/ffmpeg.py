"""Direct `ffmpeg` / `ffprobe` calls.

Deliberately not a wrapper library. Wrappers lag ffmpeg releases and turn a clear
stderr message into an opaque exception, and the two traps this module exists to
handle — rotation metadata and variable frame rate — are exactly the places where
that costs you a day.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from stratum.errors import DependencyMissing, ProbeError

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def ensure_available() -> None:
    missing = [tool for tool in (FFMPEG, FFPROBE) if shutil.which(tool) is None]
    if missing:
        raise DependencyMissing(
            f"{' and '.join(missing)} not found on PATH. STRATUM shells out to ffmpeg "
            "directly; install it and try again."
        )


def _run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise DependencyMissing(f"{cmd[0]} not found on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise ProbeError(f"{cmd[0]} timed out after {timeout}s") from exc


def run_checked(cmd: list[str], *, timeout: float | None = None) -> str:
    proc = _run(cmd, timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-12:]
        raise ProbeError(f"{cmd[0]} exited {proc.returncode}\n" + "\n".join(tail))
    return proc.stdout


def probe(path: Path) -> dict[str, Any]:
    raw = run_checked(
        [
            FFPROBE,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        timeout=120,
    )
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe returned unparseable JSON for {path}: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProbeError(f"ffprobe returned {type(parsed).__name__}, not an object, for {path}")
    return dict(parsed)


def parse_rate(value: str | None) -> float | None:
    """'30000/1001' -> 29.97. ffprobe emits rationals, and '0/0' for 'unknown'."""
    if not value:
        return None
    try:
        fraction = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    if fraction.denominator == 0 or fraction == 0:
        return None
    return float(fraction)


@dataclass(frozen=True)
class StreamInfo:
    video: dict[str, Any] | None
    audio: dict[str, Any] | None
    fmt: dict[str, Any]


def split_streams(probe_data: dict[str, Any]) -> StreamInfo:
    video = None
    audio = None
    for stream in probe_data.get("streams", []):
        kind = stream.get("codec_type")
        if kind == "video" and video is None:
            # Skip attached cover art, which ffprobe reports as a video stream.
            if stream.get("disposition", {}).get("attached_pic"):
                continue
            video = stream
        elif kind == "audio" and audio is None:
            audio = stream
    return StreamInfo(video=video, audio=audio, fmt=probe_data.get("format", {}))


def rotation_degrees(video_stream: dict[str, Any]) -> int:
    """Degrees clockwise the picture must be turned to display correctly.

    ffprobe reports this in two places depending on version and container: a
    `rotate` tag, and a `displaymatrix` side-data entry whose value is negative
    for a clockwise rotation. Read both; the side data wins because the tag is
    deprecated and sometimes absent on files that still rotate.
    """
    rotation = 0
    tags = video_stream.get("tags") or {}
    if "rotate" in tags:
        try:
            rotation = int(float(tags["rotate"]))
        except (TypeError, ValueError):
            rotation = 0

    for entry in video_stream.get("side_data_list") or []:
        if "rotation" in entry:
            try:
                rotation = -int(float(entry["rotation"]))
            except (TypeError, ValueError):
                continue
            break

    return rotation % 360


def transpose_filter(rotation: int) -> str | None:
    """The ffmpeg filter that bakes a rotation into the pixels.

    A frame extracted without this comes out sideways, and a vision model given a
    sideways image will describe a sideways world with complete confidence.
    """
    match rotation % 360:
        case 90:
            return "transpose=1"
        case 180:
            return "transpose=1,transpose=1"
        case 270:
            return "transpose=2"
        case _:
            return None


def has_audio_stream(probe_data: dict[str, Any]) -> bool:
    return split_streams(probe_data).audio is not None


def mean_volume_dbfs(path: Path) -> float | None:
    """Mean volume via the `volumedetect` filter.

    Used to decide the silent-video path. Returns None when ffmpeg gave us
    nothing to read, which is treated as "unknown", not as "silent".
    """
    proc = _run(
        [FFMPEG, "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        timeout=600,
    )
    for line in (proc.stderr or "").splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].strip().split()[0])
            except (IndexError, ValueError):
                return None
    return None


def extract_audio(src: Path, dest: Path, *, sample_rate: int = 16000) -> None:
    """16 kHz mono 16-bit WAV. Every ASR backend wants this, and resampling once
    up front stops each backend doing its own."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    run_checked(
        [
            FFMPEG,
            "-nostdin",
            "-y",
            "-i",
            str(src),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-acodec",
            "pcm_s16le",
            "-f",
            "wav",
            str(dest),
        ],
        timeout=3600,
    )


def extract_frames(
    src: Path,
    dest_dir: Path,
    *,
    fps: float,
    long_edge_px: int,
    jpeg_quality: int = 85,
    rotation: int = 0,
    pattern: str = "cand_%06d.jpg",
) -> list[Path]:
    """Uniform coarse decode.

    Never at source frame rate. A 10-minute 120 fps video is 72,000 frames; its
    information content is closer to 80.

    Rotation is handled explicitly: `-noautorotate` turns OFF ffmpeg's own
    display-matrix handling and the transpose filter does it instead. Leaving
    autorotate on and also applying a transpose rotates twice and hands the vision
    model a sideways image — which is the exact failure this stage exists to
    prevent, and which an integration test asserts against.

    Doing it ourselves rather than trusting the default also means the behaviour
    does not change under us when ffmpeg's default does.

    Rotation is applied BEFORE scaling, so the long edge is the long edge of the
    *displayed* image.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)

    filters: list[str] = [f"fps={fps}"]
    rotate = transpose_filter(rotation)
    if rotate:
        filters.append(rotate)
    filters.append(
        f"scale='if(gt(iw,ih),{long_edge_px},-2)':'if(gt(iw,ih),-2,{long_edge_px})'"
        ":force_original_aspect_ratio=decrease"
    )

    # ffmpeg's -q:v is inverted relative to the usual 1-100 quality scale.
    qscale = max(2, min(31, round(31 - (jpeg_quality / 100) * 29)))

    run_checked(
        [
            FFMPEG,
            "-nostdin",
            "-y",
            "-noautorotate",
            "-i",
            str(src),
            "-vf",
            ",".join(filters),
            "-vsync",
            "vfr",
            "-q:v",
            str(qscale),
            "-f",
            "image2",
            str(dest_dir / pattern),
        ],
        timeout=7200,
    )
    return sorted(dest_dir.glob(pattern.replace("%06d", "*")))
