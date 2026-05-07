"""ai_batch_queue — Task #513 §K.3 batching helper.

Coalesces small bursts of embed / formatter calls into a single
upstream request. The pattern is "queue + flush on size or wall-clock
window" — the same shape used by `providers.chunk_embedder`'s 48-chunk
batch, generalized so the formatter and translation hot paths can
share it without copy-paste.

A batcher is configured with:
  * `flush_size`        — flush as soon as the queue reaches this many items.
  * `flush_window_ms`   — flush at most this long after the FIRST queued item.
  * `flush_fn`          — async callable accepting `list[item] -> list[result]`.

`submit(item)` returns an `asyncio.Future` that resolves with the
per-item result. Callers `await` the future like a normal async call;
internally many callers join the same flush.

This module deliberately holds NO upstream dependencies — the actual
embedder / formatter is passed in via `flush_fn` so the batcher can be
unit-tested without touching Workers-AI / Vertex.

Cost rationale (Task #513 §K.3):
  * Embed batching: 48-chunk Workers-AI request costs the same as a
    1-chunk request (per-request flat fee); batching cuts request
    count by ~50× during bulk re-embed runs.
  * Formatter batching: Vertex Gemini 2.5 Flash bills per-call; merging
    3-5 short polish requests into one structured prompt cuts the
    per-request overhead.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Generic, List, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

FlushFn = Callable[[List[T]], Awaitable[List[R]]]


@dataclass
class _PendingItem(Generic[T, R]):
    item: T
    future: "asyncio.Future[R]"


class AsyncBatcher(Generic[T, R]):
    """Coalesce concurrent `submit(item)` calls into batched upstream
    invocations.

    Thread-safe within a single asyncio loop; do NOT share an instance
    across event loops. Construct one batcher per (flush_fn, loop) pair
    at module import time.
    """

    def __init__(
        self,
        flush_fn: FlushFn,
        *,
        flush_size: int = 16,
        flush_window_ms: int = 50,
        name: str = "batcher",
    ) -> None:
        self._flush_fn = flush_fn
        self._flush_size = max(1, int(flush_size))
        self._flush_window = max(1, int(flush_window_ms)) / 1000.0
        self._name = name
        self._pending: List[_PendingItem[T, R]] = []
        self._lock = asyncio.Lock()
        self._timer_task: Optional[asyncio.Task] = None

    async def submit(self, item: T) -> R:
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[R]" = loop.create_future()
        async with self._lock:
            self._pending.append(_PendingItem(item, fut))
            if len(self._pending) >= self._flush_size:
                # Size trigger — flush now in a detached task so submit() returns.
                batch = self._drain_locked()
                asyncio.create_task(self._run_flush(batch))
            elif self._timer_task is None or self._timer_task.done():
                self._timer_task = asyncio.create_task(self._timer_flush())
        return await fut

    def _drain_locked(self) -> List[_PendingItem[T, R]]:
        batch = self._pending
        self._pending = []
        return batch

    async def _timer_flush(self) -> None:
        try:
            await asyncio.sleep(self._flush_window)
        except asyncio.CancelledError:
            return
        async with self._lock:
            if not self._pending:
                return
            batch = self._drain_locked()
        await self._run_flush(batch)

    async def _run_flush(self, batch: List[_PendingItem[T, R]]) -> None:
        if not batch:
            return
        items = [p.item for p in batch]
        t0 = time.perf_counter()
        try:
            results = await self._flush_fn(items)
        except Exception as e:
            logger.warning("[batcher:%s] flush failed: %s", self._name, e)
            for p in batch:
                if not p.future.done():
                    p.future.set_exception(e)
            return
        dur_ms = int((time.perf_counter() - t0) * 1000)
        logger.debug(
            "[batcher:%s] flushed %d items in %d ms", self._name, len(batch), dur_ms,
        )
        if len(results) != len(batch):
            err = RuntimeError(
                f"batcher result length mismatch: got {len(results)} for "
                f"{len(batch)} items"
            )
            for p in batch:
                if not p.future.done():
                    p.future.set_exception(err)
            return
        for p, r in zip(batch, results):
            if not p.future.done():
                p.future.set_result(r)


__all__ = ["AsyncBatcher", "FlushFn"]
