import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from pr_agent.tools.pr_prompting_agent import PRPromptingAgent
from pr_agent.sessions.session import (
    Finding,
    FindingSeverity,
    FindingStatus,
    ReviewSession,
)
from pr_agent.sessions.session_manager import SessionManager


def _make_finding(fid="f_abc12345", file="app.py", start_line=10,
                  severity="high", category="bug", explanation="test issue",
                  question_for_developer="Is this intentional?",
                  confidence=0.9, status="open"):
    return Finding(
        id=fid, file=file, start_line=start_line, severity=severity,
        category=category, explanation=explanation,
        question_for_developer=question_for_developer,
        confidence=confidence, status=status,
    )


def _make_session_with_findings(findings=None):
    session = ReviewSession(
        pr_url="https://github.com/org/repo/pull/1",
        diff_content="diff --git a/app.py ...",
        pr_metadata={"title": "Fix bug", "branch": "fix/x", "language": "Python"},
    )
    if findings:
        for f in findings:
            session.add_finding(f)
        session.current_finding_id = findings[0].id
    return session


def _build_agent():
    with patch.object(PRPromptingAgent, "__init__", lambda self, *a, **kw: None):
        agent = PRPromptingAgent.__new__(PRPromptingAgent)
    agent.pr_url = "https://github.com/org/repo/pull/1"
    agent.session_manager = SessionManager()
    agent._cancelled_sessions = set()
    agent.model = "test-model"
    agent.ai_handler = AsyncMock()
    agent.prompts = None
    agent.extra_instructions = ""
    return agent


class TestParseAnalysisResponse:
    def test_empty_prediction_returns_error_summary(self):
        agent = _build_agent()
        findings, summary = agent._parse_analysis_response("")
        assert findings == []
        assert "trouble parsing" in summary

    def test_valid_json_with_findings(self):
        agent = _build_agent()
        raw = json.dumps({
            "findings": [
                {"file": "src/auth.py", "start_line": 42, "end_line": 45,
                 "severity": "critical", "category": "SQL Injection",
                 "explanation": "User input interpolated directly.",
                 "question_for_developer": "Is this user-facing?",
                 "confidence": 0.95}
            ],
            "summary": "Found 1 issue."
        })
        findings, summary = agent._parse_analysis_response(raw)
        assert len(findings) == 1
        assert findings[0]["file"] == "src/auth.py"
        assert findings[0]["start_line"] == 42
        assert findings[0]["end_line"] == 45
        assert findings[0]["severity"] == "critical"
        assert findings[0]["confidence"] == 0.95
        assert summary == "Found 1 issue."

    def test_json_wrapped_in_code_fences(self):
        agent = _build_agent()
        raw = (
            '```json\n{"findings": [{"file": "a.py", "start_line": 1, '
            '"severity": "low", "category": "style", "explanation": "minor"}], '
            '"summary": "ok"}\n```'
        )
        findings, summary = agent._parse_analysis_response(raw)
        assert len(findings) == 1
        assert findings[0]["file"] == "a.py"
        assert summary == "ok"

    def test_invalid_json_returns_error_summary(self):
        agent = _build_agent()
        findings, summary = agent._parse_analysis_response("not json at all")
        assert findings == []
        assert "trouble parsing" in summary

    def test_no_findings_key(self):
        agent = _build_agent()
        findings, summary = agent._parse_analysis_response(json.dumps({"summary": "Clean."}))
        assert findings == []
        assert summary == "Clean."


class TestUpdateFindingsFromResponse:
    def test_dismissed_marker_updates_finding(self):
        agent = _build_agent()
        f = _make_finding(fid="f_abc12345", status="open")
        session = _make_session_with_findings([f])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(
            session, "I think [f_abc12345:dismissed] is fair."
        )
        assert f.status == FindingStatus.DISMISSED

    def test_resolved_marker_updates_finding(self):
        agent = _build_agent()
        f = _make_finding(fid="f_deadbeef", status="discussed")
        session = _make_session_with_findings([f])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(
            session, "Fixed in [f_deadbeef:resolved]."
        )
        assert f.status == FindingStatus.RESOLVED

    def test_no_markers_no_change(self):
        agent = _build_agent()
        f = _make_finding(fid="f_aabbccdd", status="open")
        session = _make_session_with_findings([f])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(session, "Looks good, no changes.")
        assert f.status == FindingStatus.OPEN

    def test_unknown_finding_id_does_not_crash(self):
        agent = _build_agent()
        f = _make_finding(fid="f_real123", status="open")
        session = _make_session_with_findings([f])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(
            session, "Resolved [f_nonexistent:dismissed]."
        )
        assert f.status == FindingStatus.OPEN

    def test_multiple_markers(self):
        agent = _build_agent()
        f1 = _make_finding(fid="f_11111111", status="open")
        f2 = _make_finding(fid="f_22222222", status="open")
        session = _make_session_with_findings([f1, f2])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(
            session, "[f_11111111:dismissed] and [f_22222222:resolved]."
        )
        assert f1.status == FindingStatus.DISMISSED
        assert f2.status == FindingStatus.RESOLVED

    def test_discussed_marker(self):
        agent = _build_agent()
        f = _make_finding(fid="f_discuss", status="open")
        session = _make_session_with_findings([f])
        agent.session_manager._sessions[session.session_id] = session

        agent._update_findings_from_response(
            session, "Let's talk about [f_discuss:discussed]."
        )
        assert f.status == FindingStatus.DISCUSSED


