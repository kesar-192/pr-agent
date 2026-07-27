import copy
import json
import re
from functools import partial
from typing import AsyncGenerator, List, Optional, Set

from jinja2 import Environment, StrictUndefined

from pr_agent.algo.ai_handlers.base_ai_handler import BaseAiHandler
from pr_agent.algo.ai_handlers.litellm_ai_handler import LiteLLMAIHandler
from pr_agent.algo.pr_processing import get_pr_diff, retry_with_fallback_models
from pr_agent.algo.skills_loader import get_skills_context
from pr_agent.algo.repo_context import build_repo_context
from pr_agent.algo.token_handler import TokenHandler
from pr_agent.algo.utils import ModelType
from pr_agent.config_loader import get_settings
from pr_agent.git_providers import get_git_provider_with_context
from pr_agent.git_providers.git_provider import get_main_pr_language
from pr_agent.log import get_logger
from pr_agent.session import SessionManager
from pr_agent.session.session import Finding


class PRPromptingAgent:
    def __init__(
        self,
        pr_url: str,
        args: list = None,
        ai_handler: partial[BaseAiHandler,] = LiteLLMAIHandler,
    ):
        self.git_provider = get_git_provider_with_context(pr_url)
        self.args = args
        self.pr_url = pr_url

        self.main_language = get_main_pr_language(
            self.git_provider.get_languages(), self.git_provider.get_files()
        )

        self.ai_handler = ai_handler()
        self.ai_handler.main_pr_language = self.main_language

        self.pr_description, self.pr_description_files = (
            self.git_provider.get_pr_description(split_changes_walkthrough=True)
        )

        self.patches_diff = None
        self.prediction = None
        self.session_id = None

        self.vars = {
            "title": self.git_provider.pr.title,
            "branch": self.git_provider.get_pr_branch(),
            "description": self.pr_description,
            "language": self.main_language,
            "diff": "",
            "extra_instructions": get_settings().prompting_agent.get("extra_instructions", ""),
            "skills_context": get_skills_context(),
            "repo_context": build_repo_context(self.git_provider),
        }

        self.token_handler = TokenHandler(
            self.git_provider.pr,
            self.vars,
            get_settings().prompting_agent_analysis_prompt.system,
            get_settings().prompting_agent_analysis_prompt.user,
        )

        self.session_manager = SessionManager()
        self._cancelled_sessions: Set[str] = set()

    async def run(self) -> Optional[str]:
        try:
            if not self.git_provider.get_files():
                get_logger().info(f"PR has no files: {self.pr_url}, skipping review")
                return None

            get_logger().info(f"Starting prompting agent session for PR: {self.pr_url}")

            if get_settings().config.publish_output:
                self.git_provider.publish_comment("Preparing interactive review...", is_temporary=True)

            await retry_with_fallback_models(self._prepare_prediction, model_type=ModelType.REGULAR)

            if not self.prediction:
                self.git_provider.remove_initial_comment()
                get_logger().warning(f"No prediction for PR: {self.pr_url}")
                return None

            findings = self._parse_findings()
            pr_metadata = {
                "title": self.vars["title"],
                "branch": self.vars["branch"],
                "description": self.vars["description"],
                "language": self.vars["language"],
                "summary": self._extract_summary(),
            }

            self.session_id = self.session_manager.create_session(
                pr_url=self.pr_url,
                diff_content=self.patches_diff or "",
                pr_metadata=pr_metadata,
            )

            session = self.session_manager.get_session(self.session_id)
            for finding in findings:
                session.add_finding(finding)
            if findings:
                session.current_finding_id = findings[0].id

            if get_settings().prompting_agent.get("publish_initial_review", False):
                comment = self._format_findings_comment(findings, pr_metadata.get("summary", ""))
                self.git_provider.publish_comment(comment)

            self.git_provider.remove_initial_comment()

            get_logger().info(
                f"Session {self.session_id} created with {len(findings)} findings for {self.pr_url}"
            )
            return self.session_id

        except Exception as e:
            get_logger().error(f"Failed to start prompting agent session: {e}")
            return None

    async def handle_message(self, session_id: str, user_message: str) -> AsyncGenerator[str, None]:
        try:
            session = self.session_manager.get_session(session_id)
            self.session_manager.append_message(session_id, "user", user_message)

            if session_id in self._cancelled_sessions:
                self._cancelled_sessions.discard(session_id)
                yield json.dumps({"event": "done", "session_id": session_id, "turn": session.turn_count})
                return

            model = get_settings().config.model

            accumulated = ""
            async for chunk in self._get_discussion_prediction(model, session, user_message):
                if session_id in self._cancelled_sessions:
                    self._cancelled_sessions.discard(session_id)
                    break
                accumulated += chunk
                yield json.dumps({"event": "token", "content": chunk})

            if accumulated:
                self.session_manager.append_message(session_id, "assistant", accumulated)
                self._update_findings_from_response(session, accumulated)

            yield json.dumps({
                "event": "done",
                "session_id": session_id,
                "turn": session.turn_count,
            })

        except ValueError as e:
            yield json.dumps({"event": "error", "message": str(e)})
        except Exception as e:
            get_logger().error(f"Error handling message in session {session_id}: {e}")
            yield json.dumps({"event": "error", "message": "Internal error processing message"})

    async def cancel_generation(self, session_id: str) -> None:
        self._cancelled_sessions.add(session_id)
        get_logger().info(f"Cancellation requested for session {session_id}")

    async def generate_summary(self, session_id: str) -> str:
        session = self.session_manager.get_session(session_id)

        variables = {
            "title": session.pr_metadata.get("title", ""),
            "branch": session.pr_metadata.get("branch", ""),
            "findings": session.findings,
            "conversation_history": [
                {"role": m.role.value, "content": m.content}
                for m in session.conversation_history
            ],
            "extra_instructions": get_settings().prompting_agent.get("extra_instructions", ""),
        }

        environment = Environment(undefined=StrictUndefined)
        system_prompt = environment.from_string(
            get_settings().prompting_agent_summary_prompt.system
        ).render(variables)
        user_prompt = environment.from_string(
            get_settings().prompting_agent_summary_prompt.user
        ).render(variables)

        model = get_settings().config.model
        response, _ = await self.ai_handler.chat_completion(
            model=model,
            temperature=get_settings().config.temperature,
            system=system_prompt,
            user=user_prompt,
        )
        return response

    async def _prepare_prediction(self, model: str) -> None:
        self.patches_diff = get_pr_diff(
            self.git_provider,
            self.token_handler,
            model,
            add_line_numbers_to_hunks=True,
            disable_extra_lines=False,
        )

        if self.patches_diff:
            self.prediction = await self._get_analysis_prediction(model)
        else:
            get_logger().warning(f"Empty diff for PR: {self.pr_url}")
            self.prediction = None

    async def _get_analysis_prediction(self, model: str) -> str:
        variables = copy.deepcopy(self.vars)
        variables["diff"] = self.patches_diff

        environment = Environment(undefined=StrictUndefined)
        system_prompt = environment.from_string(
            get_settings().prompting_agent_analysis_prompt.system
        ).render(variables)
        user_prompt = environment.from_string(
            get_settings().prompting_agent_analysis_prompt.user
        ).render(variables)

        response, finish_reason = await self.ai_handler.chat_completion(
            model=model,
            temperature=get_settings().config.temperature,
            system=system_prompt,
            user=user_prompt,
        )
        return response

    async def _get_discussion_prediction(
        self, model: str, session, user_message: str
    ) -> AsyncGenerator[str, None]:
        max_turns = get_settings().prompting_agent.get("max_context_turns", 20)

        recent = session.get_recent_messages(max_turns)
        findings_list = session.findings
        current_finding_id = session.current_finding_id

        variables = {
            "title": session.pr_metadata.get("title", ""),
            "branch": session.pr_metadata.get("branch", ""),
            "findings": findings_list,
            "current_finding_id": current_finding_id,
            "conversation_history": [
                {"role": m.role.value, "content": m.content}
                for m in recent
            ],
            "extra_instructions": get_settings().prompting_agent.get("extra_instructions", ""),
        }

        environment = Environment(undefined=StrictUndefined)
        system_prompt = environment.from_string(
            get_settings().prompting_agent_discussion_prompt.system
        ).render(variables)
        user_prompt = environment.from_string(
            get_settings().prompting_agent_discussion_prompt.user
        ).render(variables)

        response, finish_reason = await self.ai_handler.chat_completion(
            model=model,
            temperature=get_settings().config.temperature,
            system=system_prompt,
            user=user_prompt,
        )
        yield response

    def _parse_findings(self) -> List[Finding]:
        if not self.prediction:
            return []

        try:
            text = self.prediction.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
                text = "\n".join(lines)

            data = json.loads(text)
        except json.JSONDecodeError as e:
            get_logger().warning(f"Failed to parse analysis JSON: {e}")
            return []

        findings = []
        for item in data.get("findings", []):
            try:
                finding = Finding(
                    file=item["file"],
                    start_line=item["start_line"],
                    end_line=item.get("end_line"),
                    severity=item["severity"],
                    category=item["category"],
                    explanation=item["explanation"],
                    question_for_developer=item.get("question_for_developer"),
                    confidence=item.get("confidence", 1.0),
                )
                findings.append(finding)
            except Exception as e:
                get_logger().warning(f"Skipping malformed finding: {e}")

        return findings

    def _extract_summary(self) -> str:
        if not self.prediction:
            return ""
        try:
            text = self.prediction.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                lines = [ln for ln in lines if not ln.strip().startswith("```")]
                text = "\n".join(lines)
            data = json.loads(text)
            return data.get("summary", "")
        except Exception:
            return ""

    def _format_findings_comment(self, findings: List[Finding], summary: str) -> str:
        parts = ["## Prompting Agent - Initial Review\n"]
        if summary:
            parts.append(f"{summary}\n")

        if not findings:
            parts.append("No issues found in this PR.\n")
            return "\n".join(parts)

        severity_icons = {
            "critical": "🔴",
            "high": "🟠",
            "medium": "🟡",
            "low": "🔵",
        }

        for f in findings:
            icon = severity_icons.get(f.severity.value, "⚪")
            parts.append(
                f"### {icon} {f.severity.value.upper()} - `{f.file}:{f.start_line}`\n"
                f"**{f.category}**\n"
                f"{f.explanation}\n"
            )
            if f.question_for_developer:
                parts.append(f"> **Q:** {f.question_for_developer}\n")

        parts.append(
            "\n---\n*Discuss this review interactively at the "
            f"[Prompting Agent](/prompting_agent?pr_url={self.pr_url})*"
        )
        return "\n".join(parts)

    def _update_findings_from_response(self, session, response: str) -> None:
        pattern = r"\[(f_[a-f0-9]+):(dismissed|resolved)\]"
        for match in re.finditer(pattern, response):
            finding_id = match.group(1)
            status = match.group(2)
            try:
                self.session_manager.update_finding(
                    session.session_id, finding_id, status=status
                )
            except ValueError:
                get_logger().debug(f"Could not update finding {finding_id}")
