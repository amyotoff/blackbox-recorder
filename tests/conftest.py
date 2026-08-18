"""
Pytest configuration and shared fixtures for blackbox-recorder tests.
"""

import os
import shutil
import tempfile
import pytest

from blackbox_recorder.config import BlackBoxConfig
from blackbox_recorder.tracer import Tracer


# Standard test personas per rules:
# Alice: tg_id: '111', username: 'alice'
# Bob:   tg_id: '222', username: 'bob'
# Bender: tg_id: '333', username: 'bender'
PERSONA_ALICE = {"tg_id": "111", "username": "alice"}
PERSONA_BOB = {"tg_id": "222", "username": "bob"}
PERSONA_BENDER = {"tg_id": "333", "username": "bender"}


@pytest.fixture
def temp_dir():
    d = tempfile.mkdtemp(prefix="blackbox_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def temp_db_path(temp_dir):
    return os.path.join(temp_dir, "test_traces.db")


@pytest.fixture
def test_tracer(temp_db_path):
    config = BlackBoxConfig(
        db_path=temp_db_path,
        retention="7d",
        max_db_size_mb=10,
        flush_interval_seconds=0.05,
    )
    t = Tracer(config=config)
    yield t
    t.close()
