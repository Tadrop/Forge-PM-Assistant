"""Daily PM briefing — deterministic text generation, no LLM call.

CLAUDE.md §5 PM BRIEFING:
    "Daily 7am — At-risk / Stuck / Who needs help"

This stays deterministic because:
- The PM reads it every morning; consistent format > clever phrasing.
- No client-facing content → no honesty risk.
- Saves an API call per PM per day (18 projects across 2 PMs).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from pydantic import BaseModel, ConfigDict, Field

from ..health.models import HealthColor, ProjectHealth
from ..tone.models import PMProfile
from ..tone.registry import PMRegistry

_SEVERITY: dict[HealthColor, int] = {
    HealthColor.RED: 0,
    HealthColor.YELLOW: 1,
    HealthColor.GREEN: 2,
}
_COLOR_LABEL: dict[HealthColor, str] = {
    HealthColor.RED: "[RED]",
    HealthColor.YELLOW: "[YELLOW]",
    HealthColor.GREEN: "[GREEN]",
}


class ProjectSummary(BaseModel):
    """One-line summary of a project's state in the briefing."""

    model_config = ConfigDict(frozen=True)

    project_id: str
    project_name: str
    color: HealthColor
    headline: str
    bullets: list[str] = Field(default_factory=list)


class DailyBriefing(BaseModel):
    """Slack message body + structured summaries — sent to the PM each morning."""

    model_config = ConfigDict(frozen=True)

    pm_id: str
    pm_name: str
    as_of: date
    body: str
    summaries: list[ProjectSummary] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def red_count(self) -> int:
        return sum(1 for s in self.summaries if s.color is HealthColor.RED)

    @property
    def yellow_count(self) -> int:
        return sum(1 for s in self.summaries if s.color is HealthColor.YELLOW)

    @property
    def green_count(self) -> int:
        return sum(1 for s in self.summaries if s.color is HealthColor.GREEN)


def _summarize(health: ProjectHealth) -> ProjectSummary:
    sig = health.signals
    bullets = list(sig.worst_examples)
    headline = (
        f"{sig.stuck_blocker_count} stuck, "
        f"{sig.overdue_count} overdue, "
        f"{sig.scope_creep_count} scope, "
        f"{sig.recent_missed_deadline_count} missed-this-week"
    )
    return ProjectSummary(
        project_id=health.project_id,
        project_name=health.project_name,
        color=health.color,
        headline=headline,
        bullets=bullets,
    )


def _render_body(pm: PMProfile, as_of: date, summaries: list[ProjectSummary]) -> str:
    reds = [s for s in summaries if s.color is HealthColor.RED]
    yellows = [s for s in summaries if s.color is HealthColor.YELLOW]
    greens = [s for s in summaries if s.color is HealthColor.GREEN]

    lines = [
        f"Morning, {pm.name}. Daily briefing — {as_of.isoformat()}.",
        "",
        f"Status: {len(reds)} red, {len(yellows)} yellow, {len(greens)} green "
        f"across {len(summaries)} projects.",
        "",
    ]

    for section_label, group in (("RED — needs attention today", reds),
                                  ("YELLOW — at risk", yellows),
                                  ("GREEN — on track", greens)):
        if not group:
            continue
        lines.append(section_label)
        for s in group:
            lines.append(f"  {_COLOR_LABEL[s.color]} {s.project_name} — {s.headline}")
            for b in s.bullets:
                lines.append(f"      - {b}")
        lines.append("")

    if not summaries:
        lines.append("No projects assigned to you. Nothing to flag.")

    return "\n".join(lines).rstrip() + "\n"


def generate_briefing(
    pm: PMProfile,
    healths: list[ProjectHealth],
    *,
    as_of: date | None = None,
) -> DailyBriefing:
    """Build a briefing covering only the projects this PM owns."""
    as_of = as_of or date.today()
    own = [h for h in healths if h.project_id in pm.projects]
    own.sort(key=lambda h: (_SEVERITY[h.color], h.project_name))
    summaries = [_summarize(h) for h in own]

    return DailyBriefing(
        pm_id=pm.id,
        pm_name=pm.name,
        as_of=as_of,
        body=_render_body(pm, as_of, summaries),
        summaries=summaries,
    )


def build_briefings_for_all_pms(
    registry: PMRegistry,
    healths: list[ProjectHealth],
    *,
    as_of: date | None = None,
) -> list[DailyBriefing]:
    """One briefing per PM in the registry."""
    return [generate_briefing(pm, healths, as_of=as_of) for pm in registry.all()]