class TestCancelGeneration:
    @pytest.mark.asyncio
    async def test_cancel_adds_to_set(self):
        agent = _build_agent()
        await agent.cancel_generation("sess123")
        assert "sess123" in agent._cancelled_sessions

    @pytest.mark.asyncio
    async def test_cancel_idempotent(self):
        agent = _build_agent()
        await agent.cancel_generation("sess123")
        await agent.cancel_generation("sess123")
        assert "sess123" in agent._cancelled_sessions

    @pytest.mark.asyncio
    async def test_cancel_skips_during_handle_message(self):
        agent = _build_agent()
        session = _make_session_with_findings([])
        agent.session_manager._sessions[session.session_id] = session
        agent._cancelled_sessions.add(session.session_id)

        chunks = []
        async for chunk in agent.handle_message(session.session_id, "hello"):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["event"] == "done"
        assert session.session_id not in agent._cancelled_sessions


class TestHandleMessageFlow:
    @pytest.mark.asyncio
    async def test_invalid_session_yields_error(self):
        agent = _build_agent()
        chunks = []
        async for chunk in agent.handle_message("nonexistent", "hello"):
            chunks.append(chunk)
        assert len(chunks) == 1
        assert chunks[0]["event"] == "error"
        assert "not found" in chunks[0]["data"]["message"]

    @pytest.mark.asyncio
    async def test_cancelled_before_generation(self):
        agent = _build_agent()
        session = _make_session_with_findings([])
        agent.session_manager._sessions[session.session_id] = session
        agent._cancelled_sessions.add(session.session_id)

        chunks = []
        async for chunk in agent.handle_message(session.session_id, "hello"):
            chunks.append(chunk)

        assert len(chunks) == 1
        assert chunks[0]["event"] == "done"
        assert session.session_id not in agent._cancelled_sessions


class TestPromptRendering:
    def test_analysis_prompt_renders(self):
        from jinja2 import Environment, StrictUndefined
        from pr_agent.config_loader import get_settings

        settings = get_settings()
        variables = {
            "title": "Test PR",
            "branch": "fix/test",
            "description": "Fixes a bug.",
            "language": "Python",
            "diff": "diff --git a/app.py ...",
            "extra_instructions": "",
            "skills_context": "",
            "repo_context": "",
        }

        env = Environment(undefined=StrictUndefined)
        system = env.from_string(settings.prompting_agent_analysis_prompt.system)
        user = env.from_string(settings.prompting_agent_analysis_prompt.user)

        system_rendered = system.render(variables)
        user_rendered = user.render(variables)

        assert "Test PR" in user_rendered
        assert "fix/test" in user_rendered
        assert "diff --git" in user_rendered
        assert "Python" in user_rendered
        assert "JSON" in system_rendered or "json" in system_rendered.lower()

    def test_discussion_prompt_renders(self):
        from jinja2 import Environment, StrictUndefined
        from pr_agent.config_loader import get_settings

        settings = get_settings()
        f = _make_finding()
        variables = {
            "title": "Test PR",
            "branch": "fix/test",
            "findings": [f],
            "current_finding_id": f.id,
            "conversation_history": [
                {"role": "user", "content": "Why is this an issue?"},
                {"role": "assistant", "content": "Because..."},
            ],
            "extra_instructions": "",
        }

        env = Environment(undefined=StrictUndefined)
        system = env.from_string(settings.prompting_agent_discussion_prompt.system)
        user = env.from_string(settings.prompting_agent_discussion_prompt.user)

        system_rendered = system.render(variables)
        user_rendered = user.render(variables)

        assert "Test PR" in user_rendered
        assert f.id in user_rendered
        assert "Why is this an issue?" in user_rendered
        assert "Because..." in user_rendered

    def test_summary_prompt_renders(self):
        from jinja2 import Environment, StrictUndefined
        from pr_agent.config_loader import get_settings

        settings = get_settings()
        f = _make_finding(status="resolved")
        variables = {
            "title": "Test PR",
            "branch": "fix/test",
            "findings": [f],
            "conversation_history": [
                {"role": "user", "content": "Fixed it."},
                {"role": "assistant", "content": "Great."},
            ],
            "extra_instructions": "",
        }

        env = Environment(undefined=StrictUndefined)
        system = env.from_string(settings.prompting_agent_summary_prompt.system)
        user = env.from_string(settings.prompting_agent_summary_prompt.user)

        system_rendered = system.render(variables)
        user_rendered = user.render(variables)

        assert "Test PR" in user_rendered
        assert "Fixed it." in user_rendered
