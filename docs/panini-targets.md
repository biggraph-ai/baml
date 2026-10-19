# Panini compilation targets and host-language support

## Decision

Panini should support **Python and TypeScript first**, followed by **C#/.NET**,
then **Java and Go**. It should also expose a stable HTTP/OpenAPI protocol so
Ruby, PHP, Kotlin, Swift, Rust, and other hosts can use Panini without waiting
for a native generator.

This is independent from the two compiler frontends:

```text
Compiler frontend implementations       Generated host targets
---------------------------------       ----------------------
Python + Lark                --+         Python package
                              +-> TIR -> TypeScript package
C++ + re2c/Bison             --+         C#/.NET package
                                         Java/Go packages
                                         OpenAPI + JSON Schema
```

The Python and C++ frontends parse and validate `.panini` source. They are not
output targets. Both must produce the same typed intermediate representation
(TIR), and every host-language generator consumes that TIR.

## What “target support” means

Panini does not translate arbitrary method bodies into subtly different Python,
TypeScript, and C# programs. That approach would duplicate scheduler, checkpoint,
policy, compensation, and inference semantics across every target.

Instead, compilation produces three layers:

1. **Portable program artifact**—versioned Panini bytecode/IR containing schemas,
   instructions, graph topology, policies, effects, source maps, and debug data.
2. **Authoritative runtime**—executes that artifact locally or as a service and
   defines graph, retry, policy, streaming, and compensation behavior.
3. **Generated host SDK**—native types and ergonomic sync/async clients that load
   or call the artifact while preserving host-language conventions.

This architecture permits two deployment modes:

- **Embedded runtime:** the generated package loads a native/WASM Panini runtime
  in the host process.
- **Remote runtime:** the generated package calls a Panini service over the
  versioned protocol.

Generators may emit target-native deterministic helper code, but agent graph
semantics remain in the artifact/runtime. A target must reject an unsupported
feature rather than silently approximate it.

## Target roadmap

| Tier | Target | Generated surface | Primary rationale | Initial runtime mode |
| --- | --- | --- | --- | --- |
| 1 | Python 3.11+ | dataclasses/Pydantic-compatible models, sync and `async` clients, typed streaming | Fast agent/ML experimentation and broad data ecosystem | Embedded native/WASM plus remote |
| 1 | TypeScript 5+ on Node.js | interfaces/types, Zod-compatible validation adapters, `Promise` APIs, async iterables | Excellent async/tool workloads and web/server adoption | Embedded native/WASM plus remote |
| 2 | C#/.NET 8+ | records/classes, nullable annotations, `Task`, `IAsyncEnumerable`, DI extensions | Enterprise services, durability, and strong contracts | Remote first, embedded later |
| 3 | Java 21+ | records/POJOs, `CompletableFuture`, `Flow.Publisher`, dependency-injection adapters | Large enterprise JVM ecosystem | Remote first |
| 3 | Go 1.23+ | structs, contexts, channels/iterators, explicit error returns | Cloud services and operational tooling | Remote first |
| Universal | OpenAPI 3.1 + JSON Schema | schemas, endpoints, events, error envelopes | Immediate access from any language | Remote |

Version numbers in this table are initial language baselines, not permanent
claims about third-party SDK availability. Ecosystem integrations such as model
providers, MCP, and A2A must be separately versioned adapters.

## Workload comparison

The following is a Panini planning assessment—not a promise that every external
vendor SDK has equivalent features in every language:

| Area | TypeScript | Python | C#/.NET | Panini implication |
| --- | --- | --- | --- | --- |
| Workflow and contract modeling | Excellent | Good with runtime validation | Excellent | Generate native types and validators for all three |
| Async API/tool workloads | Excellent | Good with disciplined `async` use | Excellent | Async-first generator contract; sync wrappers only where safe |
| ML/data ecosystem | Moderate | Excellent | More limited | Ship Python first for local experimentation and data pipelines |
| Rapid agent experimentation | Very good | Best | Moderate | Python reference UX; TypeScript second first-class UX |
| Enterprise durability | Very good | Good | Excellent | Keep durability in Panini runtime so host language does not change semantics |
| Browser/client applications | Excellent | Limited | Moderate through .NET clients | TypeScript browser client calls remote runtime; never expose provider secrets |
| Enterprise dependency injection | Good | Framework-dependent | Excellent | Generate adapters, but keep the core client framework-neutral |

External integration availability changes independently of Panini. Do not bake
phrases such as “official SDK” into the language specification. The adapter
registry records provider, protocol, target, package, version range, and tested
capabilities.

## Generated API contract

Given this Panini declaration:

```panini
public class AuditResult {
  public bool violating;
  public string reason;
}

public agent FinancialAuditor {
  public AuditResult analyze(float amount, string country) {
    provider OpenAI;
    instruction #"Audit {{ amount }} in {{ country }}."#;
  }
}
```

the public API should feel native without changing semantics.

### Python

```python
from panini_client import FinancialAuditor

auditor = FinancialAuditor()
result = await auditor.analyze(amount=12_000.0, country="US")
print(result.violating)
```

### TypeScript

```typescript
import { FinancialAuditor } from "@acme/panini-client";

const auditor = new FinancialAuditor();
const result = await auditor.analyze({ amount: 12_000, country: "US" });
console.log(result.violating);
```

### C#

```csharp
using Acme.Panini;

var auditor = new FinancialAuditor();
AuditResult result = await auditor.AnalyzeAsync(12_000f, "US", cancellationToken);
Console.WriteLine(result.Violating);
```

Target naming conventions are intentional: `analyze` can become `AnalyzeAsync`
in C#, while its stable wire operation ID remains
`com.biggraph.compliance.FinancialAuditor.analyze`. Generated names and wire IDs
must both appear in source maps and reflection metadata.

