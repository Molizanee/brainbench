from app.services.planner import (
    Edge,
    Placement,
    PlanTopic,
    Violation,
    break_cycles,
    check_order,
    clean_edges,
    find_cycle,
    insert_position,
    order_topics,
    pack_weeks,
    plan_topics,
    stable_order,
)


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


def topic(key: str, **kwargs: object) -> PlanTopic:
    data: dict[str, object] = {"estimate_minutes": 60, "relevance": 0.5, "title": key}
    data.update(kwargs)
    return PlanTopic(key=key, **data)  # type: ignore[arg-type]


def test_order_is_topological_with_priority_then_relevance_tie_breaks() -> None:
    topics = [
        topic("basics", relevance=0.5, priority_rank=2),
        topic("logs", relevance=0.9, priority_rank=0),
        topic("raft", relevance=0.6, priority_rank=0),
        topic("css", relevance=0.95, priority_rank=3),
        topic("paxos", relevance=0.7, priority_rank=1),
    ]
    edges = [Edge("raft", "logs"), Edge("raft", "basics"), Edge("logs", "basics")]

    order = order_topics(topics, edges)

    # basics is required by both high-priority topics, so it goes first despite its own rank.
    assert order == ["basics", "logs", "raft", "paxos", "css"]
    assert check_order(order, edges) == []
    assert order[-1] == "css"  # serves no target


def test_ties_without_prerequisites_follow_priority_then_relevance() -> None:
    topics = [
        topic("c", relevance=0.9, priority_rank=1),
        topic("a", relevance=0.5, priority_rank=0),
        topic("b", relevance=0.8, priority_rank=0),
    ]

    assert order_topics(topics, []) == ["b", "a", "c"]


def test_mastered_topics_move_last_unless_an_unmastered_topic_needs_them() -> None:
    topics = [
        topic("known", relevance=0.9, priority_rank=0, mastered=True),
        topic("foundation", relevance=0.9, priority_rank=0, mastered=True),
        topic("new", relevance=0.4, priority_rank=2),
        topic("advanced", relevance=0.3, priority_rank=2),
    ]
    edges = [Edge("advanced", "foundation")]

    order = order_topics(topics, edges)

    assert order.index("foundation") < order.index("advanced")
    assert order[-1] == "known"
    assert set(order) == {"known", "foundation", "new", "advanced"}


def test_order_survives_a_cycle_left_in_the_edges() -> None:
    order = order_topics([topic("a"), topic("b")], [Edge("a", "b", 0.9), Edge("b", "a", 0.1)])

    assert order == ["b", "a"]


def test_weekly_blocks_stay_within_the_budget() -> None:
    estimates = {"a": 120, "b": 150, "c": 60, "d": 300, "e": 30, "f": 270}
    order = list(estimates)

    placements = pack_weeks(order, estimates, 300)

    weeks: dict[int, int] = {}
    for p in placements:
        weeks[p.week_start] = weeks.get(p.week_start, 0) + estimates[p.key]
        assert p.week_span == 1 and not p.exceeds_weekly_budget
    assert all(total <= 300 for total in weeks.values())
    assert [p.week_start for p in placements] == [1, 1, 2, 3, 4, 4]


def test_oversized_topic_spans_weeks_alone() -> None:
    estimates = {"warmup": 100, "raft": 450, "after": 200, "huge": 601, "tail": 10}

    placements = {p.key: p for p in pack_weeks(list(estimates), estimates, 300)}

    assert placements["warmup"] == Placement("warmup", 1, 1, False)
    assert placements["raft"] == Placement("raft", 2, 2, True)
    assert placements["after"] == Placement("after", 4, 1, False)
    assert placements["huge"] == Placement("huge", 5, 3, True)
    assert placements["tail"] == Placement("tail", 8, 1, False)
    sharing = [p for p in placements.values() if p.week_start == 2]
    assert sharing == [placements["raft"]]


def test_topic_equal_to_the_budget_does_not_exceed_it() -> None:
    placements = pack_weeks(["a", "b"], {"a": 300, "b": 300}, 300)

    assert placements == [Placement("a", 1, 1, False), Placement("b", 2, 1, False)]


def test_plan_topics_orders_then_packs() -> None:
    topics = [topic("b", estimate_minutes=200), topic("a", estimate_minutes=200)]

    placements = plan_topics(topics, [Edge("a", "b")], 300)

    assert [(p.key, p.week_start) for p in placements] == [("b", 1), ("a", 2)]


def test_check_order_names_the_violated_prerequisite() -> None:
    edges = [Edge("raft", "logs"), Edge("raft", "terms")]

    assert check_order(["raft", "logs", "terms"], edges) == [
        Violation("raft", "logs"),
        Violation("raft", "terms"),
    ]
    assert check_order(["logs", "terms", "raft", "x"], edges) == []
    assert check_order(["raft"], edges) == []  # removed prerequisites do not block


def test_insert_position_respects_prerequisites_then_tie_break() -> None:
    order = ["a", "b", "c", "d"]
    keys = {
        "a": (0, -0.9, "a", "a"),
        "b": (0, -0.8, "b", "b"),
        "c": (0, -0.3, "c", "c"),
        "d": (2, -0.9, "d", "d"),
    }
    edges = [Edge("new", "a"), Edge("d", "new")]

    at = insert_position(
        order, topic("new", relevance=0.5, priority_rank=0), edges, keys.__getitem__
    )
    first = insert_position(
        order, topic("new", relevance=0.95, priority_rank=0), [], keys.__getitem__
    )
    last = insert_position(
        order, topic("new", relevance=0.1, priority_rank=3), [], keys.__getitem__
    )

    assert at == 2  # after a; before c (lower relevance); d requires it anyway
    assert first == 0 and last == 4


def test_stable_order_keeps_a_valid_order_and_minimally_fixes_an_invalid_one() -> None:
    edges = [Edge("c", "a")]

    assert stable_order(["b", "a", "c"], edges) == ["b", "a", "c"]
    assert stable_order(["c", "b", "a"], edges) == ["b", "a", "c"]
