from app.services.planner import Edge, break_cycles, clean_edges, find_cycle


def test_clean_edges_drops_self_loops_unknown_nodes_and_weaker_duplicates() -> None:
    edges = [
        Edge("b", "a", 0.4),
        Edge("b", "a", 0.7),
        Edge("a", "a", 1.0),
        Edge("c", "zzz", 0.9),
    ]

    assert clean_edges({"a", "b", "c"}, edges) == [Edge("b", "a", 0.7)]


def test_acyclic_graph_is_kept_whole() -> None:
    edges = [Edge("b", "a"), Edge("c", "b"), Edge("c", "a")]

    kept, dropped = break_cycles({"a", "b", "c"}, edges)

    assert find_cycle(edges) is None
    assert kept == sorted(edges, key=lambda e: (e.topic, e.requires)) and dropped == []


def test_cycle_loses_its_lowest_confidence_edge() -> None:
    edges = [Edge("b", "a", 0.9), Edge("c", "b", 0.8), Edge("a", "c", 0.3)]

    kept, dropped = break_cycles({"a", "b", "c"}, edges)

    assert Edge("a", "c", 0.3) not in kept and len(kept) == 2
    [drop] = dropped
    assert drop.edge == Edge("a", "c", 0.3)
    assert sorted(drop.cycle) == ["a", "b", "c"]


def test_overlapping_cycles_each_drop_one_edge() -> None:
    # a <-> b and b -> c -> d -> b: two cycles sharing b.
    edges = [
        Edge("a", "b", 0.9),
        Edge("b", "a", 0.2),
        Edge("b", "c", 0.7),
        Edge("c", "d", 0.6),
        Edge("d", "b", 0.5),
    ]

    kept, dropped = break_cycles({"a", "b", "c", "d"}, edges)

    assert {d.edge for d in dropped} == {Edge("b", "a", 0.2), Edge("d", "b", 0.5)}
    assert find_cycle(kept) is None


def test_ties_break_deterministically() -> None:
    edges = [Edge("b", "a", 0.5), Edge("a", "b", 0.5)]

    first = break_cycles({"a", "b"}, edges)
    second = break_cycles({"a", "b"}, list(reversed(edges)))

    assert first == second
    assert [d.edge for d in first[1]] == [Edge("a", "b", 0.5)]
