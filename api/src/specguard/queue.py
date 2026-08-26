"""The name the API enqueues under, and the worker registers.

One constant, in one module both sides import. It exists because the two sides had
drifted: the API enqueued ``"run_check"`` while arq had registered the function under
its own ``__name__``, ``run_check_job``. Nothing failed loudly — the job was accepted,
the worker never saw a function by that name, and every check sat at ``queued``.

A shared constant makes that class of mismatch a typo the interpreter catches rather
than a silence someone has to notice.
"""

from __future__ import annotations

#: arq registers a task under its function's ``__name__``, so this is that name.
WORKER_FUNCTION = "run_check_job"
