"""Local ASR via `faster-whisper` (CTranslate2).

The first real backend. Chosen for Phase 1 because it runs on CPU at acceptable
speed, emits word-level timestamps, needs no API key and costs nothing — which
makes it the one model stage that is not blocked on 07_OPEN_QUESTIONS Q1.

Word timestamps are requested unconditionally. They are not optional: S6 aligns a
transcript window to each keyframe, and segment granularity is too coarse when
keyframes land two seconds apart.
"""

from __future__ import annotations

import logging
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

from stratum.backends.protocols import TranscribeResult
from stratum.config.pricing import CostModel, Usage
from stratum.errors import BackendError, DependencyMissing
from stratum.schema.transcript import (
    SpeechQuality,
    Transcript,
    TranscriptSegment,
    TranscriptWord,
)

log = logging.getLogger("stratum")

_CUDA_DLLS_REGISTERED = False


def _register_cuda_dll_dirs() -> None:
    """Windows only: put the pip-installed CUDA libraries on the DLL search path.

    `nvidia-cublas-cu12` and `nvidia-cudnn-cu12` drop their DLLs inside
    site-packages rather than anywhere the loader looks by default, so
    CTranslate2 reports that it cannot load cudnn and falls back to CPU without
    saying why. Doing this explicitly is the difference between a large speedup
    and a silent one.

    Both mechanisms are needed, and neither is redundant:

    * `os.add_dll_directory` covers dependencies resolved when a DLL is loaded
      with the modern `LOAD_LIBRARY_SEARCH_*` flags — that is how cudnn gets
      found at model-load time.
    * `PATH` covers CTranslate2's own lazy loading, which calls plain
      `LoadLibraryA("cublas64_12.dll")` at the first matmul. That API predates
      `add_dll_directory` and ignores it entirely, which is why the model loads
      happily on cuda/float16 and then dies mid-transcription with
      "Library cublas64_12.dll is not found or cannot be loaded".
    """
    global _CUDA_DLLS_REGISTERED
    if _CUDA_DLLS_REGISTERED or sys.platform != "win32":
        return
    try:
        import nvidia  # type: ignore[import-not-found]
    except ImportError:
        return
    add_dll_directory = getattr(os, "add_dll_directory", None)
    if add_dll_directory is None:  # pragma: no cover - non-Windows
        return

    directories: list[str] = []
    for root in nvidia.__path__:
        for entry in sorted(Path(root).glob("*/bin")):
            if not entry.is_dir():
                continue
            directories.append(str(entry))
            try:
                add_dll_directory(str(entry))
            except OSError:  # pragma: no cover - path vanished
                continue

    if directories:
        # Prepend, so a stale system-wide CUDA install cannot shadow the versions
        # this venv was resolved against.
        existing = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join([*directories, existing]) if existing else os.pathsep.join(directories)
        log.debug("registered CUDA dll directories: %s", ", ".join(directories))

    _CUDA_DLLS_REGISTERED = True


def _cuda_available() -> bool:
    try:
        import ctranslate2  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # a broken CUDA install must not be fatal
        return False


def _segment_confidence(avg_logprob: float | None) -> float | None:
    """Whisper reports mean log-probability per segment; exp() puts it on a 0-1
    scale.

    Treat it as ordinal, not calibrated — which is what the schema says about
    confidence everywhere else.
    """
    if avg_logprob is None:
        return None
    try:
        return max(0.0, min(1.0, math.exp(avg_logprob)))
    except (OverflowError, ValueError):
        return None


