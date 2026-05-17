# Portfolio Script Refactor Design

## Why this refactor

The current scripts `run_robinhood_positions.py` and `run_portfolio_intent_agent.py`
work, but each script blends multiple responsibilities:

- CLI parsing and validation
- domain orchestration
- external AI server transport/retry logic
- tool dispatch
- presentation/report formatting

This makes reuse difficult and increases drift risk as new `run_*` scripts are added.

## Current pain points

### `run_robinhood_positions.py`

- Large inline CLI parser for Monte Carlo overrides.
- Duplicate totals calculations that are also computed in analyzer outputs.
- Very large inlined console formatting block that is hard to test independently.

### `run_portfolio_intent_agent.py`

- Inline planner loop with orchestration, parsing, tool dispatch, and synthesis.
- Tool routing implemented as long `if` chain instead of composable tool objects.
- Custom `/generate` transport logic duplicated in other scripts.
- Mixed concerns around workbook discovery, validation, and agent control flow.

## Target architecture

### 1) Shared AI server generate client

Create a reusable client in `common/compound/aiserver_generate_client.py`:

- typed request payload handling
- profile/provider support
- consistent timeout handling
- consistent HTTP error shaping/truncation
- single place to maintain `/generate` call semantics

Used by:

- `mgmt/ask_ai.py`
- `run_portfolio_intent_agent.py`
- future script and tool modules

### 2) Shared positions reporting/presentation

Create `finance_pipeline/position_reporting.py`:

- pure helper for aggregate totals
- console presenter function for position analysis output
- isolates formatting from command orchestration

Used by:

- `run_robinhood_positions.py`
- future UI/report adapters

### 3) Next-stage agent loop extraction (planned)

Introduce reusable loop primitives in a future iteration:

- `AgentState`, `PlanDecision`, `ToolResult`
- registry-based tool dispatch
- planner/synthesis clients
- generic bounded loop runner

Then map portfolio-specific tools onto those primitives.

## Reusable class and pattern recommendations

### CLI command pattern

`BaseCommand` pattern for scripts:

- `build_parser()`
- `validate(args)`
- `run(args)`
- `render(result)`
- `execute() -> exit_code`

This supports deterministic exit behavior and testable units.

### Agent tool pattern

Move from string-dispatch to tool objects:

- `name`
- `description`
- `args_schema`
- `execute(context, args)`

Dispatch through a registry map instead of `if/elif` chains.

### Service + presenter split

For script entrypoints:

- service objects do business logic + IO orchestration
- presenters format console/file output

## Phased rollout plan

### Phase 1 (start now)

- Add shared AI server generate client.
- Wire `mgmt/ask_ai.py` and `run_portfolio_intent_agent.py` to shared client.
- Extract positions report formatting/totals into reusable module.
- Wire `run_robinhood_positions.py` to reusable presenter.

### Phase 2

- Extract workbook discovery/validation to dedicated service module.
- Extract planner response parsing/validation to dedicated parser module.
- Add unit tests around parser and workbook discovery.

### Phase 3

- Replace agent tool `if/elif` chain with registry + tool classes.
- Introduce generic loop runner abstractions.
- Keep behavior/output stable while reducing script complexity.

### Phase 4

- Introduce shared base command class and migrate `run_*` scripts.
- Consolidate error taxonomy and exit-code mapping.
- Add structured logging adapter for scripts and loop execution.

## Non-goals for initial refactor

- No behavior changes in Monte Carlo calculation semantics.
- No output contract changes unless explicitly requested.
- No provider-selection policy changes in this first pass.

## Validation strategy

- Keep script stdout contract stable where possible.
- Run existing script flows on representative inputs.
- Add focused tests around extracted helpers in follow-up commits.

## Progress status

Completed so far:

- shared AI generate client extraction
- positions presenter extraction
- workbook discovery/inspection extraction
- tool registry + tool object extraction
- positions command class extraction
- loop planning and loop runner extraction
- portfolio intent command class extraction
- shared `BaseCommand` + command error abstraction extraction
- `run_robinhood_csvimport.py` migration to `BaseCommand`
- `mgmt/ask_ai.py` migration to `BaseCommand`
- unit tests for `BaseCommand`, planner parsing, and loop runner behavior

Remaining high-value extractions:

- migrate additional `run_*` scripts onto shared `BaseCommand`
- standardize command-specific `UsageError` exit-code policies across scripts
- dedicated tests for tool classes

