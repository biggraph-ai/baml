# Lexer, parser, and representation-debugging choices for Panini

## Recommendation

Use one Panini grammar and two deliberately different implementations of that
same frontend contract:

1. **Python reference frontend: Lark** with its LALR parser, for rapid language
   development, parse-tree inspection, interactive parsing, and approachable
   debugging.
2. **Production/native frontend: GNU Bison C++ (`lalr1.cc`) plus re2c**, for a
   long-lived native compiler with deterministic generated code, explicit parser
   state reports, and strong performance.
3. Keep **PLY** as the alternative when “traditional lex/yacc written entirely
   in Python” is a hard requirement. Do not select PLY merely because its API is
   familiar: first benchmark its diagnostics, typing ergonomics, packaging, and
   editor-recovery behavior against Lark.

Tree-sitter should be an editor parser, not the normative compiler parser. It is
excellent for incremental concrete syntax trees but intentionally tolerates
incomplete code and can therefore disagree with a strict compiler frontend.

The two implementations are not compatibility modes and do not accept different
surface syntaxes. The portable contract is not a particular generator. It is
the versioned token specification, grammar, AST schema, diagnostics, and shared
conformance corpus. Canonical source spellings and token names are listed in
[`panini-keywords.md`](panini-keywords.md).

Frontend implementation languages are distinct from generated host targets.
Python/Lark and C++/Bison produce one typed IR; Python, TypeScript, C#/.NET,
Java, Go, and OpenAPI generators consume it. See
[`panini-targets.md`](panini-targets.md).

## Decision matrix

| Choice | Algorithm / host | Best use | Strengths | Important tradeoffs |
| --- | --- | --- | --- | --- |
| Lark | Earley, CYK, or LALR in Python | Python reference compiler and grammar experimentation | Grammar files, automatic trees, visitors/transformers, interactive parser API, strict mode, contextual lexer | Not a classic yacc API; benchmark large files and startup time |
| PLY | lex + yacc/LALR in Python | Traditional Python yacc implementation | Small dependency surface, familiar yacc productions, `parser.out` state report, precedence support | Semantic actions live in Python docstrings/functions; weaker shared-grammar story unless generated |
| GNU Bison C++ | LALR/IELR/GLR in generated C++ | Production native compiler | Mature, typed C++ skeleton, detailed automaton reports, counterexamples, traces, Graphviz/XML reports | Requires a separate lexer and C++ build; semantic actions are generator-specific |
| re2c | Generated lexer in C/C++ | Production lexer paired with Bison | Fast deterministic scanners, explicit conditions for instruction/interpolation modes | Less convenient during early grammar iteration than Python lexing |
| Flex + Bison | Generated C/C++ | Conventional yacc toolchain | Familiar and widely packaged | re2c generally gives more direct control over scanner code and modes |
| ANTLR | Adaptive LL, many targets | One grammar generating several target languages | Broad ecosystem and parse-tree tooling | Not LALR(1); generated runtimes and behavior differ by target |
| tree-sitter | Incremental GLR-style parsing | IDEs, highlighting, structural navigation | Fast incremental CST and robust incomplete-input handling | Not a substitute for compiler acceptance or semantic validation |

## Why Python should be the reference, not necessarily the only compiler

Python minimizes the cost of experimenting with grammar and AST design. It is
also convenient for golden tests:

```text
source.panini
  -> Python parser
  -> canonical AST JSON
  -> compare with fixtures
```

Once the language stabilizes, a C++ or Rust frontend can parse the same corpus
and emit byte-for-byte equivalent normalized AST JSON. This provides portability
without requiring every host language to reimplement type checking and graph
verification immediately.

For a Python-first prototype, configure Lark approximately as follows:

```python
from lark import Lark

parser = Lark.open(
    "panini.lark",
    parser="lalr",
    lexer="contextual",
    propagate_positions=True,
    maybe_placeholders=False,
    strict=True,
)
```

The important settings are strict conflict checking and propagated source
positions. Do not catch parser-construction failures and silently fall back to
Earley: that would hide a loss of the LALR(1) guarantee.

If a literal yacc workflow is more important than Lark's tree and interactive
APIs, use PLY and always retain its generated `parser.out` in CI artifacts when
the grammar check fails. PLY should emit the same canonical AST objects as the
Lark implementation rather than exposing PLY-specific parse nodes.

## Lexer architecture

No parser generator removes the need for a precise lexer contract. Panini needs
lexical modes for at least:

- ordinary code;
- raw instruction content;
- instruction interpolation expressions;
- nested block comments; and
- typed provider configuration literals.

The lexer must attach this information to every token:

```text
kind, exact_text, decoded_value?, file_id,
start_byte, end_byte, start_line, start_column, end_line, end_column
```

Use byte offsets as the canonical span identity and line/column values as cached
display data. Preserve comments and whitespace on a trivia channel so the CST
can round-trip source exactly. Never make the compiler AST responsible for
formatting trivia.

For nested generic types, specify whether the lexer returns `>>` or two `>`
tokens. The canonical Panini recommendation is two `>` tokens while the parser
is in a type context. All implementations must follow the same rule.

## Debuggable program representations

Do not debug only the final generated Python or C++. Make every compiler stage
observable and stable enough for snapshot tests:

```text
tokens -> CST -> AST -> resolved AST/HIR -> typed IR -> graph IR -> backend IR
```

### Required CLI surface

```text
panini lex file.panini                 # token stream with spans and modes
panini parse file.panini --emit=cst    # lossless tree
panini parse file.panini --emit=ast    # normalized syntax JSON
panini check file.panini --emit=hir    # symbol IDs and resolved references
panini check file.panini --emit=tir    # inferred types and effects
panini graph file.panini --emit=dot    # orchestration CFG/graph
panini compile file.panini --emit=ir   # backend-neutral typed IR
panini explain E2301                   # stable diagnostic documentation
```

Every textual dump should be deterministic: sort map keys, use stable numeric
IDs derived from source order, omit memory addresses, normalize paths, and
provide a `--show-spans` switch. Determinism makes diffs useful and allows all
frontend implementations to share golden files.

### AST example

```json
{
  "schemaVersion": 1,
  "kind": "FunctionDecl",
  "id": "item:4",
  "name": "AuditTransaction",
  "span": {"file": "audit.panini", "start": 120, "end": 418},
  "parameters": [
    {"name": "amount", "type": {"kind": "NamedType", "name": "float"}}
  ],
  "returnType": {"kind": "NamedType", "name": "AuditResult"},
  "body": {"kind": "InferenceBody", "provider": "OpenAI", "instructionId": "instruction:4"}
}
```

The CST owns punctuation and trivia. The AST owns normalized language meaning.
Resolved and typed representations should reference AST IDs rather than copying
whole syntax subtrees.

### Graph IR example

```json
{
  "schemaVersion": 1,
  "kind": "Graph",
  "name": "ComplianceGraph",
  "stateType": "type:WorkflowState",
  "nodes": [
    {"id": "node:init", "function": "fn:Init"},
    {"id": "node:process", "function": "fn:Process", "maxVisits": 3}
  ],
  "edges": [
    {"from": "$start", "to": "node:init"},
    {"from": "node:init", "to": "node:process"},
    {"from": "node:process", "guard": "fn:UnderLimit", "to": "node:process"},
    {"from": "node:process", "guard": "$otherwise", "to": "$end"}
  ]
}
```

This representation is the right level for reachability, strongly connected
components, bounded-cycle checks, concurrency-write analysis, visualization,
and backend capability checks.

## Parser and automaton debugging

The repository includes `tools/check-panini-grammar.sh`. It asks Bison to reject
both kinds of conflicts and emits:

- generated parser source and token header;
- a complete state/item-set report;
- a Graphviz automaton; and
- Bison's XML report for programmatic inspection.

Run:

```bash
tools/check-panini-grammar.sh
```

or choose an output directory:

```bash
tools/check-panini-grammar.sh docs/grammars/panini-core.y ./tmp/grammar-debug
dot -Tsvg ./tmp/grammar-debug/panini-core.dot > ./tmp/grammar-debug/panini-core.svg
```

When a conflict appears, rerun Bison with `-Wcounterexamples` to obtain example
token sequences. Treat `%expect` as a last resort: it records only a conflict
count and can allow one conflict to replace another unnoticed.

For runtime C++ parser traces, enable Bison's parse tracing in the implementation
build and route it through the compiler's structured logger. Do not enable full
traces by default because instruction literals may contain sensitive data.

## Error handling and editor behavior

A compiler parser and editor parser have different requirements:

- the compiler parser should reject invalid syntax predictably;
- the editor parser should keep a useful tree while the user is typing;
- both should use the same token names and AST concepts where possible; and
- only the compiler's result determines whether a build succeeds.

Use explicit recovery points at semicolons, closing braces, and top-level
declaration keywords. Diagnostics need stable codes and primary/secondary spans.
Never recover by silently inventing graph bounds, policy effects, or types.

## Concrete adoption plan

1. Convert `panini-core.y` into a generator-neutral grammar source or generate
   Lark and Bison grammars from one constrained grammar model.
2. Define token fixtures before implementing instruction interpolation.
3. Implement the Python lexer/parser and normalized AST JSON dumper.
4. Add valid, invalid, ambiguity, and recovery corpora.
5. Build the Bison C++/re2c frontend and compare AST JSON in CI.
6. Add tree-sitter only after compiler syntax stabilizes.
7. Implement name/type/graph passes once both parsers agree on syntax.

This approach gives Python the shortest language-development loop, C++ a stable
production path, and every stage a representation that can be inspected instead
of debugging only generated backend code.

## Primary tool documentation

- [Lark parsers and lexer choices](https://lark-parser.readthedocs.io/en/latest/parsers.html)
- [Lark API reference](https://lark-parser.readthedocs.io/en/latest/classes.html)
- [PLY documentation](https://ply.readthedocs.io/en/latest/ply.html)
- [GNU Bison C++ parsers](https://www.gnu.org/software/bison/manual/html_node/C_002b_002b-Parsers.html)
- [GNU Bison tracing](https://www.gnu.org/software/bison/manual/html_node/Enabling-Traces.html)
- [GNU Bison Graphviz output](https://www.gnu.org/software/bison/manual/html_node/Graphviz.html)
- [re2c manual](https://re2c.org/manual/manual_c.html)
- [tree-sitter documentation](https://tree-sitter.github.io/tree-sitter/)
