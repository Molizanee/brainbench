"""Deterministic study planning (design D6, step 5): pure functions, no I/O.

Topics are identified by string keys (topic ids in practice). An edge ``Edge(topic, requires)``
means ``requires`` must be studied before ``topic``.

- :func:`order_topics`: a topological order of the prerequisites; among topics that are free to
  go next, unmastered topics first (a mastered topic keeps its place when an unmastered topic
  needs it), then higher target priority (a prerequisite counts with the best priority of the
  topics that need it), then higher relevance, then title.
- :func:`pack_weeks`: consecutive weekly blocks within the budget; a topic estimated above the
  budget sits alone in a block spanning ``ceil(estimate / budget)`` weeks, flagged.
- :func:`check_order` and :func:`insert_position` validate and extend an edited order.
"""

import heapq
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

PRIORITY_RANK = {"high": 0, "medium": 1, "low": 2}
NO_PRIORITY = len(PRIORITY_RANK)


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


@dataclass(frozen=True)
class PlanTopic:
    key: str
    estimate_minutes: int
    relevance: float = 0.0
    priority_rank: int = NO_PRIORITY
    mastered: bool = False
    title: str = ""


def sort_key(topic: PlanTopic) -> tuple[int, float, str, str]:
    """Tie-break among topics free to go next: target priority, relevance, title."""
    return (topic.priority_rank, -topic.relevance, topic.title.casefold(), topic.key)


def _requirements(edges: Sequence[Edge]) -> dict[str, set[str]]:
    requires: dict[str, set[str]] = {}
    for edge in edges:
        requires.setdefault(edge.topic, set()).add(edge.requires)
    return requires


def needed_by_unmastered(topics: Sequence[PlanTopic], edges: Sequence[Edge]) -> set[str]:
    """Keys that an unmastered topic requires, directly or transitively."""
    requires = _requirements(edges)
    needed: set[str] = set()
    stack = [t.key for t in topics if not t.mastered]
    while stack:
        for required in requires.get(stack.pop(), ()):
            if required not in needed:
                needed.add(required)
                stack.append(required)
    return needed


def _topological(
    by_key: dict[str, PlanTopic], requires: dict[str, set[str]], dependents: dict[str, list[str]]
) -> list[str]:
    """Any topological order (prerequisites first) of an acyclic graph."""
    waiting = {key: len(requires.get(key, ())) for key in by_key}
    queue = sorted(k for k, n in waiting.items() if n == 0)
    order: list[str] = []
    while queue:
        key = queue.pop()
        order.append(key)
        for dependent in dependents.get(key, ()):
            waiting[dependent] -= 1
            if waiting[dependent] == 0:
                queue.append(dependent)
    return order


def order_topics(topics: Sequence[PlanTopic], edges: Sequence[Edge]) -> list[str]:
    """Topological order with the tie-breaks above. Cycles must be broken beforehand; any edge
    left in a cycle is dropped here by :func:`break_cycles` so the order is always complete."""
    by_key = {t.key: t for t in topics}
    acyclic, _ = break_cycles(set(by_key), list(edges))
    needed = needed_by_unmastered(topics, acyclic)
    requires = _requirements(acyclic)
    dependents: dict[str, list[str]] = {}
    for edge in acyclic:
        dependents.setdefault(edge.requires, []).append(edge.topic)
    waiting = {key: len(requires.get(key, ())) for key in by_key}

    # A prerequisite inherits the best priority of the topics that need it, so the foundation
    # of a high-priority topic is not queued behind medium-priority work.
    rank = {key: t.priority_rank for key, t in by_key.items()}
    for key in reversed(_topological(by_key, requires, dependents)):
        for required in requires.get(key, ()):
            rank[required] = min(rank[required], rank[key])

    def heap_key(key: str) -> tuple[bool, tuple[int, float, str, str]]:
        topic = by_key[key]
        deferred = topic.mastered and key not in needed
        return (deferred, sort_key(replace(topic, priority_rank=rank[key])))

    ready = [(heap_key(k), k) for k, n in waiting.items() if n == 0]
    heapq.heapify(ready)
    order: list[str] = []
    while ready:
        _, key = heapq.heappop(ready)
        order.append(key)
        for dependent in dependents.get(key, ()):
            waiting[dependent] -= 1
            if waiting[dependent] == 0:
                heapq.heappush(ready, (heap_key(dependent), dependent))
    return order


@dataclass(frozen=True)
class Placement:
    key: str
    week_start: int
    week_span: int
    exceeds_weekly_budget: bool


def pack_weeks(
    order: Sequence[str], estimates: dict[str, int], budget_minutes: int
) -> list[Placement]:
    """Weekly blocks in plan order, weeks numbered from 1.

    Topics fill the current week while the running total stays within ``budget_minutes``; the
    next topic that does not fit starts a new week. A topic above the budget closes the current
    block and gets its own block of ``ceil(estimate / budget)`` weeks, flagged; the following
    topic starts after it.
    """
    budget = max(1, budget_minutes)
    placements: list[Placement] = []
    week, used = 1, 0
    for key in order:
        estimate = max(0, estimates[key])
        if estimate > budget:
            if used:
                week += 1
            span = math.ceil(estimate / budget)
            placements.append(Placement(key, week, span, True))
            week, used = week + span, 0
            continue
        if used and used + estimate > budget:
            week, used = week + 1, 0
        placements.append(Placement(key, week, 1, False))
        used += estimate
    return placements


def plan_topics(
    topics: Sequence[PlanTopic], edges: Sequence[Edge], budget_minutes: int
) -> list[Placement]:
    """Order then pack: the full deterministic planning step."""
    order = order_topics(topics, edges)
    return pack_weeks(order, {t.key: t.estimate_minutes for t in topics}, budget_minutes)


@dataclass(frozen=True)
class Violation:
    topic: str
    requires: str


def check_order(order: Sequence[str], edges: Sequence[Edge]) -> list[Violation]:
    """Edges whose required topic comes after (or without) its dependent in ``order``."""
    position = {key: i for i, key in enumerate(order)}
    return [
        Violation(e.topic, e.requires)
        for e in edges
        if e.topic in position
        and e.requires in position
        and position[e.requires] > position[e.topic]
    ]


def insert_position(
    order: Sequence[str],
    topic: PlanTopic,
    edges: Sequence[Edge],
    key_of: Callable[[str], tuple[int, float, str, str]],
) -> int:
    """Where to insert ``topic`` into ``order``: after its prerequisites, before its dependents,
    and among the topics in between by the planner's tie-break (``key_of`` gives theirs)."""
    position = {key: i for i, key in enumerate(order)}
    lower = max(
        (
            position[e.requires] + 1
            for e in edges
            if e.topic == topic.key and e.requires in position
        ),
        default=0,
    )
    upper = min(
        (position[e.topic] for e in edges if e.requires == topic.key and e.topic in position),
        default=len(order),
    )
    if lower >= upper:
        return lower
    mine = sort_key(topic)
    for index in range(lower, upper):
        if key_of(order[index]) > mine:
            return index
    return upper
