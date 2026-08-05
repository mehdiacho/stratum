"""`stratum` - the CLI.

Four commands: `process`, `inspect`, `cost`, `config`. No UI, no server, no
hosted anything - Phase 1-3 is a local CLI by decision.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path
from typing import Annotated

import typer

from stratum import __version__
from stratum.config.loader import find_config_file, load_config
from stratum.config.models import Config
from stratum.config.pricing import PricingTable
from stratum.errors import StratumError
from stratum.pipeline.orchestrator import RunResult, process
from stratum.run.directory import RunDir
from stratum.run.ledger import CostLedger
from stratum.run.manifest import STAGE_NAMES, STAGE_ORDER, Manifest
from stratum.sources.local import default_source
from stratum.util import format_duration

app = typer.Typer(
    name="stratum",
    help="Video to structured, provenance-tagged, agent-ready context.",
    add_completion=False,
)


def _use_utf8_streams() -> None:
    """Windows defaults stdout to the ANSI codepage, which turns an em dash in a
    warning into a replacement character. Messages come from all over the package,
    so fix the stream once rather than policing every string."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # A redirected or already-detached stream simply keeps what it has.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")


_use_utf8_streams()

err = typer.style


def _setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=sys.stderr,
    )


def _fail(message: str) -> None:
    typer.secho(f"error: {message}", fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


def _load(
    config_path: Path | None,
    posture: str | None,
    overrides: dict[str, object] | None = None,
) -> Config:
    try:
        return load_config(config_path, posture=posture, overrides=overrides)
    except StratumError as exc:
        _fail(str(exc))
        raise  # unreachable; keeps type checkers happy


# --------------------------------------------------------------------------
# process
# --------------------------------------------------------------------------


@app.command(name="process")
def process_(
    target: Annotated[
        str, typer.Argument(metavar="PATH", help="A video file, or a folder of them.")
    ],
    config_path: Annotated[
        Path | None, typer.Option("--config", "-c", help="Config file. Default: search upward.")
    ] = None,
    posture: Annotated[
        str | None,
        typer.Option("--posture", "-p", help="mock | hybrid | cloud | local. Overrides config."),
    ] = None,
    runs_dir: Annotated[
        Path | None, typer.Option("--runs-dir", help="Where run directories live.")
    ] = None,
    from_stage: Annotated[
        str | None,
        typer.Option("--from", help="Force a re-run from this stage onward (S0-S8)."),
    ] = None,
    retranscribe: Annotated[
        bool,
        typer.Option("--retranscribe", help="Allow S2 to be redone. It is protected by default."),
    ] = False,
    max_cost: Annotated[
        float | None, typer.Option("--max-cost", help="Override the per-video hard cost cap.")
    ] = None,
    order_by: Annotated[
        str, typer.Option("--order-by", help="For folders: name | mtime.")
    ] = "name",
    recursive: Annotated[bool, typer.Option("--recursive", "-r")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    quiet: Annotated[bool, typer.Option("--quiet", "-q")] = False,
) -> None:
    """Process a video (or every video in a folder) into an artifact."""
    _setup_logging(verbose, quiet)

    overrides: dict[str, object] = {}
    if runs_dir:
        overrides["paths"] = {"runs_dir": str(runs_dir)}
    if max_cost is not None:
        overrides["limits"] = {"max_cost_usd_per_video": max_cost}

    config = _load(config_path, posture, overrides)

    if from_stage and from_stage.upper() not in STAGE_ORDER:
        _fail(f"--from must be one of {', '.join(STAGE_ORDER)}")

    try:
        videos = list(default_source(order_by=order_by, recursive=recursive).resolve(target))
    except StratumError as exc:
        _fail(str(exc))
        return

    typer.echo(
        f"stratum {__version__} | posture {config.posture.value} | "
        f"{len(videos)} video{'s' if len(videos) != 1 else ''}"
    )

    failures = 0
    for index, video in enumerate(videos, start=1):
        typer.secho(f"\n[{index}/{len(videos)}] {video.path.name}", bold=True)
        if not video.has_metadata:
            typer.secho(
                "  no sidecar metadata found. A <video>.json with the caption is the "
                "highest-value input per second of effort in the whole system.",
                fg=typer.colors.YELLOW,
            )
        try:
            result = process(
                video,
                config,
                runs_dir=runs_dir,
                force_from=from_stage.upper() if from_stage else None,
                allow_retranscribe=retranscribe,
            )
        except StratumError as exc:
            failures += 1
            typer.secho(f"  failed: {exc}", fg=typer.colors.RED, err=True)
            continue
        _print_result(result)

    if failures:
        raise typer.Exit(code=1)


def _print_result(result: RunResult) -> None:
    ran = " ".join(result.stages_run) or "(nothing - already complete)"
    typer.echo(f"  run {result.run_id[:12]}  |  stages: {ran}")
    for note in result.notes:
        typer.secho(f"  note: {note}", fg=typer.colors.BLUE)

    if result.cost_by_stage:
        breakdown = "  ".join(
            f"{STAGE_NAMES.get(s, s)} ${usd:.4f}" for s, usd in sorted(result.cost_by_stage.items())
        )
        typer.echo(f"  cost: ${result.cost_usd:.4f}   ({breakdown})")
    else:
        typer.echo(f"  cost: ${result.cost_usd:.4f}")

    typer.echo(f"  time: {result.wall_time_s:.1f}s")

    for warning in result.warnings:
        typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)

    if result.cap_hit:
        typer.secho("  COST CAP HIT - artifact is partial", fg=typer.colors.RED, bold=True)
    elif result.partial:
        typer.secho("  artifact is partial", fg=typer.colors.YELLOW)

    if result.artifact_path.is_file():
        typer.secho(f"  -> {result.artifact_path}", fg=typer.colors.GREEN)
        typer.secho(f"  -> {result.rundir.artifact_md}", fg=typer.colors.GREEN)


# --------------------------------------------------------------------------
# inspect
# --------------------------------------------------------------------------


@app.command()
def inspect(
    target: Annotated[
        str,
        typer.Argument(
            metavar="PATH_OR_RUN_ID",
            help="A video file (probe it) or a run directory / run id (summarise it).",
        ),
    ],
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Probe a video, or summarise an existing run."""
    _setup_logging(False, True)
    config = _load(config_path, None)
    root = (runs_dir or config.paths.runs_dir).expanduser()

    path = Path(target).expanduser()
    candidate_dir = path if path.is_dir() else root / target

    if (candidate_dir / "manifest.json").is_file():
        manifest = Manifest.load(candidate_dir / "manifest.json")
        if as_json:
            typer.echo(json.dumps(manifest.model_dump(mode="json"), indent=2))
            return
        typer.secho(f"run {manifest.run_id}", bold=True)
        typer.echo(f"  source: {manifest.source_path}")
        typer.echo(f"  created: {manifest.created_at.isoformat()}")
        for stage in STAGE_ORDER:
            record = manifest.stages.get(stage)
            if record is None:
                continue
            duration = f"{record.duration_s:.1f}s" if record.duration_s else "-"
            typer.echo(
                f"  {stage} {record.name:<12} {record.status.value:<10} "
                f"{duration:>8}  ${record.cost_usd:.4f}"
            )
            if record.stats:
                typer.echo(f"       {json.dumps(record.stats, default=str)}")
        for warning in manifest.warnings:
            typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)
        return

    if not path.is_file():
        _fail(f"{target} is neither a video file nor a run directory under {root}")

    from stratum.pipeline.stages.s0_ingest import probe_video

    try:
        meta = probe_video(path)
    except StratumError as exc:
        _fail(str(exc))
        return

    if as_json:
        typer.echo(json.dumps(meta.model_dump(mode="json"), indent=2))
        return

    typer.secho(meta.filename, bold=True)
    typer.echo(f"  duration     {format_duration(meta.duration_s)} ({meta.duration_s:.2f}s)")
    stored = f"{meta.width}x{meta.height}"
    displayed = f"{meta.display_width}x{meta.display_height}"
    typer.echo(f"  resolution   {stored} (displayed {displayed})")
    typer.echo(f"  fps          avg {meta.fps_avg:.3f}, container {meta.fps_container or 0:.3f}")
    if meta.is_variable_frame_rate:
        typer.secho(
            "               variable frame rate - the average is authoritative",
            fg=typer.colors.YELLOW,
        )
    typer.echo(f"  rotation     {meta.rotation}deg")
    audio = f"yes - {meta.audio_codec or '?'}" if meta.has_audio_stream else "none (silent path)"
    typer.echo(f"  audio        {audio}")
    typer.echo(f"  video codec  {meta.video_codec}")
    typer.echo(f"  hash         {meta.content_hash[:16]}...")
    typer.echo(f"  size         {meta.size_bytes / 1_000_000:.1f} MB")


# --------------------------------------------------------------------------
# cost
# --------------------------------------------------------------------------


@app.command()
def cost(
    run: Annotated[
        str | None, typer.Argument(help="Run id or directory. Omit to total every run.")
    ] = None,
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    runs_dir: Annotated[Path | None, typer.Option("--runs-dir")] = None,
    detail: Annotated[bool, typer.Option("--detail", help="List every metered call.")] = False,
) -> None:
    """What runs actually cost. Cost that is not visible is cost that is not controlled."""
    _setup_logging(False, True)
    config = _load(config_path, None)
    root = (runs_dir or config.paths.runs_dir).expanduser()

    pricing = PricingTable.load(config.paths.pricing_table)
    stale = pricing.staleness_warning(config.limits.pricing_staleness_warn_days)
    if stale:
        typer.secho(f"warning: {stale}", fg=typer.colors.YELLOW)

    if not root.is_dir():
        typer.echo(f"no runs directory at {root}")
        return

    directories = (
        [root / run if not Path(run).is_dir() else Path(run)]
        if run
        else sorted(p for p in root.iterdir() if (p / "manifest.json").is_file())
    )

    grand_total = 0.0
    for directory in directories:
        if not (directory / "manifest.json").is_file():
            typer.secho(f"no run at {directory}", fg=typer.colors.RED, err=True)
            continue
        manifest = Manifest.load(directory / "manifest.json")
        rundir = RunDir(root=directory, run_id=manifest.run_id)
        ledger = CostLedger(rundir.ledger)
        totals = ledger.totals()
        grand_total += totals.usd

        typer.secho(f"{manifest.run_id[:12]}  {Path(manifest.source_path).name}", bold=True)
        typer.echo(
            f"  ${totals.usd:.4f}   {totals.calls} calls   "
            f"{totals.tokens_in:,} in / {totals.tokens_out:,} out   "
            f"{totals.images} images   {totals.audio_seconds / 60:.1f} audio-min"
        )
        for stage, stage_totals in sorted(ledger.by_stage().items()):
            typer.echo(
                f"    {stage} {STAGE_NAMES.get(stage, ''):<12} ${stage_totals.usd:.4f} "
                f"({stage_totals.calls} calls)"
            )
        if detail:
            for row in ledger.iter_calls():
                typer.echo(
                    f"      {row['stage']} {row['unit'] or '':<10} "
                    f"{row['provider']}/{row['model'] or '*'} "
                    f"in={row['tokens_in']} out={row['tokens_out']} img={row['images']} "
                    f"${row['usd']:.6f}"
                )

    if len(directories) > 1:
        typer.secho(f"\ntotal across {len(directories)} runs: ${grand_total:.4f}", bold=True)


# --------------------------------------------------------------------------
# config
# --------------------------------------------------------------------------


@app.command()
def config(
    config_path: Annotated[Path | None, typer.Option("--config", "-c")] = None,
    posture: Annotated[str | None, typer.Option("--posture", "-p")] = None,
    fingerprints: Annotated[
        bool, typer.Option("--fingerprints", help="Show the per-stage config fingerprints.")
    ] = False,
) -> None:
    """Show the effective configuration, after posture, file and environment merge."""
    _setup_logging(False, True)
    found = config_path or find_config_file()
    cfg = _load(config_path, posture)

    typer.secho(f"posture: {cfg.posture.value}", bold=True)
    typer.echo(f"config file: {found or '(none found - defaults and posture preset only)'}")

    for slot, backend in (
        ("transcriber", cfg.backends.transcriber),
        ("embedder", cfg.backends.embedder),
        ("reasoner", cfg.backends.reasoner),
    ):
        provider = backend.provider or typer.style("UNPINNED", fg=typer.colors.YELLOW)
        typer.echo(f"  {slot:<12} {provider} / {backend.model or '-'}")
        if backend.api_key_env:
            import os

            present = (
                "set"
                if os.environ.get(backend.api_key_env)
                else typer.style("NOT SET", fg=typer.colors.RED)
            )
            typer.echo(f"               key from ${backend.api_key_env} ({present})")

    if fingerprints:
        typer.echo("\nstage fingerprints:")
        for stage in STAGE_ORDER:
            typer.echo(f"  {stage} {STAGE_NAMES[stage]:<12} {cfg.stage_fingerprint(stage)}")

    typer.echo("\neffective config:")
    typer.echo(json.dumps(cfg.snapshot(), indent=2))


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: Annotated[bool, typer.Option("--version", help="Print the version and exit.")] = False,
) -> None:
    if version:
        typer.echo(f"stratum {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit()


if __name__ == "__main__":  # pragma: no cover
    app()
