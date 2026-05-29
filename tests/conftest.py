"""Pytest configuration for lablink-mcp tests."""

import pytest


@pytest.fixture(autouse=True)
def _disable_scpi_logging(monkeypatch):
    """Prevent tests from writing to the default log directory."""
    monkeypatch.setenv("LABLINK_LOG_DIR", "")


@pytest.fixture(autouse=True)
def _disable_auto_migration(monkeypatch):
    """Prevent tests from migrating the developer's real ~/.agentlink/instruments/
    into ~/.lablink/devices/ as a side effect. Tests that exercise the migration
    explicitly re-enable it inside the test body.
    """
    monkeypatch.setenv("LABLINK_AUTO_MIGRATE", "0")
