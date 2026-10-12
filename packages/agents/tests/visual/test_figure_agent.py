import asyncio
from pathlib import Path
from typing import Any

import pytest

from brainbench_agents.visual import (
    FigureBrief,
    FigureWorkspace,
    NodeRun,
    Storyboard,
    WorkspaceError,
    collect_outputs,
    figure_prompt,
    make_figure_agent,
    run_figure_session,
)
from brainbench_agents.visual.testing import ScriptedModel, call, result_text, say, tool_results

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
FIGURE_HTML = (
    "<!doctype html><html><head><style>svg{width:100%}</style></head><body>"
    '<svg viewBox="0 0 10 10"><rect width="4" height="4"/></svg>'
    "<script>document.title='ok'</script></body></html>"
)
BRIEF = FigureBrief.model_validate(
    {
        "applicable": True,
        "rationale": "A ballot box shows the quorum.",
        "object": "five ballot boxes on a bench",
        "true_numbers": [{"name": "servers", "value": "5"}],
        "motion": "a dial fails servers",
        "readout": "votes needed for a majority",
    }
)
STORYBOARD = Storyboard.model_validate(
    {
        "title": "Quorum",
        "scenes": [
            {"id": "intro", "objective": "o", "narration": "n", "visual": "v", "chunk_ids": ["c1"]}
        ],
    }
)


@pytest.fixture
def skill(tmp_path: Path) -> Path:
    root = tmp_path / "skill"
    (root / "references").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "kit").mkdir()
    (root / "SKILL.md").write_text("# Anatomy\n\nThe object is the explanation.")
    (root / "references" / "verify.md").write_text("Run capture.mjs.")
    (root / "scripts" / "capture.mjs").write_text("// capture")
    (root / "kit" / "iso-kit.mjs").write_text("export const x = 1")
    (tmp_path / "secret.txt").write_text("vault key")
    return root


class Node:
    """Fake sandbox: records commands and runs a side effect inside the workspace."""

    def __init__(self, root: Path, effect: Any = None, run: NodeRun | None = None) -> None:
        self.root = root
        self.effect = effect
        self.result = run or NodeRun(0, "built\n", "", duration_s=1.5)
        self.commands: list[list[str]] = []

    async def __call__(self, command: list[str]) -> NodeRun:
        self.commands.append(command)
        if self.effect is not None:
            self.effect(self.root)
        return self.result


@pytest.fixture
def workspace(tmp_path: Path, skill: Path) -> FigureWorkspace:
    root = tmp_path / "work"
    root.mkdir()
    return FigureWorkspace(root, skill, Node(root))


def test_paths_are_confined_to_the_workspace(workspace: FigureWorkspace, tmp_path: Path) -> None:
    assert workspace.write_file("src/build.mjs", "x") == "wrote src/build.mjs (1 bytes)"
    assert workspace.read_file("/workspace/src/build.mjs") == "x"
    for bad in ("../secret.txt", "/etc/passwd", "src/../../secret.txt", "~/x", ""):
        with pytest.raises(WorkspaceError):
            workspace.read_file(bad)
        with pytest.raises(WorkspaceError):
            workspace.write_file(bad, "x")
    (workspace.root / "link").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(WorkspaceError, match="outside"):
        workspace.read_file("link")
    assert "link" not in workspace.list_files()
    assert workspace.read_reference("verify") == "Run capture.mjs."
    with pytest.raises(WorkspaceError, match="unknown reference"):
        workspace.read_reference("../SKILL")
    with pytest.raises(WorkspaceError, match="available: verify"):
        workspace.read_reference("motion")


def test_only_node_scripts_in_the_workspace_or_skill_may_run(workspace: FigureWorkspace) -> None:
    workspace.write_file("build.mjs", "console.log(1)")
    assert workspace.node_command(["build.mjs", "--light"]) == ["/workspace/build.mjs", "--light"]
    assert workspace.node_command(
        ["/skill/scripts/capture.mjs", "figure.html", "--out", "/workspace/shots/a.png"]
    ) == ["/skill/scripts/capture.mjs", "figure.html", "--out", "/workspace/shots/a.png"]
    for bad in (
        ["-e", "require('fs')"],
        ["--require", "build.mjs"],
        ["missing.mjs"],
        ["notes.txt"],
        ["/skill/SKILL.mjs"],
        ["/skill/scripts/../../etc/x.mjs"],
        ["/usr/lib/node.mjs"],
        ["build.mjs", "--out", "/etc/x.png"],
        ["build.mjs", "--out", "../x.png"],
        ["build.mjs", "file:///etc/passwd"],
        [],
    ):
        with pytest.raises(WorkspaceError):
            workspace.node_command(bad)


