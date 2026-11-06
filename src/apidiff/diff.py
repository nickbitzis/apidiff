"""Compare two OpenAPI 3 documents and classify every change.

The key idea is direction. A schema used in a request is something clients
send, so the server getting stricter breaks them. A schema used in a response
is something clients read, so the server promising less breaks them. The same
edit, like adding an enum value, can be harmless in one place and breaking in
the other.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import yaml

BREAKING, WARNING, INFO = "breaking", "warning", "info"
LEVELS = (BREAKING, WARNING, INFO)
METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


@dataclass(frozen=True)
class Change:
    level: str
    code: str
    where: str
    detail: str

    def to_dict(self) -> dict:
        return asdict(self)


def load_spec(path: str | Path) -> dict:
    text = Path(path).read_text()
    spec = json.loads(text) if str(path).endswith(".json") else yaml.safe_load(text)
    if not isinstance(spec, dict) or "paths" not in spec:
        raise ValueError(f"{path} does not look like an OpenAPI document (no 'paths').")
    if not str(spec.get("openapi", "")).startswith("3"):
        raise ValueError(f"{path} is not OpenAPI 3.x. Swagger 2.0 is not supported.")
    return spec


def normalize_path(path: str) -> str:
    """`/pets/{id}` and `/pets/{petId}` are the same route to a client."""
    return re.sub(r"\{[^}]+\}", "{}", path)


class Resolver:
    def __init__(self, spec: dict):
        self.spec = spec

    def __call__(self, node):
        seen = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if not ref.startswith("#/") or ref in seen:
                return {}
            seen.add(ref)
            target = self.spec
            for part in ref[2:].split("/"):
                target = target.get(part.replace("~1", "/").replace("~0", "~"), {}) if isinstance(target, dict) else {}
            node = target
        return node if isinstance(node, dict) else {}


def _type(schema: dict):
    t = schema.get("type")
    if isinstance(t, list):  # OpenAPI 3.1 allows a list of types
        rest = tuple(sorted(x for x in t if x != "null"))
        return rest[0] if len(rest) == 1 else rest or None
    return t


def _nullable(schema: dict) -> bool:
    t = schema.get("type")
    return bool(schema.get("nullable")) or (isinstance(t, list) and "null" in t)


def flatten(schema, resolve) -> dict:
    """Merge `allOf` parts into one schema so composed models compare like plain ones."""
    schema = resolve(schema)
    if "allOf" not in schema:
        return schema
    merged = {k: v for k, v in schema.items() if k != "allOf"}
    props, required = dict(merged.get("properties", {})), list(merged.get("required", []))
    for part in schema["allOf"]:
        part = flatten(part, resolve)
        for k, v in part.items():
            if k == "properties":
                props.update(v)
            elif k == "required":
                required += [r for r in v if r not in required]
            else:
                merged.setdefault(k, v)
    if props:
        merged["properties"] = props
    if required:
        merged["required"] = required
    return merged


# Constraints where a smaller new value is stricter, and where a larger one is.
UPPER = ("maxLength", "maxItems", "maximum", "maxProperties")
LOWER = ("minLength", "minItems", "minimum", "minProperties")


class Differ:
    def __init__(self, old: dict, new: dict):
        self.old, self.new = old, new
        self.ro, self.rn = Resolver(old), Resolver(new)
        self.changes: list[Change] = []
        self._stack: set = set()

    def add(self, level, code, where, detail):
        self.changes.append(Change(level, code, where, detail))

    # Schemas ---------------------------------------------------------------

    def schema(self, old, new, where: str, field: str, direction: str, depth: int = 0):
        refs = (old.get("$ref") if isinstance(old, dict) else None, new.get("$ref") if isinstance(new, dict) else None)
        key = (refs, field, direction)
        if depth > 30 or (refs != (None, None) and key in self._stack):
            return  # recursive schema, already being compared further up
        self._stack.add(key)
        try:
            self._schema(flatten(old, self.ro), flatten(new, self.rn), where, field, direction, depth)
        finally:
            self._stack.discard(key)

    def _schema(self, old: dict, new: dict, where, field, direction, depth):
        req = direction == "request"
        label = field or "body"

        ot, nt = _type(old), _type(new)
        if ot and nt and ot != nt:
            widened = req and (ot, nt) == ("integer", "number")
            narrowed = not req and (ot, nt) == ("number", "integer")
            if widened or narrowed:
                self.add(INFO, "type-compatible", where, f"{label}: type {ot} -> {nt}, compatible in a {direction}")
            else:
                self.add(BREAKING, "type-changed", where, f"{label}: type {ot} -> {nt}")
                return

        if old.get("format") != new.get("format") and old.get("format") and new.get("format"):
            self.add(WARNING, "format-changed", where, f"{label}: format {old['format']} -> {new['format']}")

        if _nullable(old) != _nullable(new):
            got_nullable = _nullable(new)
            if req and not got_nullable:
                self.add(BREAKING, "nullable-removed", where, f"{label}: no longer accepts null")
            elif not req and got_nullable:
                self.add(BREAKING, "nullable-added", where, f"{label}: may now be null")
            else:
                self.add(INFO, "nullable-changed", where, f"{label}: nullable is now {got_nullable}")

        self._enum(old, new, where, label, req)
        if req:
            self._constraints(old, new, where, label)

        op, np_ = old.get("properties", {}), new.get("properties", {})
        oreq, nreq = set(old.get("required", [])), set(new.get("required", []))
        for name in sorted(set(op) | set(np_)):
            sub = f"{field}.{name}" if field else name
            if name in op and name not in np_:
                if req:
                    self.add(WARNING, "request-property-removed", where, f"{sub}: removed, clients that still send it may be rejected")
                else:
                    level = BREAKING if name in oreq else WARNING
                    self.add(level, "response-property-removed", where, f"{sub}: removed from the response")
            elif name not in op:
                if req and name in nreq:
                    self.add(BREAKING, "required-property-added", where, f"{sub}: new required field")
                else:
                    self.add(INFO, "property-added", where, f"{sub}: new optional field" if req else f"{sub}: new field")
            else:
                if req and name in nreq and name not in oreq:
                    self.add(BREAKING, "property-now-required", where, f"{sub}: was optional, now required")
                if not req and name in oreq and name not in nreq:
                    self.add(BREAKING, "property-no-longer-required", where, f"{sub}: may now be missing from the response")
                self.schema(op[name], np_[name], where, sub, direction, depth + 1)

        if "items" in old and "items" in new:
            self.schema(old["items"], new["items"], where, f"{label}[]", direction, depth + 1)

        if req and old.get("additionalProperties", True) is not False and new.get("additionalProperties") is False:
            self.add(BREAKING, "additional-properties-closed", where, f"{label}: no longer accepts unknown fields")

    def _enum(self, old, new, where, label, req):
        if "enum" not in old and "enum" not in new:
            return
        if "enum" not in old:
            level = BREAKING if req else INFO
            self.add(level, "enum-added", where, f"{label}: now limited to {new['enum']}")
            return
        if "enum" not in new:
            level = INFO if req else WARNING
            self.add(level, "enum-removed", where, f"{label}: no longer limited to a fixed set of values")
            return
        removed = [v for v in old["enum"] if v not in new["enum"]]
        added = [v for v in new["enum"] if v not in old["enum"]]
        if removed:
            self.add(BREAKING if req else INFO, "enum-value-removed", where, f"{label}: no longer allows {removed}")
        if added:
            # Clients that switch over every value can fail on one they have never seen.
            self.add(INFO if req else WARNING, "enum-value-added", where, f"{label}: new values {added}")

    def _constraints(self, old, new, where, label):
        for key in UPPER:
            if key in new and (key not in old or new[key] < old[key]):
                self.add(BREAKING, "constraint-tightened", where, f"{label}: {key} {old.get(key, 'none')} -> {new[key]}")
        for key in LOWER:
            if key in new and (key not in old or new[key] > old[key]):
                self.add(BREAKING, "constraint-tightened", where, f"{label}: {key} {old.get(key, 'none')} -> {new[key]}")
        if new.get("pattern") and new.get("pattern") != old.get("pattern"):
            self.add(WARNING, "pattern-changed", where, f"{label}: pattern is now {new['pattern']!r}")

    # Operations ------------------------------------------------------------

    def params(self, spec, resolve, path_item, op) -> dict:
        out = {}
        for p in list(path_item.get("parameters", [])) + list(op.get("parameters", [])):
            p = resolve(p)
            if p.get("in") == "path":
                key = ("path", None)  # path params are matched by position, not name
                out.setdefault(key, []).append(p)
            elif p.get("name"):
                out[(p["in"], p["name"])] = p
        return out

    def operation(self, where, opath, npath, oop, nop):
        if nop.get("deprecated") and not oop.get("deprecated"):
            self.add(INFO, "operation-deprecated", where, "marked deprecated")
        if oop.get("operationId") and nop.get("operationId") and oop["operationId"] != nop["operationId"]:
            self.add(WARNING, "operation-id-changed", where, f"operationId {oop['operationId']} -> {nop['operationId']} renames generated SDK methods")

        oparams = self.params(self.old, self.ro, opath, oop)
        nparams = self.params(self.new, self.rn, npath, nop)
        oparams.pop(("path", None), None)
        nparams.pop(("path", None), None)
        for key in sorted(set(oparams) | set(nparams), key=str):
            loc, name = key
            label = f"{loc} parameter {name}"
            if key not in nparams:
                self.add(WARNING, "parameter-removed", where, f"{label}: removed")
            elif key not in oparams:
                if nparams[key].get("required"):
                    self.add(BREAKING, "required-parameter-added", where, f"{label}: new and required")
                else:
                    self.add(INFO, "parameter-added", where, f"{label}: new and optional")
            else:
                o, n = oparams[key], nparams[key]
                if n.get("required") and not o.get("required"):
                    self.add(BREAKING, "parameter-now-required", where, f"{label}: was optional, now required")
                self.schema(o.get("schema", {}), n.get("schema", {}), where, label, "request")

        self.request_body(where, self.ro(oop.get("requestBody", {})), self.rn(nop.get("requestBody", {})))
        self.responses(where, oop.get("responses", {}), nop.get("responses", {}))
        self.security(where, oop, nop)

    def request_body(self, where, old, new):
        if not old and not new:
            return
        if not old:
            level = BREAKING if new.get("required") else INFO
            self.add(level, "request-body-added", where, "now takes a request body" + (" (required)" if new.get("required") else ""))
            return
        if not new:
            self.add(WARNING, "request-body-removed", where, "no longer takes a request body")
            return
        if new.get("required") and not old.get("required"):
            self.add(BREAKING, "request-body-now-required", where, "request body is now required")
        oc, nc = old.get("content", {}), new.get("content", {})
        for media in oc:
            if media not in nc:
                self.add(BREAKING, "request-media-type-removed", where, f"no longer accepts {media}")
            else:
                self.schema(oc[media].get("schema", {}), nc[media].get("schema", {}), where, "", "request")

    def responses(self, where, old, new):
        for code in sorted(set(old) | set(new)):
            label = f"response {code}"
            success = str(code).startswith("2")
            if code not in new:
                self.add(BREAKING if success else INFO, "response-removed", where, f"{label}: removed")
                continue
            if code not in old:
                self.add(INFO, "response-added", where, f"{label}: added")
                continue
            o, n = self.ro(old[code]), self.rn(new[code])
            oc, nc = o.get("content", {}), n.get("content", {})
            for media in oc:
                if media not in nc:
                    self.add(BREAKING, "response-media-type-removed", where, f"{label}: no longer returns {media}")
                else:
                    self.schema(oc[media].get("schema", {}), nc[media].get("schema", {}), f"{where} -> {code}", "", "response")

    def security(self, where, oop, nop):
        def reqs(spec, op):
            raw = op["security"] if "security" in op else spec.get("security", [])
            return [{k: set(v) for k, v in r.items()} for r in raw]

        old, new = reqs(self.old, oop), reqs(self.new, nop)
        open_before = not old or {} in old
        open_now = not new or {} in new
        if open_before and not open_now:
            self.add(BREAKING, "auth-added", where, "now requires authentication")
            return
        if open_now:
            return
        # A client that satisfied some old requirement must still satisfy a new one.
        for r in old:
            if not any(set(n) <= set(r) and all(n[k] <= r[k] for k in n) for n in new):
                self.add(BREAKING, "auth-changed", where, f"credentials that satisfied {_fmt_req(r)} no longer work")

    # Whole document --------------------------------------------------------

    def run(self) -> list[Change]:
        opaths = {normalize_path(p): (p, v) for p, v in self.old.get("paths", {}).items()}
        npaths = {normalize_path(p): (p, v) for p, v in self.new.get("paths", {}).items()}
        for key in sorted(set(opaths) | set(npaths)):
            if key not in npaths:
                self.add(BREAKING, "path-removed", opaths[key][0], "path removed")
                continue
            if key not in opaths:
                self.add(INFO, "path-added", npaths[key][0], "path added")
                continue
            (op_, oitem), (np_, nitem) = opaths[key], npaths[key]
            oitem, nitem = self.ro(oitem), self.rn(nitem)
            for m in METHODS:
                where = f"{m.upper()} {np_}"
                if m in oitem and m not in nitem:
                    self.add(BREAKING, "operation-removed", f"{m.upper()} {op_}", "operation removed")
                elif m in nitem and m not in oitem:
                    self.add(INFO, "operation-added", where, "operation added")
                elif m in oitem:
                    self.operation(where, oitem, nitem, oitem[m], nitem[m])
        order = {lvl: i for i, lvl in enumerate(LEVELS)}
        return sorted(self.changes, key=lambda c: order[c.level])


def _fmt_req(req: dict) -> str:
    return " + ".join(f"{k}{sorted(v) if v else ''}" for k, v in req.items()) or "no auth"


def diff(old: dict, new: dict) -> list[Change]:
    return Differ(old, new).run()


def suggest_bump(changes: list[Change]) -> str:
    """Semantic versioning: breaking means major, any addition means minor."""
    if any(c.level == BREAKING for c in changes):
        return "major"
    if any(c.code.endswith("-added") or c.code == "operation-deprecated" for c in changes):
        return "minor"
    return "patch"
