"""
Tests for CLI commands, JSONL export, and tree rendering.
"""

import json
import os
import subprocess
import sys
import time
from blackbox_recorder.export import export_all_to_jsonl, export_trace_to_jsonl, render_trace_tree
from blackbox_recorder.span import Span, SpanKind
from blackbox_recorder.storage import TraceStorage
from tests.conftest import PERSONA_ALICE


def test_tree_rendering():
    spans = [
        {
            "trace_id": "t1",
            "span_id": "root",
            "parent_span_id": None,
            "name": "orchestrator",
            "kind": "AGENT",
            "duration_ms": 120.5,
            "has_error": 0,
            "error": None,
        },
        {
            "trace_id": "t1",
            "span_id": "s1",
            "parent_span_id": "root",
            "name": "llm_query",
            "kind": "LLM",
            "duration_ms": 80.0,
            "has_error": 0,
            "error": None,
        },
        {
            "trace_id": "t1",
            "span_id": "s2",
            "parent_span_id": "s1",
            "name": "search_tool",
            "kind": "TOOL",
            "duration_ms": 30.2,
            "has_error": 1,
            "error": "Network timeout",
        },
    ]

    tree_str = render_trace_tree(spans)
    assert "orchestrator" in tree_str
    assert "llm_query" in tree_str
    assert "search_tool" in tree_str
    assert "Network timeout" in tree_str
    assert "❌" in tree_str


def test_jsonl_export(temp_db_path, temp_dir):
    from blackbox_recorder.config import BlackBoxConfig
    storage = TraceStorage(BlackBoxConfig(db_path=temp_db_path))

    now = time.time()
    s1 = Span(trace_id="t1", span_id="s1", name="step1", kind=SpanKind.TOOL, start_time=now - 5, end_time=now - 4)
    s2 = Span(trace_id="t1", span_id="s2", name="step2", kind=SpanKind.LLM, start_time=now - 3, end_time=now - 2)
    s3 = Span(trace_id="t2", span_id="s3", name="step3", kind=SpanKind.AGENT, start_time=now - 1, end_time=now)
    storage.insert_batch([s1, s2, s3])

    out_file = os.path.join(temp_dir, "export.jsonl")
    count = export_trace_to_jsonl(storage, "t1", out_file)
    assert count == 2

    with open(out_file, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f]
    assert len(lines) == 2
    assert lines[0]["span_id"] == "s1"
    assert lines[1]["span_id"] == "s2"


def test_cli_subcommands(temp_db_path):
    from blackbox_recorder.config import BlackBoxConfig
    storage = TraceStorage(BlackBoxConfig(db_path=temp_db_path))

    now = time.time()
    s = Span(trace_id="trace_cli_test", span_id="s_root", name="cli_agent", kind=SpanKind.AGENT, start_time=now - 2, end_time=now - 1)
    storage.insert_batch([s])

    env = os.environ.copy()
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Run CLI stats
    res = subprocess.run(
        [sys.executable, "-m", "blackbox_recorder", "--db", temp_db_path, "stats"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert "BlackBox Recorder — Statistics" in res.stdout
    assert "Total Traces:     1" in res.stdout

    # Run CLI list
    res = subprocess.run(
        [sys.executable, "-m", "blackbox_recorder", "--db", temp_db_path, "list"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert "trace_cli_test" in res.stdout

    # Run CLI show
    res = subprocess.run(
        [sys.executable, "-m", "blackbox_recorder", "--db", temp_db_path, "show", "trace_cli_test"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    assert "cli_agent" in res.stdout
