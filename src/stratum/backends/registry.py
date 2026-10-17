"""Backend construction from config.

This is the only module that knows which provider strings exist. Stages receive
an object satisfying a protocol and can never tell what is behind it.

Real adapters are imported lazily inside their branch, so `import stratum` never
drags in torch and the cloud posture never needs a local ASR model on disk.
"""

from __future__ import annotations

from stratum.backends.mock import MockEmbedder, MockReasoner, MockTranscriber
from stratum.backends.protocols import Embedder, Reasoner, Transcriber
from stratum.config.models import Config
from stratum.config.pricing import PricingTable
from stratum.errors import ConfigError, DependencyMissing

MOCK_PROVIDERS = frozenset({"mock"})

# Providers named in the posture presets that have no adapter yet. Naming them
# here means the error says "Phase 2" instead of "unknown provider", which is the
# difference between a roadmap and a bug.
PLANNED_PROVIDERS: dict[str, str] = {
    "local-siglip": "Phase 1 — local vision encoder adapter",
    "ollama": "Phase 2 — local VLM via the OpenAI-compatible endpoint",
}


def _unpinned(slot: str, posture: str) -> ConfigError:
    return ConfigError(
        f"no {slot} provider is configured for posture {posture!r}.\n"
        f"The {slot} slot is deliberately unpinned in postures.yaml — choosing one "
        "here would silently resolve an open question (07_OPEN_QUESTIONS.md Q1 for "
        "the reasoner).\n"
        f"Set backends.{slot}.provider and .model in your config, or run with "
        "`--posture mock`."
    )


def _not_built(slot: str, provider: str) -> DependencyMissing:
    phase = PLANNED_PROVIDERS.get(provider)
    if phase:
        return DependencyMissing(
            f"{slot} provider {provider!r} is planned but not implemented yet ({phase}). "
            "Run with `--posture mock` until it lands."
        )
    return DependencyMissing(f"unknown {slot} provider {provider!r}")


def build_transcriber(config: Config, pricing: PricingTable) -> Transcriber:
    cfg = config.backends.transcriber
    provider = cfg.provider
    if not provider:
        raise _unpinned("transcriber", config.posture.value)

    cost_model = pricing.cost_model(provider, cfg.model)

    if provider in MOCK_PROVIDERS:
        return MockTranscriber(
            cost_model,
            model=cfg.model,
            words_per_minute=float(cfg.options.get("words_per_minute", 90.0)),
            silent=bool(cfg.options.get("silent", False)),
        )

    if provider == "faster-whisper":
        # Imported here, not at module scope: a cloud-posture install must never
        # need the ASR extra on disk.
        from stratum.backends.faster_whisper import FasterWhisperTranscriber

        if not cfg.model:
            raise ConfigError(
                "backends.transcriber.model is unset. Pin a whisper model id in "
                "config (postures.yaml has one, with a verified_on date)."
            )
        options = dict(cfg.options)
        return FasterWhisperTranscriber(
            cost_model,
            model=cfg.model,
            device=cfg.device,
            compute_type=cfg.compute_type,
            beam_size=cfg.beam_size,
            language=cfg.language,
            vad_filter=bool(options.pop("vad_filter", True)),
            cpu_threads=int(options.pop("cpu_threads", 0)),
            options=options,
        )

    raise _not_built("transcriber", provider)


def build_embedder(config: Config, pricing: PricingTable) -> Embedder:
    cfg = config.backends.embedder
    provider = cfg.provider
    if not provider:
        raise _unpinned("embedder", config.posture.value)

    cost_model = pricing.cost_model(provider, cfg.model)

    if provider in MOCK_PROVIDERS:
        return MockEmbedder(cost_model, model=cfg.model, grid=int(cfg.options.get("grid", 16)))

    raise _not_built("embedder", provider)


def build_reasoner(config: Config, pricing: PricingTable) -> Reasoner:
    cfg = config.backends.reasoner
    provider = cfg.provider
    if not provider:
        raise _unpinned("reasoner", config.posture.value)

    cost_model = pricing.cost_model(provider, cfg.model)

    if provider in MOCK_PROVIDERS:
        return MockReasoner(
            cost_model,
            model=cfg.model,
            fail_every_n=int(cfg.options.get("fail_every_n", 0)),
        )

    raise _not_built("reasoner", provider)
