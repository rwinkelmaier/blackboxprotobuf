# BlackBox Protobuf Library

## Description
Blackbox protobuf library is a Python module for decoding and re-encoding protobuf
messages without access to the source protobuf descriptor file. This library
provides a simple Python interface to encode/decode messages that can be
integrated into other tools.

This library is targeted towards use in penetration testing where being able to
modify messages is critical and a protocol buffer definition may not be readily
available.

## Background
Protocol Buffers (protobufs)  are a standard published by Google with
accompanying libraries for binary serialization of data. Protocol buffers are
defined by a `.proto` file known to both the sender and the receiver. The actual
binary message does not contain information such as field names or most type
information.

For each field, the serialized protocol buffer includes two pieces of metadata,
a field number and the wire type. The wire type tells a parser how to parse the
length of the field, so that it can be skipped if it is not known (one protocol
buffer design goal is being able to handle messages with unknown fields). A
single wire-type generally encompasses multiple protocol buffer types, for
example the length delimited wire-type can be used for string, bytestring,
inner message or packed repeated fields. See
<https://developers.google.com/protocol-buffers/docs/encoding#structure> for
the breakdown of wire types.

The protocol buffer compiler (`protoc`) does support a similar method of
decoding protocol buffers without the definition with the `--decode_raw`
option. However, it does not provide any functionality to re-encode the decoded
message.

## How it works
The library makes a best effort guess of the type based on the provided wire type (and
occasionally field content) and builds a type definition that can be used to
re-encode the data. In general, most fields of interest are likely to be parsed
into a usable form. Users can optionally pass in custom type definitions that
override the guessed type. Custom type definitions also allow naming of fields to
improve user friendliness.

# Usage
## Installation    
The package can be installed from source with:

```
poetry install
```

BlackBox Protobuf is also available on PyPi at <https://pypi.org/project/bbpb>.
It can be installed with:

```
pip install bbpb
```

## CLI
The package defines a `bbpb` command for command line encoding/decoding.

For command line usage see [CLI.md](./CLI.md).

## Interface
The main `blackboxprotobuf` module defines an API with the core encode/decode
message functions, along with several convenience functions to make it easier
to use blackboxprotobuf with a user interface, such as encoding/decoding
directly to JSON and validating modified type definitions.

### Decode
`decode()` takes a protobuf bytestring and returns a `DecodeResult`. You can
optionally pass a type definition (as a `TypeDef` object, a dict, or a known
message type name). If none is provided, all types are inferred from the binary.

The `encoding` parameter controls outer encoding: `"none"` (raw protobuf),
`"gzip"`, `"grpc"`, or `"auto"` (detect automatically, the default).

```python
import blackboxprotobuf
import base64

data = base64.b64decode('KglNb2RpZnkgTWU=')
result = blackboxprotobuf.decode(data, encoding="none")
print(result.message)    # single decoded message dict
print(result.typedef)    # TypeDef object describing the schema
```

### Encode
`encode()` takes a message dict (or list of dicts for gRPC) and a type
definition, and returns bytes.

```python
import blackboxprotobuf
import base64

data = base64.b64decode('KglNb2RpZnkgTWU=')
result = blackboxprotobuf.decode(data, encoding="none")

result.messages[0]["5"] = "Modified Me"
new_data = blackboxprotobuf.encode(result.messages, result.typedef, encoding="none")
print(new_data)
```

### Type definition structure
The type definition object is a Python dictionary representing the type
structure of a message, it includes a type for each field and optionally a
name. Each entry in the dictionary represents a field in the message. The key
should be the field number and the value is a dictionary containing attributes.

At the minimum the dictionary should contain the 'type' entry which contains a
string identifier for the type. Valid type identifiers can be found in
`blackboxprotobuf/lib/types/type_maps.py`.

Message fields will also contain one of two entries, 'message_typedef' or
'message_type_name'. 'message_typedef' should contain a second type definition
structure for the inner message. 'message_type_name' should contain the string
identifier for a message type previously stored in
`blackboxprotobuf.known_messages`. If both are specified, the 'message_type_name'
will be ignored.

