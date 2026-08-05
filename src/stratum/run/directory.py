"""The run directory.

Layout is fixed by 01_ARCHITECTURE.md S0. The content hash is the run id, so the
same file processed twice resolves to the same directory and short-circuits.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

LARGE_FILE_THRESHOLD_BYTES = 512 * 1024 * 1024
HASH_CHUNK_BYTES = 8 * 1024 * 1024


def content_hash(path: Path) -> str:
    """SHA-256 of the file, or of the first and last 8 MB plus size for large files.

    The large-file shortcut keeps ingest from reading eight gigabytes to compute an
    identifier. Prefixing the mode into the digest input means the two schemes can
    never collide with each other.
    """
    size = path.stat().st_size
    digest = hashlib.sha256()
    if size <= LARGE_FILE_THRESHOLD_BYTES:
        digest.update(b"full:")
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    else:
        digest.update(f"ends:{size}:".encode())
        with path.open("rb") as handle:
            digest.update(handle.read(HASH_CHUNK_BYTES))
            handle.seek(-HASH_CHUNK_BYTES, 2)
            digest.update(handle.read(HASH_CHUNK_BYTES))
    return digest.hexdigest()


@dataclass(frozen=True)
class RunDir:
    """Every path the pipeline writes to. No stage constructs a path by hand."""

    root: Path
    run_id: str

    @classmethod
    def for_run(cls, runs_dir: Path, run_id: str) -> RunDir:
        return cls(root=(runs_dir / run_id).resolve(), run_id=run_id)

    # -- files --------------------------------------------------------------

    @property
    def manifest(self) -> Path:
        return self.root / "manifest.json"

    @property
    def ledger(self) -> Path:
        return self.root / "ledger.db"

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def video_meta(self) -> Path:
        return self.root / "video_meta.json"

    @property
    def sample_record(self) -> Path:
        return self.root / "sample.json"

    @property
    def selection_record(self) -> Path:
        return self.root / "selection.json"

    @property
    def modality_profile(self) -> Path:
        return self.root / "modality_profile.json"

    @property
    def transitions(self) -> Path:
        return self.root / "transitions.jsonl"

    @property
    def entities(self) -> Path:
        return self.root / "entities.json"

    @property
    def traverse_state(self) -> Path:
        """The running state carried between S6 calls. Separate from
        transitions.jsonl so that file stays a clean, inspectable record of the
        canonical transitions and nothing else."""
        return self.root / "traverse_state.json"

    @property
    def synthesis(self) -> Path:
        return self.root / "synthesis.json"

    @property
    def artifact_json(self) -> Path:
        return self.root / "artifact.json"

    @property
    def artifact_md(self) -> Path:
        return self.root / "artifact.md"

    @property
    def chunks(self) -> Path:
        return self.root / "chunks.jsonl"

    @property
    def log(self) -> Path:
        return self.root / "run.log"

    # -- directories --------------------------------------------------------

    @property
    def candidates(self) -> Path:
        """Sampled frames. Disposable after S4."""
        return self.root / "candidates"

    @property
    def keyframes(self) -> Path:
        """Retained frames. These are output, not scratch — deleting them to save
        space destroys most of the artifact's value."""
        return self.root / "keyframes"

    # -- operations ---------------------------------------------------------

    def create(self) -> RunDir:
        self.root.mkdir(parents=True, exist_ok=True)
        self.candidates.mkdir(exist_ok=True)
        self.keyframes.mkdir(exist_ok=True)
        return self

    def exists(self) -> bool:
        return self.manifest.is_file()

    def relative(self, path: Path) -> str:
        """Paths inside the artifact are relative to the run root and use forward
        slashes, so an artifact copied between machines still resolves."""
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()

    def clear_candidates(self) -> None:
        if self.candidates.exists():
            shutil.rmtree(self.candidates)
        self.candidates.mkdir(exist_ok=True)

    def clear_keyframes(self) -> None:
        if self.keyframes.exists():
            shutil.rmtree(self.keyframes)
        self.keyframes.mkdir(exist_ok=True)
