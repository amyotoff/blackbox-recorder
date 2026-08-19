"""
Retention and payload-size behaviour.

These guard the failure mode where the recorder damages what it is meant to
preserve: evicting the whole history, cutting traces into unreadable halves, or
storing payloads that can no longer be parsed.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest

from ai_blackbox_recorder import BlackBoxConfig, TraceStorage
from ai_blackbox_recorder.span import Span, SpanKind


class RetentionTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="blackbox_retention_")
        self.now = time.time()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _path(self, name):
        return os.path.join(self.temp_dir, name)

    def _storage(self, name="traces.db", **kwargs):
        return TraceStorage(BlackBoxConfig(db_path=self._path(name), **kwargs))


class TestSpaceIsActuallyReclaimed(RetentionTestCase):
    def test_new_databases_have_incremental_auto_vacuum(self):
        storage = self._storage()
        conn = sqlite3.connect(storage.db_path)
        try:
            mode = conn.execute("PRAGMA auto_vacuum;").fetchone()[0]
        finally:
            conn.close()
        # 2 == INCREMENTAL. Anything else and deletions never shrink the file,
        # which turns size enforcement into an endless eviction loop.
        self.assertEqual(mode, 2)

    def test_deleting_traces_shrinks_the_file(self):
        storage = self._storage(retention=1)
        expired = self.now - (30 * 86400)
        storage.insert_batch([
            Span(trace_id=f"t{i}", span_id=f"s{i}", name="agent", kind=SpanKind.AGENT,
                 start_time=expired, end_time=expired + 1, inputs={"payload": "Я" * 20000})
            for i in range(30)
        ])
        before = storage.get_total_db_size_bytes()
        storage.cleanup_ttl()
        after = storage.get_total_db_size_bytes()

        def _breakdown():
            parts = {suffix or "db": (os.path.getsize(storage.db_path + suffix)
                                      if os.path.exists(storage.db_path + suffix) else 0)
                     for suffix in ("", "-wal", "-shm")}
            return f"{before} -> {after}, files {parts}, sqlite {sqlite3.sqlite_version}"

        self.assertLess(after, before / 2,
                        f"freed pages were never returned to the filesystem ({_breakdown()})")

    def test_space_is_reclaimed_even_without_incremental_auto_vacuum(self):
        # Stands in for SQLite builds where the incremental path frees nothing: the
        # size cap has to hold there too, or eviction degenerates into a total wipe.
        path = self._path("legacy.db")
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")  # too late to take effect
        conn.execute("CREATE TABLE legacy_payload (x);")
        conn.close()

        storage = TraceStorage(BlackBoxConfig(db_path=path, retention=1))
        expired = self.now - (30 * 86400)
        storage.insert_batch([
            Span(trace_id=f"t{i}", span_id=f"s{i}", name="agent", kind=SpanKind.AGENT,
                 start_time=expired, end_time=expired + 1, inputs={"payload": "Я" * 20000})
            for i in range(30)
        ])
        before = storage.get_total_db_size_bytes()
        storage.cleanup_ttl()
        self.assertLess(storage.get_total_db_size_bytes(), before / 2)

    def test_migration_enables_auto_vacuum_on_an_older_database(self):
        path = self._path("legacy.db")
        # A database as 0.7.0 and earlier created it: auto_vacuum never took.
        conn = sqlite3.connect(path, isolation_level=None)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        conn.execute("CREATE TABLE legacy_payload (x);")
        self.assertEqual(conn.execute("PRAGMA auto_vacuum;").fetchone()[0], 0)
        conn.close()

        storage = TraceStorage(BlackBoxConfig(db_path=path))
        self.assertTrue(storage.ensure_incremental_vacuum())

        conn = sqlite3.connect(path)
        try:
            self.assertEqual(conn.execute("PRAGMA auto_vacuum;").fetchone()[0], 2)
        finally:
            conn.close()

        # Idempotent: a second run has nothing to do.
        self.assertFalse(storage.ensure_incremental_vacuum())


class TestEvictionKeepsRecentHistory(RetentionTestCase):
    def test_oldest_traces_go_and_recent_ones_survive(self):
        storage = self._storage(max_db_size_mb=0.5)
        for i in range(40):
            storage.insert_batch([
                Span(trace_id=f"t{i:02d}", span_id=f"s{i:02d}", name=f"agent_{i}",
                     kind=SpanKind.AGENT, start_time=self.now + i, end_time=self.now + i + 1,
                     inputs={"payload": "Я" * 10000})
            ])
        self.assertGreater(storage.get_total_db_size_bytes(), 0.5 * 1024 * 1024)

        deleted = storage.enforce_max_size()
        self.assertGreater(deleted, 0)

        surviving = {t["trace_id"] for t in storage.list_traces(limit=100)}
        self.assertTrue(surviving, "eviction emptied the database instead of trimming it")
        self.assertIn("t39", surviving, "the newest trace was evicted")
        self.assertNotIn("t00", surviving, "the oldest trace was kept")
        self.assertLessEqual(storage.get_total_db_size_bytes(), 0.5 * 1024 * 1024)

    def test_eviction_does_not_wipe_a_small_database(self):
        # Fewer traces than the eviction batch used to be: the whole history went
        # in a single pass and left nothing to investigate.
        storage = self._storage(max_db_size_mb=0.2)
        for i in range(12):
            storage.insert_batch([
                Span(trace_id=f"t{i:02d}", span_id=f"s{i:02d}", name="agent",
                     kind=SpanKind.AGENT, start_time=self.now + i, end_time=self.now + i + 1,
                     inputs={"payload": "Я" * 10000})
            ])
        storage.enforce_max_size()
        self.assertGreater(storage.get_stats()["total_traces"], 0)


class TestRetentionKeepsTracesWhole(RetentionTestCase):
    def test_a_trace_straddling_the_cutoff_is_not_cut_in_half(self):
        storage = self._storage(retention="7d")
        old = self.now - (10 * 86400)
        storage.insert_batch([
            Span(trace_id="T", span_id="root", name="agent", kind=SpanKind.AGENT,
                 start_time=old, end_time=old + 1),
            Span(trace_id="T", span_id="child", parent_span_id="root", name="tool",
                 kind=SpanKind.TOOL, start_time=self.now - 60, end_time=self.now - 59),
        ])
        storage.cleanup_ttl()

        spans = storage.get_trace("T")
        self.assertEqual(len(spans), 2, "the trace lost its root and left an orphan behind")
        ids = {s["span_id"] for s in spans}
        for span in spans:
            if span["parent_span_id"]:
                self.assertIn(span["parent_span_id"], ids)

    def test_fully_expired_traces_are_still_deleted(self):
        storage = self._storage(retention="7d")
        old = self.now - (10 * 86400)
        storage.insert_batch([
            Span(trace_id="DEAD", span_id="a", name="agent", kind=SpanKind.AGENT,
                 start_time=old, end_time=old + 1),
            Span(trace_id="DEAD", span_id="b", parent_span_id="a", name="tool",
                 kind=SpanKind.TOOL, start_time=old + 2, end_time=old + 3),
            Span(trace_id="ALIVE", span_id="c", name="agent", kind=SpanKind.AGENT,
                 start_time=self.now - 60, end_time=self.now - 59),
        ])
        self.assertEqual(storage.cleanup_ttl(), 2)
        self.assertEqual(storage.get_trace("DEAD"), [])
        self.assertEqual(len(storage.get_trace("ALIVE")), 1)


class TestOversizedPayloads(RetentionTestCase):
    def test_both_ends_of_a_long_value_are_kept(self):
        storage = self._storage(max_field_chars=4000)
        span = Span(trace_id="Y", span_id="y", name="llm", kind=SpanKind.LLM,
                    start_time=self.now, end_time=self.now + 1)
        span.set_llm_io(
            prompt="ВОПРОС В НАЧАЛЕ. " + ("бла " * 3000) + "И ГЛАВНОЕ В КОНЦЕ?",
            completion="Начало ответа. " + ("текст " * 3000) + "ИТОГОВАЯ РЕКОМЕНДАЦИЯ.",
            model="gemini-2.5-flash",
        )
        storage.insert_batch([span])

        row = storage.get_trace("Y")[0]
        # The structure survives, so callers can still reach individual fields.
        self.assertIsInstance(row["inputs"], dict)
        self.assertIsInstance(row["outputs"], dict)

        prompt, completion = row["inputs"]["prompt"], row["outputs"]["completion"]
        self.assertTrue(prompt.startswith("ВОПРОС В НАЧАЛЕ."))
        self.assertTrue(prompt.endswith("И ГЛАВНОЕ В КОНЦЕ?"), "the end of the prompt was cut away")
        self.assertTrue(completion.endswith("ИТОГОВАЯ РЕКОМЕНДАЦИЯ."), "the answer's ending was lost")
        self.assertIn("truncated", prompt)
        self.assertEqual(row["metadata"]["model"], "gemini-2.5-flash")

    def test_truncated_payloads_remain_valid_json(self):
        storage = self._storage(max_field_chars=400)
        # Bulk of small values rather than a few big ones: the document itself has
        # to be trimmed, and it must still parse.
        payload = {"messages": ["ПЕРВОЕ СООБЩЕНИЕ"] + [f"m-{i}" for i in range(300)] + ["ПОСЛЕДНЕЕ"]}
        storage.insert_batch([
            Span(trace_id="Z", span_id="z", name="llm", kind=SpanKind.LLM,
                 start_time=self.now, end_time=self.now + 1, inputs=payload)
        ])

        conn = sqlite3.connect(storage.db_path)
        try:
            stored = conn.execute("SELECT inputs FROM spans WHERE span_id = 'z'").fetchone()[0]
        finally:
            conn.close()
        json.loads(stored)  # raises if the field was cut mid-token
        self.assertLessEqual(len(stored), 400)

        text = json.dumps(storage.get_trace("Z")[0]["inputs"], ensure_ascii=False)
        self.assertIn("ПЕРВОЕ СООБЩЕНИЕ", text)
        self.assertIn("ПОСЛЕДНЕЕ", text)

    def test_payloads_within_the_limit_are_untouched(self):
        storage = self._storage(max_field_chars=10000)
        payload = {"city": "Berlin", "units": "metric"}
        storage.insert_batch([
            Span(trace_id="S", span_id="s", name="tool", kind=SpanKind.TOOL,
                 start_time=self.now, end_time=self.now + 1, inputs=payload)
        ])
        self.assertEqual(storage.get_trace("S")[0]["inputs"], payload)


if __name__ == "__main__":
    unittest.main()
