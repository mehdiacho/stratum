"""Prompt loading.

Prompts live in versioned files under `prompts/`, never as string literals. They
change more than the code does and the changes must be diffable.

File format: a leading comment block (what it is for, what it must return, its
known failure mode), then a `## SYSTEM` section and a `## USER` section. The USER
section is a Jinja2 template.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, StrictUndefined

from stratum.errors import ConfigError

_PACKAGE_PROMPTS = Path(__file__).resolve().parents[1] / "prompts"
_REPO_PROMPTS = Path(__file__).resolve().parents[3] / "prompts"

# In a source checkout the prompts live at the repo root, where they are easy to
# find and diff. A built wheel force-includes the same directory into the package.
BUNDLED_PROMPTS_DIR = _REPO_PROMPTS if _REPO_PROMPTS.is_dir() else _PACKAGE_PROMPTS

_SECTION = re.compile(r"^##\s+(SYSTEM|USER)\s*$", re.MULTILINE)

_env = Environment(
    undefined=StrictUndefined,  # a missing variable is a bug, not an empty string
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
    autoescape=False,
)


@dataclass(frozen=True)
class Prompt:
    name: str
    system: str
    user_template: str

    def render(self, **context: Any) -> tuple[str, str]:
        return self.system, _env.from_string(self.user_template).render(**context).strip()


def prompts_dir(override: str | None = None) -> Path:
    if override:
        path = Path(override).expanduser()
        if not path.is_dir():
            raise ConfigError(f"prompts.directory does not exist: {path}")
        return path
    return BUNDLED_PROMPTS_DIR


@lru_cache(maxsize=32)
def _read(path: Path) -> Prompt:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read prompt {path}: {exc}") from exc

    matches = list(_SECTION.finditer(text))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections[match.group(1)] = text[start:end].strip()

    missing = {"SYSTEM", "USER"} - sections.keys()
    if missing:
        raise ConfigError(
            f"prompt {path.name} is missing section(s) {sorted(missing)}; "
            "expected '## SYSTEM' and '## USER'"
        )

    return Prompt(name=path.name, system=sections["SYSTEM"], user_template=sections["USER"])


def load_prompt(directory: Path, filename: str) -> Prompt:
    return _read(directory / filename)
