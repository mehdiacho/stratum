"""Unit tests for the faster-whisper adapter.

None of these load a model or need the extra installed. The point is the
decision logic around the model — device and precision resolution, confidence
mapping, protocol conformance — because that is what silently does the wrong
thing. Whether the model transcribes correctly is Whisper's problem, not ours.
"""

from __future__ import annotations

import math

import pytest

from stratum.backends.faster_whisper import FasterWhisperTranscriber, _segment_confidence
from stratum.backends.protocols import Transcriber
from stratum.config.pricing import CostModel, Rates

FREE = CostModel(provider="faster-whisper", model="large-v3", rates=Rates())


def _transcriber(**kwargs: object) -> FasterWhisperTranscriber:
    params: dict[str, object] = {"model": "large-v3"}
    params.update(kwargs)
    return FasterWhisperTranscriber(FREE, **params)  # type: ignore[arg-type]


def test_satisfies_the_transcriber_protocol() -> None:
    """No pipeline stage may branch on which backend is active, so the adapter
    has to be substitutable for the mock without anything else changing."""
    assert isinstance(_transcriber(), Transcriber)


# -- precision resolution --------------------------------------------------


def test_auto_compute_type_picks_float16_on_gpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: True)
    device, compute = _transcriber(device="auto", compute_type="auto")._resolve_device()
    assert (device, compute) == ("cuda", "float16")


def test_auto_compute_type_picks_int8_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: False)
    device, compute = _transcriber(device="auto", compute_type="auto")._resolve_device()
    assert (device, compute) == ("cpu", "int8")


def test_float16_is_downgraded_on_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """CTranslate2 cannot do float16 on CPU. Silently asking for it would fail at
    model load, long after the config was written."""
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: False)
    device, compute = _transcriber(device="auto", compute_type="float16")._resolve_device()
    assert (device, compute) == ("cpu", "int8")


def test_an_explicit_compute_type_is_respected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: True)
    device, compute = _transcriber(device="auto", compute_type="int8")._resolve_device()
    assert (device, compute) == ("cuda", "int8")


def test_cpu_can_be_forced_even_with_a_gpu_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: True)
    device, _ = _transcriber(device="cpu", compute_type="auto")._resolve_device()
    assert device == "cpu"


def test_cuda_load_failure_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    """A GPU that CTranslate2 cannot actually use is common. Degrading beats
    refusing to transcribe."""
    monkeypatch.setattr("stratum.backends.faster_whisper._cuda_available", lambda: True)
    backend = _transcriber(device="auto", compute_type="auto")

    attempts: list[tuple[str, str]] = []

    def fake_build(device: str, compute_type: str) -> object:
        attempts.append((device, compute_type))
        if device == "cuda":
            raise RuntimeError("cudnn64_9.dll not found")
        return object()

    monkeypatch.setattr(backend, "_build", fake_build)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", object())

    backend._load()

    assert attempts == [("cuda", "float16"), ("cpu", "int8")]
    assert backend._device == "cpu"
    assert "cpu" in backend.name


# -- confidence mapping ----------------------------------------------------


def test_segment_confidence_maps_logprob_into_zero_one() -> None:
    assert _segment_confidence(None) is None
    assert _segment_confidence(0.0) == pytest.approx(1.0)
    assert _segment_confidence(math.log(0.5)) == pytest.approx(0.5)
    # Whisper occasionally emits values that exponentiate out of range.
    assert 0.0 <= (_segment_confidence(-50.0) or 0.0) <= 1.0
    assert (_segment_confidence(5.0) or 0.0) <= 1.0


def test_name_reports_model_and_device() -> None:
    backend = _transcriber()
    assert "large-v3" in backend.name
    assert backend.cost_model.is_free
