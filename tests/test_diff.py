import copy

import pytest

from apidiff.diff import BREAKING, INFO, WARNING, diff, normalize_path, suggest_bump


def spec(paths=None, schemas=None, security=None):
    s = {"openapi": "3.0.3", "info": {"title": "t", "version": "1"}, "paths": paths or {}}
    if schemas:
        s["components"] = {"schemas": schemas}
    if security is not None:
        s["security"] = security
    return s


def body_op(schema):
    return {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}, "responses": {"200": {"description": "ok"}}}}


def resp_op(schema):
    return {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": schema}}}}}}


def codes(changes, level=None):
    return [c.code for c in changes if level is None or c.level == level]


def test_identical_specs_have_no_changes():
    s = spec({"/a": resp_op({"type": "string"})})
    assert diff(s, copy.deepcopy(s)) == []


def test_path_and_operation_removal_is_breaking_addition_is_info():
    old = spec({"/a": {"get": {"responses": {}}, "post": {"responses": {}}}, "/b": {"get": {"responses": {}}}})
    new = spec({"/a": {"get": {"responses": {}}}, "/c": {"get": {"responses": {}}}})
    ch = diff(old, new)
    assert set(codes(ch, BREAKING)) == {"path-removed", "operation-removed"}
    assert codes(ch, INFO) == ["path-added"]


def test_renamed_path_parameter_is_the_same_route():
    assert normalize_path("/pets/{petId}/toys/{toyId}") == "/pets/{}/toys/{}"
    old = spec({"/pets/{petId}": {"get": {"responses": {}}}})
    new = spec({"/pets/{id}": {"get": {"responses": {}}}})
    assert diff(old, new) == []


def test_required_query_parameter_added_is_breaking():
    old = spec({"/a": {"get": {"responses": {}}}})
    new = spec({"/a": {"get": {"parameters": [{"name": "q", "in": "query", "required": True}], "responses": {}}}})
    assert codes(diff(old, new)) == ["required-parameter-added"]


def test_optional_parameter_added_is_info_and_becoming_required_is_breaking():
    p = {"name": "q", "in": "query", "schema": {"type": "string"}}
    base = spec({"/a": {"get": {"responses": {}}}})
    optional = spec({"/a": {"get": {"parameters": [p], "responses": {}}}})
    required = spec({"/a": {"get": {"parameters": [{**p, "required": True}], "responses": {}}}})
    assert codes(diff(base, optional)) == ["parameter-added"]
    assert codes(diff(optional, required)) == ["parameter-now-required"]
    assert codes(diff(optional, base)) == ["parameter-removed"]


def test_path_level_parameters_merge_with_operation_parameters():
    p = {"name": "X-Tenant", "in": "header", "required": True}
    old = spec({"/a": {"get": {"responses": {}}}})
    new = spec({"/a": {"parameters": [p], "get": {"responses": {}}}})
    assert codes(diff(old, new)) == ["required-parameter-added"]


def test_parameter_type_change_is_breaking():
    old = spec({"/a": {"get": {"parameters": [{"name": "n", "in": "query", "schema": {"type": "string"}}], "responses": {}}}})
    new = copy.deepcopy(old)
    new["paths"]["/a"]["get"]["parameters"][0]["schema"]["type"] = "integer"
    assert codes(diff(old, new)) == ["type-changed"]


def test_integer_to_number_is_fine_in_a_request_but_not_a_response():
    assert codes(diff(spec({"/a": body_op({"type": "integer"})}), spec({"/a": body_op({"type": "number"})})), BREAKING) == []
    assert codes(diff(spec({"/a": resp_op({"type": "integer"})}), spec({"/a": resp_op({"type": "number"})})), BREAKING) == ["type-changed"]


def test_request_new_required_property_is_breaking_optional_is_info():
    old = spec({"/a": body_op({"type": "object", "properties": {"a": {"type": "string"}}})})
    new = spec({"/a": body_op({"type": "object", "required": ["b"], "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "string"}}})})
    ch = diff(old, new)
    assert codes(ch, BREAKING) == ["required-property-added"]
    assert codes(ch, INFO) == ["property-added"]


def test_request_property_becoming_required_is_breaking():
    old = spec({"/a": body_op({"type": "object", "properties": {"a": {"type": "string"}}})})
    new = spec({"/a": body_op({"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}})})
    assert codes(diff(old, new)) == ["property-now-required"]


def test_response_property_removed_or_made_optional_is_breaking():
    old = spec({"/a": resp_op({"type": "object", "required": ["a", "b"], "properties": {"a": {"type": "string"}, "b": {"type": "string"}, "c": {"type": "string"}}})})
    new = spec({"/a": resp_op({"type": "object", "required": ["b"], "properties": {"b": {"type": "string"}}})})
    ch = diff(old, new)
    assert codes(ch, BREAKING) == ["response-property-removed"]
    # c was never guaranteed, so its removal is a warning rather than breaking.
    assert codes(ch, WARNING) == ["response-property-removed"]


def test_response_required_dropped_is_breaking():
    old = spec({"/a": resp_op({"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}})})
    new = spec({"/a": resp_op({"type": "object", "properties": {"a": {"type": "string"}}})})
    assert codes(diff(old, new)) == ["property-no-longer-required"]


@pytest.mark.parametrize(
    "maker, change, expected",
    [
        (body_op, "removed", BREAKING),
        (body_op, "added", INFO),
        (resp_op, "removed", INFO),
        (resp_op, "added", WARNING),
    ],
)
def test_enum_direction(maker, change, expected):
    small, big = {"type": "string", "enum": ["a", "b"]}, {"type": "string", "enum": ["a", "b", "c"]}
    old, new = (big, small) if change == "removed" else (small, big)
    ch = diff(spec({"/a": maker(old)}), spec({"/a": maker(new)}))
    assert [c.level for c in ch] == [expected]


def test_request_constraints_tightened_are_breaking_loosened_are_not():
    old = spec({"/a": body_op({"type": "string", "maxLength": 100, "minLength": 1})})
    tight = spec({"/a": body_op({"type": "string", "maxLength": 50, "minLength": 2})})
    loose = spec({"/a": body_op({"type": "string", "maxLength": 200})})
    assert codes(diff(old, tight)) == ["constraint-tightened", "constraint-tightened"]
    assert diff(old, loose) == []


def test_new_limit_where_there_was_none_is_breaking():
    old = spec({"/a": body_op({"type": "array", "items": {"type": "string"}})})
    new = spec({"/a": body_op({"type": "array", "maxItems": 10, "items": {"type": "string"}})})
    ch = diff(old, new)
    assert codes(ch) == ["constraint-tightened"]
    assert "none -> 10" in ch[0].detail


def test_nullable_direction():
    nul = {"type": "string", "nullable": True}
    plain = {"type": "string"}
    assert codes(diff(spec({"/a": resp_op(plain)}), spec({"/a": resp_op(nul)})), BREAKING) == ["nullable-added"]
    assert codes(diff(spec({"/a": body_op(nul)}), spec({"/a": body_op(plain)})), BREAKING) == ["nullable-removed"]
    assert codes(diff(spec({"/a": body_op(plain)}), spec({"/a": body_op(nul)})), BREAKING) == []


def test_openapi_31_type_lists():
    old = spec({"/a": resp_op({"type": "string"})})
    new = spec({"/a": resp_op({"type": ["string", "null"]})})
    assert codes(diff(old, new)) == ["nullable-added"]


def test_nested_arrays_report_a_readable_location():
    item = lambda t: {"type": "object", "properties": {"tags": {"type": "array", "items": {"type": t}}}}
    ch = diff(spec({"/a": resp_op(item("string"))}), spec({"/a": resp_op(item("integer"))}))
    assert ch[0].detail.startswith("tags[]: type string -> integer")


def test_refs_are_resolved_and_shared_schemas_reported_per_use():
    book = lambda req: {"type": "object", "required": req, "properties": {"id": {"type": "string"}}}
    paths = {"/a": resp_op({"$ref": "#/components/schemas/Book"}), "/b": resp_op({"$ref": "#/components/schemas/Book"})}
    ch = diff(spec(paths, {"Book": book(["id"])}), spec(paths, {"Book": book([])}))
    assert [c.where for c in ch] == ["GET /a -> 200", "GET /b -> 200"]


def test_recursive_schemas_terminate():
    node = {"type": "object", "properties": {"name": {"type": "string"}, "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}}}}
    node2 = copy.deepcopy(node)
    node2["properties"]["name"]["type"] = "integer"
    paths = {"/tree": resp_op({"$ref": "#/components/schemas/Node"})}
    ch = diff(spec(paths, {"Node": node}), spec(paths, {"Node": node2}))
    assert "type-changed" in codes(ch)


def test_success_response_removed_is_breaking_error_response_is_not():
    old = spec({"/a": {"get": {"responses": {"200": {"description": "ok"}, "404": {"description": "no"}}}}})
    new = spec({"/a": {"get": {"responses": {"201": {"description": "ok"}}}}})
    ch = diff(old, new)
    assert codes(ch, BREAKING) == ["response-removed"]
    assert sorted(codes(ch, INFO)) == ["response-added", "response-removed"]


def test_media_type_removed_is_breaking():
    old = spec({"/a": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {}, "text/csv": {}}}}}}})
    new = spec({"/a": {"get": {"responses": {"200": {"description": "ok", "content": {"application/json": {}}}}}}})
    assert codes(diff(old, new)) == ["response-media-type-removed"]


def test_request_body_made_required_is_breaking():
    old = spec({"/a": {"post": {"requestBody": {"content": {"application/json": {}}}, "responses": {}}}})
    new = spec({"/a": {"post": {"requestBody": {"required": True, "content": {"application/json": {}}}, "responses": {}}}})
    assert codes(diff(old, new)) == ["request-body-now-required"]


def test_closing_additional_properties_in_a_request_is_breaking():
    old = spec({"/a": body_op({"type": "object"})})
    new = spec({"/a": body_op({"type": "object", "additionalProperties": False})})
    assert codes(diff(old, new)) == ["additional-properties-closed"]


def test_auth_added_to_an_open_operation_is_breaking():
    old = spec({"/a": {"get": {"responses": {}}}})
    new = spec({"/a": {"get": {"responses": {}}}}, security=[{"key": []}])
    assert codes(diff(old, new)) == ["auth-added"]


def test_operation_security_overrides_global():
    old = spec({"/h": {"get": {"security": [], "responses": {}}}}, security=[{"key": []}])
    assert diff(old, copy.deepcopy(old)) == []


def test_new_scope_or_new_scheme_is_breaking_offering_an_alternative_is_not():
    op = lambda sec: spec({"/a": {"get": {"security": sec, "responses": {}}}})
    assert codes(diff(op([{"oauth": ["read"]}]), op([{"oauth": ["read", "write"]}]))) == ["auth-changed"]
    assert codes(diff(op([{"key": []}]), op([{"oauth": []}]))) == ["auth-changed"]
    assert diff(op([{"key": []}]), op([{"key": []}, {"oauth": []}])) == []


def test_operation_id_rename_is_a_warning():
    old = spec({"/a": {"get": {"operationId": "getA", "responses": {}}}})
    new = spec({"/a": {"get": {"operationId": "fetchA", "responses": {}}}})
    assert [(c.level, c.code) for c in diff(old, new)] == [(WARNING, "operation-id-changed")]


def test_results_are_sorted_by_severity():
    old = spec({"/a": {"get": {"responses": {}}}, "/b": {"get": {"responses": {}}}})
    new = spec({"/a": {"get": {"responses": {}}, "post": {"responses": {}}}})
    assert [c.level for c in diff(old, new)] == [BREAKING, INFO]


def test_suggest_bump():
    old = spec({"/a": {"get": {"responses": {}}}})
    assert suggest_bump(diff(old, old)) == "patch"
    assert suggest_bump(diff(old, spec({"/a": {"get": {"responses": {}}}, "/b": {"get": {"responses": {}}}}))) == "minor"
    assert suggest_bump(diff(old, spec({}))) == "major"


def test_all_of_parts_are_merged_before_comparing():
    base = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
    old = {"allOf": [{"$ref": "#/components/schemas/Base"}, {"properties": {"name": {"type": "string"}}, "required": ["name"]}]}
    new = {"allOf": [{"$ref": "#/components/schemas/Base"}, {"properties": {"name": {"type": "string"}}}]}
    paths_old, paths_new = {"/a": resp_op(old)}, {"/a": resp_op(new)}
    ch = diff(spec(paths_old, {"Base": base}), spec(paths_new, {"Base": base}))
    assert [(c.code, c.detail) for c in ch] == [("property-no-longer-required", "name: may now be missing from the response")]
