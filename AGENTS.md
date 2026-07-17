# L2 Long-Tail MEV: AI Agent Directives

## 1. Core Objective & Philosophy
This codebase is a production-grade MEV (Miner Extractable Value) extraction engine operating on the Arbitrum Long-Tail. 
* **Optimize for:** Leverage, ROI, execution speed, and EV (Expected Value) precision.
* **Avoid:** Premature optimization, artificial heuristics, and local simulation of complex EVM state machines.

## 2. Architectural Invariants

### 2.1. Pricing & Simulation (V3 Geometry)
* **DO NOT** attempt to write, integrate, or simulate Uniswap V3 tick math locally in Python. 
* All V3 pricing evaluation must be routed through the On-Chain Grid Search infrastructure (`infra/onchain_pricing.py`).
* We rely exclusively on the `Multicall3` contract wrapping the Uniswap V3 `QuoterV2` to batch-simulate exact input-to-output yields on the EVM directly. 
* V3 Paths must always be byte-packed accurately using `eth_abi.packed.encode_packed` matching the `[Token, Fee, Token]` layout.

### 2.2. Capital Sizing & Constraints
* **DO NOT** use static heuristics like `trade_size_usd = 10` or `min_spread_pct = 0.5`. Flashloans provide unconstrained capital.
* Arbitrage sizing is exclusively determined dynamically via the `Multicall3` grid search array (e.g., `[0.1, 0.5, 1.0, 5.0, 10.0]` WETH).
* The absolute and only validation gate for execution is positive Expected Value (EV): 
  `if (amount_out_wei - amount_in_wei) > estimated_gas_cost_wei: execute()`

### 2.3. Network Transport & Error Handling
* We utilize high-frequency HTTP polling against standard RPCs. 
* **DO NOT** catch generic or invalid exceptions (e.g., `BaseException`, raw strings, or module names) in network loops. 
* All `try...except` blocks wrapping RPC calls must specifically target connection/timeout objects (e.g., `httpx.RequestError`, `asyncio.TimeoutError`, `aiohttp.ClientError`).
* Network resets (HTTP/2 stream drops) must be caught silently, ignored, and reconnected without crashing the primary thread.

## 3. Telemetry & Observability
Standard `print` or generic `INFO` logs are unacceptable for process evaluations. Every cycle evaluated by `ProcessBSniper` must output a strict, multi-line telemetry block containing:
1. **[ROUTE]** The exact geometric path and fee tiers.
2. **[LATENCY]** Sub-millisecond timing of the RPC round-trip.
3. **[GRID]** The raw input-to-output matrix of the Multicall probe.
4. **[ECONOMICS]** Absolute wei accounting of Gross Revenue, Est. Gas Cost, and Net EV.
5. **[DECISION]** The deterministic action taken (`EXECUTE` or `REJECT` with reason).

## 4. Coding Standards
* Write production-quality, asynchronous Python.
* Prioritize readability and modularity.
* Provide clean type-hints for all function parameters and returns.
* When adding features, do not import bloated libraries if standard libraries or lightweight asynchronous alternatives exist.

 AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Cursor, Copilot, Antigravity, etc.) when working with code in this repository.

