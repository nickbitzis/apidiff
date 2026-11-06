import json
from pathlib import Path

import pytest

from apidiff.cli import main
from apidiff.diff import load_spec

EX = Path(__file__).resolve().parents[1] / "examples"
V1, V2 = str(EX / "bookstore-v1.yaml"), str(EX / "bookstore-v2.yaml")


def test_example_has_breaking_changes_and_exits_1(capsys):
    assert main([V1, V2]) == 1
    out = capsys.readouterr().out
    assert out.startswith("10 breaking, 4 warnings, 4 other. Suggested version bump: major.")
    assert "isbn: new required field" in out


def test_same_spec_exits_0(capsys):
    assert main([V1, V1]) == 0
    assert "No changes" in capsys.readouterr().out


def test_fail_on_never_and_warning(capsys):
    assert main([V1, V2, "--fail-on", "never"]) == 0
    assert main([V2, V2, "--fail-on", "warning"]) == 0


def test_json_output(capsys):
    main([V1, V2, "--format", "json"])
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == {"breaking": 10, "warning": 4, "info": 4}
    assert data["suggested_bump"] == "major"
    assert {"level", "code", "where", "detail"} <= set(data["changes"][0])


def test_markdown_output_folds_minor_changes(capsys):
    main([V1, V2, "--format", "markdown"])
    out = capsys.readouterr().out
    assert out.startswith("### API changes\n\n**10 breaking, 4 warnings")
    assert "| `POST /books` | isbn: new required field | `required-property-added` |" in out
    assert "<details><summary>Other changes (4)</summary>" in out


def test_hide_info(capsys):
    main([V1, V2, "--hide-info"])
    assert "Other changes" not in capsys.readouterr().out


def test_json_specs_load(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"openapi": "3.1.0", "paths": {}}))
    assert load_spec(p)["paths"] == {}


def test_bad_inputs_exit_2(tmp_path, capsys):
    swagger = tmp_path / "old.yaml"
    swagger.write_text("swagger: '2.0'\npaths: {}\n")
    assert main([str(swagger), V1]) == 2
    assert "Swagger 2.0" in capsys.readouterr().err
    assert main([str(tmp_path / "missing.yaml"), V1]) == 2
    junk = tmp_path / "junk.yaml"
    junk.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_spec(junk)
