"""
pr_agent/tools/pr_prompting_agent.py

PRPromptingAgent - the interactive, multi-turn counterpart to PRReviewer.

Follows the same construction pattern as the other tools in pr_agent/tools/
(pr_reviewer.py, pr_questions.py, etc.): built around a git_provider and an
ai_handler that are injected, so this class reuses the existing PR-Agent
infrastructure (LiteLLMAIHandler, GitProvider implementations, TokenHandler)
rather than reimplementing it.

Two small protocol classes (GitProviderProtocol / AIHandlerProtocol) are
declared purely as documentation of the interface this tool expects - swap in
the real pr_agent.git_providers.* / pr_agent.algo.ai_handlers.* classes at
call time. Lightweight Mock* implementations are included below so this
module (and prompting_server.py) can be run and tested standalone before
that wiring is done.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional, Protocol, Set

import tomllib
from jinja2 import Environment, StrictUndefined

from pr_agent.sessions.session import Finding, FindingStatus, ReviewSession
from pr_agent.sessions.session_manager import SessionManager

logger = logging.getLogger(__name__)

PROMPTS_PATH = Path(__file__).resolve().parent.parent / "settings" / "prompting_agent_prompts.toml"


# --------------------------------------------------------------------------- #
# Interfaces this tool depends on (implement with the real PR-Agent classes)
# --------------------------------------------------------------------------- #

class GitProviderProtocol(Protocol):
    """Subset of pr_agent.git_providers.git_provider.GitProvider used here."""

    def get_pr_diff(self) -> str: ...
    def get_pr_metadata(self) -> Dict[str, Any]: ...


class AIHandlerProtocol(Protocol):
    """Subset of pr_agent.algo.ai_handlers.base_ai_handler.BaseAiHandler used here."""

    async def chat_completion(
        self, system: str, user: str, model: str, temperature: float = 0.2
    ) -> str: ...

    def chat_completion_stream(
        self, system: str, user: str, model: str, temperature: float = 0.2
    ) -> AsyncGenerator[str, None]: ...


# --------------------------------------------------------------------------- #
# Prompt loading / rendering
# --------------------------------------------------------------------------- #

class PromptRenderer:
    """Loads prompting_agent_prompts.toml once and renders templates with Jinja2."""

    def __init__(self, prompts_path: Path = PROMPTS_PATH) -> None:
        with open(prompts_path, "rb") as f:
            self._prompts = tomllib.load(f)
        self._env = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)

    def render(self, prompt_key: str, part: str, **kwargs: Any) -> str:
        template_str = self._prompts[prompt_key][part]
        template = self._env.from_string(template_str)
        return template.render(**kwargs)


# --------------------------------------------------------------------------- #
# PRPromptingAgent
# --------------------------------------------------------------------------- #

class PRPromptingAgent:
    """
    Orchestrates one interactive review session: initial analysis, discussion
    turns, and the final summary. Mirrors PRReviewer's constructor shape.
    """

    def __init__(
        self,
        pr_url: str,
        git_provider: Optional[GitProviderProtocol] = None,
        ai_handler: Optional[AIHandlerProtocol] = None,
        session_manager: Optional[SessionManager] = None,
        model: Optional[str] = None,
        extra_instructions: str = "",
        repo_context: str = "",
        skills_context: str = "",
        args: Optional[list] = None,
    ) -> None:
        self.pr_url = pr_url

        if git_provider is None:
            from pr_agent.git_providers import get_git_provider_with_context
            gp = get_git_provider_with_context(pr_url)
            class _GPWrap:
                def get_pr_diff(_self) -> str:
                    parts = []
                    for f in gp.get_diff_files():
                        if f.patch:
                            parts.append(f"## File: '{f.filename}'")
                            parts.append(f.patch)
                            parts.append("")
                    return "\n".join(parts) if parts else gp.get_files()
                def get_pr_metadata(_self) -> dict:
                    return {
                        "title": gp.pr.title,
                        "branch": gp.get_pr_branch(),
                        "description": gp.get_pr_description(),
                        "language": gp.get_languages(),
                    }
            git_provider = _GPWrap()
        self.git_provider = git_provider

        if ai_handler is None:
            from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
            ai_handler = LiteLLMAIHandler()
        self.ai_handler = ai_handler

        if session_manager is None:
            session_manager = SessionManager()
        self.session_manager = session_manager

        if model is None:
            from pr_agent.config_loader import get_settings
            model = get_settings().config.model
        self.model = model

        self.extra_instructions = extra_instructions
        self.repo_context = repo_context
        self.skills_context = skills_context
        self.prompts = PromptRenderer()
        self._cancelled_sessions: Set[str] = set()

    # ------------------------------------------------------------------ #
    # Turn 1: initial analysis
    # ------------------------------------------------------------------ #

    async def run(self) -> str:
        """Fetch the diff, run initial analysis, create the session. Returns session_id."""
        diff = self.git_provider.get_pr_diff()
        metadata = self.git_provider.get_pr_metadata()

        system = self.prompts.render(
            "prompting_agent_analysis_prompt", "system",
            extra_instructions=self.extra_instructions,
        )
        user = self.prompts.render(
            "prompting_agent_analysis_prompt", "user",
            title=metadata.get("title", ""),
            branch=metadata.get("branch", ""),
            description=metadata.get("description", ""),
            language=metadata.get("language", ""),
            repo_context=self.repo_context,
            skills_context=self.skills_context,
            diff=diff,
        )

        raw_response, _ = await self.ai_handler.chat_completion(model=self.model, system=system, user=user)
        findings_data, summary = self._parse_analysis_response(raw_response)

        session_id = self.session_manager.create_session(
            pr_url=self.pr_url, diff_content=diff, pr_metadata=metadata,
        )
        for f in findings_data:
            self.session_manager.add_finding(session_id, Finding.from_dict(f))

        session = self.session_manager.get_session(session_id)
        session.add_message("assistant", summary, metadata={"phase": "analysis"})
        session.next_open_finding()

        return session_id

    @staticmethod
    def _parse_analysis_response(raw_response: str) -> tuple[list[dict], str]:
        """Parse the model's JSON output, tolerating stray markdown code fences."""
        cleaned = re.sub(r"^```(?:json)?|```$", "", raw_response.strip(), flags=re.MULTILINE).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            logger.error("Failed to parse analysis response as JSON: %s", raw_response[:500])
            return [], "I had trouble parsing my own analysis output. Please retry."
        return data.get("findings", []), data.get("summary", "")

    # ------------------------------------------------------------------ #
    # Turn 2+: discussion
    # ------------------------------------------------------------------ #

    async def cancel_generation(self, session_id: str) -> None:
        """Mark a session for cancellation. The in-flight handle_message checks this."""
        self._cancelled_sessions.add(session_id)

    @staticmethod
    def _update_findings_from_response(session: ReviewSession, response: str) -> None:
        """Parse [finding_id:status] markers in the response and update findings."""
        for match in re.finditer(r'\[([\w]+):(resolved|dismissed|discussed)\]', response):
            fid, status_str = match.groups()
            finding = session.get_finding(fid)
            if finding:
                finding.status = FindingStatus(status_str)

    async def handle_message(
        self, session_id: str, user_message: str, max_context_turns: int = 20
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Append the developer's message, stream the agent's reply token by
        token, then yield structured SSE-ready events. Yields dicts of the
        shape {"event": <name>, "data": <json-serializable>}.
        """
        if session_id in self._cancelled_sessions:
            self._cancelled_sessions.discard(session_id)
            yield {"event": "done", "data": {"session_id": session_id, "turn": 0}}
            return

        try:
            session = self.session_manager.get_session(session_id)
        except Exception:
            yield {"event": "error", "data": {"message": f"Session '{session_id}' not found or expired"}}
            return
        session.add_message("user", user_message)

        system = self.prompts.render(
            "prompting_agent_discussion_prompt", "system",
            extra_instructions=self.extra_instructions,
        )
        user = self.prompts.render(
            "prompting_agent_discussion_prompt", "user",
            title=session.pr_metadata.get("title", ""),
            branch=session.pr_metadata.get("branch", ""),
            findings=[f.to_dict() for f in session.findings],
            current_finding_id=session.current_finding_id,
            conversation_history=[
                m.to_dict() for m in session.get_recent_messages(max_context_turns)
            ],
        )

        try:
            full_reply, _ = await self.ai_handler.chat_completion(model=self.model, system=system, user=user)
            yield {"event": "token", "data": {"content": full_reply}}
        except Exception as exc:
            logger.exception("Generation failed for session %s", session_id)
            yield {"event": "error", "data": {"message": str(exc)}}
            return

        self._cancelled_sessions.discard(session_id)
        session.add_message("assistant", full_reply)
        self._update_findings_from_response(session, full_reply)

        if session.current_finding_id:
            session.update_finding(session.current_finding_id, status=FindingStatus.DISCUSSED)
            yield {
                "event": "finding_update",
                "data": {"finding_id": session.current_finding_id, "status": "discussed"},
            }

        yield {"event": "done", "data": {"session_id": session_id, "turn": session.turn_count}}

    # ------------------------------------------------------------------ #
    # Session close: summary
    # ------------------------------------------------------------------ #

    async def summarize(self, session_id: str) -> str:
        session = self.session_manager.get_session(session_id)

        system = self.prompts.render("prompting_agent_summary_prompt", "system")
        user = self.prompts.render(
            "prompting_agent_summary_prompt", "user",
            title=session.pr_metadata.get("title", ""),
            branch=session.pr_metadata.get("branch", ""),
            findings=[f.to_dict() for f in session.findings],
            conversation_history=[m.to_dict() for m in session.conversation_history],
        )
        response, _ = await self.ai_handler.chat_completion(model=self.model, system=system, user=user)
        return response


# --------------------------------------------------------------------------- #
# Mock implementations - for local/dev use before wiring in real PR-Agent infra
# --------------------------------------------------------------------------- #

class MockGitProvider:
    """Returns a fixed diff/metadata pair. Swap for a real GitProvider in production."""

    def __init__(self, pr_url: str) -> None:
        self.pr_url = pr_url

    def get_pr_diff(self) -> str:
        return (
            "--- a/src/auth.py\n+++ b/src/auth.py\n"
            "@@ -40,3 +40,3 @@\n"
            "-    user = db.execute(f\"SELECT * FROM users WHERE email='{email}'\")\n"
            "+    user = db.execute(f\"SELECT * FROM users WHERE email='{email}'\")  # unchanged, demo diff\n"
        )

    def get_pr_metadata(self) -> Dict[str, Any]:
        return {
            "title": "Fix login flow",
            "branch": "fix/login",
            "description": "Demo PR metadata for local testing.",
            "language": "Python",
        }


class MockAIHandler:
    """Echoes canned responses instead of calling a real model. For local testing only."""

    async def chat_completion(self, system: str, user: str, model: str, temperature: float = 0.2) -> str:
        return json.dumps({
            "findings": [{
                "file": "src/auth.py",
                "start_line": 42,
                "end_line": 42,
                "severity": "critical",
                "category": "SQL Injection",
                "explanation": "The query interpolates the email variable directly into the SQL string.",
                "question_for_developer": "Is this endpoint reachable by unauthenticated users?",
                "confidence": 0.9,
            }],
            "summary": "Found 1 critical issue in this diff.",
        })

    async def chat_completion_stream(self, system: str, user: str, model: str, temperature: float = 0.2):
        canned = "Thanks for the context. Based on what's in the diff, here's my reasoning..."
        for word in canned.split(" "):
            yield word + " "