class FasterWhisperTranscriber:
    """Satisfies the `Transcriber` protocol. Imports the library lazily so a
    cloud-posture install never needs it on disk."""

    def __init__(
        self,
        cost_model: CostModel,
        *,
        model: str,
        device: str = "auto",
        compute_type: str = "int8",
        beam_size: int = 5,
        language: str | None = None,
        vad_filter: bool = True,
        cpu_threads: int = 0,
        options: dict[str, Any] | None = None,
    ) -> None:
        self._cost_model = cost_model
        self._model_name = model
        self.requested_device = device
        self.compute_type = compute_type
        self.beam_size = beam_size
        self.language = language
        self.vad_filter = vad_filter
        self.cpu_threads = cpu_threads
        self.options = dict(options or {})
        self._model: Any = None
        self._device: str = "cpu"

    @property
    def name(self) -> str:
        return f"faster-whisper:{self._model_name}@{self._device}"

    @property
    def cost_model(self) -> CostModel:
        return self._cost_model

    # -- model loading -----------------------------------------------------

    def _resolve_device(self) -> tuple[str, str]:
        """Returns (device, compute_type), degrading rather than failing.

        A machine with a GPU that CTranslate2 cannot actually use is common —
        missing cuDNN, a driver mismatch, too little VRAM. Falling back to CPU
        with a warning beats refusing to transcribe.
        """
        device = self.requested_device
        compute_type = self.compute_type

        if device == "auto":
            _register_cuda_dll_dirs()
            device = "cuda" if _cuda_available() else "cpu"
        elif device == "cuda":
            _register_cuda_dll_dirs()

        if compute_type == "auto":
            # What 02_BACKEND_ABSTRACTION.md's config comment already promises:
            # "int8 on CPU, float16 on GPU". Making it a real value beats leaving
            # every caller to remember it.
            compute_type = "float16" if device == "cuda" else "int8"
        elif device == "cpu" and compute_type in ("float16", "fp16"):
            # CTranslate2 does not do float16 on CPU, and int8 is the right CPU
            # default anyway.
            log.info("compute_type float16 is not usable on CPU; using int8")
            compute_type = "int8"

        return device, compute_type

    def _build(self, device: str, compute_type: str) -> Any:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        return WhisperModel(
            self._model_name,
            device=device,
            compute_type=compute_type,
            cpu_threads=self.cpu_threads,
        )

    def _load(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import faster_whisper  # noqa: F401
        except ImportError as exc:
            raise DependencyMissing(
                "faster-whisper is not installed. It is an optional extra: "
                "uv pip install -e .[asr]  (add ,cuda for GPU support)."
            ) from exc

        device, compute_type = self._resolve_device()

        try:
            model = self._build(device, compute_type)
        except Exception as exc:  # fall back before giving up
            if device != "cuda":
                raise BackendError(
                    f"could not load faster-whisper model {self._model_name!r} on "
                    f"{device}/{compute_type}: {exc}"
                ) from exc
            log.warning(
                "CUDA load failed (%s); falling back to CPU int8. Transcription will "
                "be slower but will complete.",
                exc,
            )
            device, compute_type = "cpu", "int8"
            try:
                model = self._build(device, compute_type)
            except Exception as inner:
                raise BackendError(
                    f"could not load faster-whisper model {self._model_name!r}: {inner}"
                ) from inner

        self._device = device
        self.compute_type = compute_type
        self._model = model
        log.info("faster-whisper %s loaded on %s/%s", self._model_name, device, compute_type)
        return model

    # -- transcription -----------------------------------------------------

    def transcribe(self, wav_path: Path, *, language: str | None = None) -> TranscribeResult:
        model = self._load()
        started = time.monotonic()

        try:
            segments_iter, info = model.transcribe(
                str(wav_path),
                language=language or self.language,
                beam_size=self.beam_size,
                word_timestamps=True,
                vad_filter=self.vad_filter,
                **self.options,
            )
        except Exception as exc:  # surfaced as a stage failure
            raise BackendError(f"faster-whisper failed on {wav_path.name}: {exc}") from exc

        segments: list[TranscriptSegment] = []
        words: list[TranscriptWord] = []

        # `segments_iter` is a generator; transcription happens as it is consumed.
        for index, seg in enumerate(segments_iter):
            segments.append(
                TranscriptSegment(
                    id=index,
                    start=round(float(seg.start), 3),
                    end=round(float(seg.end), 3),
                    text=(seg.text or "").strip(),
                    confidence=_segment_confidence(getattr(seg, "avg_logprob", None)),
                )
            )
            for word in getattr(seg, "words", None) or []:
                token = (word.word or "").strip()
                if not token:
                    continue
                probability = getattr(word, "probability", None)
                words.append(
                    TranscriptWord(
                        start=round(float(word.start), 3),
                        end=round(float(word.end), 3),
                        text=token,
                        confidence=(
                            max(0.0, min(1.0, float(probability)))
                            if probability is not None
                            else None
                        ),
                    )
                )

        duration = float(getattr(info, "duration", 0.0) or 0.0)
        transcript = Transcript(
            language=getattr(info, "language", None),
            duration_s=duration,
            has_speech=bool(words or segments),
            speech_quality=SpeechQuality.GOOD if words else SpeechQuality.NONE,
            segments=segments,
            words=words,
            backend=self.name,
            model=self._model_name,
        )

        if not words and segments:
            log.warning(
                "%s returned segments but no word timings; S6 alignment will be coarse",
                self.name,
            )

        return TranscribeResult(
            transcript=transcript,
            usage=Usage(
                audio_seconds=duration,
                wall_time_s=time.monotonic() - started,
            ),
        )
