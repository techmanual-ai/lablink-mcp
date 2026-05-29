"""Pytest configuration for lablink-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _disable_scpi_logging(monkeypatch):
    """Prevent tests from writing to the default log directory."""
    monkeypatch.setenv("LABLINK_LOG_DIR", "")
