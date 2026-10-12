import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import FakeSandbox, job_context, result, seed_topic, storyboard

from app.core.config import settings
from app.db.schema import Asset, UsageCounter
from app.db.schema import Storyboard as StoryboardRow
from app.jobs.errors import JobSkipped, PermanentJobError
from app.services.figure import CHROME_WRAPPER, NODE_ENTRYPOINT, run_figure
from app.services.sandbox import SandboxResult
from app.services.storyboard import VisualDeps
from brainbench_agents.visual import ChunkRef
from brainbench_agents.visual.testing import (
    ScriptedModel,
    Turn,
    call,
    result_text,
    say,
    tool_results,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
FIGURE_HTML = (
    "<!doctype html><html><body><svg viewBox='0 0 10 10'><rect width='4' height='4'/></svg>"
    "<script>document.title='quorum'</script></body></html>"
)
BRIEF = {
    "applicable": True,
    "rationale": "Ballot boxes make the quorum physical.",
    "object": "five ballot boxes on a bench",
    "true_numbers": [{"name": "servers", "value": "5"}],
    "motion": "a dial fails servers one by one",
    "readout": "votes needed for a majority",
}


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data = tmp_path / "data"
    skill = data / "skills" / "anatomy"
    (skill / "references").mkdir(parents=True)
    (skill / "scripts").mkdir()
    (skill / "SKILL.md").write_text("# Anatomy\n\nThe object is the explanation.")
    (skill / "references" / "verify.md").write_text("capture it")
    (skill / "scripts" / "capture.mjs").write_text("// capture")
    (data / "vault.key").write_bytes(b"k" * 32)
    monkeypatch.setattr(settings, "brainbench_data", data)
    return data


class Models:
    def __init__(self, **turns: list[Turn]) -> None:
        self.models = {step: ScriptedModel(list(t)) for step, t in turns.items()}
        self.steps: list[str] = []

    async def __call__(self, session: AsyncSession, step: str) -> ScriptedModel:
        self.steps.append(step)
        return self.models[step]


def deps(models: Models, sandbox: FakeSandbox) -> VisualDeps:
    def search(session: AsyncSession, topic_id: uuid.UUID) -> Any:
        async def run(query: str, k: int) -> Sequence[ChunkRef]:
            return []

        return run

    return VisualDeps(model=models, search=search, sandbox=sandbox, retry_strategy=None)  # type: ignore[arg-type]


def capture(png: bytes = PNG, name: str = "preview.png") -> Any:
    def step(mounts: dict[str, Path], command: list[str]) -> SandboxResult:
        (mounts["/workspace"] / name).write_bytes(png)
        return result(0, stdout="saved /workspace/preview.png\n", duration_s=12.0)

    return step


def figure_models(
    chunk_ids: list[str], session_turns: list[Turn], brief: dict[str, Any] = BRIEF
) -> Models:
    return Models(
        storyboard=[[call("Storyboard", storyboard(chunk_ids))]],
        figure_brief=[[call("FigureBrief", brief)]],
        figure=session_turns,
    )


async def test_figure_built_through_tools_becomes_a_ready_asset(
    session: AsyncSession, data_dir: Path
) -> None:
    seeded = await seed_topic(session)
    models = figure_models(
        seeded.chunk_ids,
        [
            [call("read_reference", {"name": "verify"})],
            [call("write_file", {"path": "build.mjs", "content": "// builds figure.html"})],
            [call("write_file", {"path": "figure.html", "content": FIGURE_HTML})],
            [call("read_file", {"path": "../../vault.key"})],
            [
                call(
                    "run_node",
                    {"args": ["/skill/scripts/capture.mjs", "figure.html", "--out", "preview.png"]},
                )
            ],
            [say("Built the ballot-box figure.")],
        ],
    )
    sandbox = FakeSandbox([capture()])
    ctx, rec = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})

    await run_figure(ctx, deps(models, sandbox))
    await session.commit()

    assert models.steps == ["storyboard", "figure_brief", "figure"]
    rejected = tool_results([models.models["figure"].requests[4]["last_message"]])[0]
    assert rejected["status"] == "error" and "outside the workspace" in result_text(rejected)
    (run,) = sandbox.runs
    assert run["image"] == "brainbench/anatomy"
    assert run["command"] == ["/skill/scripts/capture.mjs", "figure.html", "--out", "preview.png"]
    assert run["entrypoint"] == NODE_ENTRYPOINT and run["workdir"] == "/workspace"
    assert run["environment"]["CHROME_PATH"] == CHROME_WRAPPER
    assert [(m.target, m.read_only) for m in run["mounts"]] == [
        ("/workspace", False),
        ("/skill", True),
    ]
    assert run["mounts"][1].path == data_dir / "skills" / "anatomy"
    assert run["limits"].timeout_s == settings.figure_node_timeout_s

    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.format == "figure" and asset.is_current and asset.script is None
    assert (data_dir / asset.files["html"]).read_text() == FIGURE_HTML
    assert (data_dir / asset.files["png"]).read_bytes() == PNG
    assert asset.files["source"] == [f"assets/{seeded.topic_id}/{asset.id}/source/build.mjs"]
    provenance = asset.provenance
    assert provenance["brief"]["object"] == "five ballot boxes on a bench"
    assert provenance["build_source"] == [
        {"path": "build.mjs", "size": 21, "content": "// builds figure.html"}
    ]
    assert provenance["stop_reason"] == "end_turn" and provenance["turns"] == 6
    assert "assistant -> write_file(" in provenance["transcript_tail"]
    row = await session.get(StoryboardRow, asset.storyboard_id)
    assert (
        row is not None
        and row.format == "figure"
        and row.figure_brief
        == {
            **BRIEF,
            "parts": [],
            "theme": "dark",
            "true_numbers": [{"name": "servers", "value": "5", "unit": None, "chunk_id": None}],
        }
    )
    minutes = (await session.execute(select(UsageCounter))).scalar_one()
    assert minutes.value == pytest.approx(0.2)
    assert rec.progress[:3] == [
        "writing the storyboard",
        "deriving the figure brief",
        "building the figure",
    ]
    assert not (data_dir / "jobs" / str(ctx.job_id)).exists()


