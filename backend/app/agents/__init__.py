"""Reserved Agent boundary. No provider is connected in this release."""
from typing import Protocol


class AgentAdapter(Protocol):
    async def available(self) -> bool: ...


class UnavailableAgent:
    async def available(self) -> bool:
        return False
