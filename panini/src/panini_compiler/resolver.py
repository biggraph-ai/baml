"""Resolve and type-check the sequential graph and decision profile.

Local static method contracts remain opaque. Stateless agent decision methods
are typed and lowered with explicit adapter and output-binding contracts. Guards
never invoke methods or models. Unsupported semantics fail explicitly.
"""

from __future__ import annotations

from copy import deepcopy
import json
import math
import re
from threading import TIMEOUT_MAX

from .diagnostics import Diagnostic
from .ir import CheckResult, PROFILE
from .decisions import valid_adapter


PRIMITIVES = {"bool", "int", "float", "string", "void"}


def nodes(item: dict, kind: str | None = None) -> list[dict]:
    return [
        c
        for c in item.get("children", [])
        if c["kind"] != "Token" and (kind is None or c["kind"] == kind)
    ]


def tokens(item: dict, token_type: str | None = None) -> list[dict]:
    return [
        c
        for c in item.get("children", [])
        if c["kind"] == "Token" and (token_type is None or c["tokenType"] == token_type)
    ]


def text(item: dict) -> str:
    if item["kind"] == "Token":
        return item["text"]
    return "".join(text(c) for c in item.get("children", []))


def decision_enum(name: str) -> str | None:
    match = re.fullmatch(r"Decision<([^<>]+)>", name)
    return match[1] if match else None


def non_abstaining(expression: dict, truth: bool) -> set[str]:
    kind = expression["kind"]
    children = nodes(expression)
    if kind == "Atom":
        return non_abstaining(children[0], truth)
    if kind == "Unary" and expression["operator"] == "!":
        return non_abstaining(children[0], not truth)
    if (
        kind == "Member"
        and tokens(expression, "NAME")[0]["text"] == "abstained"
        and not truth
    ):
        return {text(children[0])}
    if kind == "Binary":
        op = expression["operator"]
        if op in {"==", "!="}:
            for member, literal in (children, children[::-1]):
                if member["kind"] != "Member":
                    continue
                name = tokens(member, "NAME")[0]["text"]
                if name == "abstained" and literal["kind"] in {"True", "False"}:
                    value = literal["kind"] == "True"
                    if (value if (truth == (op == "==")) else not value) is False:
                        return {text(nodes(member)[0])}
                if (
                    name == "choice"
                    and literal["kind"] == "Null"
                    and truth == (op == "!=")
                ):
                    return {text(nodes(member)[0])}
        if op in {"&&", "||"}:
            left = non_abstaining(children[0], truth)
            right = non_abstaining(children[1], truth)
            return left | right if (op == "&&") == truth else left & right
    return set()


