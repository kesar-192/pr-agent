import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pr_agent.tools.pr_prompting_agent import PRPromptingAgent
from pr_agent.session.session import (
    Finding,
    FindingSeverity,
    FindingStatus,
    ReviewSession,
)
from pr_agent.session.session_manager import SessionManager


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
    agent.prediction = None
    agent.patches_diff = None
    agent.session_id = None
    agent.pr_url = "https://github.com/org/repo/pull/1"
    agent.vars = {"title": "Fix bug", "branch": "fix/x", "language": "Python"}
    agent.session_manager = SessionManager()
    agent._cancelled_sessions = set()
    return agent


class TestParseFindings:
    def test_empty_prediction_returns_empty_list(self):
        agent = _build_agent()
        agent.prediction = None
        assert agent._parse_findings() == []

    def test_empty_string_returns_empty_list(self):
        agent = _build_agent()
        agent.prediction = ""
        assert agent._parse_findings() == []

    def test_valid_json_with_findings(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [
                {
                    "file": "src/auth.py",
                    "start_line": 42,
                    "end_line": 45,
                    "severity": "critical",
                    "category": "SQL Injection",
                    "explanation": "User input interpolated directly.",
                    "question_for_developer": "Is this user-facing?",
                    "confidence": 0.95,
                }
            ],
            "summary": "Found 1 issue."
        })
        findings = agent._parse_findings()
        assert len(findings) == 1
        assert findings[0].file == "src/auth.py"
        assert findings[0].start_line == 42
        assert findings[0].end_line == 45
        assert findings[0].severity == FindingSeverity.CRITICAL
        assert findings[0].category == "SQL Injection"
        assert findings[0].confidence == 0.95
        assert findings[0].question_for_developer == "Is this user-facing?"

    def test_valid_json_with_empty_findings(self):
        agent = _build_agent()
        agent.prediction = json.dumps({"findings": [], "summary": "Clean PR."})
        assert agent._parse_findings() == []

    def test_json_wrapped_in_code_fences(self):
        agent = _build_agent()
        agent.prediction = (
            '```json\n{"findings": [{"file": "a.py", "start_line": 1, '
            '"severity": "low", "category": "style", "explanation": "minor"}], '
            '"summary": "ok"}\n```'
        )
        findings = agent._parse_findings()
        assert len(findings) == 1
        assert findings[0].file == "a.py"

    def test_invalid_json_returns_empty(self):
        agent = _build_agent()
        agent.prediction = "not json at all"
        assert agent._parse_findings() == []

    def test_malformed_finding_skipped(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [
                {"file": "a.py"},
                {"file": "b.py", "start_line": 5, "severity": "low",
                 "category": "style", "explanation": "ok"},
            ],
            "summary": "mixed"
        })
        findings = agent._parse_findings()
        assert len(findings) == 1
        assert findings[0].file == "b.py"

    def test_multiple_findings_order(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [
                {"file": f"file{i}.py", "start_line": i * 10,
                 "severity": sev, "category": "cat", "explanation": "exp"}
                for i, sev in enumerate(["critical", "high", "medium", "low"], 1)
            ],
            "summary": "4 issues"
        })
        findings = agent._parse_findings()
        assert len(findings) == 4
        severities = [f.severity for f in findings]
        assert severities == [
            FindingSeverity.CRITICAL, FindingSeverity.HIGH,
            FindingSeverity.MEDIUM, FindingSeverity.LOW,
        ]

    def test_end_line_defaults_to_start_line(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [{"file": "x.py", "start_line": 7,
                          "severity": "low", "category": "c", "explanation": "e"}],
            "summary": ""
        })
        findings = agent._parse_findings()
        assert findings[0].end_line == 7

    def test_confidence_defaults_to_1(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [{"file": "x.py", "start_line": 1,
                          "severity": "high", "category": "c", "explanation": "e"}],
            "summary": ""
        })
        findings = agent._parse_findings()
        assert findings[0].confidence == 1.0


