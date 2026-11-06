# apidiff

Compare two versions of an OpenAPI 3 spec and find the changes that will break existing clients. Run it in CI so a breaking change shows up in review, before it ships.

42 tests. One dependency (PyYAML).

## Why

Most API outages caused by a deploy are not bugs. Someone renamed a field, made a parameter required, or tightened a limit, and every client built against the old contract started failing. The spec already says what changed. This tool reads both versions and sorts each change into breaking, warning, or safe.

The rules depend on direction:

- **Requests** are what clients send. The server getting stricter breaks them: a new required field, a lower `maxLength`, a removed enum value, a closed `additionalProperties`.
- **Responses** are what clients read. The server promising less breaks them: a removed field, a field that may now be missing or null, a new enum value a strict client has never seen.

So adding `audiobook` to a `format` enum is safe in a request body and a warning in a response. Most diff tools treat both the same.

## Quick start

```
$ uv run apidiff examples/bookstore-v1.yaml examples/bookstore-v2.yaml
10 breaking, 4 warnings, 4 other. Suggested version bump: major.

Breaking (10)
  GET /books              query parameter limit: maximum 100 -> 50  [constraint-tightened]
  GET /books -> 200       items[].price: may now be missing from the response  [property-no-longer-required]
  GET /books -> 200       items[].price: may now be null  [nullable-added]
  POST /books             isbn: new required field  [required-property-added]
  POST /books             title: maxLength 300 -> 200  [constraint-tightened]
  ...
  DELETE /books/{id}      credentials that satisfied apiKey no longer work  [auth-changed]

Warnings (4)
  GET /books -> 200       items[].format: new values ['audiobook']  [enum-value-added]
  DELETE /books/{id}      operationId deleteBook -> removeBook renames generated SDK methods  [operation-id-changed]
  ...
```

The two example specs describe a made-up bookstore API. Version 2 makes a realistic set of changes. Note what is not flagged: `/books/{bookId}` became `/books/{id}`, and that is the same route to a client.

A change to a shared schema is reported once for each operation that uses it, so reviewers can see which endpoints are affected.

## In CI

The exit code is 1 when breaking changes exist, 0 otherwise, and 2 when a spec cannot be read. `--fail-on warning` is stricter and `--fail-on never` only reports.

`--format markdown` writes a table you can post as a pull request comment. Minor changes fold into a `<details>` block. `--format json` is for other tools.

A GitHub Actions job that compares the spec on a pull request with the one on `main`:

```yaml
name: API contract
on: pull_request
jobs:
  apidiff:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: { fetch-depth: 0 }
      - uses: astral-sh/setup-uv@v6
      - run: git show origin/main:openapi.yaml > /tmp/base.yaml
      - run: uvx --from git+https://github.com/nickbitzis/apidiff apidiff /tmp/base.yaml openapi.yaml --format markdown >> "$GITHUB_STEP_SUMMARY"
```

## Rules

| Rule | Level | Notes |
|---|---|---|
| `path-removed`, `operation-removed` | breaking | |
| `required-parameter-added`, `parameter-now-required` | breaking | path, query, header, and cookie parameters |
| `type-changed` | breaking | `integer` to `number` is allowed in requests, `number` to `integer` in responses |
| `required-property-added`, `property-now-required` | breaking | request bodies |
| `response-property-removed` | breaking or warning | breaking if the field was required |
| `property-no-longer-required`, `nullable-added` | breaking | responses |
| `nullable-removed` | breaking | requests |
| `enum-value-removed` | breaking | requests |
| `enum-value-added` | warning | responses |
| `constraint-tightened` | breaking | `maxLength`, `minLength`, `maximum`, `minimum`, `maxItems`, `minItems`, and the property counts, in requests |
| `additional-properties-closed` | breaking | requests |
| `request-body-now-required`, `request-media-type-removed`, `response-media-type-removed` | breaking | |
| `response-removed` | breaking for 2xx, info otherwise | |
| `auth-added`, `auth-changed` | breaking | a client that satisfied an old security requirement must still satisfy a new one |
| `operation-id-changed` | warning | renames methods in generated SDKs |
| `format-changed`, `pattern-changed`, `parameter-removed`, `request-property-removed` | warning | |

Local `$ref`s are resolved, including recursive schemas. OpenAPI 3.1 type lists such as `["string", "null"]` are understood. Swagger 2.0 is not supported. Convert it to OpenAPI 3 first.

## Limits

`allOf` parts are merged before comparing. `oneOf` and `anyOf` are not compared yet, so changes inside them are not reported. External `$ref`s to other files are treated as empty. Neither shows up in the example, but large real specs use both.

## Tests

```bash
uv run --group dev pytest
```

## License

MIT. See [LICENSE](LICENSE).
