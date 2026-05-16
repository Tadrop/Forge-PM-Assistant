"""Draft sinks — where generated drafts are written for PM review.

CLAUDE.md §3: "Never send anything externally without PM approval."

The default sink is FileSystemDraftSink: every draft is written as JSON
on disk, the PM opens it, copies the body where it belongs, hits send.
In live mode the SlackDraftSink and GmailDraftSink layer on top to put
the draft *in the PM's queue* — but the destination is always the PM,
never the client or leadership directly.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from ..briefing.briefing import DailyBriefing
from ..drafters.models import AgendaDraft, EscalationDraft, NudgeDraft, StatusDraft

logger = logging.getLogger(__name__)


class DraftSink(Protocol):
    def emit_nudge(self, draft: NudgeDraft) -> None: ...
    def emit_status(self, draft: StatusDraft) -> None: ...
    def emit_escalation(self, draft: EscalationDraft) -> None: ...
    def emit_agenda(self, draft: AgendaDraft) -> None: ...
    def emit_briefing(self, briefing: DailyBriefing) -> None: ...


def _write_json(path: Path, payload: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")


class FileSystemDraftSink:
    """Writes every draft to ./drafts/<YYYY-MM-DD>/<type>/<id>.json.

    Safe by construction — no external API calls. This is the default sink
    when DRY_RUN=true (which is the .env.example default).
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _today_dir(self, subdir: str) -> Path:
        return self.root / date.today().isoformat() / subdir

    def emit_nudge(self, draft: NudgeDraft) -> None:
        _write_json(self._today_dir("nudges") / f"{draft.task_id}.json", draft)
        logger.info("emit_nudge.fs task_id=%s pm=%s", draft.task_id, draft.pm_id)

    def emit_status(self, draft: StatusDraft) -> None:
        _write_json(self._today_dir("status") / f"{draft.project_id}.json", draft)
        logger.info("emit_status.fs project_id=%s pm=%s", draft.project_id, draft.pm_id)

    def emit_escalation(self, draft: EscalationDraft) -> None:
        _write_json(self._today_dir("escalations") / f"{draft.project_id}.json", draft)
        logger.info(
            "emit_escalation.fs project_id=%s pm=%s", draft.project_id, draft.pm_id
        )

    def emit_agenda(self, draft: AgendaDraft) -> None:
        _write_json(
            self._today_dir("agendas") / f"{draft.project_id}_{draft.meeting_date.isoformat()}.json",
            draft,
        )
        logger.info("emit_agenda.fs project_id=%s pm=%s", draft.project_id, draft.pm_id)

    def emit_briefing(self, briefing: DailyBriefing) -> None:
        _write_json(self._today_dir("briefings") / f"{briefing.pm_id}.json", briefing)
        logger.info("emit_briefing.fs pm=%s", briefing.pm_id)


class SlackDraftSink:
    """Posts to a PM review channel via slack-sdk. Internal use only.

    Used for: nudges, briefings, escalations (all internal communication).
    Status and agenda drafts go to Gmail, not Slack.
    """

    def __init__(self, bot_token: str, pm_review_channel: str, leadership_channel: str):
        if not bot_token:
            raise ValueError("SLACK_BOT_TOKEN is required")
        from slack_sdk import WebClient  # type: ignore

        self._client = WebClient(token=bot_token)
        self.pm_review_channel = pm_review_channel
        self.leadership_channel = leadership_channel

    def _post(self, channel: str, text: str, *, blocks: list | None = None) -> None:
        from slack_sdk.errors import SlackApiError  # type: ignore

        try:
            self._client.chat_postMessage(channel=channel, text=text, blocks=blocks)
        except SlackApiError as exc:
            logger.error("slack_post_failed channel=%s err=%s", channel, exc.response)
            raise

    def emit_nudge(self, draft: NudgeDraft) -> None:
        header = (
            f":memo: *Nudge draft for {draft.pm_name}* — "
            f"task `{draft.task_name}` ({draft.flag.value}) on {draft.project_name}"
        )
        body = f"{header}\n```\n{draft.body}\n```\n_Requires PM approval before sending._"
        self._post(self.pm_review_channel, body)

    def emit_status(self, draft: StatusDraft) -> None:
        # Status drafts live in Gmail; this is a Slack heads-up only.
        head = (
            f":envelope_with_arrow: *Weekly status draft ready* — "
            f"{draft.project_name} ({draft.color.value})"
        )
        self._post(self.pm_review_channel, f"{head}\nCheck your Gmail drafts.")

    def emit_escalation(self, draft: EscalationDraft) -> None:
        head = (
            f":rotating_light: *Escalation draft for {draft.pm_name}* — "
            f"{draft.project_name} just flipped to RED"
        )
        body = (
            f"{head}\n```\n{draft.body}\n```\n"
            f"_Target channel after approval:_ {draft.channel}\n"
            "_Requires PM approval before sending._"
        )
        self._post(self.pm_review_channel, body)

    def emit_agenda(self, draft: AgendaDraft) -> None:
        head = (
            f":calendar: *Meeting agenda draft* — "
            f"{draft.project_name}, meeting {draft.meeting_date.isoformat()}"
        )
        body = f"{head}\n```\n{draft.body}\n```\n_Edit before sharing with client._"
        self._post(self.pm_review_channel, body)

    def emit_briefing(self, briefing: DailyBriefing) -> None:
        self._post(self.pm_review_channel, briefing.body)


