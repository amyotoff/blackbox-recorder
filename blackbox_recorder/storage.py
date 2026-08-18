"""
SQLite WAL Storage Engine with TTL retention and DB size limit enforcement.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any, Dict, List, Optional

from blackbox_recorder.config import BlackBoxConfig
from blackbox_recorder.span import Span


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
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA auto_vacuum = INCREMENTAL;")
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

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

    def _serialize_field(self, obj: Any) -> Optional[str]:
        if obj is None:
            return None
        try:
            val = json.dumps(obj, default=str, ensure_ascii=False)
        except Exception:
            val = str(obj)
            
        if len(val) > self.config.max_field_chars:
            val = val[: self.config.max_field_chars] + "... [TRUNCATED]"
        return val

    def get_total_db_size_bytes(self) -> int:
        """Calculate total disk footprint including WAL and SHM files."""
        total = 0
        for path in (self.db_path, f"{self.db_path}-wal", f"{self.db_path}-shm"):
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
        """Delete spans older than configured retention period."""
        cutoff = int(time.time()) - (self.config.retention_days * 86400)
        conn = self._get_connection()
        try:
            conn.execute("BEGIN;")
            cursor = conn.execute("DELETE FROM spans WHERE created_at < ?", (cutoff,))
            deleted = cursor.rowcount
            conn.execute("COMMIT;")
            
            if deleted > 0:
                conn.execute("PRAGMA incremental_vacuum;")
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except Exception:
                    pass
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
                cursor = conn.execute("""
                SELECT DISTINCT trace_id, MIN(start_time) as min_time
                FROM spans
                GROUP BY trace_id
                ORDER BY min_time ASC
                LIMIT 50
                """)
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

                conn.execute("PRAGMA incremental_vacuum;")
                try:
                    conn.execute("PRAGMA wal_checkpoint(PASSIVE);")
                except Exception:
                    pass
                
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
                MAX(start_time) as newest_timestamp
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
                "retention_days": self.config.retention_days,
                "max_db_size_mb": self.config.max_db_size_mb,
            }
        finally:
            conn.close()
