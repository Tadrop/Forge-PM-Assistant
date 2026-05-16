"""Nudge drafter — per-PM tone, stuck-vs-untouched aware.

Pipeline:
    1. Resolve PM owning the project (PMRegistry)
    2. Embed a query describing the nudge intent
    3. Retrieve top-K past nudges from the PM's tone namespace
    4. Build prompt with tone examples + flag-specific guidance
    5. Claude drafts → wrapped in NudgeDraft (requires_pm_approval=True)
"""
from __future__ import annotations

import logging
from typing import Protocol

from ..monitor.models import Task, TaskFlag
from ..scope.embeddings import Embedder
from ..tone.models import PMProfile, ToneExample
from ..tone.registry import PMRegistry
from ..tone.tone_store import ToneStore
from .models import NudgeDraft

logger = logging.getLogger(__name__)


FLAG_GUIDANCE = {
    TaskFlag.STUCK_BLOCKER: (
        "This task is 'in progress' with dependencies and has had no activity for 5+ days. "
        "Ask about the BLOCKING DEPENDENCY specifically — what's holding it up upstream. "
        "Do not assume the assignee is at fault."
    ),
    TaskFlag.UNTOUCHED: (
        "This task is 'to do' with no dependencies and has had no activity for 5+ days. "
        "Ask whether the assignee has the context they need to START. "
        "Do not scold — they may not have been pinged about it yet."
    ),
}


SYSTEM_PROMPT = """You are drafting an internal Slack nudge ON BEHALF OF a Forge Creative Agency project manager.
The PM will REVIEW your draft before sending — never imply it has already been sent or approved.

You MUST imitate the PM's voice. Sentence length, openers, sign-offs, formality,
emoji usage — match the tone examples exactly. Different PMs sound different;
your output should reflect THIS PM, not a generic helpful tone.

Flag-specific behavior is non-negotiable:
- stuck_blocker: ask about the dependency, not the person's effort.
- untouched: ask about context/onboarding, not about whether they've started.

HARD RULES:
- Under 60 words. One paragraph. Slack message, not email.
- No headers, no bullet points, no markdown lists.
- Open by addressing the assignee directly.
- Never invent deadlines, commitments, or context that wasn't given.
- Never include "[draft]" markers — the system adds those."""


class NudgeLLM(Protocol):
    def draft(
        self,
        *,
        task: Task,
        flag: TaskFlag,
        pm: PMProfile,
        tone_examples: list[ToneExample],
    ) -> str: ...


def _format_tone_examples(examples: list[ToneExample]) -> str:
    if not examples:
        return "  (no past nudges available — write in a neutral professional voice)"
    return "\n".join(f"  {i+1}. {e.text}" for i, e in enumerate(examples))


def build_user_prompt(
    task: Task,
    flag: TaskFlag,
    pm: PMProfile,
    tone_examples: list[ToneExample],
) -> str:
    assignee_block = (
        ", ".join(task.assignee_names) if task.assignee_names else "(unassigned)"
    )
    return f"""PM voice: {pm.name}
Tone examples from {pm.name}:
{_format_tone_examples(tone_examples)}

Project: {task.project_name}
Task: {task.name}
Assignee(s): {assignee_block}
Flag: {flag.value}

Flag guidance:
{FLAG_GUIDANCE[flag]}

Draft the Slack nudge now, in {pm.name}'s voice."""


class ClaudeNudgeLLM:
    def __init__(self, api_key: str, model: str = "claude-opus-4-7", *, max_tokens: int = 400):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        from anthropic import Anthropic  # type: ignore

        self._client = Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens

    def draft(self, *, task, flag, pm, tone_examples) -> str:
        msg = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.4,  # a bit of voice variation, still grounded
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": build_user_prompt(task, flag, pm, tone_examples),
                }
            ],
        )
        for block in msg.content:
            if getattr(block, "type", None) == "text":
                return block.text.strip()
        return ""


class NudgeDrafter:
    def __init__(
        self,
        llm: NudgeLLM,
        embedder: Embedder,
        tone_store: ToneStore,
        pm_registry: PMRegistry,
        *,
        top_k: int = 3,
    ):
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        self.llm = llm
        self.embedder = embedder
        self.tone_store = tone_store
        self.pm_registry = pm_registry
        self.top_k = top_k

    @staticmethod
    def supports(flag: TaskFlag) -> bool:
        return flag in FLAG_GUIDANCE

    def draft(self, task: Task, flag: TaskFlag) -> NudgeDraft:
        if not self.supports(flag):
            raise ValueError(
                f"NudgeDrafter only handles stuck_blocker and untouched; got {flag}"
            )
        pm = self.pm_registry.for_project(task.project_id)

        query = f"{flag.value} nudge about: {task.name}"
        qvec = self.embedder.embed_query(query)
        tone_examples = self.tone_store.retrieve(pm.tone_namespace, qvec, self.top_k)

        body = self.llm.draft(
            task=task, flag=flag, pm=pm, tone_examples=tone_examples
        )

        draft = NudgeDraft(
            task_id=task.id,
            task_name=task.name,
            project_id=task.project_id,
            project_name=task.project_name,
            pm_id=pm.id,
            pm_name=pm.name,
            assignee_names=task.assignee_names,
            assignee_slack_user_ids=[],  # populated by future ClickUp↔Slack mapping
            flag=flag,
            body=body,
            tone_example_ids=[e.example_id for e in tone_examples],
            tone_example_previews=[e.text[:80] for e in tone_examples],
        )
        logger.info(
            "nudge_drafted task_id=%s pm=%s flag=%s tone_examples=%d",
            task.id,
            pm.id,
            flag.value,
            len(tone_examples),
        )
        return draft
