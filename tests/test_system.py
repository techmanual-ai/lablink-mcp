"""Tests for the system topology subsystem.

Covers:
- load_system(): missing file -> None, malformed TOML -> ConfigError, valid parse
- device_slice(): link/net filtering for both alias and passive-id nodes
- validate_system(): all four warning checks
- system_topology tool: no file, malformed file, full graph, alias slice
- connect() topology_context injection (the safety-critical route)
- diagnose() topology_context injection
- _system_audit() topology_warnings (malformed topology must not flip ready)
- Error isolation §4.2: malformed topology never breaks connect/diagnose/audit
- Unknown severity loads without error and produces a soft warning
- alias/id namespace collision warning
"""

import dataclasses
import json
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import lablink.mcp_server as mcp_server
from lablink.base import (
    Constraint,
    ConnectResult,
    DeviceConnections,
    DiagnosticResult,
    Link,
    Net,
    NetEndpoint,
    SystemNode,
    SystemTopology,
)
from lablink.exceptions import ConfigError
from lablink.system import device_slice, validate_system


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RF_BENCH_TOML = textwrap.dedent("""\
    name = "rf_validation_bench"

    [[node]]
    alias = "siglent_sdg6022"
    role  = "signal generator"
    [[node]]
    alias = "tek_mso44"
    role  = "oscilloscope"
    [[node]]
    alias = "dut_serial"
    role  = "DUT"
    [[node]]
    alias = "keysight_e36313"
    role  = "power supply"
    [[node]]
    id   = "pad_10db"
    role = "10 dB attenuator"

    [[link]]
    from   = "siglent_sdg6022:OUTPUT1"
    to     = "pad_10db:IN"
    signal = "stimulus waveform"
    params = { impedance_ohm = 50, coupling = "AC" }

    [[link]]
    from   = "pad_10db:OUT"
    to     = "dut_serial:WFM_IN_B"
    signal = "stimulus waveform (attenuated)"
    params = { impedance_ohm = 50 }

    [[link]]
    from   = "dut_serial:SIG_OUT_A"
    to     = "tek_mso44:CH2"
    signal = "DUT response"
    params = { probe = "10x" }

    [[link]]
    from   = "keysight_e36313:CH1"
    to     = "dut_serial:12V_IN"
    signal = "DC power"
    params = { nominal_v = 12.0 }
      [[link.constraint]]
      severity = "critical"
      limit    = "voltage <= 13.5"
      note     = "DUT is damaged above 13.5 V on 12V_IN."

    [[net]]
    name      = "ref_10mhz"
    signal    = "10 MHz reference clock"
    params    = { frequency_hz = 10_000_000 }
    endpoints = [
      { port = "siglent_sdg6022:REF_OUT", role = "master" },
      { port = "tek_mso44:REF_IN",        role = "slave"  },
      { port = "dut_serial:10MHZ_REF",    role = "slave"  },
    ]
""")


@pytest.fixture()
def rf_bench_toml(tmp_path) -> Path:
    p = tmp_path / "topology.toml"
    p.write_text(_RF_BENCH_TOML, encoding="utf-8")
    return p


@pytest.fixture()
def rf_bench_topology(rf_bench_toml) -> SystemTopology:
    from lablink.config import load_system

    with patch("lablink.config.get_topology_file", return_value=rf_bench_toml):
        topo = load_system()
    assert topo is not None
    return topo


# ---------------------------------------------------------------------------
# load_system()
# ---------------------------------------------------------------------------


