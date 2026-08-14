import json
from pathlib import Path

import pytest
from lark.exceptions import UnexpectedInput

from panini_compiler.cli import main
from panini_compiler.parser import PaniniParser


EXAMPLE = Path(__file__).parents[1] / "examples" / "audit.panini"


def test_documented_program_parses_to_versioned_ast() -> None:
    source = EXAMPLE.read_text(encoding="utf-8")
    ast = PaniniParser.create().ast(source, EXAMPLE)

    assert ast["schemaVersion"] == 1
    assert ast["kind"] == "Start"
    assert json.dumps(ast, sort_keys=True) == json.dumps(ast, sort_keys=True)


def test_tokens_have_utf8_byte_spans_and_instruction_mode() -> None:
    source = 'class Café { instruction #"hello"#; }'
    tokens = PaniniParser.create().lex(source, "unicode.panini")

    instruction = next(token for token in tokens if token.kind == "INSTRUCTION_LITERAL")
    assert instruction.mode == "instruction"
    assert instruction.exact_text == '#"hello"#'
    assert instruction.span.start == len(source[: source.index('#"')].encode("utf-8"))


def test_invalid_syntax_is_rejected() -> None:
    with pytest.raises(UnexpectedInput):
        PaniniParser.create().parse("class MissingSemicolon { string name }")


def test_cli_reports_stable_syntax_diagnostic(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    source = tmp_path / "invalid.panini"
    source.write_text("class Broken {", encoding="utf-8")

    assert main(["parse", str(source)]) == 1
    assert "E1001: invalid Panini syntax" in capsys.readouterr().err