### JSON Encode/Decode

`decode_to_json()` and `encode_from_json()` are convenience functions for
encoding/decoding messages to JSON instead of a Python dictionary. They
automatically sort the output, encode bytestrings for display, and collect
example values in `result.annotations`.

### Export/import protofile

`export_protofile` and `import_protofile` convert a `.proto` file to/from
the blackboxprotobuf type definition format. These functions do not implement
a full parser and may break on some types. In particular, `import` statements
in `.proto` files are not supported — any imported files must be processed
separately with `import_protofile` first.


### Validate Typedef

The `validate_typedef` function is designed to sanity check modified type
definitions and make sure they are internally consistent and consistent with
the previous type definition (if provided). This should help catch issues such
as changing a field to an incompatible type or duplicate field names.

### Output Helper Functions

`sort_typedef(typedef)` sorts the fields of a typedef dict for more readable
output. Fields are sorted by field number; within each field, metadata keys
(name, type) appear before long nested content.

### Config

Many of the functions accept a `config` keyword argument of the
`blackboxprotobuf.lib.config.Config` class. The config object allows modifying
some of the encoding/decoding functionality and storing some state. This
replaces some variables that were global before.

At the moment this includes:

* `known_types` - Mapping of message type names to typedef (previously
  `blackboxprotobuf.known_messages`)

* `default_binary_type` - Change the default type choice for binary fields when
  decoding previously unknown fields. Defaults to `bytes` but can be set to
  `bytes_hex` to return a hex encoded string instead. `bytes_base64` might be
  another option in the future. The type can always be changed for an
  individual field by changing the `type` in the typedef.

* `default_types` - Change the default type choice for any wiretype when
  decoding a previously unknown field. For example, to default to unsigned
  integers for all varints, set `default_types[WIRETYPE_VARINT] =
  'uint'`.

All API functions default to using the global `blackboxprotobuf.lib.config.default`
object if no `config=` is specified.

## Type Breakdown
The following is a quick breakdown of wire types and default values. See
<https://developers.google.com/protocol-buffers/docs/encoding> for more detailed
information from Google.

### Variable Length Integers (varint)
The `varint` wire type represents integers with multiple bytes where one bit of
each is dedicated to indicating if it is the last byte. This can be used to
represent integers (signed/unsigned), boolean values or enums. Integers can be
encoded using three variations:

- `uint`: Varint encoding with no representation of negative numbers.
- `int`: Standard encoding but inefficient for negative numbers (always 10 bytes).
- `sint`: Uses ZigZag encoding to efficiently represent negative numbers by
  mapping negative numbers into the integer space. For example -1 is converted
  to 1, 1 to 2, -2 to 3, and so on. This can result in drastically different
  numbers if a type is misinterpreted and either the original or incorrect type
  is `sint`.

The default is currently `int` with no ZigZag encoding.

### Fixed32/64
The fixed length wire types have an implicit size based on the wire type. These
support either fixed size integers (signed/unsigned) or fixed size floating
point numbers (float/double). The default type for these is the floating point
type as most integers are more likely to be represented by a varint.

### Length Delimited
Length delimited wire types are prefixed with a `varint` indicating the length.
This is used for strings, bytestrings, inner messages and packed repeated
fields. Messages can generally be identified by validating if it is a valid
protobuf binary. If it is not a message, the default type is a string/byte
which are relatively interchangeable in Python. A different default type (such
as `bytes_hex`) can be specified by changing
`blackboxprotobuf.lib.types.default_binary_type`.

Packed repeated fields are arrays of either `varints` or a fixed length wire
type. Non-packed repeated fields use a separate tag (wire type + field number)
for each element, allowing them to be easily identified and parsed. However,
packed repeated fields only have the initial length delimited wire type tag.
The parser is assumed to know the full type already for parsing out the
individual elements. This makes this field type difficult to differentiate from
an arbitrary byte string and will require user intervention to identify. In
protobuf version 2, repeated fields had to be explicitly declared packed in the
definition. In protobuf version 3, repeated fields are packed by default and
are likely to become more common.