> **Scope:** This file configures agents working on the [`addyosmani/agent-skills`](https://github.com/addyosmani/agent-skills) repository itself. It is not meant to be copied into other projects or into a global agent configuration; the reusable assets are the skills in `skills/`, not this file.

## Repository Overview

A collection of skills for Claude.ai and Claude Code for senior software engineers. Skills are packaged instructions and scripts that extend Claude and your coding agents capabilities.

## OpenCode Integration

OpenCode uses a **skill-driven execution model** powered by the `skill` tool and this repository's `/skills` directory.

### Core Rules

- If a task matches a skill, you MUST invoke it
- Skills are located in `skills/<skill-name>/SKILL.md`
- Never implement directly if a skill applies
- Always follow the skill instructions exactly (do not partially apply them)

### Intent → Skill Mapping

The agent should automatically map user intent to skills:

- Feature / new functionality → `spec-driven-development`, then `incremental-implementation`, `test-driven-development`
- Planning / breakdown → `planning-and-task-breakdown`
- Bug / failure / unexpected behavior → `debugging-and-error-recovery`
- Code review → `code-review-and-quality`
- Refactoring / simplification → `code-simplification`
- API or interface design → `api-and-interface-design`
- UI work → `frontend-ui-engineering`

### Lifecycle Mapping (Implicit Commands)

OpenCode does not support slash commands like `/spec` or `/plan`.

Instead, the agent must internally follow this lifecycle:

- DEFINE → `spec-driven-development`
- PLAN → `planning-and-task-breakdown`
- BUILD → `incremental-implementation` + `test-driven-development`
- VERIFY → `debugging-and-error-recovery`
- REVIEW → `code-review-and-quality`
- SHIP → `shipping-and-launch`

### Execution Model

For every request:

1. Determine if any skill applies (even 1% chance)
2. Invoke the appropriate skill using the `skill` tool
3. Follow the skill workflow strictly
4. Only proceed to implementation after required steps (spec, plan, etc.) are complete

### Anti-Rationalization

The following thoughts are incorrect and must be ignored:

- "This is too small for a skill"
- "I can just quickly implement this"
- "I’ll gather context first"

Correct behavior:

- Always check for and use skills first

This ensures OpenCode behaves similarly to Claude Code with full workflow enforcement.

## Orchestration: Personas, Skills, and Commands

This repo has three composable layers. They have different jobs and should not be confused:

- **Skills** (`skills/<name>/SKILL.md`) — workflows with steps and exit criteria. The *how*. Mandatory hops when an intent matches.
- **Personas** (`agents/<role>.md`) — roles with a perspective and an output format. The *who*.
- **Slash commands** (`.claude/commands/*.md`) — user-facing entry points. The *when*. The orchestration layer.

Composition rule: **the user (or a slash command) is the orchestrator. Personas do not invoke other personas.** A persona may invoke skills.

The only multi-persona orchestration pattern this repo endorses is **parallel fan-out with a merge step** — used by `/ship` to run `code-reviewer`, `security-auditor`, and `test-engineer` concurrently and synthesize their reports. Do not build a "router" persona that decides which other persona to call; that's the job of slash commands and intent mapping.

See [docs/agents.md](docs/agents.md) for the decision matrix and [references/orchestration-patterns.md](references/orchestration-patterns.md) for the full pattern catalog.

**Claude Code interop:** the personas in `agents/` work as Claude Code subagents (auto-discovered from this plugin's `agents/` directory) and as Agent Teams teammates (referenced by name when spawning). Two platform constraints align with our rules: subagents cannot spawn other subagents, and teams cannot nest. Plugin agents silently ignore the `hooks`, `mcpServers`, and `permissionMode` frontmatter fields.

## Creating a New Skill

> **Before you start:** run the pre-flight checks in [CONTRIBUTING.md](CONTRIBUTING.md#before-proposing-a-new-skill), search the catalog, check open PRs (`gh pr list --state open`), confirm the idea fits [docs/skill-anatomy.md](docs/skill-anatomy.md), and justify the gap in your PR description. Most new-skill ideas overlap an existing skill or an open PR; prefer extending an existing skill over adding a near-duplicate. CONTRIBUTING.md is the single source of truth for this workflow.

Skills in this repo are markdown-first: each lives at `skills/<kebab-case-name>/SKILL.md` with YAML frontmatter (`name`, `description`) and follows the section anatomy (Overview, When to Use, Process, Common Rationalizations, Red Flags, Verification). Add a `scripts/` directory only when the skill ships runnable helpers; most skills are markdown only, and there are no per-skill zip packages.

For the full format, naming conventions, frontmatter rules, supporting-file thresholds, and writing principles, see [docs/skill-anatomy.md](docs/skill-anatomy.md), the single source of truth for skill structure. Do not restate that guidance here, link to it.
