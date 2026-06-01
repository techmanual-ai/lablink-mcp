"""System topology graph logic.

Graph operations over a parsed SystemTopology. Pure functions — no I/O,
no driver deps. Callers are responsible for guarding the None case (absent
topology file) before passing to these functions.

Two public functions:
  device_slice(topology, alias) -> DeviceConnections
  validate_system(topology, known_aliases) -> list[str]
"""

from lablink.base import (
    Constraint,
    DeviceConnections,
    Link,
    Net,
    SystemTopology,
)

_KNOWN_SEVERITIES = frozenset({"info", "warning", "critical"})


def _port_prefix(port: str) -> str:
    """Return the handle portion of an ``<handle>:<PORT>`` port string."""
    return port.split(":")[0]


def _build_handle_sets(topology: SystemTopology) -> tuple[set[str], set[str]]:
    """Return (alias_set, id_set) for all nodes in the topology."""
    aliases: set[str] = set()
    ids: set[str] = set()
    for node in topology.nodes:
        if node.alias is not None:
            aliases.add(node.alias)
        if node.id is not None:
            ids.add(node.id)
    return aliases, ids


def device_slice(topology: SystemTopology, alias: str) -> DeviceConnections:
    """Return the subset of the topology that references ``alias``.

    Walks all links and nets, collecting those whose port prefixes resolve
    to ``alias``. Port resolution follows the spec: alias-first, then id —
    but this function only cares about the target alias, so it checks whether
    any port prefix equals ``alias`` directly.

    Neighbors are the other handles (alias or id) on matching links/nets.
    Constraints are the flat union of all constraints on matching links/nets.

    Args:
        topology: A parsed SystemTopology (never None).
        alias: The device alias to filter for.

    Returns:
        DeviceConnections for the given alias (may be empty if no wiring).
    """
    matched_links: list[Link] = []
    matched_nets: list[Net] = []
    neighbor_handles: set[str] = set()
    all_constraints: list[Constraint] = []

    for link in topology.links:
        from_h = _port_prefix(link.from_port)
        to_h = _port_prefix(link.to_port)
        if alias in (from_h, to_h):
            matched_links.append(link)
            all_constraints.extend(link.constraints)
            other = to_h if from_h == alias else from_h
            if other != alias:
                neighbor_handles.add(other)

    for net in topology.nets:
        handles = {_port_prefix(ep.port) for ep in net.endpoints}
        if alias in handles:
            matched_nets.append(net)
            all_constraints.extend(net.constraints)
            for h in handles:
                if h != alias:
                    neighbor_handles.add(h)

    return DeviceConnections(
        alias=alias,
        links=matched_links,
        nets=matched_nets,
        neighbors=sorted(neighbor_handles),
        constraints=all_constraints,
    )


def validate_system(topology: SystemTopology, known_aliases: list[str]) -> list[str]:
    """Return a list of soft advisory warnings about the topology.

    Never raises. Four checks (see docs/system_connections_plan.md §6 file 3):
      1. Unresolved port prefix — prefix matches no node alias and no node id.
      2. Declared-but-unconfigured device — a managed node whose alias has no
         <alias>.toml on disk. Informational: a bench may be mapped before all
         configs are written.
      3. Unknown severity — a constraint whose severity is outside
         {info, warning, critical}.
      4. alias/id namespace collision — a passive node whose id equals some
         other node's alias (would be silently shadowed by alias-first resolution).

    Args:
        topology: A parsed SystemTopology (never None).
        known_aliases: Alias stems from config.list_configured_aliases().

    Returns:
        List of human-readable warning strings; empty means no issues found.
    """
    warnings: list[str] = []
    alias_set, id_set = _build_handle_sets(topology)
    all_handles = alias_set | id_set
    known_set = set(known_aliases)

    # Check 1: unresolved port prefixes
    all_port_prefixes: list[str] = []
    for link in topology.links:
        all_port_prefixes.append(_port_prefix(link.from_port))
        all_port_prefixes.append(_port_prefix(link.to_port))
    for net in topology.nets:
        for ep in net.endpoints:
            all_port_prefixes.append(_port_prefix(ep.port))

    seen_unresolved: set[str] = set()
    for prefix in all_port_prefixes:
        if prefix not in all_handles and prefix not in seen_unresolved:
            warnings.append(
                f"Unresolved port prefix '{prefix}': matches no node alias or id."
            )
            seen_unresolved.add(prefix)

    # Check 2: declared-but-unconfigured managed devices
    for node in topology.nodes:
        if node.alias is not None and node.alias not in known_set:
            warnings.append(
                f"Node alias '{node.alias}' has no device config "
                f"({node.alias}.toml not found). "
                "The bench may be partially configured."
            )

    # Check 3: unknown severity values
    all_constraints: list[Constraint] = []
    for link in topology.links:
        all_constraints.extend(link.constraints)
    for net in topology.nets:
        all_constraints.extend(net.constraints)

    seen_unknown_sev: set[str] = set()
    for c in all_constraints:
        if c.severity not in _KNOWN_SEVERITIES and c.severity not in seen_unknown_sev:
            warnings.append(
                f"Constraint has unrecognized severity '{c.severity}'. "
                f"Known values: {sorted(_KNOWN_SEVERITIES)}. "
                "Treat as at-least-warning."
            )
            seen_unknown_sev.add(c.severity)

    # Check 4: alias/id namespace collision
    for node in topology.nodes:
        if node.id is not None and node.id in alias_set:
            warnings.append(
                f"Passive node id '{node.id}' collides with a managed node alias. "
                "Port resolution uses alias-first, so this id will be shadowed."
            )

    return warnings