def test_collector_finds_the_figure_by_content(workspace: FigureWorkspace) -> None:
    workspace.write_file("out/page.html", FIGURE_HTML)
    workspace.write_file("out/page-light.html", FIGURE_HTML + "<!-- light -->")
    workspace.write_file(
        "remote.html", FIGURE_HTML.replace("<head>", '<head><script src="https://cdn.x/y.js">')
    )
    workspace.write_file("build.mjs", "// build")
    (workspace.root / "shots").mkdir()
    (workspace.root / "shots" / "a.png").write_bytes(PNG)
    (workspace.root / "fake.png").write_bytes(b"not a png")
    outputs = collect_outputs(workspace)
    assert outputs.html == workspace.root / "out" / "page.html"
    assert outputs.png == workspace.root / "shots" / "a.png"
    assert [workspace.relative(p) for p in outputs.sources] == ["build.mjs"]
    workspace.write_file("figure.html", FIGURE_HTML)
    (workspace.root / "preview.png").write_bytes(PNG)
    outputs = collect_outputs(workspace)
    assert outputs.html is not None and outputs.html.name == "figure.html"
    assert outputs.png is not None and outputs.png.name == "preview.png"


async def test_session_builds_figure_through_tools_and_sees_its_png(
    tmp_path: Path, skill: Path
) -> None:
    root = tmp_path / "work"
    root.mkdir()

    def capture(workspace_root: Path) -> None:
        (workspace_root / "preview.png").write_bytes(PNG)

    node = Node(root, capture)
    workspace = FigureWorkspace(root, skill, node)
    model = ScriptedModel(
        [
            [say("Reading the verify notes."), call("read_reference", {"name": "verify"})],
            [call("write_file", {"path": "figure.html", "content": FIGURE_HTML})],
            [call("read_file", {"path": "../secret.txt"})],
            [
                call(
                    "run_node",
                    {"args": ["/skill/scripts/capture.mjs", "figure.html", "--out", "preview.png"]},
                )
            ],
            [say("Done: five ballot boxes and a quorum dial.")],
        ]
    )
    agent = make_figure_agent(model, workspace, brief=BRIEF, storyboard=STORYBOARD)
    session = await run_figure_session(
        agent, figure_prompt("Quorum", language="en"), max_turns=10, timeout_s=30
    )

    assert session.stop_reason == "end_turn" and not session.timed_out
    assert session.turns == 5
    system = model.requests[0]["system_prompt"]
    assert system.startswith("# Anatomy") and "five ballot boxes on a bench" in system
    assert "Content-Security-Policy" in system
    assert model.requests[0]["tools"] == [
        "list_files",
        "read_file",
        "write_file",
        "read_reference",
        "run_node",
    ]
    rejected = tool_results([model.requests[3]["last_message"]])[0]
    assert rejected["status"] == "error" and "outside the workspace" in result_text(rejected)
    assert node.commands == [["/skill/scripts/capture.mjs", "figure.html", "--out", "preview.png"]]
    after_node = model.requests[4]["last_message"]["content"]
    assert any("image" in block for block in after_node)  # the model saw its own PNG
    assert "PNG files written: preview.png" in result_text(
        tool_results([model.requests[4]["last_message"]])[0]
    )
    outputs = collect_outputs(workspace)
    assert outputs.html == root / "figure.html" and outputs.png == root / "preview.png"
    transcript = session.transcript_tail()
    assert "assistant -> write_file(" in transcript
    assert "tool <- [error]" in transcript
    assert transcript.endswith("assistant: Done: five ballot boxes and a quorum dial.")


async def test_turn_budget_stops_the_session(workspace: FigureWorkspace) -> None:
    model = ScriptedModel([[call("list_files", {})]] * 10)
    agent = make_figure_agent(model, workspace, brief=BRIEF, storyboard=STORYBOARD)
    session = await run_figure_session(agent, "build", max_turns=3, timeout_s=30)
    assert session.stop_reason == "limit_turns" and session.turns == 3
    assert len(model.requests) == 3


async def test_wall_clock_limit_and_cancellation_stop_the_session(
    tmp_path: Path, skill: Path
) -> None:
    root = tmp_path / "work"
    root.mkdir()
    (root / "build.mjs").write_text("// slow")

    class SlowNode(Node):
        async def __call__(self, command: list[str]) -> NodeRun:
            await asyncio.sleep(0.3)
            return await super().__call__(command)

    workspace = FigureWorkspace(root, skill, SlowNode(root))
    model = ScriptedModel([[call("run_node", {"args": ["build.mjs"]})]] * 20)
    agent = make_figure_agent(model, workspace, brief=BRIEF, storyboard=STORYBOARD)
    session = await run_figure_session(agent, "build", max_turns=50, timeout_s=0.5, poll_s=0.05)
    assert session.timed_out and session.stop_reason == "cancelled"
    assert len(model.requests) < 5

    model = ScriptedModel([[call("list_files", {})]] * 20)
    agent = make_figure_agent(model, workspace, brief=BRIEF, storyboard=STORYBOARD)
    session = await run_figure_session(
        agent, "build", max_turns=50, timeout_s=30, cancelled=lambda: True, poll_s=0.01
    )
    assert session.cancelled and not session.timed_out and session.stop_reason == "cancelled"


def test_figure_prompt_names_previous_source_and_feedback() -> None:
    prompt = figure_prompt(
        "Quorum", language="de", feedback="bigger dial", previous_files=["build.mjs"]
    )
    assert "de" in prompt and "bigger dial" in prompt and "(build.mjs)" in prompt
