"""Deterministic study planning (design D6, step 5): pure functions, no I/O.

Topics are identified by string keys (topic ids in practice). An edge ``Edge(topic, requires)``
means ``requires`` must be studied before ``topic``.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Edge:
    topic: str
    requires: str
    confidence: float = 1.0


@dataclass(frozen=True)
class DroppedEdge:
    edge: Edge
    # The cycle the edge closed, as topic keys in dependency order.
    cycle: tuple[str, ...]


def clean_edges(nodes: set[str], edges: list[Edge]) -> list[Edge]:
    """Edges between known, distinct nodes; a repeated pair keeps its highest confidence."""
    best: dict[tuple[str, str], Edge] = {}
    for edge in edges:
        if edge.topic == edge.requires or edge.topic not in nodes or edge.requires not in nodes:
            continue
        key = (edge.topic, edge.requires)
        if key not in best or edge.confidence > best[key].confidence:
            best[key] = edge
    return sorted(best.values(), key=lambda e: (e.topic, e.requires))


def find_cycle(edges: list[Edge]) -> list[Edge] | None:
    """One cycle of ``edges`` (as the edges along it), or None when the graph is acyclic."""
    out: dict[str, list[Edge]] = {}
    for edge in sorted(edges, key=lambda e: (e.topic, e.requires)):
        out.setdefault(edge.topic, []).append(edge)
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished
    path: list[Edge] = []

    def visit(node: str) -> list[Edge] | None:
        state[node] = 1
        for edge in out.get(node, []):
            nxt = edge.requires
            if state.get(nxt) == 1:
                # nxt is on the path (no self-loops after cleaning): the cycle starts there.
                start = next(i for i, e in enumerate(path) if e.topic == nxt)
                return [*path[start:], edge]
            if nxt not in state:
                path.append(edge)
                found = visit(nxt)
                if found is not None:
                    return found
                path.pop()
        state[node] = 2
        return None

    for node in sorted(out):
        if node not in state:
            found = visit(node)
            if found is not None:
                return found
    return None


def break_cycles(nodes: set[str], edges: list[Edge]) -> tuple[list[Edge], list[DroppedEdge]]:
    """Make the prerequisite graph acyclic by dropping, per cycle, its lowest-confidence edge.

    Ties break on the edge's keys so the result is deterministic. Returns the kept edges and the
    dropped ones with the cycle each closed.
    """
    kept = clean_edges(nodes, edges)
    dropped: list[DroppedEdge] = []
    while (cycle := find_cycle(kept)) is not None:
        weakest = min(cycle, key=lambda e: (e.confidence, e.topic, e.requires))
        kept.remove(weakest)
        dropped.append(DroppedEdge(weakest, tuple(e.topic for e in cycle)))
    return kept, dropped
