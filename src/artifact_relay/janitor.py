"""Background cleanup.

Three jobs, all idempotent and safe to run concurrently with serving:

1. delete artifacts whose ``expires_at`` has passed;
2. delete artifact directories with no metadata row (a crash between ``os.replace`` and the
   ``INSERT``), but only once they are older than :data:`ORPHAN_GRACE_SECONDS` — a directory
   that has *just* appeared may be a publish in flight, and deleting it would destroy a live
   artifact's bytes;
3. delete abandoned staging directories under ``tmp/``.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from artifact_relay.storage import ArtifactStore, is_valid_artifact_id

logger = logging.getLogger("artifact_relay.janitor")

ORPHAN_GRACE_SECONDS = 300
STAGING_MAX_AGE_SECONDS = 3600


@dataclass(slots=True)
class SweepResult:
    expired: int = 0
    orphans: int = 0
    staging: int = 0

    @property
    def total(self) -> int:
        return self.expired + self.orphans + self.staging


def _age_seconds(path_mtime: float, now: float) -> float:
    return now - path_mtime


def sweep(store: ArtifactStore, now: datetime | None = None) -> SweepResult:
    moment = now or datetime.now(UTC)
    wall = moment.timestamp()
    result = SweepResult()

    for artifact_id in store.expired_ids(moment):
        if store.delete(artifact_id):
            result.expired += 1

    known = store.known_ids()
    for entry in store.artifacts_dir.iterdir():
        if not entry.is_dir() or entry.name in known:
            continue
        if not is_valid_artifact_id(entry.name):
            continue
        if _age_seconds(entry.stat().st_mtime, wall) < ORPHAN_GRACE_SECONDS:
            continue  # possibly a publish in flight
        shutil.rmtree(entry, ignore_errors=True)
        result.orphans += 1

    for entry in store.tmp_dir.iterdir():
        if not entry.is_dir():
            continue
        if _age_seconds(entry.stat().st_mtime, wall) < STAGING_MAX_AGE_SECONDS:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        result.staging += 1

    return result


async def run_janitor(store: ArtifactStore, interval_seconds: float, stop: asyncio.Event) -> None:
    """Sweep every ``interval_seconds`` until ``stop`` is set. Never raises.

    The wait comes *first*: :func:`startup_sweep` has already run by the time this task
    starts, so sweeping immediately would only duplicate it.
    """
    while not stop.is_set():
        with suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_seconds)
        if stop.is_set():
            return
        try:
            result = await asyncio.to_thread(sweep, store)
            if result.total:
                logger.info(
                    "janitor swept",
                    extra={
                        "expired": result.expired,
                        "orphans": result.orphans,
                        "staging": result.staging,
                    },
                )
        except Exception:
            logger.exception("janitor sweep failed")


def startup_sweep(store: ArtifactStore) -> SweepResult:
    """Catch up on anything that expired while the process was down."""
    result = sweep(store)
    if result.total:
        logger.info(
            "startup sweep",
            extra={
                "expired": result.expired,
                "orphans": result.orphans,
                "staging": result.staging,
            },
        )
    return result
