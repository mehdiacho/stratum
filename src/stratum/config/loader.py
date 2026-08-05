"""Config loading: posture preset, then user file, then environment.

Precedence, lowest to highest:

    model defaults  <  posture preset  <  user YAML  <  STRATUM_* env vars  <  CLI flags

Secrets never participate. `api_key_env` names a variable; the loader never reads
the key itself and the snapshot written into a run directory never contains one.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from stratum.config.models import Config, Posture
from stratum.errors import ConfigError

_POSTURES_FILE = Path(__file__).with_name("postures.yaml")

CONFIG_FILENAMES = ("stratum.yaml", "stratum.yml", ".stratum.yaml")

ENV_PREFIX = "STRATUM_"


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Overlay wins. Nested dicts merge; everything else replaces.

    An explicit `null` in the overlay replaces — that is how a posture preset
    leaves a slot deliberately unpinned.
    """
    out = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def load_postures() -> dict[str, dict[str, Any]]:
    try:
        return yaml.safe_load(_POSTURES_FILE.read_text(encoding="utf-8")) or {}
    except OSError as exc:  # pragma: no cover - packaging failure
        raise ConfigError(f"cannot read posture presets: {exc}") from exc


def _coerce(text: str) -> Any:
    """Env vars arrive as strings. YAML-parse them so numbers and booleans work."""
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return text


def _env_overlay(environ: dict[str, str] | None = None) -> dict[str, Any]:
    """STRATUM_PIPELINE__SAMPLE__SAMPLE_FPS=4 -> {'pipeline': {'sample': {'sample_fps': 4}}}

    Double underscore is the nesting separator, because single underscores appear
    inside key names.
    """
    env = environ if environ is not None else dict(os.environ)
    overlay: dict[str, Any] = {}
    for raw_key, raw_value in env.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        path = raw_key[len(ENV_PREFIX) :].lower().split("__")
        if not path or not path[0]:
            continue
        cursor = overlay
        for part in path[:-1]:
            nxt = cursor.setdefault(part, {})
            if not isinstance(nxt, dict):
                nxt = {}
                cursor[part] = nxt
            cursor = nxt
        cursor[path[-1]] = _coerce(raw_value)
    return overlay


def find_config_file(start: Path | None = None) -> Path | None:
    """Walk up from `start` looking for a config file. Stops at the filesystem root."""
    cursor = (start or Path.cwd()).resolve()
    for directory in (cursor, *cursor.parents):
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_config(
    path: Path | None = None,
    *,
    posture: Posture | str | None = None,
    overrides: dict[str, Any] | None = None,
    search_from: Path | None = None,
    environ: dict[str, str] | None = None,
) -> Config:
    """Build the effective config.

    `posture` given explicitly wins over the one named in the file, because it
    arrives from a CLI flag.
    """
    user_raw: dict[str, Any] = {}
    config_path = path or find_config_file(search_from)
    if path is not None and not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    if config_path and config_path.is_file():
        try:
            user_raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise ConfigError(f"cannot parse {config_path}: {exc}") from exc
        if not isinstance(user_raw, dict):
            raise ConfigError(f"{config_path} must contain a YAML mapping at the top level")

    env_raw = _env_overlay(environ)

    chosen = posture or env_raw.get("posture") or user_raw.get("posture") or Posture.MOCK
    posture_name = chosen.value if isinstance(chosen, Posture) else str(chosen)
    postures = load_postures()
    if posture_name not in postures:
        raise ConfigError(f"unknown posture {posture_name!r}; known: {', '.join(sorted(postures))}")

    merged: dict[str, Any] = {"posture": posture_name}
    merged = _deep_merge(merged, postures[posture_name])
    merged = _deep_merge(merged, user_raw)
    merged = _deep_merge(merged, env_raw)
    if overrides:
        merged = _deep_merge(merged, overrides)
    merged["posture"] = posture_name

    try:
        return Config.model_validate(merged)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration:\n{exc}") from exc


def resolve_api_key(env_name: str | None, *, environ: dict[str, str] | None = None) -> str | None:
    """Read a key from the environment by name. The value is never logged, never
    written to a run directory, and never included in a config snapshot."""
    if not env_name:
        return None
    env = environ if environ is not None else dict(os.environ)
    return env.get(env_name) or None
