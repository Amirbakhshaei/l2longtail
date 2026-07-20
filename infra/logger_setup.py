"""
Centralized loguru setup for the Arbitrum long-tail MEV engine.

This module owns the AI-readable telemetry export. It configures a single
loguru logger with two sinks:

  1. ``sys.stdout``  — human-readable text (console) for live ops.
  2. ``logs/telemetry.jsonl`` — machine-readable JSON Lines (``serialize=True``)
     used for profitability debugging / downstream AI analysis. Rotated at
     100 MB to bound disk on long-running Spaces / VPS deployments.

Language-boundary note: this is the *Python* control-plane logger. The native
Rust extension (``alloy_executor``) does NOT log here, so its text never
corrupts the JSONL schema — only the Python engine emits structured records.
"""
from __future__ import annotations

import sys
from pathlib import Path

import os

from loguru import logger as loguru_logger

# JSONL telemetry sink location. Defaults to a local ``logs/`` dir. On HF
# Spaces the persistent volume is mounted at ``/data``; set TELEMETRY_DIR to
# ``/data/logs`` (or rely on app.py) so the export survives container rebuilds.
_TELEMETRY_DIR = Path(os.getenv("TELEMETRY_DIR", "logs"))
_TELEMETRY_PATH = _TELEMETRY_DIR / "telemetry.jsonl"


def configure_telemetry_logger(level: str = "INFO") -> None:
    """Install the loguru sinks exactly once.

    Safe to call multiple times (e.g. from app.py and a script) — it removes
    any prior loguru handlers first so we never double-emit to stdout.
    """
    loguru_logger.remove()
    # 1) Human-readable console output (shows every record, structured binds
    #    included — the bound fields render after the message).
    loguru_logger.add(
        sys.stdout,
        level=level,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | "
            "{extra[event]: <12} | {message}"
        ),
    )
    # 2) Structured JSONL export for AI / profitability debugging.
    _TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
    loguru_logger.add(
        str(_TELEMETRY_PATH),
        level=level,
        format="{message}",
        serialize=True,
        rotation="100 MB",
        enqueue=True,  # async write; never blocks the hot path
    )


# Configure on import so any module importing this logger is ready.
configure_telemetry_logger()