class TestExtractSummary:
    def test_empty_prediction(self):
        agent = _build_agent()
        agent.prediction = None
        assert agent._extract_summary() == ""

    def test_valid_summary(self):
        agent = _build_agent()
        agent.prediction = json.dumps({
            "findings": [], "summary": "This PR fixes a login bug."
        })
        assert agent._extract_summary() == "This PR fixes a login bug."

    def test_missing_summary_key(self):
        agent = _build_agent()
        agent.prediction = json.dumps({"findings": []})
        assert agent._extract_summary() == ""

    def test_invalid_json(self):
        agent = _build_agent()
        agent.prediction = "not json"
        assert agent._extract_summary() == ""

    def test_code_fences_stripped(self):
        agent = _build_agent()
        agent.prediction = '```json\n{"findings": [], "summary": "fenced"}\n```'
        assert agent._extract_summary() == "fenced"


class TestFormatFindingsComment:
    def test_empty_findings(self):
        agent = _build_agent()
        result = agent._format_findings_comment([], "All clean.")
        assert "## Prompting Agent" in result
        assert "No issues found" in result
        assert "All clean." in result

    def test_single_finding_with_question(self):
        agent = _build_agent()
        f = _make_finding(severity="critical", file="auth.py",
                          start_line=42, category="SQL Injection",
                          explanation="Direct interpolation.",
                          question_for_developer="User-facing?")
        result = agent._format_findings_comment([f], "Found 1 issue.")
        assert "CRITICAL" in result
        assert "auth.py:42" in result
        assert "SQL Injection" in result
        assert "Direct interpolation." in result
        assert "User-facing?" in result

    def test_finding_without_question(self):
        agent = _build_agent()
        f = _make_finding(severity="low", question_for_developer=None)
        result = agent._format_findings_comment([f], "")
        assert "Q:" not in result

    def test_multiple_severity_icons(self):
        agent = _build_agent()
        findings = [
            _make_finding(fid=f"f_{i}", severity=s, file=f"f{i}.py",
                          start_line=i, category="c", explanation="e")
            for i, s in enumerate(["critical", "high", "medium", "low"], 1)
        ]
        result = agent._format_findings_comment(findings, "")
        assert "CRITICAL" in result
        assert "HIGH" in result
        assert "MEDIUM" in result
        assert "LOW" in result

    def test_summary_included(self):
        agent = _build_agent()
        result = agent._format_findings_comment([], "Looks good overall.")
        assert "Looks good overall." in result

    def test_no_summary(self):
        agent = _build_agent()
        result = agent._format_findings_comment([], "")
        parts = result.split("\n")
        assert parts[1] == ""


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


class TestHandleMessageFlow:
    @pytest.mark.asyncio
    async def test_invalid_session_yields_error(self):
        agent = _build_agent()
        chunks = []
        async for chunk in agent.handle_message("nonexistent", "hello"):
            chunks.append(json.loads(chunk))
        assert len(chunks) == 1
        assert chunks[0]["event"] == "error"
        assert "not found" in chunks[0]["message"]

    @pytest.mark.asyncio
    async def test_cancel_during_generation(self):
        agent = _build_agent()
        session = _make_session_with_findings([])
        agent.session_manager._sessions[session.session_id] = session
        agent._cancelled_sessions.add(session.session_id)

        discussion_prompt = SimpleNamespace(
            system="{{ title }}", user="{{ title }}"
        )
        with patch(
            "pr_agent.tools.pr_prompting_agent.get_settings"
        ) as mock_settings:
            mock_settings.return_value.config.model = "test-model"
            mock_settings.return_value.config.temperature = 0.7
            mock_settings.return_value.prompting_agent = SimpleNamespace(
                get=lambda k, d=None: d
            )
            mock_settings.return_value.prompting_agent_discussion_prompt = discussion_prompt
            chunks = []
            async for chunk in agent.handle_message(
                session.session_id, "hello"
            ):
                chunks.append(json.loads(chunk))

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

        system_rendered = system.render(variables)  # noqa: F841
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

        system_rendered = system.render(variables)  # noqa: F841
        user_rendered = user.render(variables)

        assert "Test PR" in user_rendered
        assert "Fixed it." in user_rendered
