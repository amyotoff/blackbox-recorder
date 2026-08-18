"""
Tests for core Tracer functionality (synchronous, context manager, hierarchy).
"""

import time
import pytest
from blackbox_recorder.span import SpanKind
from tests.conftest import PERSONA_ALICE, PERSONA_BOB, PERSONA_BENDER


def test_sync_function_tracing(test_tracer):
    @test_tracer.trace(name="weather_tool", kind=SpanKind.TOOL)
    def fetch_weather(city: str) -> str:
        return f"TODAY IS 2026-08-18. Weather in {city} is sunny, 24C."

    test_tracer.set_session_id(PERSONA_ALICE["tg_id"])
    result = fetch_weather("Berlin")
    assert "2026-08-18" in result

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
    assert len(traces) == 1
    trace_id = traces[0]["trace_id"]

    spans = test_tracer.storage.get_trace(trace_id)
    assert len(spans) == 1
    assert spans[0]["name"] == "weather_tool"
    assert spans[0]["kind"] == SpanKind.TOOL
    assert spans[0]["inputs"]["city"] == "Berlin"
    assert "sunny" in spans[0]["outputs"]
    assert spans[0]["session_id"] == PERSONA_ALICE["tg_id"]
    assert spans[0]["duration_ms"] >= 0


def test_automatic_nested_hierarchy(test_tracer):
    @test_tracer.trace(name="calculator", kind=SpanKind.TOOL)
    def multiply(a: int, b: int) -> int:
        return a * b

    @test_tracer.trace(name="llm_reasoning", kind=SpanKind.LLM)
    def llm_step(prompt: str) -> int:
        return multiply(6, 7)

    @test_tracer.trace(name="alice_agent", kind=SpanKind.AGENT)
    def run_agent(query: str) -> str:
        res = llm_step(f"TODAY IS 2026-08-18. Query: {query}")
        return f"Agent answer for Alice: {res}"

    test_tracer.set_session_id(PERSONA_ALICE["tg_id"])
    out = run_agent("Calculate 6 * 7")
    assert "42" in out

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
    assert len(traces) == 1
    
    spans = test_tracer.storage.get_trace(traces[0]["trace_id"])
    assert len(spans) == 3

    # Verify hierarchy
    root_span = next(s for s in spans if s["name"] == "alice_agent")
    llm_span = next(s for s in spans if s["name"] == "llm_reasoning")
    calc_span = next(s for s in spans if s["name"] == "calculator")

    assert root_span["parent_span_id"] is None
    assert llm_span["parent_span_id"] == root_span["span_id"]
    assert calc_span["parent_span_id"] == llm_span["span_id"]


def test_context_manager_span(test_tracer):
    test_tracer.set_session_id(PERSONA_BOB["tg_id"])

    with test_tracer.span("bob_pipeline", kind=SpanKind.CHAIN) as root:
        root.set_metadata("user", PERSONA_BOB["username"])
        
        with test_tracer.span("inner_step", kind=SpanKind.RETRIEVER) as child:
            child.set_metric("tokens", 150)
            child.finish(output={"status": "found 3 docs"})

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_BOB["tg_id"])
    assert len(traces) == 1

    spans = test_tracer.storage.get_trace(traces[0]["trace_id"])
    assert len(spans) == 2
    inner = next(s for s in spans if s["name"] == "inner_step")
    assert inner["metrics"]["tokens"] == 150
    assert inner["outputs"]["status"] == "found 3 docs"


def test_error_recording_in_spans(test_tracer):
    @test_tracer.trace(name="failing_tool", kind=SpanKind.TOOL)
    def broken_tool():
        raise ValueError("Simulated incident: API rate limit exceeded")

    @test_tracer.trace(name="bender_system", kind=SpanKind.AGENT)
    def bender_bot():
        broken_tool()

    test_tracer.set_session_id(PERSONA_BENDER["tg_id"])

    with pytest.raises(ValueError, match="Simulated incident"):
        bender_bot()

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_BENDER["tg_id"], has_error=True)
    assert len(traces) == 1

    spans = test_tracer.storage.get_trace(traces[0]["trace_id"])
    tool_span = next(s for s in spans if s["name"] == "failing_tool")
    assert tool_span["has_error"] == 1
    assert "rate limit exceeded" in tool_span["error"]

    agent_span = next(s for s in spans if s["name"] == "bender_system")
    assert agent_span["has_error"] == 1
