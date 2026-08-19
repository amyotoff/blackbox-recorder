"""
Tests for SQLite storage, TTL retention cleanup, and DB size limit eviction.
"""

import time
from ai_blackbox_recorder.config import BlackBoxConfig
from ai_blackbox_recorder.span import Span, SpanKind
from ai_blackbox_recorder.storage import TraceStorage
from tests.conftest import PERSONA_ALICE, PERSONA_BOB


def test_storage_wal_mode_and_init(temp_db_path):
    config = BlackBoxConfig(db_path=temp_db_path, retention="7d")
    storage = TraceStorage(config)

    with storage._get_connection() as conn:
        cursor = conn.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"


def test_retention_ttl_cleanup(temp_db_path):
    config = BlackBoxConfig(db_path=temp_db_path, retention="7d")
    storage = TraceStorage(config)

    now = time.time()
    old_time = now - (10 * 86400)  # 10 days ago (expired)
    recent_time = now - (2 * 86400)  # 2 days ago (valid)

    # Insert old span
    s_old = Span(
        trace_id="trace_old",
        span_id="span_old",
        name="old_agent",
        kind=SpanKind.AGENT,
        session_id=PERSONA_ALICE["tg_id"],
        start_time=old_time,
        end_time=old_time + 1,
    )

    # Insert recent span
    s_recent = Span(
        trace_id="trace_recent",
        span_id="span_recent",
        name="recent_agent",
        kind=SpanKind.AGENT,
        session_id=PERSONA_BOB["tg_id"],
        start_time=recent_time,
        end_time=recent_time + 1,
    )

    storage.insert_batch([s_old, s_recent])

    stats = storage.get_stats()
    assert stats["total_spans"] == 2

    # Run TTL cleanup
    deleted = storage.cleanup_ttl()
    assert deleted == 1

    remaining_stats = storage.get_stats()
    assert remaining_stats["total_spans"] == 1

    remaining = storage.get_trace("trace_recent")
    assert len(remaining) == 1
    assert remaining[0]["name"] == "recent_agent"


def test_max_db_size_enforcement(temp_db_path):
    # Set a tiny max size (0.01 MB = ~10 KB) for testing
    config = BlackBoxConfig(db_path=temp_db_path, max_db_size_mb=0.01, retention="30d")
    storage = TraceStorage(config)

    # Create 5 traces with large payload
    spans = []
    for i in range(5):
        t_id = f"trace_batch_{i}"
        s = Span(
            trace_id=t_id,
            span_id=f"span_{i}",
            name=f"large_op_{i}",
            kind=SpanKind.LLM,
            start_time=time.time() + i,
            inputs={"heavy_data": "X" * 10000},  # ~10KB each
        )
        spans.append(s)

    storage.insert_batch(spans)

    # Check if enforce_max_size evicts oldest traces
    deleted = storage.enforce_max_size()
    assert deleted > 0


def test_retention_string_parsing():
    assert BlackBoxConfig(retention="7d").retention_days == 7
    assert BlackBoxConfig(retention="week").retention_days == 7
    assert BlackBoxConfig(retention="30d").retention_days == 30
    assert BlackBoxConfig(retention="month").retention_days == 30
    assert BlackBoxConfig(retention="60d").retention_days == 60
    assert BlackBoxConfig(retention="2months").retention_days == 60
    assert BlackBoxConfig(retention=45).retention_days == 45
