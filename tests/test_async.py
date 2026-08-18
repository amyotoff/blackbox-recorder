"""
Tests for asynchronous agent workflows, async @trace, and concurrent asyncio tasks.
"""

import asyncio
import pytest
from blackbox_recorder.span import SpanKind
from tests.conftest import PERSONA_ALICE, PERSONA_BOB


@pytest.mark.asyncio
async def test_async_trace_decorator(test_tracer):
    @test_tracer.trace(name="async_llm_call", kind=SpanKind.LLM)
    async def fake_llm(prompt: str) -> str:
        await asyncio.sleep(0.01)
        return f"TODAY IS 2026-08-18. LLM Response to '{prompt}'"

    @test_tracer.trace(name="alice_async_agent", kind=SpanKind.AGENT)
    async def alice_agent(user_query: str) -> str:
        res = await fake_llm(user_query)
        return res

    test_tracer.set_session_id(PERSONA_ALICE["tg_id"])
    result = await alice_agent("What is the date?")
    assert "2026-08-18" in result

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
    assert len(traces) == 1

    spans = test_tracer.storage.get_trace(traces[0]["trace_id"])
    assert len(spans) == 2

    root = next(s for s in spans if s["name"] == "alice_async_agent")
    child = next(s for s in spans if s["name"] == "async_llm_call")

    assert root["parent_span_id"] is None
    assert child["parent_span_id"] == root["span_id"]
    assert child["session_id"] == PERSONA_ALICE["tg_id"]


@pytest.mark.asyncio
async def test_concurrent_agent_tasks(test_tracer):
    @test_tracer.trace(name="worker_task", kind=SpanKind.CHAIN)
    async def execute_subtask(task_name: str, duration: float) -> str:
        await asyncio.sleep(duration)
        return f"Completed {task_name}"

    @test_tracer.trace(name="bob_parallel_agent", kind=SpanKind.AGENT)
    async def parallel_agent():
        t1 = execute_subtask("fetch_news", 0.02)
        t2 = execute_subtask("fetch_weather", 0.01)
        results = await asyncio.gather(t1, t2)
        return results

    test_tracer.set_session_id(PERSONA_BOB["tg_id"])
    res = await parallel_agent()
    assert len(res) == 2

    test_tracer.flush()
    traces = test_tracer.storage.list_traces(session_id=PERSONA_BOB["tg_id"])
    assert len(traces) == 1

    spans = test_tracer.storage.get_trace(traces[0]["trace_id"])
    assert len(spans) == 3

    root = next(s for s in spans if s["name"] == "bob_parallel_agent")
    subtasks = [s for s in spans if s["name"] == "worker_task"]
    assert len(subtasks) == 2
    for st in subtasks:
        assert st["parent_span_id"] == root["span_id"]