class TestLoadSystem:
    def test_missing_file_returns_none(self, tmp_path):
        from lablink.config import load_system

        with patch("lablink.config.get_topology_file", return_value=tmp_path / "no.toml"):
            result = load_system()
        assert result is None

    def test_valid_parse(self, rf_bench_topology):
        topo = rf_bench_topology
        assert topo.name == "rf_validation_bench"
        assert len(topo.nodes) == 5
        assert len(topo.links) == 4
        assert len(topo.nets) == 1

    def test_passive_node_has_id_not_alias(self, rf_bench_topology):
        pad = next(n for n in rf_bench_topology.nodes if n.id == "pad_10db")
        assert pad.alias is None

    def test_constraint_parsed(self, rf_bench_topology):
        power_link = next(lk for lk in rf_bench_topology.links if lk.signal == "DC power")
        assert len(power_link.constraints) == 1
        c = power_link.constraints[0]
        assert c.severity == "critical"
        assert c.limit == "voltage <= 13.5"

    def test_net_endpoints_parsed(self, rf_bench_topology):
        net = rf_bench_topology.nets[0]
        assert net.name == "ref_10mhz"
        assert len(net.endpoints) == 3
        roles = {ep.role for ep in net.endpoints}
        assert roles == {"master", "slave"}

    def test_malformed_toml_raises_config_error(self, tmp_path):
        from lablink.config import load_system

        bad = tmp_path / "topology.toml"
        bad.write_text("not valid toml !!!! [[[", encoding="utf-8")
        with patch("lablink.config.get_topology_file", return_value=bad):
            with pytest.raises(ConfigError):
                load_system()

    def test_node_missing_both_alias_and_id_raises_config_error(self, tmp_path):
        from lablink.config import load_system

        bad = tmp_path / "topology.toml"
        bad.write_text('[[node]]\nrole = "something"\n', encoding="utf-8")
        with patch("lablink.config.get_topology_file", return_value=bad):
            with pytest.raises(ConfigError, match="alias.*id"):
                load_system()

    def test_constraint_missing_severity_raises_config_error(self, tmp_path):
        from lablink.config import load_system

        toml = textwrap.dedent("""\
            [[node]]
            alias = "dev_a"
            [[link]]
            from = "dev_a:OUT"
            to   = "dev_a:IN"
              [[link.constraint]]
              limit = "v <= 5"
              note  = "severity omitted"
        """)
        p = tmp_path / "topology.toml"
        p.write_text(toml, encoding="utf-8")
        with patch("lablink.config.get_topology_file", return_value=p):
            with pytest.raises(ConfigError, match="severity"):
                load_system()

    def test_unknown_severity_loads_without_error(self, tmp_path):
        from lablink.config import load_system

        toml = textwrap.dedent("""\
            [[node]]
            alias = "dev_a"
            [[link]]
            from = "dev_a:OUT"
            to   = "dev_a:IN"
              [[link.constraint]]
              severity = "catastrophic"
              limit    = "v <= 5"
        """)
        p = tmp_path / "topology.toml"
        p.write_text(toml, encoding="utf-8")
        with patch("lablink.config.get_topology_file", return_value=p):
            topo = load_system()
        assert topo is not None
        assert topo.links[0].constraints[0].severity == "catastrophic"


# ---------------------------------------------------------------------------
# device_slice()
# ---------------------------------------------------------------------------


class TestDeviceSlice:
    def test_managed_device_slice(self, rf_bench_topology):
        s = device_slice(rf_bench_topology, "dut_serial")
        # dut_serial appears in: pad->dut link, dut->tek link, power link, ref net
        assert len(s.links) == 3
        assert len(s.nets) == 1
        assert "pad_10db" in s.neighbors
        assert "tek_mso44" in s.neighbors
        assert "keysight_e36313" in s.neighbors

    def test_passive_id_node_slice(self, rf_bench_topology):
        s = device_slice(rf_bench_topology, "pad_10db")
        assert len(s.links) == 2
        assert "siglent_sdg6022" in s.neighbors
        assert "dut_serial" in s.neighbors

    def test_device_not_in_topology_returns_empty(self, rf_bench_topology):
        s = device_slice(rf_bench_topology, "unknown_alias")
        assert s.links == []
        assert s.nets == []
        assert s.neighbors == []
        assert s.constraints == []

    def test_constraints_collected(self, rf_bench_topology):
        s = device_slice(rf_bench_topology, "dut_serial")
        severities = [c.severity for c in s.constraints]
        assert "critical" in severities

    def test_serializes_cleanly_through_asdict(self, rf_bench_topology):
        """Confirm the topology_context dict (what crosses the MCP boundary) is
        JSON-serializable and contains severity as a plain string."""
        from dataclasses import asdict

        s = device_slice(rf_bench_topology, "dut_serial")
        d = asdict(s)
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        # Find the critical constraint
        all_constraints = [
            c for lk in parsed["links"] for c in lk["constraints"]
        ]
        critical = [c for c in all_constraints if c["severity"] == "critical"]
        assert len(critical) == 1


# ---------------------------------------------------------------------------
# validate_system()
# ---------------------------------------------------------------------------


