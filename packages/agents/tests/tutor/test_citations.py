from brainbench_agents.tutor import ChunkRef, CitationLedger, citation_label, location_label


def ref(chunk: str, title: str, location: dict[str, object], score: float = 0.8) -> ChunkRef:
    return ChunkRef(chunk, "s-" + title, title, dict(location), f"text of {chunk}", score)


def test_location_labels_per_source_type() -> None:
    assert location_label({"page": 5}) == "p. 5"
    assert location_label({"path": "docs/raft.md"}) == "docs/raft.md"
    fragment = "#:~:text=Raft%20is%20a%20consensus%20algorithm%20designed%20to,understand"
    assert location_label({"fragment": fragment}) == "“Raft is a consensus algorithm designed…”"
    assert location_label({"fragment": "#:~:text=pre-,Leader%20election,-post"}) == (
        "“Leader election…”"
    )
    assert location_label({}) == "note"


def test_citation_label_keeps_brackets_unambiguous() -> None:
    assert citation_label("Raft [extended]  paper", {"page": 2}) == "[Raft (extended) paper, p. 2]"


def test_ledger_recognizes_exact_and_lenient_labels() -> None:
    ledger = CitationLedger()
    p5 = ref("c1", "Raft paper", {"page": 5})
    p9 = ref("c2", "Raft paper", {"page": 9})
    repo = ref("c3", "etcd", {"path": "raft/raft.go"})
    ledger.add([p5, p9, repo])
    text = (
        "Majorities overlap [raft PAPER,  p. 5]. Terms [Raft paper, page 9] and code "
        "[etcd, raft/raft.go]."
    )
    assert ledger.scan(text) == [p5, p9, repo]
    assert ledger.scan(text + " again [Raft paper, p. 5]") == []
    assert ledger.cited == [p5, p9, repo]


def test_ledger_never_cites_what_was_not_retrieved() -> None:
    ledger = CitationLedger()
    ledger.add([ref("c1", "Raft paper", {"page": 5})])
    assert ledger.scan("See [Paxos Made Simple, p. 3] and [Raft paper, p. 40] and [1].") == []
    assert ledger.cited == []
