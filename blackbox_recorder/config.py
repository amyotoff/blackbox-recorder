"""
Configuration management for blackbox-recorder.
"""

from __future__ import annotations

import dataclasses
from typing import Union


@dataclasses.dataclass
class BlackBoxConfig:
    """
    Configuration options for BlackBox Tracer.
    """
    db_path: str = "blackbox_traces.db"
    retention: Union[str, int] = "30d"       # "7d", "30d", "60d", "week", "month", "2months" or int days
    max_db_size_mb: int = 300               # Hard disk cap: older traces evicted when exceeded
    enabled: bool = True                    # Master toggle
    batch_size: int = 100                   # Flush batch size
    flush_interval_seconds: float = 0.5     # Maximum wait before worker thread persists queue
    cleanup_interval_hours: int = 6         # Periodic TTL & size cleanup interval
    capture_inputs: bool = True             # Record function inputs
    capture_outputs: bool = True            # Record function return values
    max_field_chars: int = 100_000          # Safeguard character cap for single payload

    @property
    def retention_days(self) -> int:
        """Parse human-friendly retention string or int to days."""
        if isinstance(self.retention, int):
            return max(1, self.retention)
        
        val = str(self.retention).strip().lower()
        if val in ("7d", "7days", "week", "1w", "1week"):
            return 7
        elif val in ("30d", "30days", "month", "1m", "1month"):
            return 30
        elif val in ("60d", "60days", "2m", "2months", "2month"):
            return 60
        elif val in ("90d", "90days", "3m", "3months", "3month", "quarter"):
            return 90
        elif val.endswith("d"):
            try:
                return max(1, int(val[:-1]))
            except ValueError:
                return 30
        else:
            try:
                return max(1, int(val))
            except ValueError:
                return 30
