# Migrating to blackboxprotobuf v2

## Summary of breaking changes

The eight encode/decode functions from v1 have been replaced by two core
functions (`decode` and `encode`) and two JSON wrappers (`decode_to_json` and
`encode_from_json`). The encoding type (none/gzip/grpc) is now an explicit
parameter instead of a different function name.

Additionally, `TypeDef` and `FieldDef` are now public types returned from
`decode`. Direct dict access to the typedef is still possible via `.to_dict()`,
but the `TypeDef` object should be preferred as it preserves internal state
needed for byte-stable re-encoding.

---

## Function renames

| v1 | v2 |
|---|---|
| `decode_message(data, typedef)` | `decode(data, typedef, encoding="none").message` |
| `encode_message(msg, typedef)` | `encode(msg, typedef, encoding="none")` |
| `protobuf_to_json(data, typedef)` | `decode_to_json(data, typedef, encoding="none")` |
| `protobuf_from_json(json_str, typedef)` | `encode_from_json(json_str, typedef, encoding="none")` |
| `decode_wrapped_message(data)` | `decode(data, encoding="auto")` |
| `encode_wrapped_message(msgs, typedef, enc)` | `encode(msgs, typedef, encoding=enc)` |
| `export_protofile(types, path)` | `export_protofile(types, path)` *(unchanged)* |
| `import_protofile(path)` | `import_protofile(path)` *(unchanged)* |

---

## Migration examples

### Decode then re-encode (basic)

```python
# v1
message, typedef = blackboxprotobuf.decode_message(data)
message["1"] = "new value"
new_data = blackboxprotobuf.encode_message(message, typedef)

# v2
result = blackboxprotobuf.decode(data, encoding="none")
result.messages[0]["1"] = "new value"
new_data = blackboxprotobuf.encode(result.messages, result.typedef, encoding="none")
# or use the convenience method:
new_data = result.re_encode()
```

### Decode with a known typedef

```python
# v1
message, typedef = blackboxprotobuf.decode_message(data, typedef=my_typedef)

# v2
result = blackboxprotobuf.decode(data, my_typedef, encoding="none")
message = result.message       # single-message shortcut (raises if multiple)
typedef = result.typedef       # TypeDef object
typedef_dict = typedef.to_dict()  # plain dict, if you need it
```

### JSON round-trip

```python
# v1
json_str, typedef = blackboxprotobuf.protobuf_to_json(data, typedef)
new_data = blackboxprotobuf.protobuf_from_json(json_str, typedef)

# v2
result = blackboxprotobuf.decode_to_json(data, typedef, encoding="none")
json_str = result.message_json
# Pass result.typedef (TypeDef object) to preserve field ordering:
new_data = blackboxprotobuf.encode_from_json(json_str, result.typedef, encoding="none")
```

### gRPC / wrapped messages

```python
# v1 — had to pick the right function based on encoding
message, typedef = blackboxprotobuf.decode_grpc_message(data)

# v2 — encoding= parameter replaces separate function names
result = blackboxprotobuf.decode(data, encoding="grpc")   # explicit
result = blackboxprotobuf.decode(data, encoding="auto")   # auto-detect (default)
messages = result.messages   # list; may have > 1 message for grpc
```

---

## DecodeResult attributes

`decode()` returns a `DecodeResult` namedtuple:

| Attribute | Type | Description |
|---|---|---|
| `.messages` | `list[dict]` | Decoded messages (length 1 for none/gzip) |
| `.typedef` | `TypeDef` | Inferred/provided schema |
| `.encoding` | `str` | Detected encoding (`"none"`, `"gzip"`, `"grpc"`) |
| `.annotations` | `dict` | Example values keyed by field path |
| `.message` | `dict` | Shortcut for `.messages[0]`; raises if `len > 1` |
| `.re_encode(encoding, config)` | `bytes` | Re-encode with same typedef |

`decode_to_json()` returns a `JSONDecodeResult` with the same attributes, plus:

| Attribute | Type | Description |
|---|---|---|
| `.messages_json` | `str` | JSON array string of all decoded messages |
| `.message_json` | `str` | JSON object string for the single message (raises if `len > 1`) |

---

## TypeDef object

The `TypeDef` object supports dict-like access and is the recommended way to
inspect and modify schemas:

```python
result = blackboxprotobuf.decode(data, encoding="none")
typedef = result.typedef

# Read
field_type = typedef["1"].type
field_name = typedef["1"].name

# Modify
typedef["1"].type = "string"
typedef["1"].name = "email"

# Navigate nested messages
typedef["5"].message_typedef["3"].type = "float"

# Re-encode after modification (field_order preserved inside TypeDef object)
new_data = blackboxprotobuf.encode(result.messages, typedef, encoding="none")
```

`typedef.to_dict()` converts back to a plain dict (for JSON serialization,
display, etc.). Note: `field_order` is **not** included in `to_dict()` output.
For byte-stable re-encoding, always pass the `TypeDef` object directly rather
than going through `to_dict()` → `TypeDef.from_dict()`.

---

## Removed from the public API

The following were exported in v1 but are no longer public in v2:

- `decode_message`, `encode_message`
- `protobuf_to_json`, `protobuf_from_json`
- `decode_wrapped_message`, `encode_wrapped_message`
- `decode_grpc_message`, `encode_grpc_message`
- `NAME_REGEX` (internal constant; never intended for external use)
- `MutableTypeDef`, `MutableFieldDef` (merged into `TypeDef`/`FieldDef`)
- `Config.preserve_field_order` (removed; field_order is now always preserved
  in the TypeDef object when available)
