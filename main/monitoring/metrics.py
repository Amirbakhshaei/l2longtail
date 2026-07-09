from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

graph_instances = Gauge("longtail_graph_instances", "Number of active graph instances")
gemini_latency = Histogram("longtail_gemini_latency_seconds", "Gemini API call latency")
rpc_errors = Counter("longtail_rpc_errors_total", "Total RPC errors", ["error_type"])
abort_counter = Counter("longtail_aborts_total", "Total aborted runs", ["reason"])
execution_counter = Counter("longtail_executions_total", "Total executions", ["mode"])
cache_hits = Counter("longtail_cache_hits_total", "Cache hits", ["cache_type"])
cache_misses = Counter("longtail_cache_misses_total", "Cache misses", ["cache_type"])


def start_metrics_server(port: int = 9090) -> None:
    start_http_server(port)
