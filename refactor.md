# System Role: Principal Quant Engineer
# Core Objective
The engine is currently blind to V3 price movements because `process_a_indexer` (LogsPoller) is exclusively listening for V2 `Sync` events. We must upgrade the HTTP ingestion payload to capture V3 `Swap` events, extract the spot price mathematically, and feed it to the Bellman-Ford graph. Additionally, we need a telemetry heartbeat in `process_b_sniper` to objectively prove graph evaluations are running.

# Directives

## 1. Multi-Topic Event Ingestion (`process_a_indexer.py` or `LogsPoller`)
* **The Payload:** Update the `eth_getLogs` RPC payload to listen for BOTH V2 `Sync` and V3 `Swap` events simultaneously.
  * V2 Sync Topic: `0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1`
  * V3 Swap Topic: `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67`
  * Set the `topics` parameter to act as a logical OR: `[["0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1", "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"]]`

## 2. V3 Spot Price Extraction
* **The Parser:** Inside the log processing loop, differentiate between V2 and V3 events by checking `log['topics'][0]`.
* **V3 Logic:** If the topic is a V3 `Swap`:
  * The `data` field contains unindexed parameters. Strip the `0x` prefix if present.
  * The `sqrtPriceX96` variable is located at byte offset 64 to 96 (hex characters 128 to 192).
  * Extract it as an integer: `sqrt_price_x96 = int(data_hex[128:192], 16)`
  * Calculate the basic spot price ratio: `spot_price_ratio = (sqrt_price_x96 / (2**96)) ** 2`
  * Push this `spot_price_ratio` to the graph state to update the edge weights dynamically, identical to the pipeline for V2 reserves.

## 3. The Discovery Heartbeat (`process_b_sniper.py`)
* The sniper is currently silent when no cycles are found, making observability impossible.
* Initialize a state counter in the class: `self.cycles_evaluated = 0`
* Increment `self.cycles_evaluated` every time the Bellman-Ford algorithm runs.
* Add an asynchronous timer or check within the main loop. Every 60 seconds, output a structured heartbeat log: 
  `self.logger.info(f"[HEARTBEAT] Graph active. Evaluated {self.cycles_evaluated} potential routing cycles in the last 60s.")`
* Reset `self.cycles_evaluated` to `0` immediately after logging.

# Output Requirements
Provide the refactored log parsing logic for the HTTP poller demonstrating V3 `sqrtPriceX96` extraction, and the exact Heartbeat implementation for `ProcessBSniper`. Do not include conversational filler.