## Type mapping

| Panini | Python | TypeScript | C# | Wire format |
| --- | --- | --- | --- | --- |
| `bool` | `bool` | `boolean` | `bool` | JSON Boolean |
| `int` | `int` | `number` with checked range | `long` by default | signed 64-bit JSON integer contract |
| `float` | `float` | `number` | `double` | IEEE-754 JSON number contract |
| `decimal` | `Decimal` | string-backed decimal type/adapter | `decimal` | canonical decimal string |
| `string` | `str` | `string` | `string` | UTF-8 JSON string |
| `T?` | `T | None` | `T | null` | `T?` | nullable value |
| `List<T>` | `list[T]` | `T[]` / `ReadonlyArray<T>` | `IReadOnlyList<T>` | JSON array |
| `Map<string,T>` | `dict[str,T]` | `Record<string,T>` | `IReadOnlyDictionary<string,T>` | JSON object |
| `bytes` | `bytes` | `Uint8Array` | `ReadOnlyMemory<byte>` | base64 string |
| `Instant` | timezone-aware `datetime` | `Temporal.Instant` adapter/string | `DateTimeOffset` | RFC 3339 string |
| `Stream<T>` | `AsyncIterator[T]` | `AsyncIterable<T>` | `IAsyncEnumerable<T>` | ordered event stream |

Numeric overflow, non-finite floats, time zones, map-key constraints, missing
versus null, and unknown enum values require normative wire rules. Code
generators must not rely on each language's default serializer behavior.

## Async, streaming, cancellation, and errors

The canonical generated operation is asynchronous. Every target must support:

- request cancellation and runtime deadlines;
- backpressured streaming or an explicitly bounded buffer;
- structured errors with stable Panini error codes;
- trace, graph-run, and checkpoint identifiers;
- deterministic serialization of inputs and outputs; and
- propagation of source-map locations for runtime failures.

Host mappings include:

| Concept | Python | TypeScript | C# |
| --- | --- | --- | --- |
| Async result | coroutine / `Awaitable<T>` | `Promise<T>` | `Task<T>` |
| Stream | `AsyncIterator<T>` | `AsyncIterable<T>` | `IAsyncEnumerable<T>` |
| Cancellation | task cancellation plus explicit token object | `AbortSignal` | `CancellationToken` |
| Typed failure | generated exception hierarchy plus error value | discriminated error envelope / generated error class | generated exception hierarchy plus result envelope |

Panini must specify whether a declared `throws E` maps to exceptions, result
objects, or both. The wire protocol always uses a typed error envelope; a
generator may expose idiomatic exceptions without discarding the envelope.

## MCP, A2A, and provider adapters

MCP and A2A support belong behind Panini capability interfaces:

```panini
public interface Capability<I, O> {
  O invoke(I input, InvocationContext context) throws CapabilityError;
}
```

An adapter may use a native host SDK where available or the protocol transport
directly. Generated user APIs must remain stable across those implementation
choices. Every adapter publishes a capability manifest such as:

```json
{
  "adapter": "mcp",
  "protocolVersion": "<version-range>",
  "targets": ["python", "typescript", "dotnet"],
  "features": ["tools", "resources", "prompts", "streaming"],
  "transport": ["stdio", "http"]
}
```

The compiler checks this manifest when a selected backend requires a feature.
This prevents a program from compiling for a target whose adapter lacks, for
example, streaming or a required transport.

## Backend configuration

Targets belong in project configuration rather than `.panini` source:

```toml
[panini]
language_version = "1"
artifact = "build/compliance.pnir"

[target.python]
package = "acme_compliance"
runtime = "embedded"

[target.typescript]
package = "@acme/compliance"
runtime = "embedded"

[target.dotnet]
namespace = "Acme.Compliance"
runtime = "remote"
endpoint_env = "PANINI_ENDPOINT"
```

Recommended CLI:

```text
panini check
panini build                         # compile source once to PNIR
panini generate python
panini generate typescript
panini generate dotnet
panini generate openapi
panini capabilities --target dotnet # explain supported/missing features
```

`panini build` is target-neutral. `panini generate` consumes the built IR and
must not re-parse source independently.

## Backend capability contract

Each target/runtime pair declares capabilities rather than assuming parity:

```text
embedded-runtime, remote-runtime, streaming, checkpoints,
durable-timers, parallel-branches, compensation, local-capabilities,
remote-capabilities, mcp-stdio, mcp-http, a2a, observability
```

Compilation behavior is one of:

- **supported**—generate the target;
- **supported with explicit adapter**—require configuration and generate it;
- **unsupported**—fail with a diagnostic naming the missing capability.

No “best effort” lowering is allowed for durability, compensation, policy, or
concurrency semantics.

## Delivery plan

1. Freeze the PNIR and wire type system before implementing three generators.
2. Implement Python and TypeScript generators against the reference runtime.
3. Add OpenAPI/JSON Schema generation as the universal compatibility surface.
4. Add C# with remote runtime first, including streaming and cancellation.
5. Add embedded C# only after the native ABI and packaging story are stable.
6. Add Java and Go from the same generator conformance suite.
7. Require every generator to pass shared serialization, error, stream, and
   graph-run fixtures.

This ordering gives experimentation and web workloads first-class support while
preserving an enterprise path and preventing target languages from fragmenting
Panini's execution semantics.

### Reference implementation status (M6)

The Python reference profile now provides `panini build`, `panini inspect`, and
`panini capabilities`. Its self-checking source/PNIR bundle, compatibility
fixture and measured evaluation harness are described in
[artifacts and evaluation](panini-artifacts-evaluation.md). Additional target
SDKs and generators above remain design directions rather than implemented
backends.
