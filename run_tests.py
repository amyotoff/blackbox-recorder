"""
Zero-dependency test runner using Python standard library (unittest + asyncio).
Can be executed anywhere without installing pytest: python3 run_tests.py
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from blackbox_recorder.config import BlackBoxConfig
from blackbox_recorder.export import export_all_to_jsonl, export_trace_to_jsonl, render_trace_tree
from blackbox_recorder.span import Span, SpanKind
from blackbox_recorder.storage import TraceStorage
from blackbox_recorder.tracer import Tracer


PERSONA_ALICE = {"tg_id": "111", "username": "alice"}
PERSONA_BOB = {"tg_id": "222", "username": "bob"}
PERSONA_BENDER = {"tg_id": "333", "username": "bender"}


class TestBlackBoxRecorder(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blackbox_unittest_")
        self.db_path = os.path.join(self.temp_dir, "traces.db")
        self.config = BlackBoxConfig(
            db_path=self.db_path,
            retention="7d",
            max_db_size_mb=10,
            flush_interval_seconds=0.05,
        )
        self.tracer = Tracer(config=self.config)

    def tearDown(self):
        self.tracer.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_sync_function_and_inputs_outputs(self):
        @self.tracer.trace(name="weather_tool", kind=SpanKind.TOOL)
        def fetch_weather(city: str) -> str:
            return f"TODAY IS 2026-08-18. Weather in {city} is sunny, 24C."

        self.tracer.set_session_id(PERSONA_ALICE["tg_id"])
        result = fetch_weather("Berlin")
        self.assertIn("2026-08-18", result)

        self.tracer.flush()
        traces = self.tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
        self.assertEqual(len(traces), 1)

        spans = self.tracer.storage.get_trace(traces[0]["trace_id"])
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["name"], "weather_tool")
        self.assertEqual(spans[0]["inputs"]["city"], "Berlin")
        self.assertIn("sunny", spans[0]["outputs"])
        self.assertEqual(spans[0]["session_id"], PERSONA_ALICE["tg_id"])

    def test_nested_call_hierarchy(self):
        @self.tracer.trace(name="multiply_tool", kind=SpanKind.TOOL)
        def multiply(a: int, b: int) -> int:
            return a * b

        @self.tracer.trace(name="llm_agent_step", kind=SpanKind.LLM)
        def llm_step(query: str) -> int:
            return multiply(6, 7)

        @self.tracer.trace(name="alice_agent", kind=SpanKind.AGENT)
        def run_agent():
            return llm_step("TODAY IS 2026-08-18. Multiply 6 by 7")

        self.tracer.set_session_id(PERSONA_ALICE["tg_id"])
        res = run_agent()
        self.assertEqual(res, 42)

        self.tracer.flush()
        traces = self.tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
        self.assertEqual(len(traces), 1)

        spans = self.tracer.storage.get_trace(traces[0]["trace_id"])
        self.assertEqual(len(spans), 3)

        root = next(s for s in spans if s["name"] == "alice_agent")
        llm = next(s for s in spans if s["name"] == "llm_agent_step")
        tool = next(s for s in spans if s["name"] == "multiply_tool")

        self.assertIsNone(root["parent_span_id"])
        self.assertEqual(llm["parent_span_id"], root["span_id"])
        self.assertEqual(tool["parent_span_id"], llm["span_id"])

    def test_context_manager(self):
        self.tracer.set_session_id(PERSONA_BOB["tg_id"])
        with self.tracer.span("bob_pipeline", kind=SpanKind.CHAIN) as root:
            root.set_metadata("user", PERSONA_BOB["username"])
            with self.tracer.span("inner_retriever", kind=SpanKind.RETRIEVER) as child:
                child.set_metric("tokens", 120)
                child.finish(output="retrieved doc")

        self.tracer.flush()
        traces = self.tracer.storage.list_traces(session_id=PERSONA_BOB["tg_id"])
        self.assertEqual(len(traces), 1)

        spans = self.tracer.storage.get_trace(traces[0]["trace_id"])
        self.assertEqual(len(spans), 2)
        inner = next(s for s in spans if s["name"] == "inner_retriever")
        self.assertEqual(inner["metrics"]["tokens"], 120)
        self.assertEqual(inner["outputs"], "retrieved doc")

    def test_error_capture(self):
        @self.tracer.trace(name="failing_tool", kind=SpanKind.TOOL)
        def fail():
            raise RuntimeError("API timeout simulation")

        @self.tracer.trace(name="bender_bot", kind=SpanKind.AGENT)
        def run_bender():
            fail()

        self.tracer.set_session_id(PERSONA_BENDER["tg_id"])
        with self.assertRaises(RuntimeError):
            run_bender()

        self.tracer.flush()
        traces = self.tracer.storage.list_traces(session_id=PERSONA_BENDER["tg_id"], has_error=True)
        self.assertEqual(len(traces), 1)

        spans = self.tracer.storage.get_trace(traces[0]["trace_id"])
        tool = next(s for s in spans if s["name"] == "failing_tool")
        self.assertEqual(tool["has_error"], 1)
        self.assertIn("API timeout simulation", tool["error"])

    def test_ttl_retention(self):
        now = time.time()
        s_old = Span(
            trace_id="t_old",
            span_id="s_old",
            name="old_agent",
            kind=SpanKind.AGENT,
            start_time=now - (15 * 86400),
            end_time=now - (15 * 86400) + 1,
        )
        s_new = Span(
            trace_id="t_new",
            span_id="s_new",
            name="new_agent",
            kind=SpanKind.AGENT,
            start_time=now - 100,
            end_time=now - 99,
        )
        self.tracer.storage.insert_batch([s_old, s_new])
        self.assertEqual(self.tracer.storage.get_stats()["total_spans"], 2)

        deleted = self.tracer.storage.cleanup_ttl()
        self.assertEqual(deleted, 1)
        self.assertEqual(self.tracer.storage.get_stats()["total_spans"], 1)

    def test_max_db_size_eviction(self):
        # Set tiny limit
        cfg = BlackBoxConfig(
            db_path=os.path.join(self.temp_dir, "size_test.db"),
            max_db_size_mb=0.001,  # ~1 KB limit
        )
        st = TraceStorage(cfg)
        spans = [
            Span(
                trace_id=f"trace_{i}",
                span_id=f"span_{i}",
                name=f"large_{i}",
                start_time=time.time() + i,
                inputs={"data": "Y" * 50000},
            )
            for i in range(5)
        ]
        st.insert_batch(spans)
        
        # Verify size exceeds limit
        self.assertGreater(st.get_total_db_size_bytes(), 0.001 * 1024 * 1024)
        deleted = st.enforce_max_size()
        self.assertGreater(deleted, 0)

    def test_async_workflow(self):
        async def run_async_test():
            @self.tracer.trace(name="async_substep", kind=SpanKind.TOOL)
            async def sub(name: str):
                await asyncio.sleep(0.01)
                return f"done {name}"

            @self.tracer.trace(name="async_root", kind=SpanKind.AGENT)
            async def root():
                t1 = sub("task1")
                t2 = sub("task2")
                return await asyncio.gather(t1, t2)

            self.tracer.set_session_id(PERSONA_ALICE["tg_id"])
            res = await root()
            self.assertEqual(len(res), 2)

        asyncio.run(run_async_test())
        self.tracer.flush()
        traces = self.tracer.storage.list_traces(session_id=PERSONA_ALICE["tg_id"])
        self.assertEqual(len(traces), 1)

        spans = self.tracer.storage.get_trace(traces[0]["trace_id"])
        self.assertEqual(len(spans), 3)

    def test_tree_rendering_and_cli(self):
        now = time.time()
        s1 = Span(trace_id="t1", span_id="s1", name="root_op", kind=SpanKind.AGENT, start_time=now - 5, end_time=now - 4)
        s2 = Span(trace_id="t1", span_id="s2", parent_span_id="s1", name="tool_op", kind=SpanKind.TOOL, start_time=now - 4.5, end_time=now - 4.1)
        self.tracer.storage.insert_batch([s1, s2])

        spans = self.tracer.storage.get_trace("t1")
        tree = render_trace_tree(spans)
        self.assertIn("root_op", tree)
        self.assertIn("tool_op", tree)

        # Test CLI invocation with PYTHONPATH
        env = os.environ.copy()
        env["PYTHONPATH"] = os.path.dirname(os.path.abspath(__file__))
        
        res = subprocess.run(
            [sys.executable, "-m", "blackbox_recorder", "--db", self.db_path, "stats"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        self.assertIn("Total Traces:     1", res.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
