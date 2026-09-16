"""Adapters that expose the execution engine to application modules."""

from typing import Any, AsyncGenerator, Dict

from app.execution.agents.resolver import AgentResolver
from app.execution.graph import build_execution_graph, get_graph_config
from app.execution.ports import ExecutionPort
from app.execution.state import State


class LangGraphExecutionAdapter(ExecutionPort):
    """Current in-process LangGraph adapter behind the execution port."""

    def __init__(self, agent_resolver: AgentResolver | None = None) -> None:
        self.agent_resolver = agent_resolver or AgentResolver()

    async def create_plan(self, run_id: str, initial_state: State) -> State:
        graph = build_execution_graph(agent_resolver=self.agent_resolver)
        return await graph.ainvoke(initial_state, config=get_graph_config(run_id))

    async def continue_conversation(self, run_id: str, message: str) -> State:
        graph = build_execution_graph(agent_resolver=self.agent_resolver)
        return await graph.ainvoke(
            {"messages": [message]},
            config=get_graph_config(run_id),
        )

    async def _execute(
        self,
        run_id: str,
        initial_state: State,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        graph = build_execution_graph(agent_resolver=self.agent_resolver)
        async for chunk in graph.astream(
            initial_state,
            config=get_graph_config(run_id),
            stream_mode="updates",
        ):
            yield chunk

    def execute_run(
        self,
        run_id: str,
        initial_state: State,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        return self._execute(run_id, initial_state)

    async def _stream(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        graph = build_execution_graph(agent_resolver=self.agent_resolver)
        async for chunk in graph.astream(
            {"mode": "executing"},
            config=get_graph_config(run_id),
            stream_mode="updates",
        ):
            yield chunk

    def stream_execution(self, run_id: str) -> AsyncGenerator[Dict[str, Any], None]:
        return self._stream(run_id)