class Resolver:
    def __init__(
        self, adapters: dict | None = None, effects: dict | None = None
    ) -> None:
        self.result = CheckResult()
        self.declarations: dict[str, dict] = {}
        self.fields: dict[str, dict[str, tuple[str, dict]]] = {}
        self.methods: dict[str, tuple[str, dict]] = {}
        self.enums: dict[str, set[str]] = {}
        self.types = set(PRIMITIVES)
        self.used = False
        self.adapters = deepcopy(adapters or {})
        self.effects = deepcopy(effects or {})
        self.decisions: dict[str, dict] = {}
        self.safe_choices: set[str] = set()

    def error(self, code: str, message: str, item: dict) -> None:
        self.result.diagnostics.append(Diagnostic(code, message, item["span"]))

    def unsupported(self, item: dict, description: str) -> None:
        self.error("E2000", f"Unsupported in {PROFILE}: {description}", item)

    def resolve(self, ast: dict) -> CheckResult:
        # A resolver is single-use, preventing accidental symbol leakage between files.
        if self.used:
            raise ValueError("Create a new Resolver for each program")
        self.used = True
        if ast.get("schemaVersion") != 2 or ast.get("kind") != "Start":
            raise ValueError("Resolver requires a semantic AST v2 program")
        ast = deepcopy(ast)
        declarations = []
        for child in nodes(ast):
            declarations.extend(
                nodes(child) if child["kind"] == "Declaration" else [child]
            )
        for declaration in declarations:
            kind = declaration["kind"]
            if kind == "PackageDecl":
                continue
            if kind not in {
                "ClassDecl",
                "AgentDecl",
                "EnumDecl",
                "GraphDecl",
                "PolicyDecl",
            }:
                self.unsupported(declaration, kind)
                continue
            name = tokens(declaration, "NAME")[0]["text"]
            if name in self.declarations or name in PRIMITIVES or name == "Decision":
                self.error("E2002", f"Duplicate declaration '{name}'", declaration)
                continue
            self.declarations[name] = declaration
            self.result.symbols.append(
                {
                    "id": declaration["id"],
                    "name": name,
                    "kind": kind,
                    "span": declaration["span"],
                }
            )
            if kind in {"ClassDecl", "EnumDecl"}:
                self.types.add(name)
        for name, declaration in self.declarations.items():
            if declaration["kind"] == "EnumDecl":
                names = tokens(declaration, "NAME")[1:]
                self.enums[name] = set()
                for member in names:
                    if member["text"] in self.enums[name]:
                        self.error(
                            "E2002", f"Duplicate enum member '{member['text']}'", member
                        )
                    self.enums[name].add(member["text"])
                    self.result.symbols.append(
                        {
                            "id": member["id"],
                            "name": f"{name}.{member['text']}",
                            "kind": "EnumMember",
                            "owner": declaration["id"],
                            "type": name,
                            "span": member["span"],
                        }
                    )
            if declaration["kind"] in {"ClassDecl", "AgentDecl"}:
                self.collect_class(name, declaration)
        self.collect_decisions()
        self.collect_effects()
        for name, declaration in self.declarations.items():
            if declaration["kind"] == "GraphDecl":
                graph = self.graph(name, declaration)
                self.result.graphs.append(graph)
        self.attach_policies()
        for symbol in self.result.symbols:
            if "type" in symbol:
                symbol["typeSymbol"] = (
                    f"builtin:{symbol['type']}"
                    if symbol["type"] in PRIMITIVES
                    else self.type_id(symbol["type"])
                )
            for parameter in symbol.get("parameters", []):
                parameter["typeSymbol"] = (
                    f"builtin:{parameter['type']}"
                    if parameter["type"] in PRIMITIVES
                    else self.type_id(parameter["type"])
                )
        self.result.symbols.sort(key=lambda s: (s["span"]["start"], s["id"]))
        self.result.diagnostics.sort(
            key=lambda d: (d.span["file"], d.span["start"], d.code, d.message)
        )
        # Never emit an apparently valid artifact on failed resolution.
        if self.result.diagnostics:
            self.result.graphs.clear()
        return self.result

    def type_name(self, item: dict) -> str:
        name = text(item)
        enum = decision_enum(name)
        if enum is not None:
            if self.declarations.get(enum, {}).get("kind") == "EnumDecl":
                return name
            self.error("E2501", f"Decision requires a local enum, got '{enum}'", item)
            return name
        if name not in self.types:
            if any(c in name for c in "<>?[]|") or "." in name:
                self.unsupported(item, f"type '{name}'")
            else:
                self.error("E2001", f"Unknown type '{name}'", item)
        return name

    def collect_class(self, name: str, declaration: dict) -> None:
        self.fields[name] = {}
        if nodes(declaration, "GenericParams") or nodes(declaration, "ClassBases"):
            self.unsupported(declaration, "generic classes or inheritance")
        class_modifiers = [text(m) for m in nodes(declaration, "Modifier")]
        if len(class_modifiers) != len(set(class_modifiers)) or set(class_modifiers) - {
            "public",
            "internal",
        }:
            self.unsupported(declaration, "class modifiers beyond public/internal")
        body = nodes(declaration, "ClassBody")[0]
        seen = set()
        if declaration["kind"] == "AgentDecl" and not nodes(body):
            self.unsupported(declaration, "an agent must declare decision methods")
        for entry in nodes(body):
            members = nodes(entry) if entry["kind"] == "ClassMember" else [entry]
            for member in members:
                if member["kind"] != "MemberDecl":
                    self.unsupported(member, member["kind"])
                    continue
                member_name = tokens(member, "NAME")[0]["text"]
                if member_name in seen:
                    self.error(
                        "E2002",
                        f"Duplicate member or unsupported overload '{name}.{member_name}'",
                        member,
                    )
                    continue
                seen.add(member_name)
                parts = [c for c in nodes(member) if c["kind"] != "Modifier"]
                declared_type = self.type_name(parts[0])
                self.result.symbols.append(
                    {
                        "id": member["id"],
                        "name": f"{name}.{member_name}",
                        "kind": "Field"
                        if parts[1]["kind"] == "FieldTail"
                        else "Method",
                        "type": declared_type,
                        "owner": declaration["id"],
                        "span": member["span"],
                    }
                )
                modifiers = [text(m) for m in nodes(member, "Modifier")]
                if len(modifiers) != len(set(modifiers)):
                    self.error("E2002", "Duplicate member modifier", member)
                tail = parts[1]
                if tail["kind"] == "FieldTail":
                    if declaration["kind"] == "AgentDecl":
                        self.unsupported(member, "stateful decision agents")
                    if set(modifiers) - {"public", "internal"}:
                        self.unsupported(
                            member, "field modifiers beyond public/internal"
                        )
                    if declared_type == "void":
                        self.error("E2003", "Field cannot have type void", member)
                    if nodes(tail):
                        self.unsupported(tail, "field initializers")
                    self.fields[name][member_name] = (declared_type, member)
                else:
                    symbol = self.result.symbols[-1]
                    symbol["modifiers"] = modifiers
                    symbol["parameters"] = [
                        {
                            "id": parameter["id"],
                            "name": tokens(parameter, "NAME")[0]["text"],
                            "type": text(nodes(parameter)[0]),
                            "span": parameter["span"],
                        }
                        for parameter in nodes(
                            nodes(tail, "Parameters")[0], "Parameter"
                        )
                    ]
                    symbol["bodyStatus"] = "opaque-unverified"
                    symbol["throws"] = [
                        text(nodes(t)[0]) for t in nodes(tail, "ThrowsClause")
                    ]
                    parameter_names = [p["name"] for p in symbol["parameters"]]
                    if len(parameter_names) != len(set(parameter_names)):
                        self.error("E2002", "Duplicate method parameter", member)
                    self.methods[f"{name}.{member_name}"] = (declared_type, member)
                    for parameter in nodes(nodes(tail, "Parameters")[0], "Parameter"):
                        if self.type_name(nodes(parameter)[0]) == "void":
                            self.error(
                                "E2003", "Parameter cannot have type void", parameter
                            )
                    for throws in nodes(tail, "ThrowsClause"):
                        if self.type_name(nodes(throws)[0]) == "void":
                            self.error("E2003", "throws cannot have type void", throws)
                    if nodes(tail, "GenericParams"):
                        self.unsupported(tail, "generic methods")

    def collect_effects(self) -> None:
        from .effects import validate_effect

        for name, descriptor in self.effects.items():
            method = self.methods.get(name)
            if method is None:
                location = next(
                    iter(self.declarations.values()),
                    {"span": {"file": "<manifest>", "start": 0, "end": 0}},
                )
                self.error(
                    "E2401",
                    f"Effect manifest references unknown callable '{name}'",
                    location,
                )
                continue
            _, member = method
            try:
                effect = validate_effect(descriptor)
                if effect["kind"] in {"mutation", "recovery"}:
                    if (
                        self.declarations.get(effect["receiptType"], {}).get("kind")
                        != "ClassDecl"
                    ):
                        raise ValueError("receiptType must name a local receipt class")
                if name in self.decisions:
                    raise ValueError(
                        "Decision adapters cannot be declared external mutation capabilities"
                    )
                symbol = next(s for s in self.result.symbols if s["id"] == member["id"])
                symbol["effect"] = effect
            except ValueError as error:
                self.error("E2401", str(error), member)

    def attach_policies(self) -> None:
        for name, declaration in self.declarations.items():
            if declaration["kind"] != "PolicyDecl":
                continue
            names = tokens(declaration, "NAME")
            if len(names) != 2:
                self.error(
                    "E2601",
                    "A policy requires an explicit 'for GraphName' binding",
                    declaration,
                )
                continue
            graph = next(
                (g for g in self.result.graphs if g["name"] == names[1]["text"]), None
            )
            if graph is None:
                self.error("E2001", "Unknown policy graph", declaration)
                continue
            self.state_parameter_id = graph["stateParameterSymbol"]
            state = next(
                s["name"] for s in self.result.symbols if s["id"] == graph["stateType"]
            )
            policy = {"name": name, "rules": [], "span": declaration["span"]}
            seen = set()
            for member in nodes(declaration):
                if member["kind"] == "PolicyInvariant":
                    expression = nodes(member)[0]
                    self.safe_choices = set()
                    if (
                        self.expression_type(expression, state, graph["stateParameter"])
                        != "bool"
                    ):
                        self.error(
                            "E2601", "Policy invariant must be Boolean", expression
                        )
                    graph["invariants"].append(expression)
                elif member["kind"] == "PolicyRule":
                    rule_name = tokens(member, "NAME")[0]["text"]
                    if rule_name in seen:
                        self.error("E2002", "Duplicate policy rule", member)
                    seen.add(rule_name)
                    expression = nodes(member)[0]
                    self.safe_choices = set()
                    if (
                        self.expression_type(expression, state, graph["stateParameter"])
                        != "bool"
                    ):
                        self.error(
                            "E2601", "Policy predicate must be Boolean", expression
                        )
                    effect = nodes(member)[1] if len(nodes(member)) > 1 else None
                    # keep_all_tokens preserves the policy effect rule even for 'end'.
                    effect_text = text(effect) if effect else ""
                    if (
                        any(t["text"] == "end" for t in tokens(member))
                        and effect is None
                    ):
                        action = {"kind": "end"}
                    elif (
                        effect
                        and effect["kind"] == "PolicyEffect"
                        and effect_text.startswith("throw")
                    ):
                        value = nodes(effect)[0]
                        if (
                            self.expression_type(value, state, graph["stateParameter"])
                            != "string"
                        ):
                            self.error(
                                "E2601",
                                "Policy throw requires a string expression",
                                value,
                            )
                        action = {"kind": "throw", "expression": value}
                    else:
                        self.error(
                            "E2601", "Policy actions support end and throw only", member
                        )
                        continue
                    policy["rules"].append(
                        {"name": rule_name, "predicate": expression, "action": action}
                    )
            graph["policies"].append(policy)

    def string_value(self, spelling: str, location: dict) -> str:
        try:
            value = json.loads(spelling)
            value.encode("utf-8")
            return value
        except (ValueError, UnicodeError):
            self.error(
                "E2003", "Invalid JSON string escape or Unicode scalar", location
            )
            return ""

    def type_id(self, name: str) -> str | None:
        enum = decision_enum(name)
        if enum is not None:
            return "builtin:Decision:" + self.declarations.get(enum, {}).get("id", enum)
        return self.declarations.get(name, {}).get("id")

    def collect_decisions(self) -> None:
        for name, (returned, member) in self.methods.items():
            owner = self.declarations[name.split(".")[0]]
            tail = nodes(member, "MethodTail")[0]
            bodies = nodes(tail, "DecisionBody")
            if not bodies:
                if owner["kind"] == "AgentDecl":
                    self.unsupported(member, "agents support only decision bodies")
                continue
            body = bodies[0]
            enum = decision_enum(returned)
            if owner["kind"] != "AgentDecl" or enum not in self.enums:
                self.error(
                    "E2501",
                    "Decision bodies require a stateless agent method returning Decision<Enum>",
                    member,
                )
                continue
            modifiers = [text(m) for m in nodes(member, "Modifier")]
            parameters = nodes(nodes(tail, "Parameters")[0], "Parameter")
            if (
                modifiers != ["public"]
                or len(parameters) != 1
                or len(nodes(parameters[0])) != 1
            ):
                self.error(
                    "E2501",
                    "Decision method requires public visibility and one state parameter",
                    member,
                )
                continue
            state = text(nodes(parameters[0])[0])
            if self.declarations.get(state, {}).get("kind") != "ClassDecl":
                self.error(
                    "E2501", "Decision input must be a local state class", parameters[0]
                )
                continue
            expressions = nodes(body)
            model_expr, input_expr = expressions[:2]
            if model_expr["kind"] == "Name":
                model = text(model_expr)
            elif model_expr["kind"] == "String":
                model = self.string_value(text(model_expr), model_expr)
            else:
                self.error(
                    "E2504",
                    "decision_model requires a configured adapter name",
                    model_expr,
                )
                continue
            adapter = self.adapters.get(model)
            if not valid_adapter(adapter):
                self.error(
                    "E2504",
                    f"Adapter '{model}' requires a probabilities output manifest and pinned version",
                    model_expr,
                )
                continue
            self.state_parameter_id = parameters[0]["id"]
            self.safe_choices = set()
            variable = tokens(parameters[0], "NAME")[0]["text"]
            if self.expression_type(input_expr, state, variable) != "string":
                self.error("E2501", "decide input must have type string", input_expr)
            options = []
            for option in nodes(body, "DecisionOption"):
                options.append(
                    {
                        "name": tokens(option, "NAME")[0]["text"],
                        "description": self.string_value(
                            tokens(option, "STRING")[0]["text"], option
                        ),
                        "span": option["span"],
                    }
                )
            labels = [o["name"] for o in options]
            if len(labels) != len(set(labels)) or set(labels) != self.enums[enum]:
                self.error(
                    "E2502",
                    f"Decision options must cover enum '{enum}' exactly once",
                    body,
                )
            threshold = 0.0
            gates = nodes(body, "DecisionGate")
            if gates:
                expression = nodes(gates[0])[0]
                if expression["kind"] not in {"Integer", "Float"}:
                    self.error(
                        "E2506",
                        "abstain_below requires a finite constant in [0,1]",
                        expression,
                    )
                else:
                    threshold = float(text(expression))
                    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
                        self.error(
                            "E2506",
                            "abstain_below requires a finite constant in [0,1]",
                            expression,
                        )
            spec = {
                "model": model,
                "version": adapter["version"],
                "output": "probabilities",
                "enum": enum,
                "input": input_expr,
                "parameter": variable,
                "options": options,
                "threshold": threshold,
            }
            self.decisions[name] = spec
            symbol = next(s for s in self.result.symbols if s["id"] == member["id"])
            symbol.update(
                decision=spec, effects=["inference"], bodyStatus="typed-decision"
            )

    def method(
        self, reference: dict, state: str, output: str | None = None
    ) -> str | None:
        name = text(reference)
        if name not in self.methods:
            self.error("E2001", f"Unknown callable '{name}'", reference)
            return None
        returned, member = self.methods[name]
        modifiers = {text(m) for m in nodes(member, "Modifier")}
        decision = self.decisions.get(name)
        if decision is not None:
            valid_modifiers = modifiers == {"public"}
        else:
            valid_modifiers = modifiers == {"public", "static"}
        if not valid_modifiers:
            self.unsupported(
                reference,
                f"'{name}' must be a public static method without additional modifiers",
            )
        tail = nodes(member, "MethodTail")[0]
        parameters = nodes(nodes(tail, "Parameters")[0], "Parameter")
        if (
            returned != (output or state)
            or len(parameters) != 1
            or text(nodes(parameters[0])[0]) != state
        ):
            self.error(
                "E2003",
                f"Callable '{name}' must have signature ({state}) -> {output or state}",
                reference,
            )
        for parameter in parameters:
            if len(nodes(parameter)) != 1:
                self.unsupported(parameter, "default parameters")
        return member["id"]

    def expression_type(self, item: dict, state: str, variable: str) -> str:
        inferred = self._expression_type(item, state, variable)
        item["resolvedType"] = inferred
        return inferred

    def _expression_type(self, item: dict, state: str, variable: str) -> str:
        kind = item["kind"]
        children = nodes(item)
        if kind in {"Integer", "Float", "String", "True", "False"}:
            if kind == "String":
                self.string_value(text(item), item)
            elif kind in {"Integer", "Float"}:
                try:
                    value = int(text(item)) if kind == "Integer" else float(text(item))
                    valid = (
                        (0 <= value < 2**63)
                        if kind == "Integer"
                        else math.isfinite(value)
                    )
                except ValueError:
                    valid = False
                if not valid:
                    self.error(
                        "E2003",
                        "Numeric literal exceeds the finite float/signed-64-bit integer profile",
                        item,
                    )
            return {
                "Integer": "int",
                "Float": "float",
                "String": "string",
                "True": "bool",
                "False": "bool",
            }[kind]
        if kind == "Null":
            return "null"
        if kind == "Atom" and len(children) == 1:
            return self.expression_type(children[0], state, variable)
        if kind == "Name":
            name = text(item)
            if name == variable:
                item["resolvedSymbol"] = self.state_parameter_id
                return state
            self.error("E2001", f"Unknown value '{name}'", item)
            return "<error>"
        if kind == "Member":
            base = children[0]
            field = tokens(item, "NAME")[0]["text"]
            if (
                base["kind"] == "Name"
                and text(base) != variable
                and text(base) in self.enums
            ):
                enum = text(base)
                if field in self.enums[enum]:
                    item["resolvedSymbol"] = next(
                        s["id"]
                        for s in self.result.symbols
                        if s["name"] == f"{enum}.{field}"
                    )
                    return enum
                self.error("E2001", f"Unknown enum member '{enum}.{field}'", item)
                return "<error>"
            base_type = self.expression_type(base, state, variable)
            enum = decision_enum(base_type)
            if enum is not None:
                if field == "abstained":
                    return "bool"
                if field == "choice":
                    if text(base) not in self.safe_choices:
                        self.error(
                            "E2505",
                            "Decision choice requires a preceding non-abstention or non-null guard",
                            item,
                        )
                    return enum
                self.error("E2001", f"Unknown Decision field '{field}'", item)
                return "<error>"
            if field in self.fields.get(base_type, {}):
                field_type, declaration = self.fields[base_type][field]
                item["resolvedSymbol"] = declaration["id"]
                return field_type
            if base_type != "<error>":
                self.error("E2001", f"Unknown field '{field}' on '{base_type}'", item)
            return "<error>"
        if kind == "Binary":
            op = item["operator"]
            # Short-circuit flow facts only apply in the RHS of &&. OR cannot
            # establish a global fact unless both arms establish it.
            saved_null = self.safe_choices.copy()
            if op in {"==", "!="}:
                for a, b in ((children[0], children[1]), (children[1], children[0])):
                    if (
                        b["kind"] == "Null"
                        and a["kind"] == "Member"
                        and tokens(a, "NAME")[0]["text"] == "choice"
                    ):
                        self.safe_choices.add(text(nodes(a)[0]))
            left = self.expression_type(children[0], state, variable)
            if op == "&&":
                self.safe_choices |= non_abstaining(children[0], True)
            elif op == "||":
                self.safe_choices |= non_abstaining(children[0], False)
            right = self.expression_type(children[1], state, variable)
            self.safe_choices = saved_null
            if "<error>" in (left, right):
                return "<error>"
            if (
                op in {"==", "!="}
                and "null" in {left, right}
                and any(
                    c["kind"] == "Member"
                    and decision_enum(nodes(c)[0].get("resolvedType", "")) is not None
                    and tokens(c, "NAME")[0]["text"] == "choice"
                    for c in children
                )
            ):
                return "bool"
            if op in {"&&", "||"} and left == right == "bool":
                return "bool"
            if (
                op in {"==", "!="}
                and left == right
                and left in ({"bool", "int", "float", "string"} | self.enums.keys())
            ):
                return "bool"
            if (
                op in {"<", "<=", ">", ">="}
                and left == right
                and left in {"int", "float"}
            ):
                return "bool"
            # Reject arithmetic until overflow/divide-by-zero and guard totality are specified.
            if op in {"=", "+=", "-=", "*=", "/=", "%="}:
                self.unsupported(item, "assignment in graph expressions")
            elif op not in {"&&", "||", "==", "!=", "<", "<=", ">", ">="}:
                self.unsupported(item, f"guard operator '{op}'")
            else:
                self.error(
                    "E2003", f"Operator '{op}' cannot compare {left} and {right}", item
                )
            return "<error>"
        if kind == "Unary":
            op = item["operator"]
            if (
                op == "-"
                and children[0]["kind"] == "Integer"
                and text(children[0]) == str(2**63)
            ):
                children[0]["resolvedType"] = "int"
                return "int"
            operand = self.expression_type(children[0], state, variable)
            if op == "!" and operand == "bool":
                return "bool"
            if (
                op == "-"
                and operand in {"int", "float"}
                and children[0]["kind"] in {"Integer", "Float"}
            ):
                return operand
            self.unsupported(item, f"guard unary operator '{op}' on {operand}")
            return "<error>"
        self.unsupported(
            item, f"graph expression {kind}; guards must be pure and total"
        )
        return "<error>"

    def graph(self, name: str, declaration: dict) -> dict:
        header_names = tokens(declaration, "NAME")
        variable = header_names[1]["text"]
        self.state_parameter_id = header_names[1]["id"]
        children = nodes(declaration)
        type_nodes = [
            c
            for c in children
            if c["kind"]
            not in {
                "GraphNode",
                "GraphStart",
                "GraphEdge",
                "GraphInvariant",
                "GraphState",
            }
        ]
        state = self.type_name(type_nodes[0])
        if self.declarations.get(state, {}).get("kind") != "ClassDecl":
            self.error("E2003", "Graph state must be a local class", type_nodes[0])
        if len(type_nodes) > 1 and self.type_name(type_nodes[1]) != state:
            self.error(
                "E2003", "Graph return type must match state type", type_nodes[1]
            )
        self.result.symbols.append(
            {
                "id": self.state_parameter_id,
                "name": f"{name}.{variable}",
                "kind": "StateParameter",
                "type": state,
                "span": header_names[1]["span"],
            }
        )
        graph = {
            "id": declaration["id"],
            "name": name,
            "span": declaration["span"],
            "stateType": self.declarations.get(state, {}).get("id"),
            "stateParameter": variable,
            "stateParameterSymbol": self.state_parameter_id,
            "nodes": [],
            "starts": [],
            "transitions": [],
            "invariants": [],
            "policies": [],
        }
        seen = set()
        for member in children:
            kind = member["kind"]
            if kind == "GraphNode":
                node_name = tokens(member, "NAME")[0]["text"]
                if node_name in seen:
                    self.error("E2002", f"Duplicate graph node '{node_name}'", member)
                seen.add(node_name)
                reference = nodes(member, "QualifiedName")[0]
                write_contracts = nodes(member, "WritesContract")
                binding = None
                output = None
                if write_contracts:
                    path = text(nodes(write_contracts[0])[0]).split(".")
                    if (
                        len(path) != 2
                        or path[0] != variable
                        or path[1] not in self.fields.get(state, {})
                    ):
                        self.error(
                            "E2503",
                            "writes must target a direct writable state field",
                            write_contracts[0],
                        )
                    else:
                        output, field = self.fields[state][path[1]]
                        binding = {
                            "field": path[1],
                            "symbol": field["id"],
                            "type": output,
                        }
                node = {
                    "id": member["id"],
                    "name": node_name,
                    "span": member["span"],
                    "callable": self.method(reference, state, output),
                    "contracts": [],
                }
                if binding is not None:
                    node["outputBinding"] = binding
                if (
                    decision_enum(output or "") is not None
                    and text(reference) not in self.decisions
                ):
                    self.error(
                        "E2503",
                        "Decision output requires a declared decision method",
                        reference,
                    )
                contracts = set()
                for contract in nodes(member):
                    contract_kind = contract["kind"]
                    if contract_kind == "QualifiedName":
                        continue
                    if contract_kind in contracts:
                        self.error("E2002", f"Duplicate {contract_kind}", contract)
                    contracts.add(contract_kind)
                    lowered = {"kind": contract_kind, "span": contract["span"]}
                    if contract_kind == "MaxVisitsContract":
                        try:
                            value = int(tokens(contract, "INT")[0]["text"])
                        except ValueError:
                            value = 0
                        if value < 1:
                            self.error(
                                "E2004",
                                "max_visits must be a positive integer",
                                contract,
                            )
                        lowered["value"] = value
                    elif contract_kind == "CompensateContract":
                        lowered["callable"] = self.method(nodes(contract)[0], state)
                    elif contract_kind == "WritesContract":
                        lowered["binding"] = binding
                    elif contract_kind == "OnErrorContract":
                        lowered["targetName"] = text(nodes(contract)[0])
                    elif contract_kind == "TimeoutContract":
                        expression = nodes(contract)[0]
                        targets = nodes(contract)[1:]
                        if targets:
                            lowered["targetName"] = text(targets[0])
                        valid = False
                        if expression["kind"] == "Call":
                            call_parts = nodes(expression)
                            if (
                                len(call_parts) == 2
                                and call_parts[0]["kind"] == "Name"
                                and text(call_parts[0])
                                in {"seconds", "minutes", "hours"}
                            ):
                                args = nodes(call_parts[1])
                                if len(args) == 1 and args[0]["kind"] in {
                                    "Integer",
                                    "Float",
                                }:
                                    value = float(text(args[0]))
                                    seconds = (
                                        value
                                        * {"seconds": 1, "minutes": 60, "hours": 3600}[
                                            text(call_parts[0])
                                        ]
                                    )
                                    valid = (
                                        0 < seconds <= TIMEOUT_MAX
                                        and math.isfinite(seconds)
                                    )
                                    lowered.update(
                                        {"unit": text(call_parts[0]), "value": value}
                                    )
                        if not valid:
                            self.error(
                                "E2004",
                                "timeout requires seconds/minutes/hours with a positive finite numeric literal",
                                contract,
                            )
                    node["contracts"].append(lowered)
                graph["nodes"].append(node)
            elif kind == "GraphStart":
                graph["starts"].append(
                    {
                        "targetName": tokens(member, "NAME")[0]["text"],
                        "span": member["span"],
                    }
                )
            elif kind == "GraphEdge":
                transition = nodes(member)[0]
                edge = {
                    "sourceName": tokens(member, "NAME")[0]["text"],
                    "span": member["span"],
                    "branches": [],
                }
                if transition["kind"] == "ConditionalTransition":
                    guard, yes, no = nodes(transition)
                    self.safe_choices = set()
                    guard_type = self.expression_type(guard, state, variable)
                    guard["resolvedType"] = guard_type
                    if guard_type not in {"bool", "<error>"}:
                        self.error("E2003", "Graph guard must have type bool", guard)
                    edge["branches"] = [
                        {"guard": guard, "targetName": text(yes)},
                        {"otherwise": True, "targetName": text(no)},
                    ]
                else:
                    edge["branches"] = [{"targetName": text(transition)}]
                graph["transitions"].append(edge)
            elif kind == "GraphInvariant":
                expression = nodes(member)[0]
                self.safe_choices = set()
                if self.expression_type(expression, state, variable) != "bool":
                    self.error(
                        "E2601",
                        "Invariant must be a pure Boolean expression",
                        expression,
                    )
                graph["invariants"].append(expression)
            elif kind == "GraphState":
                self.unsupported(member, "GraphState initializers")
        # Names are retained alongside IDs. Unknown endpoints are name errors;
        # entry cardinality, transitions, reachability and cycles are graph checks.
        node_ids = {n["name"]: n["id"] for n in graph["nodes"]}
        for node in graph["nodes"]:
            for contract in node["contracts"]:
                if "targetName" in contract:
                    self.endpoint(contract, "target", node_ids, contract)
            method = next(
                (s for s in self.result.symbols if s["id"] == node["callable"]), {}
            )
            effect = method.get("effect")
            if effect and effect["kind"] == "mutation":
                compensators = [
                    c for c in node["contracts"] if c["kind"] == "CompensateContract"
                ]
                if not compensators and not effect.get("irreversiblePolicy"):
                    self.error(
                        "E2401",
                        "Mutation requires compensation or an explicit irreversible policy",
                        node,
                    )
                if compensators and effect.get("irreversiblePolicy"):
                    self.error(
                        "E2401",
                        "Choose compensation or irreversible policy, not both",
                        node,
                    )
                if compensators:
                    recovery = next(
                        (
                            s
                            for s in self.result.symbols
                            if s["id"] == compensators[0]["callable"]
                        ),
                        {},
                    )
                    recovery_effect = recovery.get("effect", {})
                    if (
                        recovery_effect.get("kind") != "recovery"
                        or recovery_effect.get("receiptType") != effect["receiptType"]
                    ):
                        self.error(
                            "E2401",
                            "Compensator requires a recovery capability with the same receipt type",
                            node,
                        )
            if effect and effect["kind"] == "recovery":
                self.error(
                    "E2401",
                    "Recovery capabilities can only be invoked as compensators",
                    node,
                )
        for start in graph["starts"]:
            self.endpoint(start, "target", node_ids, declaration)
        for edge in graph["transitions"]:
            self.endpoint(edge, "source", node_ids, declaration)
            for branch in edge["branches"]:
                self.endpoint(branch, "target", node_ids, edge)
        return graph

    def endpoint(self, item: dict, role: str, ids: dict, location: dict) -> None:
        name = item[role + "Name"]
        if role == "target" and name == "end":
            item[role] = "$end"
        elif name in ids:
            item[role] = ids[name]
        else:
            self.error(
                "E2001",
                f"Unknown graph {role} '{name}'",
                item if "span" in item else location,
            )
