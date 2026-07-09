---
name: quant-slippage-invariant
description: Use this skill to compute exact Constant Product AMM execution slippage and isolated net profit.
---
# Market Impact Calculation Rules

When evaluating trading viability for liquid tokens, run localized NumPy/Pandas processing loops matching these explicit mathematical metrics:

1. Calculate the real-world price impact penalty:
   Expected Slippage % = (Trade Size USD / Pool Liquidity USD) * 100

2. Derive the net arbitrage yield, applying the hardcoded L2 Arbitrum execution gas cost ($0.02 base configuration):
   Net Profit USD = ((Gross Spread % - Expected Slippage %) / 100) * Trade Size USD - 0.02

3. Execution Parameter: If Net Profit USD is less than $0.50, flag the context as unviable and abort execution.