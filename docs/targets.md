# Target languages and agent integrations

Panini describes orchestration independently of the code implementing a tool or
model call. The host application supplies that code; the runtime owns graph
progress, budgets, validated state updates, authorization and effect records.
This boundary is intended to let different host languages participate without
reimplementing workflow semantics differently in every SDK.

## Availability

| Host or integration | Availability | Role in agent applications |
| --- | --- | --- |
| Python 3.11+ | Implemented compiler, synchronous reference executor and callback API | Model/data experiments, local tools, evaluation and backend integration |
| TypeScript / Node.js | Planned; no generator or SDK ships | Asynchronous tool services, web backends and application-facing agent clients |
| C# / .NET | Planned; no generator or SDK ships | Typed service integration, enterprise identity and operational tooling |
| Java | Planned; no generator or SDK ships | JVM services and existing business-system integrations |
| Go | Planned; no generator or SDK ships | Cloud services and operational control planes |
| HTTP / OpenAPI + JSON Schema | Proposed service boundary; no server or generated protocol ships | Access from any language through a common runtime contract |
| Native / WASM embedding | Proposed; no runtime ABI ships | Local execution from hosts beyond Python |

Compiler implementation languages and host targets are different concepts. The
working frontend is Python with Lark. The Bison grammar is a syntax prototype,
not a native frontend. Neither TypeScript nor C# source generation is available.

## Using Python today

Python applications compile source with `compile_source`, provide ordinary
state-transformer callbacks, register probability adapters for decisions, and
bind external tools through `Capability`. Mutations require a durable journal;
declared capabilities require an authorizer. Source method bodies remain opaque.
See the [development guide](../panini/README.md) and runnable
[decision](../panini/examples/run_decision.py) and
[effect](../panini/examples/run_refund.py) integrations.

The runtime API is synchronous. Tool callbacks can communicate with external
services, but no built-in provider, remote workflow service, MCP adapter or A2A
adapter is supplied. The example decision and payment bindings are local mocks.

## Cross-language architecture

The intended architecture has three contracts:

1. A portable source/PNIR artifact that describes schemas, decisions, graph
   topology, policies, effects and source locations.
2. One authoritative runtime contract for dispatch, state validation, failure
   handling, journaling and recovery.
3. Host-facing bindings that present native types and transport/runtime clients.

The current artifact loader recompiles bundled source and compares PNIR. It is
not a standalone bytecode engine. Future generators or service clients must
preserve the same semantics and declare unsupported features explicitly.

A remote deployment would centralize execution and expose run state, errors,
receipts and traces over a versioned protocol. An embedded deployment would need
an explicit runtime ABI and packaging strategy. Neither deployment architecture
is implemented beyond the in-process Python reference runtime.

## Language-specific design considerations

| Concern | Python | TypeScript | .NET / Java / Go |
| --- | --- | --- | --- |
| Typed state | Runtime validation against local schemas | Planned generated types plus runtime validation | Planned native records/classes/structs plus validation |
| Tool invocation | Registered callbacks and capability objects | Planned async service/client interfaces | Planned host-native cancellation and service bindings |
| Decisions | Validated probability callback | Planned adapter using the same response contract | Planned adapter using the same response contract |
| Durability | SQLite reference journal | Must preserve authoritative counters and receipts | Must preserve authoritative counters and receipts |
| Errors | Structured runtime failures and diagnostics | Planned structured envelopes with source locations | Planned structured envelopes with source locations |

Wire types require explicit rules before SDKs can be interoperable. Panini state
integers are signed 64-bit; JavaScript numbers cannot exactly represent every
such integer. A future wire contract must choose a lossless representation.
Finite floats, enum identity, nested state, missing versus null, schema versions
and receipt validation also need shared conformance fixtures. The reference
profile does not currently support arbitrary lists, maps, decimals or streams.

## Agent protocols and adapters

Model providers, MCP tool endpoints and A2A peers belong behind adapters, not in
workflow guards. An adapter should publish its versioned capabilities and make
its authorization, idempotency, reconciliation and cancellation behavior clear.
The runtime cannot attest that a provider produced a claimed model response or
that a remote service followed a declared contract.

Adapters must distinguish a known failure from an unknown remote outcome.
Cancellation acknowledgement is not proof that a mutation did not commit.
Business-level idempotency may span several graph visits and cannot rely solely
on per-invocation keys. These obligations apply regardless of host language.

## Compatibility requirements

A future target needs shared tests for state serialization, decision validation,
errors, budgets, policies, deadlines, receipts, uncertain outcomes and resume.
Unsupported durability or authorization features must be rejected rather than
silently approximated. `panini capabilities` currently reports the Python
reference profile; there is no `generate` command or `--target` switch.
