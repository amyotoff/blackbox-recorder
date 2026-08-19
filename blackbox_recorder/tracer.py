"""
Core Tracer implementation for BlackBox Recorder.
Provides @trace decorator, context manager, and non-blocking background queue.
"""

from __future__ import annotations

import atexit
import contextlib
import contextvars
import functools
import inspect
import queue
import threading
import time
import weakref
from typing import Any, Callable, Dict, Iterator, List, Optional, Union

from blackbox_recorder.config import BlackBoxConfig
from blackbox_recorder.span import Span, SpanKind
from blackbox_recorder.storage import TraceStorage

# Context variables for automatic hierarchy tracking across sync & async
_CURRENT_SPAN: contextvars.ContextVar[Optional[Span]] = contextvars.ContextVar("blackbox_current_span", default=None)
_CURRENT_TRACE_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("blackbox_current_trace_id", default=None)
_CURRENT_SESSION_ID: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "blackbox_current_session_id", default=None
)


class Tracer:
    """
    Main Flight Recorder instance for tracking agent reasoning and tool executions.
    """

    def __init__(self, config: Optional[BlackBoxConfig] = None):
        self.config = config or BlackBoxConfig()
        self.storage = TraceStorage(config=self.config)
        self._queue: queue.Queue[Optional[Span]] = queue.Queue()
        self._stop_event = threading.Event()
        self._close_lock = threading.Lock()
        self._closed = False
        self._last_cleanup = time.time()

        # Start non-blocking daemon worker thread
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True, name="blackbox-worker")
        self._worker_thread.start()

        _LIVE_TRACERS.add(self)

    def _worker_loop(self) -> None:
        """Background thread worker to flush queued spans to SQLite in batches."""
        while not self._stop_event.is_set():
            batch: List[Span] = []
            try:
                # Wait for at least one item or timeout
                item = self._queue.get(timeout=self.config.flush_interval_seconds)
                if item is None:  # Sentinel to shutdown
                    self._queue.task_done()
                    break
                batch.append(item)

                # Drain more items if available (up to batch_size)
                while len(batch) < self.config.batch_size:
                    try:
                        extra = self._queue.get_nowait()
                        if extra is None:
                            self._queue.task_done()
                            break
                        batch.append(extra)
                    except queue.Empty:
                        break
            except queue.Empty:
                pass

            if batch:
                try:
                    self.storage.insert_batch(batch)
                except Exception as exc:
                    # Never crash the host application on logging errors
                    print(f"[blackbox-recorder] Storage insert error: {exc}")
                finally:
                    for _ in batch:
                        self._queue.task_done()

            # Check if periodic cleanup is due
            now = time.time()
            if now - self._last_cleanup > (self.config.cleanup_interval_hours * 3600):
                try:
                    self.storage.cleanup_all()
                except Exception:
                    pass
                self._last_cleanup = now

    def flush(self, timeout: Optional[float] = None) -> bool:
        """
        Wait until all pending spans in the queue are written to SQLite.

        Returns False if the wait timed out or the writer thread is gone — in
        both cases some spans may still be unwritten.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                if not self._worker_thread.is_alive():
                    return False
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                self._queue.all_tasks_done.wait(timeout=0.05)
        return True

    def close(self, timeout: Optional[float] = None) -> None:
        """Gracefully stop worker thread and flush all data. Safe to call twice."""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True

        self.flush(timeout=self.config.flush_timeout_seconds if timeout is None else timeout)
        self._stop_event.set()
        self._queue.put(None)
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        _LIVE_TRACERS.discard(self)

    # ---------------- Context Helpers ----------------

    @staticmethod
    def set_session_id(session_id: str) -> None:
        """Set current session/user ID in context for all subsequent child spans."""
        _CURRENT_SESSION_ID.set(session_id)

    @staticmethod
    def get_session_id() -> Optional[str]:
        """Get current session ID from context."""
        return _CURRENT_SESSION_ID.get()

    @staticmethod
    def set_trace_id(trace_id: str) -> None:
        """Explicitly set active trace_id in context."""
        _CURRENT_TRACE_ID.set(trace_id)

    @staticmethod
    def get_trace_id() -> Optional[str]:
        """Get active trace_id from context."""
        return _CURRENT_TRACE_ID.get()

    @staticmethod
    def get_current_span() -> Optional[Span]:
        """Get current active span from context."""
        return _CURRENT_SPAN.get()

    # ---------------- Core Span Lifecycle ----------------

    def _start_span(
        self,
        name: str,
        kind: Union[SpanKind, str] = SpanKind.CHAIN,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> tuple[Span, contextvars.Token, Optional[contextvars.Token]]:
        parent = _CURRENT_SPAN.get()
        trace_id = _CURRENT_TRACE_ID.get() or (parent.trace_id if parent else None)
        session_id = _CURRENT_SESSION_ID.get() or (parent.session_id if parent else None)

        span = Span.create(
            name=name,
            kind=kind,
            trace_id=trace_id,
            parent_span_id=parent.span_id if parent else None,
            session_id=session_id,
            inputs=inputs,
            metadata=metadata,
        )

        trace_token = None
        if not _CURRENT_TRACE_ID.get():
            trace_token = _CURRENT_TRACE_ID.set(span.trace_id)

        span_token = _CURRENT_SPAN.set(span)

        if self.config.record_open_spans:
            # Persist the span the moment it starts, with end_time still NULL.
            # If the process is killed before the function returns, this row is
            # what tells you where it died; the finished row replaces it by span_id.
            self._queue.put(span.snapshot())

        return span, span_token, trace_token

    def _end_span(
        self,
        span: Span,
        outputs: Any = None,
        error: Optional[str | Exception] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.config.enabled:
            return
        span.finish(output=outputs, error=error, metrics=metrics)
        self._queue.put(span)

    # ---------------- Decorator API ----------------

    def trace(
        self,
        name: Optional[str] = None,
        kind: Union[SpanKind, str] = SpanKind.CHAIN,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Callable:
        """
        Decorator to record execution of sync or async functions.
        """
        def decorator(func: Callable) -> Callable:
            return _build_traced(func, lambda: self, name or func.__name__, kind, metadata)

        return decorator

    # ---------------- Context Manager API ----------------

    @contextlib.contextmanager
    def span(
        self,
        name: str,
        kind: Union[SpanKind, str] = SpanKind.CHAIN,
        metadata: Optional[Dict[str, Any]] = None,
        inputs: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Span]:
        """
        Context manager for manual block-level tracing.
        """
        if not self.config.enabled:
            # Yield dummy span
            yield Span.create(name=name, kind=kind)
            return

        span, span_token, trace_token = self._start_span(
            name=name,
            kind=kind,
            metadata=metadata,
            inputs=inputs,
        )
        try:
            yield span
            self._end_span(span)
        except Exception as exc:
            self._end_span(span, error=exc)
            raise
        finally:
            _CURRENT_SPAN.reset(span_token)
            if trace_token:
                _CURRENT_TRACE_ID.reset(trace_token)

    @staticmethod
    def _extract_inputs(func: Callable, args: tuple, kwargs: dict) -> Dict[str, Any]:
        """Safely extract function arguments into key-value dictionary."""
        inputs: Dict[str, Any] = {}
        try:
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            for k, v in bound.arguments.items():
                inputs[k] = v
        except Exception:
            if kwargs:
                inputs.update(kwargs)
            if args:
                inputs["__args__"] = list(args)
        return inputs


def _build_traced(
    func: Callable,
    resolve_tracer: Callable[[], Tracer],
    op_name: str,
    kind: Union[SpanKind, str],
    metadata: Optional[Dict[str, Any]],
) -> Callable:
    """
    Wrap func so every call is recorded by whatever tracer resolve_tracer returns.

    The tracer is resolved per call rather than per decoration, so decorating a
    function never forces the default tracer (and its database file) into
    existence while the module is still being imported.
    """
    if inspect.iscoroutinefunction(func):
        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            recorder = resolve_tracer()
            if not recorder.config.enabled:
                return await func(*args, **kwargs)

            inputs = recorder._extract_inputs(func, args, kwargs)
            with recorder.span(op_name, kind=kind, metadata=metadata, inputs=inputs) as active:
                result = await func(*args, **kwargs)
                active.outputs = result
                return result

        return async_wrapper

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        recorder = resolve_tracer()
        if not recorder.config.enabled:
            return func(*args, **kwargs)

        inputs = recorder._extract_inputs(func, args, kwargs)
        with recorder.span(op_name, kind=kind, metadata=metadata, inputs=inputs) as active:
            result = func(*args, **kwargs)
            active.outputs = result
            return result

    return sync_wrapper


# ---------------- Default tracer (lazily created) ----------------

_LIVE_TRACERS: "weakref.WeakSet[Tracer]" = weakref.WeakSet()
_default_tracer: Optional[Tracer] = None
_default_config: Optional[BlackBoxConfig] = None
_default_lock = threading.Lock()


def _flush_on_exit() -> None:
    """Drain every live tracer before the interpreter tears down daemon threads."""
    for recorder in list(_LIVE_TRACERS):
        try:
            recorder.close()
        except Exception:
            pass


# Registering the hook is free; it opens no file and starts no thread.
atexit.register(_flush_on_exit)


def configure(config: BlackBoxConfig) -> None:
    """
    Set the configuration for the default tracer.

    Call this before the first traced function runs. If the default tracer is
    already running it is closed (flushing its buffer) and rebuilt on next use.
    """
    global _default_config, _default_tracer
    with _default_lock:
        _default_config = config
        previous, _default_tracer = _default_tracer, None
    if previous is not None:
        previous.close()


def get_tracer() -> Tracer:
    """Return the process-wide default tracer, creating it on first use."""
    global _default_tracer
    if _default_tracer is None:
        with _default_lock:
            if _default_tracer is None:
                _default_tracer = Tracer(config=_default_config)
    return _default_tracer


def trace(
    name: Optional[str] = None,
    kind: Union[SpanKind, str] = SpanKind.CHAIN,
    metadata: Optional[Dict[str, Any]] = None,
) -> Callable:
    """Decorator recording sync or async calls into the default tracer."""
    def decorator(func: Callable) -> Callable:
        return _build_traced(func, get_tracer, name or func.__name__, kind, metadata)

    return decorator


def span(
    name: str,
    kind: Union[SpanKind, str] = SpanKind.CHAIN,
    metadata: Optional[Dict[str, Any]] = None,
    inputs: Optional[Dict[str, Any]] = None,
) -> Any:
    """Context manager recording a block into the default tracer."""
    return get_tracer().span(name, kind=kind, metadata=metadata, inputs=inputs)


# Context helpers touch only contextvars, so they need no tracer at all.
set_session_id = Tracer.set_session_id
set_trace_id = Tracer.set_trace_id


class _DefaultTracerProxy:
    """
    Attribute-forwarding stand-in for the default tracer.

    Keeps `from blackbox_recorder import tracer` free of side effects: the real
    Tracer, its SQLite file and its worker thread appear on first attribute use.
    """

    __slots__ = ()

    def __getattr__(self, item: str) -> Any:
        return getattr(get_tracer(), item)

    def __repr__(self) -> str:
        state = "active" if _default_tracer is not None else "not started"
        return f"<blackbox default tracer: {state}>"


tracer = _DefaultTracerProxy()
