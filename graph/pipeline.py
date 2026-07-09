from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from agents.execution_node import build_execution_node
from agents.filter_gate_node import build_filter_gate_node
from agents.ingestion_node import build_ingestion_node
from agents.rag_auditor_node import SourceFetcher, build_rag_auditor_node
from agents.slippage_analyst_node import build_slippage_analyst_node
from agents.state import ArbitrageState, Status
from config.settings import Settings
from db.blacklist import BlacklistDB
from db.cache import ContractCache
from infra.keystore import Keystore
from infra.rpc_manager import RPCManager
from monitoring.alerts import TelegramAlerts

logger = logging.getLogger(__name__)


def route_on_status(state: Any) -> str:
    if isinstance(state, ArbitrageState):
        status = state.status
    elif isinstance(state, dict):
        status = state.get("status")
    else:
        status = getattr(state, "status", None)
    if isinstance(status, Status):
        return "abort" if status == Status.ABORTED else "continue"
    return "abort" if status == "ABORTED" else "continue"


async def terminal_node(state: ArbitrageState) -> ArbitrageState:
    logger.info(
        "terminal: run_id=%s status=%s reason=%s",
        state.run_id,
        state.status.value,
        state.reason or "N/A",
    )
    return state


def build_pipeline(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
    rpc_manager: RPCManager | None = None,
    keystore: Keystore | None = None,
    alerts: TelegramAlerts | None = None,
    source_fetcher: SourceFetcher | None = None,
) -> StateGraph[ArbitrageState]:
    graph = StateGraph(ArbitrageState)

    graph.add_node("ingestion", build_ingestion_node(settings))
    graph.add_node("filter_gate", build_filter_gate_node(settings, blacklist_db))
    graph.add_node(
        "rag_auditor",
        build_rag_auditor_node(settings, cache, rpc_manager, source_fetcher),
    )
    graph.add_node("slippage_analyst", build_slippage_analyst_node(settings))
    exec_node = build_execution_node(settings, cache, keystore, rpc_manager, alerts)
    graph.add_node("execution", exec_node)
    graph.add_node("terminal", terminal_node)

    graph.set_entry_point("ingestion")

    graph.add_edge("ingestion", "filter_gate")

    graph.add_conditional_edges(
        "filter_gate",
        route_on_status,
        {"abort": "terminal", "continue": "rag_auditor"},
    )

    graph.add_conditional_edges(
        "rag_auditor",
        route_on_status,
        {"abort": "terminal", "continue": "slippage_analyst"},
    )

    graph.add_conditional_edges(
        "slippage_analyst",
        route_on_status,
        {"abort": "terminal", "continue": "execution"},
    )

    graph.add_edge("execution", "terminal")
    graph.add_edge("terminal", END)

    return graph


async def compile_graph(
    settings: Settings,
    cache: ContractCache,
    blacklist_db: BlacklistDB,
    rpc_manager: RPCManager | None = None,
    keystore: Keystore | None = None,
    alerts: TelegramAlerts | None = None,
    source_fetcher: SourceFetcher | None = None,
) -> Any:
    await cache.init()

    graph = build_pipeline(
        settings, cache, blacklist_db, rpc_manager, keystore, alerts, source_fetcher
    )
    return graph.compile()
