# Agent Architecture

## Definition

This project treats an Agent as the composition of a Model and a Harness:

```text
Agent = Model + Harness
```

The Model proposes native tool calls or final text. The Harness owns the
deterministic execution environment around those proposals.

## Boundaries

### Model

`bot_app/model/` owns provider adaptation. A model adapter may translate a
provider wire format, but it must return the provider-neutral `ModelTurn`
contract. It does not execute tools, write memory, send messages, or apply
domain policy.

### Harness

`bot_app/harness/` owns:

- run state and the model/tool observation loop;
- tool argument validation, timeout, exception normalization, and result limits;
- run and tool budgets;
- hard policies such as BTD6 knowledge grounding;
- context preparation and post-delivery conversation commit;
- per-session serialization, run lifecycle state, and trace events.

The Harness never imports NcatBot and never sends a QQ message.

### Application

`bot_app/application.py` owns the transaction around the Agent:

```text
receive -> generate -> deliver -> commit -> finalize usage
```

Conversation memory is committed only after successful delivery. The session
lock covers generation, delivery, and commit so messages from one session
cannot reorder the conversation.

The Harness may publish provider-neutral `ModelTurnUpdate` observations through
an optional callback. `BotApplication` can use those observations for
best-effort progress delivery when a group enables model-process messages. The
terminal model turn still uses the normal final delivery path, so it is sent
exactly once and remains the only reply committed to conversation memory.

### Adapters

`plugin.py`, `bot_app/runner.py`, and scheduled tasks are adapters. They convert
channel or timer events into `EventPayload` and delegate to `BotApplication`.
Both runtime entry points use `bot_app/bootstrap.py` as their composition root.

### Tools

`bot_app/tools/` owns deterministic domain capabilities. A tool consists of a
JSON schema and an async handler. Tool handlers receive `ToolDependencies` and
must return `CommandResultPayload`; they do not call the Model or send output.

## Run Protocol

The native tool-calling state flow is:

```text
context_ready
    -> policy_required_tools? -> execute -> observations
    -> model_turn
       -> tool_calls -> validate -> execute -> observations -> model_turn
       -> final_text -> policy_check -> AgentOutcome
```

Every tool observation is paired with the original tool-call ID. Invalid JSON,
schema errors, unknown tools, timeouts, and handler exceptions become failed
observations so the Model can recover within the remaining budget.

Only model-originated tool calls are represented as native assistant/tool
message pairs. Tools required by a deterministic policy are injected as
`harness_tool_observations`; the Harness never fabricates an assistant turn or
provider-specific reasoning fields on behalf of the Model.

`ModelTurnUpdate` contains only the Model's public `content`, provider-neutral
tool calls, model name, and turn metadata. Provider reasoning fields such as
`reasoning_content` are retained only in the native API conversation history
when required by the provider and are never exposed through progress delivery.
The QQ progress adapter sends only non-empty public `content`; tool calls and
turn metadata are not rendered into group messages.

## Invariants

1. Only the Model adapter handles provider-specific response objects.
2. Only `ToolExecutor` executes registered tools in the Agent path.
3. Hard safety or evidence requirements are code policies, not prompt-only rules.
4. No channel API is imported by Model, Harness, or tool domain modules.
5. No assistant turn is committed before successful delivery.
6. Every Run has a terminal reason and finalizes token accounting on all exits.
7. Tool schemas in `ToolRegistry` are the runtime source of truth.
8. The core loop does not contain tool-name-specific domain branching.
9. Progress delivery is observational: it does not mutate context or replace
   final delivery and memory commit.

## Extension Rules

- Add a provider by implementing `ModelGateway` and mapping its response to
  `ModelTurn`.
- Add a tool by returning a `ConcreteTool` from the appropriate domain builder
  and registering that builder in `ToolRegistry`.
- Add a hard behavior requirement as a policy invoked by the Harness; keep
  advisory behavior in the Agent instructions.
- Add a channel by adapting its events and delivery API around
  `BotApplication.process_event()`.
- Add persistent conversation storage behind the context/session boundary;
  do not put database writes into the Model loop.