class TestValidateSystem:
    def test_valid_bench_no_warnings(self, rf_bench_topology):
        known = ["siglent_sdg6022", "tek_mso44", "dut_serial", "keysight_e36313"]
        warnings = validate_system(rf_bench_topology, known)
        assert warnings == []

    def test_unresolved_port_prefix(self):
        topo = SystemTopology(
            nodes=[SystemNode(alias="dev_a")],
            links=[Link(from_port="dev_a:OUT", to_port="ghost:IN")],
        )
        warnings = validate_system(topo, ["dev_a"])
        assert any("ghost" in w for w in warnings)

    def test_declared_but_unconfigured_device(self, rf_bench_topology):
        warnings = validate_system(rf_bench_topology, [])  # no .toml on disk
        assert any("siglent_sdg6022" in w for w in warnings)

    def test_unknown_severity_produces_warning(self):
        topo = SystemTopology(
            nodes=[SystemNode(alias="dev_a"), SystemNode(alias="dev_b")],
            links=[
                Link(
                    from_port="dev_a:OUT",
                    to_port="dev_b:IN",
                    constraints=[Constraint(severity="catastrophic", limit="v <= 5")],
                )
            ],
        )
        warnings = validate_system(topo, ["dev_a", "dev_b"])
        assert any("catastrophic" in w for w in warnings)

    def test_alias_id_collision_warning(self):
        topo = SystemTopology(
            nodes=[
                SystemNode(alias="dev_a"),
                SystemNode(id="dev_a"),  # collides with the alias above
            ],
            links=[],
        )
        warnings = validate_system(topo, ["dev_a"])
        assert any("collision" in w.lower() or "shadowed" in w.lower() for w in warnings)

    def test_does_not_raise(self, rf_bench_topology):
        validate_system(rf_bench_topology, [])  # must never raise


# ---------------------------------------------------------------------------
# do_system_topology()
# ---------------------------------------------------------------------------


class TestSystemTopologyTool:
    def test_no_topology_file_returns_success_with_note(self, tmp_path):
        with patch("lablink.mcp_server.load_system", return_value=None), \
             patch("lablink.mcp_server.get_topology_file", return_value=tmp_path / "topology.toml"):
            result = mcp_server.do_system_topology()
        assert result["success"] is True
        assert result["topology"] is None
        assert "note" in result["metadata"]

    def test_malformed_file_returns_success_false(self):
        with patch("lablink.mcp_server.load_system", side_effect=ConfigError("bad TOML")):
            result = mcp_server.do_system_topology()
        assert result["success"] is False
        assert "bad TOML" in result["error"]
        assert "lablink topology validate" in result["hint"]

    def test_full_graph_returned_without_alias(self, rf_bench_topology):
        with patch("lablink.mcp_server.load_system", return_value=rf_bench_topology):
            result = mcp_server.do_system_topology()
        assert result["success"] is True
        assert "topology" in result
        assert result["topology"]["name"] == "rf_validation_bench"

    def test_alias_slice_returned_with_alias(self, rf_bench_topology):
        with patch("lablink.mcp_server.load_system", return_value=rf_bench_topology):
            result = mcp_server.do_system_topology("dut_serial")
        assert result["success"] is True
        assert "topology_context" in result
        assert result["topology_context"]["alias"] == "dut_serial"

    def test_alias_with_no_wiring_returns_empty_slice(self, rf_bench_topology):
        with patch("lablink.mcp_server.load_system", return_value=rf_bench_topology):
            result = mcp_server.do_system_topology("not_in_topology")
        assert result["success"] is True
        assert result["topology_context"]["links"] == []


# ---------------------------------------------------------------------------
# connect() topology_context injection
# ---------------------------------------------------------------------------


