"""Seed lifecycle for the adversarial evaluation harness (Phase 41.5 / AI-001).

Every seeding channel in ``golden/adversarial.json`` is a side effect that must
be undone before the next case runs: ``knowledge_seed`` writes an article that
later cases can retrieve, ``attachment_seed`` uploads a file.  The harness used
to undo them on a best-effort basis -- the cleanup loop sat *after* the message
loop, so an early return skipped it entirely, and the retire response was never
inspected.  A seed that survives stays retrievable, and four cases in the set
share the identical message (``配送一般多久能到``), so one leaked poisoned
article turned three *later* cases red while naming none of them.

A safety gate is allowed to fail; it is not allowed to fail quietly, or to blame
the wrong case.  So this lifecycle is fail-closed:

* :class:`Cleanup` pairs a human-readable label with an ``undo`` that raises
  :class:`SeedRetireError` when the side effect is still present afterwards --
  "the request returned 200" is not evidence, only "the article reads back
  inactive" is.
* :class:`SeedLedger` runs every registered undo, records failures as leaks
  attributed to the owning case, and exposes :attr:`SeedLedger.leaks` so the
  caller can stop: once a seed has leaked, no later result is trustworthy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


class SeedRetireError(RuntimeError):
    """The seeded side effect is still present after its cleanup ran."""


@dataclass(frozen=True)
class Cleanup:
    """One seeding side effect plus the undo that must remove it."""

    label: str
    undo: Callable[[], None]


@dataclass
class SeedLedger:
    """Per-run registry of pending cleanups and observed leaks."""

    _pending: dict[str, list[Cleanup]] = field(default_factory=dict)
    _leaks: list[str] = field(default_factory=list)

    def register(self, case_id: str, cleanup: Cleanup) -> None:
        self._pending.setdefault(case_id, []).append(cleanup)

    @property
    def leaks(self) -> list[str]:
        """Leak descriptions (``<case id>: <label>: <detail>``), oldest first."""
        return list(self._leaks)

    @property
    def pending(self) -> dict[str, int]:
        """Case id -> number of cleanups not retired yet."""
        return {case_id: len(items) for case_id, items in self._pending.items()}

    def retire(self, case_id: str) -> None:
        """Run every cleanup registered by ``case_id`` and record its leaks.

        Never raises: by the time cleanups run the case result already exists, so
        a failure has to be reported through :attr:`leaks` rather than replace
        that result.  Every registered cleanup is attempted even when an earlier
        one fails -- a first failure must not be allowed to hide a second leak.
        """
        for cleanup in self._pending.pop(case_id, []):
            try:
                cleanup.undo()
            except Exception as exc:  # any failure means "cannot prove it is gone"
                self._leaks.append(f"{case_id}: {cleanup.label}: {exc}")