class GmailDraftSink:
    """Creates Gmail drafts (NOT sends) for client-facing status + agenda.

    Requires the OAuth dance to have been completed and a stored token at
    GMAIL_TOKEN_PATH. See README §Setup for credential setup.
    """

    SCOPES = ["https://www.googleapis.com/auth/gmail.compose"]

    def __init__(self, credentials_path: str, token_path: str):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self._service = None  # lazy init

    def _get_service(self):
        if self._service is not None:
            return self._service
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        from googleapiclient.discovery import build  # type: ignore

        creds = None
        token_path = Path(self.token_path)
        if token_path.exists():
            creds = Credentials.from_authorized_user_file(str(token_path), self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_path, self.SCOPES
                )
                creds = flow.run_local_server(port=0)
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        self._service = build("gmail", "v1", credentials=creds)
        return self._service

    def _create_draft(self, to: str | None, subject: str, body: str) -> None:
        import base64
        from email.mime.text import MIMEText

        msg = MIMEText(body)
        if to:
            msg["to"] = to
        msg["subject"] = subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
        service = self._get_service()
        service.users().drafts().create(
            userId="me", body={"message": {"raw": raw}}
        ).execute()

    # Only client-facing drafts go to Gmail.
    def emit_status(self, draft: StatusDraft) -> None:
        self._create_draft(to=None, subject=draft.subject, body=draft.body)
        logger.info("emit_status.gmail project_id=%s", draft.project_id)

    def emit_agenda(self, draft: AgendaDraft) -> None:
        subject = f"Agenda — {draft.project_name} ({draft.meeting_date.isoformat()})"
        self._create_draft(to=None, subject=subject, body=draft.body)
        logger.info("emit_agenda.gmail project_id=%s", draft.project_id)

    # No-ops for internal drafts (handled by Slack sink).
    def emit_nudge(self, draft: NudgeDraft) -> None:
        return None

    def emit_escalation(self, draft: EscalationDraft) -> None:
        return None

    def emit_briefing(self, briefing: DailyBriefing) -> None:
        return None


class CompositeSink:
    """Fans out each draft to multiple sinks. Failures in one don't block others."""

    def __init__(self, sinks: list[DraftSink]):
        self.sinks = sinks

    def _emit(self, method: str, payload) -> None:
        for s in self.sinks:
            try:
                getattr(s, method)(payload)
            except Exception:  # noqa: BLE001 — sinks are best-effort
                logger.exception("sink %s %s failed", type(s).__name__, method)

    def emit_nudge(self, draft): self._emit("emit_nudge", draft)
    def emit_status(self, draft): self._emit("emit_status", draft)
    def emit_escalation(self, draft): self._emit("emit_escalation", draft)
    def emit_agenda(self, draft): self._emit("emit_agenda", draft)
    def emit_briefing(self, briefing): self._emit("emit_briefing", briefing)
