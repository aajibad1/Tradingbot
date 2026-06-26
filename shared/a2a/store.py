"""In-memory Task store — backs ``tasks/get`` and ``tasks/cancel``.

These agents are mostly synchronous (a task is created already-completed), so the
store is a bounded LRU-ish dict, not a durable queue. A per-process cap keeps a
long-running agent from leaking memory on task history; eviction is oldest-first."""
from __future__ import annotations

import threading
from collections import OrderedDict

from .models import Task


class InMemoryTaskStore:
    """Thread-safe, size-capped Task store keyed by task id."""

    def __init__(self, max_tasks: int = 1024) -> None:
        self._max = max_tasks
        self._lock = threading.Lock()
        self._tasks: "OrderedDict[str, Task]" = OrderedDict()

    def put(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.id] = task
            self._tasks.move_to_end(task.id)
            while len(self._tasks) > self._max:
                self._tasks.popitem(last=False)

    def get(self, task_id: str) -> Task | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None:
                self._tasks.move_to_end(task_id)
            return task
