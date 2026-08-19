"""
BlackBox Recorder — Universal KISS Flight Recorder for AI Agents.

Zero external dependencies (Python 3.10+ stdlib only).
"""

from ai_blackbox_recorder.config import BlackBoxConfig
from ai_blackbox_recorder.export import export_all_to_jsonl, export_trace_to_jsonl, render_trace_tree
from ai_blackbox_recorder.span import Span, SpanKind
from ai_blackbox_recorder.storage import TraceStorage
from ai_blackbox_recorder.tracer import (
    Tracer,
    configure,
    get_tracer,
    set_session_id,
    set_trace_id,
    span,
    trace,
    tracer,
)

__version__ = "0.8.0"

__all__ = [
    "tracer",
    "Tracer",
    "configure",
    "get_tracer",
    "trace",
    "span",
    "Span",
    "SpanKind",
    "BlackBoxConfig",
    "TraceStorage",
    "set_session_id",
    "set_trace_id",
    "export_trace_to_jsonl",
    "export_all_to_jsonl",
    "render_trace_tree",
    "__version__",
]
