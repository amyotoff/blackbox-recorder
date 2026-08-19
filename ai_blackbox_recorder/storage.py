"""
SQLite WAL Storage Engine with TTL retention and DB size limit enforcement.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from ai_blackbox_recorder.config import BlackBoxConfig
from ai_blackbox_recorder.span import Span


class TraceStorage:
    """
    High-performance, zero-dependency SQLite storage for AI agent traces.
    """

    def __init__(self, config: Optional[BlackBoxConfig] = None):
        self.config = config or BlackBoxConfig()
        self.db_path = self.config.db_path

        # Ensure parent directory exists
        db_dir = os.path.dirname(os.path.abspath(self.db_path))
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)

        self._init_db()
        self.cleanup_all()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, isolation_level=None)
        # auto_vacuum has to be set before the database header exists. Switching the
        # journal mode writes that header, so setting it afterwards is silently
        # ignored and the file never hands freed pages back to the filesystem.
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _reclaim_free_pages(self, conn: sqlite3.Connection) -> None:
        """
        Hand pages freed by DELETE back to the filesystem.

        `PRAGMA incremental_vacuum` releases one page per step, so a bare execute()
        reclaims exactly one page and leaves the file at its high-water mark: it has
        to be driven to completion. Even then some SQLite builds leave the freelist
        populated, and there the only way to shrink the file is a full rewrite — so
        check the result and fall back to VACUUM rather than let the size cap quietly
        become unenforceable, which is what turns eviction into a total wipe.
        """
        try:
            conn.execute("PRAGMA incremental_vacuum;").fetchall()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
            if conn.execute("PRAGMA freelist_count;").fetchone()[0] == 0:
                return
        except Exception:
            return

        try:
            conn.execute("VACUUM;")
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE);").fetchall()
        except Exception:
            pass

    def ensure_incremental_vacuum(self) -> bool:
        """
        Enable incremental auto-vacuum on a database that was created without it.

        auto_vacuum can only be changed by rewriting the file, so this runs a one-off
        VACUUM. Databases written before 0.8.0 have it disabled, and until it is on,
        deleting traces frees nothing on disk. Returns True if a migration ran.
        """
        conn = self._get_connection()
        try:
            if conn.execute("PRAGMA auto_vacuum;").fetchone()[0] == 2:
                return False
            conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
            conn.execute("VACUUM;")
            return True
        except Exception:
            return False
        finally:
            conn.close()

    def _init_db(self) -> None:
        conn = self._get_connection()
        try:
            conn.execute("BEGIN;")
            conn.execute("""
            CREATE TABLE IF NOT EXISTS spans (
                span_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL,
                parent_span_id TEXT,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                session_id TEXT,
                start_time REAL NOT NULL,
                end_time REAL,
                duration_ms REAL,
                inputs TEXT,
                outputs TEXT,
                error TEXT,
                metadata TEXT,
                metrics TEXT,
                has_error INTEGER DEFAULT 0,
                created_at INTEGER DEFAULT (unixepoch())
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_trace_id ON spans(trace_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_session_id ON spans(session_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_created_at ON spans(created_at);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_kind ON spans(kind);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_spans_has_error ON spans(has_error);")
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    @staticmethod
    def _dumps(obj: Any) -> str:
        try:
            return json.dumps(obj, default=str, ensure_ascii=False)
        except Exception:
            return json.dumps(str(obj), ensure_ascii=False)

    @staticmethod
    def _middle_out(text: str, budget: int) -> str:
        """
        Drop the middle of a string and keep both ends.

        Cutting only the tail throws away the end of the story — the final answer,
        the last tool result, the exception that ended the run — which is usually
        the reason someone opened the trace at all.
        """
        if len(text) <= budget:
            return text
        head = budget // 2
        tail = budget - head
        return f"{text[:head]}… [truncated: {len(text) - budget} chars] …{text[-tail:]}"

    def _shrink_strings(self, obj: Any, budget: int) -> Any:
        """Trim long strings wherever they sit, leaving the structure around them intact."""
        if isinstance(obj, str):
            return self._middle_out(obj, budget)
        if isinstance(obj, dict):
            return {k: self._shrink_strings(v, budget) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._shrink_strings(v, budget) for v in obj]
        return obj

    def _serialize_field(self, obj: Any) -> Optional[str]:
        """
        Serialize a payload small enough to store, and still parseable afterwards.

        Oversized payloads keep both ends, and the stored text stays valid JSON: a
        field chopped mid-token used to come back from the database as a raw string
        instead of the structure that was recorded.
        """
        if obj is None:
            return None

        cap = self.config.max_field_chars
        val = self._dumps(obj)
        if len(val) <= cap:
            return val

        # A few outsized strings inside a modest structure — the usual case for a
        # long prompt or completion. Trim the strings, keep the shape readable.
        val = self._dumps(self._shrink_strings(obj, max(1024, cap // 4)))
        if len(val) <= cap:
            return val

        # Bulk rather than a few big values. Trim the document itself and store it
        # as a JSON string, so a reader still gets valid JSON back. Escaping grows
        # the result, so the budget is walked down until it fits.
        budget = cap
        candidate = self._dumps(self._middle_out(val, budget))
        while len(candidate) > cap and budget > 256:
            budget //= 2
            candidate = self._dumps(self._middle_out(val, budget))
        return candidate

    def get_total_db_size_bytes(self) -> int:
        """
        Disk footprint of the recording: the database plus its write-ahead log.

        The `-shm` file is deliberately left out. It is a fixed-size shared-memory
        index that SQLite recreates whenever the database is opened, not recorded
        data, and no amount of eviction frees it. Counting it charges every database
        a constant overhead, which under a small size cap means eviction deletes the
        entire history and is still over budget.
        """
        total = 0
        for path in (self.db_path, f"{self.db_path}-wal"):
            if os.path.exists(path):
                total += os.path.getsize(path)
        return total

    def insert_batch(self, spans: List[Span]) -> None:
        """Persist a batch of spans atomically."""
        if not spans:
            return

        rows = []
        for s in spans:
            has_error = 1 if s.error else 0
            inputs_str = self._serialize_field(s.inputs) if self.config.capture_inputs else None
            outputs_str = self._serialize_field(s.outputs) if self.config.capture_outputs else None
            metadata_str = self._serialize_field(s.metadata)
            metrics_str = self._serialize_field(s.metrics)

            rows.append((
                s.span_id,
                s.trace_id,
                s.parent_span_id,
                s.name,
                str(s.kind),
                s.session_id,
                s.start_time,
                s.end_time,
                s.duration_ms,
                inputs_str,
                outputs_str,
                s.error,
                metadata_str,
                metrics_str,
                has_error,
                int(s.start_time),
            ))

        conn = self._get_connection()
        try:
            conn.execute("BEGIN;")
            conn.executemany("""
            INSERT OR REPLACE INTO spans (
                span_id, trace_id, parent_span_id, name, kind, session_id,
                start_time, end_time, duration_ms, inputs, outputs, error,
                metadata, metrics, has_error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, rows)
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def cleanup_ttl(self) -> int:
        """
        Delete traces whose spans have all aged past the retention period.

        Retention is decided per trace, not per row. Expiring rows individually cuts
        a trace in half at the cutoff and leaves children pointing at a parent that
        no longer exists, which is unreadable as a tree; a trace instead survives
        until its newest span expires.
        """
        cutoff = int(time.time()) - (self.config.retention_days * 86400)
        conn = self._get_connection()
        try:
            conn.execute("BEGIN;")
            cursor = conn.execute("""
            DELETE FROM spans WHERE trace_id IN (
                SELECT trace_id FROM spans
                GROUP BY trace_id
                HAVING MAX(created_at) < ?
            )
            """, (cutoff,))
            deleted = cursor.rowcount
            conn.execute("COMMIT;")

            if deleted > 0:
                self._reclaim_free_pages(conn)
            return deleted
        except Exception:
            conn.execute("ROLLBACK;")
            return 0
        finally:
            conn.close()

    def enforce_max_size(self) -> int:
        """
        Enforce max DB size in MB.
        If file size exceeds threshold, evicts oldest traces until within limit.
        """
        max_bytes = int(self.config.max_db_size_mb * 1024 * 1024)
        if not os.path.exists(self.db_path):
            return 0

        current_size = self.get_total_db_size_bytes()
        total_deleted = 0

        if current_size <= max_bytes:
            return 0

        conn = self._get_connection()
        try:
            # Checkpoint first to sync WAL into main DB
            try:
                conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
            except Exception:
                pass

            while current_size > max_bytes:
                total_traces = conn.execute("SELECT COUNT(DISTINCT trace_id) FROM spans").fetchone()[0]
                if not total_traces:
                    break

                # Evict a slice of the oldest traces rather than a fixed 50: on a
                # small database a fixed batch takes the whole history in one pass,
                # leaving nothing to investigate with.
                batch = max(1, min(50, total_traces // 10))
                cursor = conn.execute("""
                SELECT trace_id
                FROM spans
                GROUP BY trace_id
                ORDER BY MIN(start_time) ASC
                LIMIT ?
                """, (batch,))
                oldest_traces = [row[0] for row in cursor.fetchall()]
                if not oldest_traces:
                    break

                conn.execute("BEGIN;")
                placeholders = ",".join("?" * len(oldest_traces))
                del_cursor = conn.execute(
                    f"DELETE FROM spans WHERE trace_id IN ({placeholders})",
                    oldest_traces
                )
                deleted_rows = del_cursor.rowcount
                conn.execute("COMMIT;")

                total_deleted += deleted_rows

                self._reclaim_free_pages(conn)
                current_size = self.get_total_db_size_bytes()
                if deleted_rows == 0:
                    break
            return total_deleted
        except Exception:
            conn.execute("ROLLBACK;")
            return total_deleted
        finally:
            conn.close()

    def cleanup_all(self) -> Dict[str, int]:
        """Perform full maintenance: TTL cleanup + max size enforcement."""
        ttl_deleted = self.cleanup_ttl()
        size_deleted = self.enforce_max_size()
        return {"ttl_deleted": ttl_deleted, "size_deleted": size_deleted}

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        """Retrieve all spans for a specific trace_id, ordered by start_time."""
        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
            SELECT * FROM spans
            WHERE trace_id = ?
            ORDER BY start_time ASC
            """, (trace_id,))
            rows = cursor.fetchall()

            result = []
            for r in rows:
                item = dict(r)
                for field in ("inputs", "outputs", "metadata", "metrics"):
                    if item.get(field):
                        try:
                            item[field] = json.loads(item[field])
                        except Exception:
                            pass
                result.append(item)
            return result
        finally:
            conn.close()

    def list_traces(
        self,
        limit: int = 50,
        session_id: Optional[str] = None,
        has_error: Optional[bool] = None,
        since: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """List distinct traces with aggregated metadata."""
        query = """
        SELECT
            trace_id,
            session_id,
            MIN(start_time) as start_time,
            MAX(COALESCE(end_time, start_time)) as end_time,
            ROUND((MAX(COALESCE(end_time, start_time)) - MIN(start_time)) * 1000, 2) as duration_ms,
            COUNT(span_id) as span_count,
            SUM(has_error) as error_count,
            SUM(end_time IS NULL) as incomplete_count,
            (
                SELECT name FROM spans s2
                WHERE s2.trace_id = spans.trace_id AND s2.parent_span_id IS NULL
                LIMIT 1
            ) as root_name
        FROM spans
        WHERE 1=1
        """
        params: List[Any] = []

        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if has_error is True:
            query += " AND trace_id IN (SELECT DISTINCT trace_id FROM spans WHERE has_error = 1)"
        elif has_error is False:
            query += " AND trace_id NOT IN (SELECT DISTINCT trace_id FROM spans WHERE has_error = 1)"
        if since is not None:
            query += " AND start_time >= ?"
            params.append(since)

        query += " GROUP BY trace_id ORDER BY start_time DESC LIMIT ?"
        params.append(limit)

        conn = self._get_connection()
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Get aggregate database and recording statistics."""
        db_size_bytes = self.get_total_db_size_bytes()
        conn = self._get_connection()
        try:
            cursor = conn.execute("""
            SELECT
                COUNT(span_id) as total_spans,
                COUNT(DISTINCT trace_id) as total_traces,
                SUM(has_error) as total_errors,
                MIN(start_time) as oldest_timestamp,
                MAX(start_time) as newest_timestamp,
                SUM(end_time IS NULL) as total_incomplete
            FROM spans;
            """)
            row = cursor.fetchone()
            return {
                "db_path": self.db_path,
                "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
                "total_spans": row[0] or 0,
                "total_traces": row[1] or 0,
                "total_errors": row[2] or 0,
                "oldest_timestamp": row[3],
                "newest_timestamp": row[4],
                "total_incomplete": row[5] or 0,
                "retention_days": self.config.retention_days,
                "max_db_size_mb": self.config.max_db_size_mb,
            }
        finally:
            conn.close()
