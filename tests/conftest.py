"""Pytest configuration for agentlink-visa tests."""

import pytest


@pytest.fixture(autouse=True)
def _disable_scpi_logging(monkeypatch):
    """Prevent tests from writing to the default log directory."""
    monkeypatch.setenv("AGENTLINK_LOG_DIR", "")
