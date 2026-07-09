from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver


def create_checkpointer() -> MemorySaver:
    return MemorySaver()