async def test_session_that_writes_nothing_fails_with_transcript(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    models = figure_models(
        seeded.chunk_ids, [[call("list_files", {})], [say("I could not build it.")]]
    )
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(PermanentJobError) as err:
        await run_figure(ctx, deps(models, FakeSandbox([])))
    assert err.value.reason == "no-figure"
    message = str(err.value)
    assert "without a standalone HTML figure" in message and "Transcript tail:" in message
    assert "assistant: I could not build it." in message
    assert "The workspace is empty." in message
    assert (await session.execute(select(Asset))).first() is None


async def test_not_applicable_brief_skips_without_a_session(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    models = figure_models(
        seeded.chunk_ids, [], brief={"applicable": False, "rationale": "A naming convention."}
    )
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(JobSkipped, match="A naming convention"):
        await run_figure(ctx, deps(models, FakeSandbox([])))
    assert models.steps == ["storyboard", "figure_brief"]
    row = (await session.execute(select(StoryboardRow))).scalar_one()
    assert row.figure_brief is not None and row.figure_brief["applicable"] is False
    brief_prompt = models.models["figure_brief"].requests[0]["last_message"]["content"][0]["text"]
    assert "A candidate becomes leader" in brief_prompt  # the cited chunks reach the brief


async def test_missing_preview_is_captured_by_the_job(session: AsyncSession) -> None:
    seeded = await seed_topic(session)
    models = figure_models(
        seeded.chunk_ids,
        [[call("write_file", {"path": "out/figure.html", "content": FIGURE_HTML})], [say("done")]],
    )
    sandbox = FakeSandbox([capture()])
    ctx, rec = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    await run_figure(ctx, deps(models, sandbox))
    assert sandbox.runs[0]["command"] == [
        "/skill/scripts/capture.mjs",
        "out/figure.html",
        "--out",
        "preview.png",
    ]
    assert "capturing the preview" in rec.progress
    asset = (await session.execute(select(Asset))).scalar_one()
    assert asset.provenance["html_source"] == "out/figure.html"


async def test_turn_budget_without_figure_fails_with_reason(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "figure_max_turns", 2)
    seeded = await seed_topic(session)
    models = figure_models(seeded.chunk_ids, [[call("list_files", {})]] * 5)
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(PermanentJobError, match="2-turn budget") as err:
        await run_figure(ctx, deps(models, FakeSandbox([])))
    assert err.value.reason == "turn-budget"


async def test_missing_skill_fails_before_the_session(
    session: AsyncSession, data_dir: Path
) -> None:
    (data_dir / "skills" / "anatomy" / "SKILL.md").unlink()
    seeded = await seed_topic(session)
    models = figure_models(seeded.chunk_ids, [])
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    with pytest.raises(PermanentJobError) as err:
        await run_figure(ctx, deps(models, FakeSandbox([])))
    assert err.value.reason == "skill-missing"


async def test_regeneration_seeds_previous_source_and_feedback(
    session: AsyncSession, data_dir: Path
) -> None:
    seeded = await seed_topic(session)
    first = figure_models(
        seeded.chunk_ids,
        [
            [call("write_file", {"path": "src/build.mjs", "content": "// v1"})],
            [call("write_file", {"path": "figure.html", "content": FIGURE_HTML})],
            [say("done")],
        ],
    )
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    await run_figure(ctx, deps(first, FakeSandbox([capture()])))
    await session.commit()
    old = (await session.execute(select(Asset))).scalar_one()

    def check_seeded(messages: Any) -> list[Any]:
        return [call("read_file", {"path": "src/build.mjs"})]

    second = figure_models(
        seeded.chunk_ids,
        [
            check_seeded,
            [call("write_file", {"path": "figure.html", "content": FIGURE_HTML})],
            [say("done")],
        ],
    )
    payload = {
        "topic_id": str(seeded.topic_id),
        "feedback": "make the dial bigger",
        "previous_asset_id": str(old.id),
    }
    ctx, _ = await job_context(session, "figure", payload)
    await run_figure(ctx, deps(second, FakeSandbox([capture()])))
    figure_model = second.models["figure"]
    prompt = figure_model.requests[0]["last_message"]["content"][0]["text"]
    assert "make the dial bigger" in prompt and "(src/build.mjs)" in prompt
    read = tool_results([figure_model.requests[1]["last_message"]])[0]
    assert result_text(read) == "// v1"
    assert (
        "make the dial bigger"
        in second.models["figure_brief"].requests[0]["last_message"]["content"][0]["text"]
    )
