"""
Data models and Span representation for blackbox-recorder.
"""

from __future__ import annotations

import dataclasses
import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Optional


class SpanKind(str, Enum):
    """OpenInference-aligned Span Kinds for agent workflows."""
    AGENT = "AGENT"         # Root orchestrator / reasoning agent loop
    LLM = "LLM"             # LLM API invocation (prompt + completion)
    TOOL = "TOOL"           # Tool / Function execution
    RETRIEVER = "RETRIEVER"  # Vector DB / Search / RAG retriever
    CHAIN = "CHAIN"         # Deterministic multi-step workflow / step

    def __str__(self) -> str:
        return self.value


@dataclasses.dataclass
class Span:
    """
    Represents an atomic execution span within an AI agent's execution tree.

    For LLM spans (kind=LLM), use set_llm_io() to record prompt, completion,
    thinking/chain-of-thought, token usage, and model metadata in a structured way.
    """
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    name: str = "unnamed_span"
    kind: SpanKind | str = SpanKind.CHAIN
    session_id: Optional[str] = None
    start_time: float = dataclasses.field(default_factory=time.time)
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    inputs: Optional[Dict[str, Any]] = None
    outputs: Optional[Any] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = dataclasses.field(default_factory=dict)
    metrics: Dict[str, Any] = dataclasses.field(default_factory=dict)

    @classmethod
    def create(
        cls,
        name: str,
        kind: SpanKind | str = SpanKind.CHAIN,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None,
        session_id: Optional[str] = None,
        inputs: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "Span":
        kind_str = kind.value if isinstance(kind, Enum) else str(kind)
        return cls(
            trace_id=trace_id or uuid.uuid4().hex,
            span_id=uuid.uuid4().hex,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind_str,
            session_id=session_id,
            start_time=time.time(),
            inputs=inputs or {},
            metadata=metadata or {},
            metrics={},
        )

    def snapshot(self) -> "Span":
        """
        Copy the span with independent mutable containers.

        Used to hand an in-progress span to the writer thread: the caller keeps
        mutating the original (set_metadata, set_llm_io) while the copy is
        serialized, so the two never touch the same dict.
        """
        return dataclasses.replace(
            self,
            inputs=dict(self.inputs) if isinstance(self.inputs, dict) else self.inputs,
            metadata=dict(self.metadata),
            metrics=dict(self.metrics),
        )

    def finish(
        self,
        output: Any = None,
        error: Optional[str | Exception] = None,
        metrics: Optional[Dict[str, Any]] = None,
    ) -> "Span":
        """Marks the span as complete and computes latency."""
        self.end_time = time.time()
        self.duration_ms = round((self.end_time - self.start_time) * 1000, 2)
        if output is not None:
            self.outputs = output
        if error is not None:
            self.error = str(error)
        if metrics:
            self.metrics.update(metrics)
        return self

    # ---- LLM-specific structured recording ----

    def set_llm_io(
        self,
        prompt: Any = None,
        completion: Any = None,
        thinking: Optional[str] = None,
        system_prompt: Optional[str] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
        total_tokens: Optional[int] = None,
        stop_reason: Optional[str] = None,
        tool_calls: Optional[List[Dict[str, Any]]] = None,
    ) -> "Span":
        """
        Record structured LLM call details: prompt, completion, chain-of-thought,
        token usage, tool calls, and model parameters.

        Usage:
            span.set_llm_io(
                prompt="What is the capital of France?",
                completion="The capital of France is Paris.",
                thinking="User asks about geography...",
                system_prompt="You are a helpful assistant.",
                model="gemini-2.5-flash",
                prompt_tokens=12, completion_tokens=8,
                tool_calls=[{"name": "search", "args": {"q": "France capital"}}],
            )
        """
        if self.inputs is None:
            self.inputs = {}
        if self.metadata is None:
            self.metadata = {}

        # ---- Inputs: what went into the LLM ----
        if system_prompt is not None:
            self.inputs["system_prompt"] = system_prompt
        if prompt is not None:
            self.inputs["prompt"] = prompt
        if messages is not None:
            self.inputs["messages"] = messages

        # ---- Outputs: what came out of the LLM ----
        llm_output: Dict[str, Any] = {}
        if completion is not None:
            llm_output["completion"] = completion
        if thinking is not None:
            llm_output["thinking"] = thinking
        if tool_calls is not None:
            llm_output["tool_calls"] = tool_calls
        if stop_reason is not None:
            llm_output["stop_reason"] = stop_reason
        if llm_output:
            self.outputs = llm_output

        # ---- Model metadata ----
        if model is not None:
            self.metadata["model"] = model
        if temperature is not None:
            self.metadata["temperature"] = temperature

        # ---- Token metrics ----
        if prompt_tokens is not None:
            self.metrics["prompt_tokens"] = prompt_tokens
        if completion_tokens is not None:
            self.metrics["completion_tokens"] = completion_tokens
        if total_tokens is not None:
            self.metrics["total_tokens"] = total_tokens
        elif prompt_tokens is not None and completion_tokens is not None:
            self.metrics["total_tokens"] = prompt_tokens + completion_tokens

        return self

    def set_tool_call(
        self,
        tool_name: Optional[str] = None,
        tool_args: Optional[Dict[str, Any]] = None,
        tool_result: Any = None,
    ) -> "Span":
        """
        Record structured tool call details.

        Usage:
            span.set_tool_call(
                tool_name="web_search",
                tool_args={"query": "weather in Paris"},
                tool_result={"temp": 22, "condition": "sunny"},
            )
        """
        if tool_name is not None:
            self.metadata["tool_name"] = tool_name
        if tool_args is not None:
            self.inputs = tool_args
        if tool_result is not None:
            self.outputs = tool_result
        return self

    # ---- Generic helpers ----

    def set_metadata(self, key: str, value: Any) -> "Span":
        self.metadata[key] = value
        return self

    def set_metric(self, key: str, value: Any) -> "Span":
        self.metrics[key] = value
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Convert span to serializable dict."""
        kind_str = self.kind.value if isinstance(self.kind, Enum) else str(self.kind)
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "kind": kind_str,
            "session_id": self.session_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "metadata": self.metadata,
            "metrics": self.metrics,
        }
