"""The figure job against the real ``brainbench/anatomy`` sandbox and the vendored skill.

Needs the skill installed at ``$BRAINBENCH_DATA/skills/anatomy`` (``just visual-integration``
installs the pinned commit there); it is copied into the test's own data directory.
"""

import shutil
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.visual_seed import job_context, seed_topic, storyboard

from app.core.config import settings
from app.db.schema import Asset
from app.services.figure import run_figure
from app.services.sandbox import SandboxRunner
from app.services.storyboard import VisualDeps
from brainbench_agents.visual import ChunkRef
from brainbench_agents.visual.testing import ScriptedModel, call, result_text, say, tool_results

pytestmark = pytest.mark.integration

INSTALLED_SKILL = settings.skills_dir / "anatomy"
BUILD = """\
import { writeFileSync } from "node:fs";
const cells = Array.from({ length: 5 }, (_, i) =>
  `<rect x="${10 + i * 40}" y="40" width="30" height="30" fill="#4a8"/>`).join("");
writeFileSync("figure.html", `<!doctype html><html><head><style>
body{margin:0;background:#111}.iso-plate{width:240px;height:120px}</style></head><body>
<div class="iso-plate"><svg viewBox="0 0 240 120">${cells}</svg></div>
<script>document.title = "quorum";</script></body></html>`);
console.log("wrote figure.html");
"""
BRIEF = {
    "applicable": True,
    "rationale": "Ballot boxes make the quorum physical.",
    "object": "five ballot boxes",
    "true_numbers": [{"name": "servers", "value": "5"}],
    "motion": "a dial fails servers",
    "readout": "votes needed",
}


@pytest.fixture
def data(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    if not (INSTALLED_SKILL / "SKILL.md").is_file():
        pytest.skip(f"needs the Anatomy skill at {INSTALLED_SKILL} (just visual-integration)")
    shutil.copytree(INSTALLED_SKILL, data_dir / "skills" / "anatomy", symlinks=True)
    monkeypatch.setattr(settings, "brainbench_data", data_dir)
    return data_dir


async def test_figure_written_through_tools_renders_in_the_real_sandbox(
    session: AsyncSession, anatomy_image: str, data: Path
) -> None:
    seeded = await seed_topic(session)
    figure_model = ScriptedModel(
        [
            [call("write_file", {"path": "build.mjs", "content": BUILD})],
            [call("run_node", {"args": ["build.mjs"]})],
            [call("run_node", {"args": ["-e", "require('child_process')"]})],
            [
                call(
                    "run_node",
                    {"args": ["/skill/scripts/capture.mjs", "figure.html", "--out", "preview.png"]},
                )
            ],
            [say("Five ballot boxes.")],
        ]
    )
    models = {
        "storyboard": ScriptedModel([[call("Storyboard", storyboard(seeded.chunk_ids))]]),
        "figure_brief": ScriptedModel([[call("FigureBrief", BRIEF)]]),
        "figure": figure_model,
    }

    async def model(session: AsyncSession, step: str) -> ScriptedModel:
        return models[step]

    def search(session: AsyncSession, topic_id: uuid.UUID) -> Any:
        async def run(query: str, k: int) -> list[ChunkRef]:
            return []

        return run

    deps = VisualDeps(
        model=model,
        search=search,
        sandbox=SandboxRunner(data_dir=data, host_data_dir=data),
        retry_strategy=None,
    )
    ctx, _ = await job_context(session, "figure", {"topic_id": str(seeded.topic_id)})
    await run_figure(ctx, deps)
    await session.commit()

    build_result = result_text(tool_results([figure_model.requests[2]["last_message"]])[0])
    assert "exited with code 0" in build_result and "wrote figure.html" in build_result
    refused = tool_results([figure_model.requests[3]["last_message"]])[0]
    assert refused["status"] == "error" and "node flags" in result_text(refused)
    after_capture = figure_model.requests[4]["last_message"]["content"]
    assert any("image" in block for block in after_capture)

    asset = (await session.execute(select(Asset))).scalar_one()
    png = (data / asset.files["png"]).read_bytes()
    assert png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000
    assert "<svg" in (data / asset.files["html"]).read_text()
    assert [s["path"] for s in asset.provenance["build_source"]] == ["build.mjs"]
