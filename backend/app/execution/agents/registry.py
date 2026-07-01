from typing import Dict, Type, List
from app.execution.agents.base import BaseAgent, SupervisorAgent, WorkerAgent

class AgentRegistry:
    """
    Registry for loading and instantiating agent implementations dynamically.
    """
    _registry: Dict[str, Type[BaseAgent]] = {}

    @classmethod
    def register(cls, name: str):
        """
        Decorator to register an agent class under a specific name.
        """
        def decorator(agent_cls: Type[BaseAgent]):
            cls._registry[name] = agent_cls
            return agent_cls
        return decorator

    @classmethod
    def get_agent_class(cls, name: str) -> Type[BaseAgent]:
        """
        Retrieve an agent class by its registered name.
        """
        if name not in cls._registry:
            raise ValueError(f"Agent '{name}' is not registered in AgentRegistry.")
        return cls._registry[name]

    @classmethod
    def create_agent(cls, name: str, **kwargs) -> BaseAgent:
        """
        Factory method to instantiate a registered agent.
        """
        agent_cls = cls.get_agent_class(name)
        return agent_cls(**kwargs)

    @classmethod
    def list_agents(cls) -> List[str]:
        """
        List all registered agent names.
        """
        return list(cls._registry.keys())

# Pre-register default platform agents
AgentRegistry._registry["supervisor"] = SupervisorAgent
AgentRegistry._registry["worker"] = WorkerAgent
