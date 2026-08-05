"""Test fixtures.

Videos are GENERATED with ffmpeg rather than committed. 07_OPEN_QUESTIONS.md Q2
asks Mehdi for a real 60-90 second self-recorded reference video, and that is a
different thing from these: these are synthetic, tiny, deterministic, and exist
to exercise the awkward paths. When the real reference video arrives it slots in
alongside them; it does not replace them.

The awkward inputs are fixtures here because each one is real and each one will
otherwise be discovered in production:

  silent          no audio stream at all
  rotated         90-degree rotation metadata, not rotated pixels
  vfr             variable frame rate
  tiny            shorter than one sampling interval
  static          no shot boundaries whatsoever
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from stratum.config.loader import load_config
from stratum.config.models import Config, Posture
from stratum.sources.protocols import VideoRef

FFMPEG = shutil.which("ffmpeg")
requires_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not on PATH")


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        [FFMPEG or "ffmpeg", "-nostdin", "-y", *args],
        capture_output=True,
        text=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "ffmpeg fixture generation failed:\n" + "\n".join(proc.stderr.splitlines()[-15:])
        )


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("videos")


def _testsrc(dest: Path, *, duration: float, fps: int, size: str = "320x240") -> None:
    """A moving test pattern. It genuinely changes frame to frame, so pHash and
    embedding dedup have something real to work on."""
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:rate={fps}:duration={duration}",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(dest),
        ]
    )


@pytest.fixture(scope="session")
def video_with_audio(fixtures_dir: Path) -> Path:
    """The ordinary case: moving picture, a tone on the audio track, three cuts."""
    dest = fixtures_dir / "with_audio.mp4"
    if dest.exists():
        return dest
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=6",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            "-shortest",
            str(dest),
        ]
    )
    return dest


@pytest.fixture(scope="session")
def video_silent(fixtures_dir: Path) -> Path:
    """No audio stream at all. A normal path, not an error."""
    dest = fixtures_dir / "silent.mp4"
    if dest.exists():
        return dest
    _testsrc(dest, duration=4, fps=10)
    return dest


@pytest.fixture(scope="session")
def video_rotated(fixtures_dir: Path) -> Path:
    """Rotation metadata, not rotated pixels — the phone-footage trap."""
    dest = fixtures_dir / "rotated.mp4"
    if dest.exists():
        return dest
    source = fixtures_dir / "_rot_src.mp4"
    if not source.exists():
        _testsrc(source, duration=3, fps=10, size="320x180")
    # `-display_rotation` is an INPUT option, and its sign is the counter-clockwise
    # angle. -90 here writes a display matrix reporting rotation -90, which is what
    # phone footage carrying the legacy `rotate=90` tag looks like to ffprobe, and
    # what S0 must read back as 90 degrees clockwise.
    _run(["-display_rotation", "-90", "-i", str(source), "-c", "copy", str(dest)])
    return dest


@pytest.fixture(scope="session")
def video_vfr(fixtures_dir: Path) -> Path:
    """Variable frame rate: container fps and average fps disagree."""
    dest = fixtures_dir / "vfr.mp4"
    if dest.exists():
        return dest
    source = fixtures_dir / "_vfr_src.mp4"
    if not source.exists():
        _testsrc(source, duration=4, fps=30)
    # Keep four frames out of every ten and pass the ORIGINAL timestamps through.
    # The result is bursts separated by gaps: r_frame_rate stays 30 while the
    # average drops to about 12.6, which is the divergence S0 has to notice.
    _run(
        [
            "-i",
            str(source),
            "-vf",
            r"select=lt(mod(n\,10)\,4)",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(dest),
        ]
    )
    return dest


@pytest.fixture(scope="session")
def video_tiny(fixtures_dir: Path) -> Path:
    """Shorter than one sampling interval at the default 2 fps."""
    dest = fixtures_dir / "tiny.mp4"
    if dest.exists():
        return dest
    _testsrc(dest, duration=0.3, fps=30)
    return dest


@pytest.fixture(scope="session")
def video_static(fixtures_dir: Path) -> Path:
    """A single unchanging colour. No shot boundaries, nothing to deduplicate
    against — the degenerate case for S3 and S4."""
    dest = fixtures_dir / "static.mp4"
    if dest.exists():
        return dest
    _run(
        [
            "-f",
            "lavfi",
            "-i",
            "color=c=darkgreen:size=320x240:rate=10:duration=5",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            str(dest),
        ]
    )
    return dest


@pytest.fixture
def mock_config(tmp_path: Path) -> Config:
    """Mock posture, sampling turned up so short fixtures still yield candidates."""
    return load_config(
        None,
        posture=Posture.MOCK,
        environ={},
        overrides={
            "paths": {"runs_dir": str(tmp_path / "runs")},
            "pipeline": {
                "sample": {"sample_fps": 4.0, "long_edge_px": 256},
                "select": {"max_keyframes_total": 40, "max_keyframes_per_minute": 240},
            },
        },
    )


@pytest.fixture
def ref_for(request: pytest.FixtureRequest):
    def _make(path: Path, **kwargs: object) -> VideoRef:
        return VideoRef(path=path, **kwargs)  # type: ignore[arg-type]

    return _make
