"""Resolve workflow tasks to catalog-backed agent profiles and tools."""

from dataclasses import dataclass
from typing import Dict, Mapping, Protocol

from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.execution.agents.base import BaseAgent
from app.execution.agents.registry import AgentRegistry
from app.execution.state import Task
from app.execution.tools.base import ToolRegistry


class AgentProfile(BaseModel):
    """Runtime-safe subset of an agent catalog record."""

    name: str
    system_prompt: str
    tool_names: list[str] = Field(default_factory=list)
    runtime_name: str = "worker"


class AgentProfileProvider(Protocol):
    """Port used by execution to load an agent profile from a catalog."""

    async def get_agent(self, identifier: str) -> AgentProfile | None:
        """Load an active profile by stable ID or name."""


@dataclass(frozen=True)
class ResolvedAgent:
    """Agent profile and the concrete tools authorized for one task."""

    profile: AgentProfile
    tools: list[BaseTool]
    missing_tool_names: tuple[str, ...] = ()
    denied_tool_names: tuple[str, ...] = ()


DEFAULT_AGENT_PROFILES: Mapping[str, AgentProfile] = {
    "worker": AgentProfile(
        name="worker",
        system_prompt="You execute the assigned task using only the tools provided to you.",
        tool_names=["web_search", "file_reader"],
    ),
    "source_researcher": AgentProfile(
        name="source_researcher",
        system_prompt=(
            "You collect reliable primary sources for a technology research task. "
            "Prefer web search and article crawling, preserve URLs, and distinguish "
            "source content from your own interpretation."
        ),
        tool_names=["web_search", "news_crawler", "http_request"],
    ),
    "synthesis_agent": AgentProfile(
        name="synthesis_agent",
        system_prompt=(
            "You synthesize the outputs of earlier research tasks into concise, "
            "well-supported findings without inventing evidence."
        ),
        tool_names=["text_summarizer", "file_reader"],
    ),
    "report_agent": AgentProfile(
        name="report_agent",
        system_prompt=(
            "You produce a structured Markdown research report from the available "
            "evidence and clearly label limitations and source references."
        ),
        tool_names=["markdown_report_generator", "python_executor", "file_writer"],
    ),
    "chart_agent": AgentProfile(
        name="chart_agent",
        system_prompt=(
            "You turn structured research data into an accurate, readable chart "
            "specification and explain the data fields used."
        ),
        tool_names=["python_executor", "file_reader", "file_writer"],
    ),
}


LEGACY_PROFILE_ALIASES: Mapping[str, str] = {
    "news_crawler": "source_researcher",
    "web_search": "source_researcher",
    "researcher": "source_researcher",
    "text_summarizer": "synthesis_agent",
    "summarizer": "synthesis_agent",
    "markdown_report_generator": "report_agent",
    "report_generator": "report_agent",
    "chart_generator": "chart_agent",
}


class AgentResolver:
    """Resolve a task without exposing catalog or registry details to graph nodes."""

    def __init__(
        self,
        provider: AgentProfileProvider | None = None,
        fallback_profiles: Mapping[str, AgentProfile] | None = None,
    ) -> None:
        self.provider = provider
        self.fallback_profiles = fallback_profiles or DEFAULT_AGENT_PROFILES
        self._cache: Dict[str, AgentProfile] = {}

    async def resolve(self, task: Task) -> ResolvedAgent:
        """Load the selected profile and resolve its authorized tool instances."""

        profile = await self._resolve_profile(task)
        profile_tools = set(profile.tool_names)
        denied = tuple(
            tool_name for tool_name in task.tool_names if tool_name not in profile_tools
        )
        requested_tools = (
            [tool_name for tool_name in task.tool_names if tool_name in profile_tools]
            if task.tool_names
            else profile.tool_names
        )
        tools: list[BaseTool] = []
        missing: list[str] = []
        for tool_name in requested_tools:
            try:
                tools.append(ToolRegistry.get_tool(tool_name))
            except ValueError:
                missing.append(tool_name)

        if (requested_tools or denied) and not tools:
            raise ValueError(
                f"Agent '{profile.name}' has no authorized registered tools available. "
                f"Requested: {requested_tools}; denied: {list(denied)}."
            )
        return ResolvedAgent(
            profile=profile,
            tools=tools,
            missing_tool_names=tuple(missing),
            denied_tool_names=denied,
        )

    async def _resolve_profile(self, task: Task) -> AgentProfile:
        explicit_identifier = task.agent_id
        inferred_name = self._infer_profile_name(task)
        lookup_keys = [explicit_identifier] if explicit_identifier else [inferred_name]

        for key in lookup_keys:
            if not key:
                continue
            if key in self._cache:
                return self._cache[key]
            if self.provider is not None:
                profile = await self.provider.get_agent(key)
                if profile is not None:
                    self._cache[key] = profile
                    return profile

        if explicit_identifier:
            normalized_identifier = explicit_identifier.strip().lower()
            fallback_key = LEGACY_PROFILE_ALIASES.get(
                normalized_identifier,
                normalized_identifier,
            )
            fallback = self.fallback_profiles.get(fallback_key)
            if fallback is None:
                raise ValueError(f"Agent profile '{explicit_identifier}' was not found.")
            return fallback

        profile = self.fallback_profiles.get(inferred_name)
        if profile is None:
            raise ValueError(f"Agent profile '{inferred_name}' was not found.")
        self._cache[inferred_name] = profile
        return profile

    @classmethod
    def _infer_profile_name(cls, task: Task) -> str:
        requested = (task.node or "worker").strip().lower()
        if requested in cls.fallback_profile_names() and requested != "worker":
            return requested
        alias = LEGACY_PROFILE_ALIASES.get(requested)
        if alias:
            return alias

        description = task.description.lower()
        keyword_groups = (
            (
                "source_researcher",
                ("crawl", "crawler", "search", "source", "cào", "tìm kiếm", "thu thập"),
            ),
            (
                "synthesis_agent",
                ("summar", "synthesis", "summary", "tóm tắt", "tổng hợp"),
            ),
            (
                "report_agent",
                ("report", "markdown", "báo cáo"),
            ),
            (
                "chart_agent",
                ("chart", "plot", "graph", "biểu đồ"),
            ),
        )
        for profile_name, keywords in keyword_groups:
            if any(keyword in description for keyword in keywords):
                return profile_name
        return "worker"

    @staticmethod
    def fallback_profile_names() -> set[str]:
        return set(DEFAULT_AGENT_PROFILES)

    @staticmethod
    def create_agent(
        resolved: ResolvedAgent,
        llm: BaseChatModel,
        task: Task,
    ) -> BaseAgent:
        """Instantiate the registered runtime class with the resolved profile."""

        system_prompt = (
            f"{resolved.profile.system_prompt}\n"
            f"Your assigned task is: {task.description}."
        )
        agent_class = AgentRegistry.get_agent_class(resolved.profile.runtime_name)
        return agent_class(
            name=resolved.profile.name,
            system_prompt=system_prompt,
            llm=llm,
            tools=resolved.tools,
        )
