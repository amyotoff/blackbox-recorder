"""
Guards for the two promises a flight recorder cannot break:

1. Importing the library does nothing observable — no database, no thread.
2. Whatever was running when the process died is still on disk afterwards.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

from blackbox_recorder.config import BlackBoxConfig
from blackbox_recorder.export import render_trace_tree
from blackbox_recorder.span import SpanKind
from blackbox_recorder.storage import TraceStorage
from blackbox_recorder.tracer import Tracer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class _ScriptRunner(unittest.TestCase):
    """Runs agent scripts in a throwaway directory, isolated from this process."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blackbox_crash_")
        self.db_path = os.path.join(self.temp_dir, "traces.db")
        self.env = os.environ.copy()
        self.env["PYTHONPATH"] = REPO_ROOT

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _write_script(self, source: str) -> str:
        path = os.path.join(self.temp_dir, "agent.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(source)
        return path

    def _run(self, source: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, self._write_script(source)],
            capture_output=True,
            text=True,
            check=True,
            cwd=self.temp_dir,
            env=self.env,
        )

    def _read_spans(self, trace_id=None):
        storage = TraceStorage(BlackBoxConfig(db_path=self.db_path))
        if trace_id:
            return storage.get_trace(trace_id)
        traces = storage.list_traces(limit=10_000)
        return [s for t in traces for s in storage.get_trace(t["trace_id"])]


class TestImportIsSideEffectFree(_ScriptRunner):
    def test_import_and_decoration_touch_no_disk(self):
        res = self._run("""
import os
from blackbox_recorder import trace, tracer, SpanKind

@trace(kind=SpanKind.TOOL)
def never_called(x):
    return x

print("PROXY:" + repr(tracer))
print("FILES:" + ",".join(sorted(os.listdir("."))))
""")
        files = res.stdout.split("FILES:")[1].strip()
        self.assertNotIn(".db", files, f"import created a database: {files}")
        self.assertIn("not started", res.stdout)

    def test_first_traced_call_creates_the_database(self):
        self._run("""
import os
from blackbox_recorder import trace, tracer, SpanKind

@trace(name="calc", kind=SpanKind.TOOL)
def add(a, b):
    return a + b

assert not os.path.exists("blackbox_traces.db")
add(2, 2)
tracer.flush()
assert os.path.exists("blackbox_traces.db"), "first traced call did not create the DB"
""")

    def test_configure_before_first_use_wins(self):
        self._run(f"""
from blackbox_recorder import BlackBoxConfig, configure, trace, tracer, SpanKind

configure(BlackBoxConfig(db_path={self.db_path!r}, flush_interval_seconds=0.05))

@trace(name="configured_tool", kind=SpanKind.TOOL)
def work(city):
    return "sunny in " + city

work("Berlin")
tracer.flush()
""")
        self.assertTrue(os.path.exists(self.db_path), "configure() db_path was ignored")
        spans = self._read_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0]["name"], "configured_tool")


class TestTailSurvivesProcessDeath(_ScriptRunner):
    def test_sigkill_leaves_the_running_span_on_disk(self):
        script = self._write_script(f"""
import time
from blackbox_recorder import BlackBoxConfig, configure, get_tracer, SpanKind

configure(BlackBoxConfig(db_path={self.db_path!r}, flush_interval_seconds=0.05))
recorder = get_tracer()

with recorder.span("hung_agent", kind=SpanKind.AGENT, inputs={{"query": "why is it stuck"}}):
    recorder.flush()
    print("READY", flush=True)
    time.sleep(60)
""")
        proc = subprocess.Popen(
            [sys.executable, script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=self.temp_dir,
            env=self.env,
        )
        try:
            self.assertEqual(proc.stdout.readline().strip(), "READY")
            proc.kill()  # SIGKILL: no atexit, no finally, no mercy
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()

        spans = self._read_spans()
        self.assertEqual(len(spans), 1, "the span that was running was lost")
        self.assertEqual(spans[0]["name"], "hung_agent")
        self.assertIsNone(spans[0]["end_time"], "a killed span must stay unfinished")
        self.assertEqual(spans[0]["inputs"]["query"], "why is it stuck")

        tree = render_trace_tree(spans)
        self.assertIn("⏳", tree)
        self.assertIn("unfinished", tree)

    def test_exit_without_close_still_persists_everything(self):
        self._run(f"""
from blackbox_recorder import BlackBoxConfig, configure, trace, SpanKind

configure(BlackBoxConfig(db_path={self.db_path!r}))

@trace(name="burst", kind=SpanKind.TOOL)
def work(i):
    return i * 2

for i in range(300):
    work(i)
# No flush(), no close(): the atexit hook is the only thing that can save this.
""")
        spans = self._read_spans()
        self.assertEqual(len(spans), 300, "spans were dropped at interpreter shutdown")
        self.assertTrue(all(s["end_time"] is not None for s in spans))
        self.assertEqual(spans[0]["outputs"], spans[0]["inputs"]["i"] * 2)


class TestOpenSpanLifecycle(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blackbox_open_")
        self.tracer = Tracer(BlackBoxConfig(
            db_path=os.path.join(self.temp_dir, "traces.db"),
            flush_interval_seconds=0.05,
        ))

    def tearDown(self):
        self.tracer.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_open_row_is_written_then_replaced_by_the_finished_one(self):
        with self.tracer.span("slow_step", kind=SpanKind.CHAIN, inputs={"n": 1}) as active:
            self.tracer.flush()
            mid = self.tracer.storage.get_trace(active.trace_id)
            self.assertEqual(len(mid), 1, "span was not recorded while still running")
            self.assertIsNone(mid[0]["end_time"])
            self.assertEqual(mid[0]["inputs"]["n"], 1)
            active.set_metadata("model", "gemini-2.5-flash")

        self.tracer.flush()
        done = self.tracer.storage.get_trace(active.trace_id)
        self.assertEqual(len(done), 1, "the finished span must replace the open one, not duplicate it")
        self.assertIsNotNone(done[0]["end_time"])
        self.assertEqual(done[0]["metadata"]["model"], "gemini-2.5-flash")

    def test_open_spans_can_be_disabled(self):
        quiet = Tracer(BlackBoxConfig(
            db_path=os.path.join(self.temp_dir, "quiet.db"),
            flush_interval_seconds=0.05,
            record_open_spans=False,
        ))
        try:
            with quiet.span("step", kind=SpanKind.CHAIN) as active:
                quiet.flush()
                self.assertEqual(quiet.storage.get_trace(active.trace_id), [])
            quiet.flush()
            self.assertEqual(len(quiet.storage.get_trace(active.trace_id)), 1)
        finally:
            quiet.close()

    def test_close_is_idempotent(self):
        self.tracer.close()
        self.tracer.close()

    def test_decorator_still_records_inputs_outputs_and_errors(self):
        @self.tracer.trace(name="divide", kind=SpanKind.TOOL)
        def divide(a, b=2):
            return a / b

        self.assertEqual(divide(10), 5)
        with self.assertRaises(ZeroDivisionError):
            divide(1, 0)

        self.tracer.flush()
        spans = [s for t in self.tracer.storage.list_traces() for s in self.tracer.storage.get_trace(t["trace_id"])]
        ok = next(s for s in spans if not s["has_error"])
        failed = next(s for s in spans if s["has_error"])
        self.assertEqual(ok["inputs"], {"a": 10, "b": 2})
        self.assertEqual(ok["outputs"], 5)
        self.assertIn("division by zero", failed["error"])
        self.assertIsNotNone(failed["end_time"], "a failed span must still be closed")


if __name__ == "__main__":
    unittest.main()
