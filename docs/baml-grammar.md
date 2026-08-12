# BAML surface grammar (compiler2 extraction)

This document is a readable extraction of the grammar implemented by
`baml_compiler_lexer` and `baml_compiler_parser`. It is intended as a map of the
accepted surface language, not as a replacement for the parser source.

For a machine-checked LALR(1) core and an analysis of evolving this syntax into
a portable Java/C#-familiar orchestration language, see
[`panini-language-design.md`](panini-language-design.md) and
[`grammars/panini-core.y`](grammars/panini-core.y).

## Parser model and notation

BAML's compiler2 parser is **not generated from an LR, LALR, or Yacc grammar**.
It is a hand-written, error-recovering recursive-descent parser. Expressions are
parsed with Pratt binding powers, and the parser emits a lossless Rowan concrete
syntax tree (CST). Consequently, some decisions use token lookahead or parsing
context and cannot be captured exactly by context-free EBNF.

The grammar below uses:

- `x?` for zero or one `x`;
- `x*` for zero or more `x`;
- `x+` for one or more `x`;
- `(x | y)` for alternatives;
- quoted text for literal tokens;
- `IDENT`, `INT`, `BIGINT`, and `FLOAT` for lexer token classes; and
- comments beginning with `#` to document parser restrictions.

Whitespace, newlines, `//` comments, `///` documentation comments, and
`/* ... */` comments are generally trivia. Newlines remain significant in a
few disambiguation and recovery rules, including uncolonized fields and
parameters, expression statements, and LLM-function fields.

## Lexical elements

```ebnf
IDENT     ::= /\$[A-Za-z_][A-Za-z0-9_]*/
            | /[A-Za-z_][A-Za-z0-9_-]*(\$[A-Za-z_][A-Za-z0-9_-]*)*/ ;

INT       ::= decimal_int | hex_int | octal_int | binary_int ;
BIGINT    ::= INT "n" ;
FLOAT     ::= decimal_float | scientific_float ;

string    ::= '"' string_content '"' ;
bytes     ::= "b" string ;
raw       ::= hash_run '"' raw_content '"' matching_hash_run ;
backtick  ::= tick_run backtick_content matching_tick_run ;

literal   ::= string | bytes | raw | backtick
            | INT | BIGINT | FLOAT | "true" | "false" | "null" ;
```

Raw strings must close with the same number of `#` characters with which they
opened. Backtick strings similarly support multiple opening ticks and `${...}`
interpolation. The lexer deliberately emits delimiter and content tokens; the
parser assembles complete string CST nodes.

## Compilation unit

```ebnf
source_file ::= top_level_item* EOF ;

top_level_item ::= block_attribute* enum_decl
                 | block_attribute* class_decl
                 | block_attribute* interface_decl
                 | block_attribute* function_decl
                 | implementation
                 | client_decl
                 | test_decl
                 | testset_decl
                 | retry_policy_decl
                 | template_string_decl
                 | type_alias_decl
                 | top_level_binding
                 | generator_decl ;       # accepted only for migration diagnostics
```

## Attributes and generic declarations

```ebnf
attribute       ::= "@"  path attribute_arguments? ;
block_attribute ::= "@@" path attribute_arguments? ;

attribute_arguments ::= "(" (attribute_argument ("," attribute_argument)* ","?)? ")" ;
attribute_argument  ::= (IDENT "=")? expression ;

generic_params ::= "<" generic_param ("," generic_param)* ","? ">" ;
generic_param  ::= IDENT ("extends" type_expr ("&" type_expr)*)?
                   ("as" IDENT)? ;
```

## Data and interface declarations

```ebnf
enum_decl    ::= "enum" IDENT "{" enum_member* "}" ;
enum_member  ::= block_attribute
               | IDENT attribute* ("," | ";")? ;

class_decl   ::= "class" IDENT generic_params? "{" class_member* "}" ;
class_member ::= block_attribute
               | field ("," | ";")?
               | function_decl
               | implementation_body ;

interface_decl ::= "interface" IDENT generic_params? requires_clause?
                   "{" interface_member* "}" ;
requires_clause ::= "requires" type_expr ("," type_expr)* ;
interface_member ::= block_attribute
                   | field ("," | ";")?
                   | associated_type ("," | ";")?
                   | interface_method ;

field ::= IDENT ":"? type_expr attribute* ;

associated_type ::= "type" IDENT
                    ("extends" type_expr ("&" type_expr)*)?
                    ("=" type_expr)? ;

interface_method ::= block_attribute* "function" function_name generic_params?
                     parameter_list "->" type_expr throws_clause?
                     block_expr? ;

implementation ::= ("implement" | "implements") generic_params?
                   type_expr "for" type_expr implementation_body ;

implementation_body ::= ("implement" | "implements") type_expr? "{"
                        implementation_member* "}" ;

implementation_member ::= block_attribute* function_decl
                        | associated_type ("," | ";")?
                        | field_link ("," | ";")?
                        | field ("," | ";")? ;

field_link ::= IDENT "as" IDENT ;
```

