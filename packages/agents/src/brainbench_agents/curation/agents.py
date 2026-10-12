"""Agent factories for the curation steps and the usage hook (design D6).

One factory per task class: ``topic_extraction``, ``topic_merge``, ``scoring``,
``prerequisites``. Each builds a Strands ``Agent`` with its system prompt, the tools it may call,
and a :class:`UsageRecorder`. :func:`run_structured` runs one call whose answer is the step's
Pydantic output model; each call starts from an empty conversation, so a factory's agent can be
reused across windows and batches.
"""

from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from brainbench_agents.curation.models import (
    Prerequisites,
    TopicExtraction,
    TopicMerge,
    TopicScoring,
)
from brainbench_agents.curation.prompts import (
    PREREQUISITES_SYSTEM,
    SCORING_SYSTEM,
    TOPIC_EXTRACTION_SYSTEM,
    TOPIC_MERGE_SYSTEM,
)

__lazy_modules__ = {
    "strands",
    "strands.event_loop._retry",
    "strands.hooks",
    "strands.models.model",
    "strands.types.tools",
}
from strands import Agent
from strands.event_loop._retry import ModelRetryStrategy
from strands.hooks import AfterInvocationEvent, HookCallback, HookProvider, HookRegistry
from strands.models.model import Model
from strands.types.tools import AgentTool

TOPIC_EXTRACTION = "topic_extraction"
TOPIC_MERGE = "topic_merge"
SCORING = "scoring"
PREREQUISITES = "prerequisites"

# Task classes the curation job routes to providers, in pipeline order.
CURATION_TASK_CLASSES: tuple[str, ...] = (TOPIC_EXTRACTION, TOPIC_MERGE, SCORING, PREREQUISITES)

OUTPUT_MODELS: dict[str, type[BaseModel]] = {
    TOPIC_EXTRACTION: TopicExtraction,
    TOPIC_MERGE: TopicMerge,
    SCORING: TopicScoring,
    PREREQUISITES: Prerequisites,
}

SYSTEM_PROMPTS: dict[str, str] = {
    TOPIC_EXTRACTION: TOPIC_EXTRACTION_SYSTEM,
    TOPIC_MERGE: TOPIC_MERGE_SYSTEM,
    SCORING: SCORING_SYSTEM,
    PREREQUISITES: PREREQUISITES_SYSTEM,
}

# Receives (step, usage) after every agent invocation, usage being
# {"input_tokens", "output_tokens", "total_tokens"} for that invocation only.
type UsageSink = Callable[[str, dict[str, int]], None]


class UsageRecorder(HookProvider):
    """``AfterInvocationEvent`` hook: hands each invocation's token usage to ``sink``.

    Provider-level ``provider_usage`` rows are written by the model adapter; this hook gives the
    caller the per-step totals for its own log (the curation run's step log). The agent's
    metrics accumulate across invocations, so the recorder reports the difference since the last
    one, and it reports a failed invocation's usage too.
    """

    def __init__(self, step: str, sink: UsageSink) -> None:
        self.step = step
        self.sink = sink
        self._seen = (0, 0)

    def register_hooks(self, registry: HookRegistry, **kwargs: Any) -> None:
        registry.add_callback(AfterInvocationEvent, self._after_invocation)

    def _after_invocation(self, event: AfterInvocationEvent) -> None:
        usage = event.agent.event_loop_metrics.accumulated_usage
        total_in, total_out = int(usage.get("inputTokens", 0)), int(usage.get("outputTokens", 0))
        delta_in, delta_out = total_in - self._seen[0], total_out - self._seen[1]
        self._seen = (total_in, total_out)
        self.sink(
            self.step,
            {
                "input_tokens": delta_in,
                "output_tokens": delta_out,
                "total_tokens": delta_in + delta_out,
            },
        )


def _agent(
    step: str,
    model: Model,
    *,
    tools: Sequence[AgentTool] = (),
    usage_sink: UsageSink | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    hooks: list[HookProvider | HookCallback[Any]] = (
        [UsageRecorder(step, usage_sink)] if usage_sink else []
    )
    extra: dict[str, Any] = {} if retry_strategy is None else {"retry_strategy": retry_strategy}
    return Agent(
        model=model,
        system_prompt=SYSTEM_PROMPTS[step],
        tools=list(tools),
        hooks=hooks,
        callback_handler=None,
        name=step,
        **extra,
    )


def topic_extraction_agent(
    model: Model,
    *,
    search_chunks: AgentTool | None = None,
    usage_sink: UsageSink | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    """Extracts candidate topics with chunk citations from one window of a source."""
    tools = [search_chunks] if search_chunks is not None else []
    return _agent(
        TOPIC_EXTRACTION,
        model,
        tools=tools,
        usage_sink=usage_sink,
        retry_strategy=retry_strategy,
    )


def topic_merge_agent(
    model: Model,
    *,
    usage_sink: UsageSink | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    """Writes one topic per group of near-duplicate candidates."""
    return _agent(TOPIC_MERGE, model, usage_sink=usage_sink, retry_strategy=retry_strategy)


def scoring_agent(
    model: Model,
    *,
    get_profile: AgentTool | None = None,
    get_sessions: AgentTool | None = None,
    search_chunks: AgentTool | None = None,
    usage_sink: UsageSink | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    """Scores a batch of topics against the profile and session history."""
    tools = [t for t in (get_profile, get_sessions, search_chunks) if t is not None]
    return _agent(SCORING, model, tools=tools, usage_sink=usage_sink, retry_strategy=retry_strategy)


def prerequisites_agent(
    model: Model,
    *,
    usage_sink: UsageSink | None = None,
    retry_strategy: ModelRetryStrategy | None = None,
) -> Agent:
    """Proposes prerequisite edges, with confidence, between the kept topics."""
    return _agent(PREREQUISITES, model, usage_sink=usage_sink, retry_strategy=retry_strategy)


async def run_structured[T: BaseModel](agent: Agent, output_model: type[T], prompt: str) -> T:
    """One structured call on a fresh conversation; the answer is validated as ``output_model``.

    Uses the agent loop with ``structured_output_model`` (Strands' current form of
    ``agent.structured_output``), so the agent may call its tools first and the usage hook sees
    an ``AgentResult``. Model errors propagate unchanged (a provider throttle stays detectable).
    """
    agent.messages.clear()
    result = await agent.invoke_async(prompt, structured_output_model=output_model)
    output = result.structured_output
    if not isinstance(output, output_model):
        raise TypeError(f"{agent.name} returned no {output_model.__name__}")
    return output
