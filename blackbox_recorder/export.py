"""
Export utilities for blackbox-recorder (JSONL, OpenInference, ASCII trees).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from blackbox_recorder.storage import TraceStorage


def export_trace_to_jsonl(storage: TraceStorage, trace_id: str, output_file: str) -> int:
    """Export a single trace to JSONL file."""
    spans = storage.get_trace(trace_id)
    if not spans:
        return 0

    with open(output_file, "w", encoding="utf-8") as f:
        for span in spans:
            f.write(json.dumps(span, ensure_ascii=False) + "\n")

    return len(spans)


def export_all_to_jsonl(storage: TraceStorage, output_file: str, since: Optional[float] = None) -> int:
    """Export all spans to JSONL file."""
    traces = storage.list_traces(limit=10_000, since=since)
    count = 0

    with open(output_file, "w", encoding="utf-8") as f:
        for t in traces:
            spans = storage.get_trace(t["trace_id"])
            for span in spans:
                f.write(json.dumps(span, ensure_ascii=False) + "\n")
                count += 1

    return count


def _truncate(text: str, max_len: int = 200) -> str:
    """Truncate text for tree display, preserving readability."""
    if not text:
        return ""
    text = str(text).replace("\n", "\\n")
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def render_trace_tree(spans: List[Dict[str, Any]], verbose: bool = False) -> str:
    """
    Render hierarchical ASCII execution tree for terminal incident analysis.
    When verbose=True, includes prompts, completions, thinking, tokens, and tool calls.
    """
    if not spans:
        return "Empty trace."

    # Build parent -> children map
    span_map = {s["span_id"]: s for s in spans}
    children_map: Dict[Optional[str], List[str]] = {}

    for s in spans:
        p_id = s.get("parent_span_id")
        if p_id not in children_map:
            children_map[p_id] = []
        children_map[p_id].append(s["span_id"])

    lines: List[str] = []
    root_trace_id = spans[0]["trace_id"]

    # Compute totals
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_errors = 0
    total_incomplete = 0
    for s in spans:
        metrics = s.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        total_prompt_tokens += metrics.get("prompt_tokens", 0)
        total_completion_tokens += metrics.get("completion_tokens", 0)
        if s.get("has_error") or s.get("error"):
            total_errors += 1
        if s.get("end_time") is None:
            total_incomplete += 1

    lines.append(f"📦 Trace ID: {root_trace_id}")
    lines.append(f"⏱️  Total Spans: {len(spans)}")
    if total_prompt_tokens or total_completion_tokens:
        total_tokens = total_prompt_tokens + total_completion_tokens
        lines.append(
            f"🔤 Tokens: {total_prompt_tokens} in → {total_completion_tokens} out ({total_tokens} total)"
        )
    if total_errors:
        lines.append(f"❌ Errors: {total_errors}")
    if total_incomplete:
        lines.append(
            f"⏳ Unfinished: {total_incomplete} "
            "(still running, or the process died before they returned)"
        )
    lines.append("─" * 70)

    def _render_node(span_id: str, prefix: str = "", is_last: bool = True) -> None:
        span = span_map[span_id]
        name = span["name"]
        kind = span["kind"]
        incomplete = span.get("end_time") is None
        duration = "unfinished" if incomplete else f"{span.get('duration_ms')}ms"
        if span.get("has_error") or span.get("error"):
            status_icon = "❌"
        elif incomplete:
            status_icon = "⏳"
        else:
            status_icon = "✅"

        # Token summary inline
        metrics = span.get("metrics") or {}
        if isinstance(metrics, str):
            try:
                metrics = json.loads(metrics)
            except Exception:
                metrics = {}
        token_info = ""
        pt = metrics.get("prompt_tokens")
        ct = metrics.get("completion_tokens")
        if pt is not None or ct is not None:
            token_info = f" 🔤 {pt or 0}→{ct or 0}"

        # Model name inline for LLM spans
        meta = span.get("metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        model_info = ""
        if meta.get("model"):
            model_info = f" [{meta['model']}]"

        connector = "└── " if is_last else "├── "
        lines.append(f"{prefix}{connector}{status_icon} [{kind}] {name}{model_info} ({duration}){token_info}")

        child_prefix = prefix + ("    " if is_last else "│   ")

        if span.get("error"):
            lines.append(f"{child_prefix}⚠️  ERROR: {span['error']}")

        # Verbose: show LLM prompt/completion/thinking/tool_calls
        if verbose:
            inputs = span.get("inputs") or {}
            if isinstance(inputs, str):
                try:
                    inputs = json.loads(inputs)
                except Exception:
                    inputs = {}
            outputs = span.get("outputs") or {}
            if isinstance(outputs, str):
                try:
                    outputs = json.loads(outputs)
                except Exception:
                    outputs = {}

            if kind == "LLM":
                # System prompt
                if inputs.get("system_prompt"):
                    lines.append(f"{child_prefix}📋 System: {_truncate(str(inputs['system_prompt']), 300)}")
                # User prompt / messages
                if inputs.get("prompt"):
                    lines.append(f"{child_prefix}💬 Prompt: {_truncate(str(inputs['prompt']), 500)}")
                if inputs.get("messages"):
                    for msg in inputs["messages"][-3:]:  # last 3 messages
                        role = msg.get("role", "?")
                        content = _truncate(str(msg.get("content", "")), 300)
                        lines.append(f"{child_prefix}   [{role}]: {content}")
                # Thinking / CoT
                if isinstance(outputs, dict) and outputs.get("thinking"):
                    lines.append(f"{child_prefix}🧠 Thinking: {_truncate(str(outputs['thinking']), 500)}")
                # Completion
                if isinstance(outputs, dict) and outputs.get("completion"):
                    lines.append(f"{child_prefix}🤖 Response: {_truncate(str(outputs['completion']), 500)}")
                elif not isinstance(outputs, dict) and outputs:
                    lines.append(f"{child_prefix}🤖 Response: {_truncate(str(outputs), 500)}")
                # Tool calls within LLM response
                if isinstance(outputs, dict) and outputs.get("tool_calls"):
                    for tc in outputs["tool_calls"]:
                        tc_name = tc.get("name", "unknown")
                        tc_args = _truncate(json.dumps(tc.get("args", {}), default=str, ensure_ascii=False), 200)
                        lines.append(f"{child_prefix}🔧 Tool Call: {tc_name}({tc_args})")
                # Stop reason
                if isinstance(outputs, dict) and outputs.get("stop_reason"):
                    lines.append(f"{child_prefix}⏹️  Stop: {outputs['stop_reason']}")
            elif kind == "TOOL":
                # Tool inputs and outputs
                if inputs:
                    tool_name = meta.get("tool_name", "")
                    if tool_name:
                        lines.append(f"{child_prefix}🔧 Tool: {tool_name}")
                    formatted_args = _truncate(json.dumps(inputs, default=str, ensure_ascii=False), 400)
                    lines.append(f"{child_prefix}📥 Args: {formatted_args}")
                if outputs:
                    formatted_res = _truncate(json.dumps(outputs, default=str, ensure_ascii=False), 400)
                    lines.append(f"{child_prefix}📤 Result: {formatted_res}")
            elif kind == "AGENT":
                # Agent reasoning inputs/outputs
                if inputs:
                    formatted_input = _truncate(json.dumps(inputs, default=str, ensure_ascii=False), 400)
                    lines.append(f"{child_prefix}📥 Input: {formatted_input}")
                if isinstance(outputs, dict) and outputs.get("thinking"):
                    lines.append(f"{child_prefix}🧠 Reasoning: {_truncate(str(outputs['thinking']), 500)}")
                if outputs:
                    out_str = outputs
                    if isinstance(outputs, dict) and "thinking" in outputs:
                        out_str = {k: v for k, v in outputs.items() if k != "thinking"}
                    if out_str:
                        formatted_out = _truncate(json.dumps(out_str, default=str, ensure_ascii=False), 400)
                        lines.append(f"{child_prefix}📤 Output: {formatted_out}")
            else:
                # Generic spans
                if inputs:
                    formatted_input = _truncate(json.dumps(inputs, default=str, ensure_ascii=False), 300)
                    lines.append(f"{child_prefix}📥 Input: {formatted_input}")
                if outputs:
                    formatted_output = _truncate(json.dumps(outputs, default=str, ensure_ascii=False), 300)
                    lines.append(f"{child_prefix}📤 Output: {formatted_output}")

        children = children_map.get(span_id, [])
        for i, child_id in enumerate(children):
            child_is_last = (i == len(children) - 1)
            next_prefix = prefix + ("    " if is_last else "│   ")
            _render_node(child_id, prefix=next_prefix, is_last=child_is_last)

    # Find root spans (parent is None or parent not in this trace)
    roots = [s["span_id"] for s in spans if not s.get("parent_span_id") or s.get("parent_span_id") not in span_map]
    if not roots and spans:
        roots = [spans[0]["span_id"]]

    for i, root_id in enumerate(roots):
        _render_node(root_id, prefix="", is_last=(i == len(roots) - 1))

    return "\n".join(lines)