class TestConnectTopologyInjection:
    def _make_driver(self):
        driver = MagicMock()
        driver.connect.return_value = ConnectResult(
            success=True, alias="dut_serial", interface_type="visa", identity="DUT"
        )
        return driver

    def test_topology_context_injected_on_connect(self, rf_bench_topology):
        from lablink.interfaces.visa import VisaDriverConfig

        config = VisaDriverConfig(
            alias="dut_serial", type="visa", timeout_ms=5000, resource_string="USB0::INSTR"
        )
        driver = self._make_driver()
        with patch("lablink.mcp_server.load_config", return_value=config), \
             patch("lablink.mcp_server.get_driver", return_value=driver), \
             patch("lablink.mcp_server.load_device_memory", return_value=None), \
             patch("lablink.mcp_server.load_system", return_value=rf_bench_topology), \
             patch("lablink.mcp_server._missing_python_deps", return_value=[]):
            result = mcp_server.do_connect("dut_serial")

        assert result["success"] is True
        tc = result["topology_context"]
        assert tc is not None
        assert tc["alias"] == "dut_serial"
        # Critical constraint must surface through connect()
        all_c = [c for lk in tc["links"] for c in lk["constraints"]]
        assert any(c["severity"] == "critical" for c in all_c)

    def test_malformed_topology_does_not_break_connect(self):
        from lablink.interfaces.visa import VisaDriverConfig

        config = VisaDriverConfig(
            alias="scope", type="visa", timeout_ms=5000, resource_string="USB0::INSTR"
        )
        driver = self._make_driver()
        driver.connect.return_value = ConnectResult(
            success=True, alias="scope", interface_type="visa"
        )
        with patch("lablink.mcp_server.load_config", return_value=config), \
             patch("lablink.mcp_server.get_driver", return_value=driver), \
             patch("lablink.mcp_server.load_device_memory", return_value=None), \
             patch("lablink.mcp_server.load_system", side_effect=ConfigError("bad")), \
             patch("lablink.mcp_server._missing_python_deps", return_value=[]):
            result = mcp_server.do_connect("scope")

        assert result["success"] is True
        assert result["topology_context"] is None


# ---------------------------------------------------------------------------
# diagnose() topology_context injection
# ---------------------------------------------------------------------------


class TestDiagnoseTopologyInjection:
    def test_topology_context_injected_on_diagnose(self, rf_bench_topology):
        from lablink.interfaces.visa import VisaDriverConfig

        config = VisaDriverConfig(
            alias="dut_serial", type="visa", timeout_ms=5000, resource_string="USB0::INSTR"
        )
        driver = MagicMock()
        driver.diagnose.return_value = DiagnosticResult(
            ready=True, alias="dut_serial", interface_type="visa"
        )
        with patch("lablink.mcp_server.load_config", return_value=config), \
             patch("lablink.mcp_server.get_driver", return_value=driver), \
             patch("lablink.mcp_server.load_device_memory", return_value=None), \
             patch("lablink.mcp_server.load_system", return_value=rf_bench_topology), \
             patch("lablink.mcp_server._missing_python_deps", return_value=[]):
            result = mcp_server.do_diagnose("dut_serial")

        assert result["ready"] is True
        tc = result["topology_context"]
        assert tc is not None
        assert tc["alias"] == "dut_serial"


# ---------------------------------------------------------------------------
# _system_audit() topology_warnings
# ---------------------------------------------------------------------------


class TestSystemAuditTopologyWarnings:
    def _run_audit_with_topo(self, topo, known_aliases=None):
        """Helper: run _system_audit with a mocked topology and known_aliases."""
        if known_aliases is None:
            known_aliases = []
        with patch("lablink.mcp_server.load_system", return_value=topo), \
             patch("lablink.mcp_server.list_configured_aliases", return_value=known_aliases):
            return mcp_server._system_audit()

    def test_malformed_topology_does_not_flip_ready(self):
        with patch("lablink.mcp_server.load_system", side_effect=ConfigError("bad")), \
             patch("lablink.mcp_server.list_configured_aliases", return_value=[]):
            result = mcp_server._system_audit()
        # ready reflects driver deps only — topology error must not change it
        assert "topology_error" not in result.get("action_items", [])
        assert any("parse error" in w.lower() for w in result.get("topology_warnings", []))

    def test_topology_parse_error_appears_in_topology_warnings_not_action_items(self):
        with patch("lablink.mcp_server.load_system", side_effect=ConfigError("oops")), \
             patch("lablink.mcp_server.list_configured_aliases", return_value=[]):
            result = mcp_server._system_audit()
        topo_warns = result.get("topology_warnings", [])
        action_items = result.get("action_items", [])
        assert any("oops" in w for w in topo_warns)
        assert not any("oops" in item for item in action_items)

    def test_unresolved_port_warning_in_topology_warnings(self):
        topo = SystemTopology(
            nodes=[SystemNode(alias="dev_a")],
            links=[Link(from_port="dev_a:OUT", to_port="ghost:IN")],
        )
        result = self._run_audit_with_topo(topo, ["dev_a"])
        topo_warns = result.get("topology_warnings", [])
        assert any("ghost" in w for w in topo_warns)
        # Must not appear in action_items
        action_items = result.get("action_items", [])
        assert not any("ghost" in item for item in action_items)

    def test_no_topology_file_produces_no_topology_warnings(self):
        result = self._run_audit_with_topo(topo=None, known_aliases=[])
        assert result.get("topology_warnings", []) == []