The parser accepts both `implement` and `implements` in implementation
positions to produce useful migration and formatting behavior. Semantic stages
decide whether an associated type needs a binding and whether a field-like
member is a valid interface field link.

## Functions and LLM functions

```ebnf
function_decl ::= "function" function_name generic_params? parameter_list
                  "->" type_expr throws_clause? function_body ;
function_name ::= IDENT
                | "implement" | "implements" | "extends" | "requires" | "interface" ;

parameter_list ::= "(" (parameter ("," parameter)* ","?)? ")" ;
parameter      ::= "self"
                 | (IDENT | "client") ":"? type_expr ("=" expression)? ;
throws_clause  ::= "throws" type_expr ;

function_body ::= block_expr | llm_body ;

llm_body  ::= "{" llm_field* "}" ;
llm_field ::= "client" ":"? expression
            | "tools"  ":"? expression
            | "prompt" ":"? (string | raw | backtick | expression)
            | type_builder_block ;
```

An LLM body is recognized contextually: a top-level `client` or `prompt` field
selects the LLM-body parser, while ordinary control-flow statements select the
expression-body parser. An LLM body must contain exactly one `client` and one
`prompt`; `tools` and `type_builder` are optional. Its fields are newline
separated rather than comma or semicolon separated.

## Named clients, tests, and configuration declarations

```ebnf
client_decl ::= "client" IDENT "=" expression ";"?
              | "client" "<" IDENT ">" IDENT config_block ; # legacy form

retry_policy_decl ::= "retry_policy" IDENT config_block ;

template_string_decl ::= "template_string" IDENT parameter_list?
                         (string | raw | backtick) ;

type_alias_decl ::= "type" IDENT "=" type_expr attribute* ";"? ;

test_decl ::= "test" expression ("with" expression)? block_expr
            | "test" IDENT config_block ;                    # legacy form

testset_decl ::= "testset" expression ("with" expression)? testset_body ;
testset_body ::= "{" (statement | test_decl | testset_decl)* "}" ;

type_builder_block ::= "type_builder" ":"? "{"
                       (class_decl | enum_decl | type_alias_decl
                       | "dynamic" (class_decl | enum_decl))* "}" ;

config_block ::= "{" config_item* "}" ;
config_item  ::= IDENT ":"? (config_block | config_value) attribute* ;
config_value ::= expression | IDENT | config_array ;
config_array ::= "[" config_value* "]" ;

generator_decl ::= "generator" IDENT? balanced_brace_tokens ;
```

`generator` is still recognized so the compiler can emit a targeted diagnostic;
generators are configured in `baml.toml` rather than BAML source.

## Types

```ebnf
type_expr ::= type_primary type_suffix* ;
type_suffix ::= "[]" | "?" | "|" type_primary | attribute ;

type_primary ::= path type_arguments?
               | string | raw | BIGINT | INT | negative_integer
               | "true" | "false"
               | parenthesized_or_function_type
               | associated_projection ;

type_arguments ::= "<" type_argument ("," type_argument)* ","? ">" ;
type_argument  ::= type_expr | IDENT "=" type_expr ;

parenthesized_or_function_type ::= "(" function_type_parameters? ")"
                                   ("->" type_expr throws_clause?)? ;
function_type_parameters ::= function_type_parameter
                             ("," function_type_parameter)* ","? ;
function_type_parameter ::= type_expr | IDENT "?"? ":" type_expr ;

associated_projection ::= "(" type_expr "as" type_expr ")" "." IDENT ;
path ::= IDENT ("." IDENT)* ;
```

Important parser restrictions:

- float literal **types** are rejected, although floats are valid values;
- a function value type cannot declare its own generic parameter list;
- optional and array suffixes may be chained;
- union parsing is suppressed in pattern-atom contexts so `|` can form a union
  pattern; and
- `>>` is split contextually when closing nested generic argument lists.

## Statements and control flow

```ebnf
block_expr ::= "{" statement* expression? "}" ;

statement ::= binding ";"?
            | "return" expression? ";"?
            | "throw" expression ";"?
            | "defer" expression ";"?
            | while_expr
            | for_expr
            | "break" ";"?
            | "continue" ";"?
            | expression ";"? ;

binding ::= ("let" | "const") pattern (":" type_expr)? "=" expression ;

while_expr ::= "while" expression block_expr
             | "while" let_pattern "=" expression block_expr ;

for_expr ::= "for" ("(" c_for_header ")" | for_in_header) block_expr ;
c_for_header ::= statement? ";" expression? ";" expression? ;
for_in_header ::= ("let" | "const")? pattern "in" expression ;

if_expr ::= "if" expression block_expr ("else" (if_expr | block_expr))? ;
if_let_expr ::= "if" let_pattern "=" expression block_expr
                ("else" (if_expr | block_expr))? ;

match_expr ::= "match" expression "{" match_arm* "}" ;
match_arm  ::= pattern ("if" expression)? "=>" expression ("," | ";")? ;
```

