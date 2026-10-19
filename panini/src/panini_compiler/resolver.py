"""Resolve and type-check the explicitly small M1 graph profile.

Only local non-generic classes/enums and public static (State)->State method
contracts are supported. Method bodies are opaque and not certified. Guards
never invoke those methods. Unsupported semantics fail explicitly.
"""

from __future__ import annotations

from copy import deepcopy

from .diagnostics import Diagnostic
from .ir import CheckResult


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


class Resolver:
    def __init__(self) -> None:
        self.result = CheckResult()
        self.declarations: dict[str, dict] = {}
        self.fields: dict[str, dict[str, tuple[str, dict]]] = {}
        self.methods: dict[str, tuple[str, dict]] = {}
        self.enums: dict[str, set[str]] = {}
        self.types = set(PRIMITIVES)
        self.used = False

    def error(self, code: str, message: str, item: dict) -> None:
        self.result.diagnostics.append(Diagnostic(code, message, item["span"]))

    def unsupported(self, item: dict, description: str) -> None:
        self.error(
            "E2000", f"Unsupported in local-static-state-v1: {description}", item
        )

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
            if kind not in {"ClassDecl", "EnumDecl", "GraphDecl"}:
                self.unsupported(declaration, kind)
                continue
            name = tokens(declaration, "NAME")[0]["text"]
            if name in self.declarations or name in PRIMITIVES:
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
            if declaration["kind"] == "ClassDecl":
                self.collect_class(name, declaration)
        for name, declaration in self.declarations.items():
            if declaration["kind"] == "GraphDecl":
                graph = self.graph(name, declaration)
                self.result.graphs.append(graph)
        for symbol in self.result.symbols:
            if "type" in symbol:
                symbol["typeSymbol"] = (
                    f"builtin:{symbol['type']}"
                    if symbol["type"] in PRIMITIVES
                    else self.declarations.get(symbol["type"], {}).get("id")
                )
            for parameter in symbol.get("parameters", []):
                parameter["typeSymbol"] = (
                    f"builtin:{parameter['type']}"
                    if parameter["type"] in PRIMITIVES
                    else self.declarations.get(parameter["type"], {}).get("id")
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

    def method(self, reference: dict, state: str) -> str | None:
        name = text(reference)
        if name not in self.methods:
            self.error("E2001", f"Unknown callable '{name}'", reference)
            return None
        returned, member = self.methods[name]
        modifiers = {text(m) for m in nodes(member, "Modifier")}
        if not {"public", "static"} <= modifiers or modifiers - {"public", "static"}:
            self.unsupported(
                reference,
                f"'{name}' must be a public static method without additional modifiers",
            )
        tail = nodes(member, "MethodTail")[0]
        parameters = nodes(nodes(tail, "Parameters")[0], "Parameter")
        if (
            returned != state
            or len(parameters) != 1
            or text(nodes(parameters[0])[0]) != state
        ):
            self.error(
                "E2003",
                f"Callable '{name}' must have signature ({state}) -> {state}",
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
            return {
                "Integer": "int",
                "Float": "float",
                "String": "string",
                "True": "bool",
                "False": "bool",
            }[kind]
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
            if field in self.fields.get(base_type, {}):
                field_type, declaration = self.fields[base_type][field]
                item["resolvedSymbol"] = declaration["id"]
                return field_type
            if base_type != "<error>":
                self.error("E2001", f"Unknown field '{field}' on '{base_type}'", item)
            return "<error>"
        if kind == "Binary":
            op = item["operator"]
            left, right = [self.expression_type(c, state, variable) for c in children]
            if "<error>" in (left, right):
                return "<error>"
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
        if state not in self.fields:
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
                node = {
                    "id": member["id"],
                    "name": node_name,
                    "span": member["span"],
                    "callable": self.method(reference, state),
                    "contracts": [],
                }
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
                    elif contract_kind == "TimeoutContract":
                        expression = nodes(contract)[0]
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
                                    valid = 0 < value < float("inf")
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
            elif kind in {"GraphInvariant", "GraphState"}:
                self.unsupported(member, f"{kind} obligations are not verified")
        # Names are retained alongside IDs. Unknown endpoints are M1 name errors;
        # entry cardinality, duplicate transitions, reachability and cycles are M2.
        node_ids = {n["name"]: n["id"] for n in graph["nodes"]}
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