## Patterns

```ebnf
pattern ::= pattern_atom ("|" pattern_atom)* ;

pattern_atom ::= "_"
               | literal_pattern
               | type_expr
               | path
               | path "(" pattern_list? ")"
               | path "{" field_patterns? "}"
               | "[" array_patterns? "]"
               | "(" pattern ")" ;

let_pattern ::= ("let" | "const")? pattern (":" type_expr)? ;
pattern_list ::= pattern ("," pattern)* ","? ;
field_patterns ::= field_pattern ("," field_pattern)* ","? ;
field_pattern ::= IDENT (":" pattern)? | "..." IDENT? ;
array_patterns ::= array_pattern ("," array_pattern)* ","? ;
array_pattern ::= pattern | "..." pattern? ;
```

Pattern/type ambiguity is resolved with parser context and later semantic
analysis, so this section is intentionally more permissive than a declarative
parser-generator grammar would be.

## Expressions

```ebnf
expression ::= assignment_expr ;

primary_expr ::= literal
               | path
               | "env" "." IDENT
               | "(" expression ")"
               | array_literal
               | map_literal
               | block_expr
               | if_expr | if_let_expr | match_expr
               | lambda_expr
               | "throw" expression
               | "return" expression?
               | "break" expression?
               | "continue"
               | spawn_expr ;

lambda_expr ::= "(" lambda_parameters? ")" ("->" | "=>")
                (expression | block_expr) ;
lambda_parameters ::= lambda_parameter ("," lambda_parameter)* ","? ;
lambda_parameter ::= IDENT (":" type_expr)? ;

spawn_expr ::= "spawn" expression? ("with" expression ("," expression)*)?
               block_expr ;

array_literal ::= "[" (expression | spread)
                  ("," (expression | spread))* ","? "]" ;
map_literal ::= "{" map_entry ("," map_entry)* ","? "}" ;
map_entry ::= expression ":" expression | spread ;
object_literal ::= path "{" object_field ("," object_field)* ","? "}" ;
object_field ::= path ":" expression | IDENT | spread ;
spread ::= "..." expression ;

postfix_expr ::= primary_expr
                 (generic_arguments
                 | call_arguments
                 | "[" expression "]"
                 | "?." (IDENT | call_arguments | "[" expression "]")
                 | ("." | "$") IDENT
                 | object_literal
                 | tagged_backtick)* ;

prefix_expr ::= ("-" | "!" | "~" | "++" | "--" | "await") prefix_expr
              | postfix_expr ;

catch_expr ::= expression ("catch" catch_clause | "catch_all" catch_clause)+ ;
catch_clause ::= pattern? ("=>" expression | block_expr) ;
```

The following table is the implemented Pratt precedence from weakest to
strongest. Assignment is right-associative; the other listed binary levels,
including null coalescing, are left-associative.

| Precedence | Operators |
| --- | --- |
| 1 | `=`, `+=`, `-=`, `*=`, `/=`, `%=`, `&=`, `\|=`, `^=`, `<<=`, `>>=` |
| 2 | `??` |
| 3 | `\|\|` |
| 4 | `&&` |
| 5 | `\|` |
| 6 | `^` |
| 7 | `&` |
| 8 | `==`, `!=` |
| 9 | `<`, `>`, `<=`, `>=`, `instanceof`, `is` |
| 10 | `<<`, `>>` |
| 11 | `+`, `-` |
| 12 | `*`, `/`, `%` |
| 13 | prefix operators |
| 14 | calls, indexing, member access, optional chaining, generic application |

## Why this is not an exact standalone parser specification

The implementation intentionally accepts malformed or obsolete forms to build
a useful CST and issue focused diagnostics. It also uses contextual keywords
such as `type`, `as`, `with`, `const`, `prompt`, and `tools`; newline-sensitive
lookahead; generic-versus-comparison speculation; constructor-versus-block
lookahead; and semantic lowering after parsing. A generated parser based only
on this EBNF would therefore accept or reject a few edge cases differently.

For exact behavior, the sources of truth are:

1. `baml_compiler_lexer/src/tokens.rs` for tokenization;
2. `baml_compiler_parser/src/parser.rs` for grammar, recovery, and precedence;
3. `baml_compiler_syntax/src/syntax_kind.rs` for CST node/token kinds; and
4. parser, formatter, and diagnostic snapshots under `baml_tests`.
