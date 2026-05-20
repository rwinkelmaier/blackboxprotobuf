"""The `blackboxprotobuf.lib.api` module provides high level functions for
decoding and re-encoding protobuf messages.

Most functions take the input data, a type definition and a config object.

The 'message_type' or type definition (typedef) is a blackboxprotobuf specific format
which defines which types should be used for decoding/encoding each field. It
is optional for decoding functions but required for encoding funtions. The
decoding function will return a typedef that is require to re-encode the array.
If a typedef was provided during decoding, then those types will be used for
decoding and the typedef return will be the original typedef + any new fields
in the message.

The config argument is the Config object from `blackboxprotobuf.lib.config` and
allows reconfiguring default types and stores "known" message typedefs that can
be referenced within other typedefs. This argument can be left out to use a
default shared config object.
"""

# Copyright (c) 2018-2024 NCC Group Plc
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import re
import six
import json
import collections
import blackboxprotobuf.lib.protofile
import blackboxprotobuf.lib.types.length_delim
import blackboxprotobuf.lib.types.type_maps
from blackboxprotobuf.lib.config import default as default_config
from blackboxprotobuf.lib.exceptions import (
    BlackboxProtobufException,
    TypedefException,
    EncoderException,
    DecoderException,
)
from blackboxprotobuf.lib.typedef import (
    _ImmutableTypeDef,
    TypeDef,
    FieldDef,
    _to_public_typedef,
)
from blackboxprotobuf.lib import payloads

__all__ = [
    # Primary API (v2)
    "decode",
    "encode",
    "decode_to_json",
    "encode_from_json",
    "TypeDef",
    "FieldDef",
    "DecodeResult",
    "JSONDecodeResult",
    "validate_typedef",
    "sort_typedef",
    "export_protofile",
    "import_protofile",
    # Exceptions
    "BlackboxProtobufException",
    "TypedefException",
    "EncoderException",
    "DecoderException",
]

_DecodeResultBase = collections.namedtuple(  # type: ignore[name-match]
    "DecodeResult", ["messages", "typedef", "encoding", "annotations"]
)


class DecodeResult(_DecodeResultBase):
    """Result of a decode operation.

    Attributes:
        messages: list of decoded message dicts (length 1 for none/gzip,
            may be > 1 for grpc).
        typedef: TypeDef object representing the inferred/provided schema.
        encoding: the outer encoding that was used ("none", "gzip", "grpc").
        annotations: dict of example values keyed by field path, extracted
            from the first decoded message. Previously embedded in the typedef
            as `example_value_ignored`.
    """

    @property
    def message(self):
        # type: () -> object
        """Return the single decoded message. Raises ValueError if > 1 message."""
        if len(self.messages) != 1:
            raise ValueError(
                "DecodeResult has %d messages; use .messages to access them"
                % len(self.messages)
            )
        return self.messages[0]

    def re_encode(self, encoding=None, config=None):
        # type: (Optional[str], Optional[Config]) -> bytes
        """Re-encode the decoded messages using the embedded typedef.

        Args:
            encoding: output encoding override. Defaults to the encoding used
                during decode (self.encoding).
            config: optional Config override.
        Returns:
            Encoded bytes with the given outer encoding applied.
        """
        if config is None:
            from blackboxprotobuf.lib.config import default as default_config

            config = default_config
        out_encoding = encoding if encoding is not None else self.encoding
        encoded = [
            bytes(
                blackboxprotobuf.lib.types.length_delim.encode_message(
                    msg, config, self.typedef
                )
            )
            for msg in self.messages
        ]
        if len(encoded) == 1 and out_encoding != "grpc":
            return payloads.encode_payload(encoded[0], out_encoding)
        return payloads.encode_payload(encoded, out_encoding)


_JSONDecodeResultBase = collections.namedtuple(  # type: ignore[name-match]
    "JSONDecodeResult", ["messages_json", "typedef", "encoding", "annotations"]
)


class JSONDecodeResult(_JSONDecodeResultBase):
    """Result of a JSON-decode operation (decode_to_json).

    Attributes:
        messages_json: JSON string representing the messages. Always a JSON
            array (even for single messages) for consistency.
        typedef: TypeDef object representing the inferred/provided schema.
        encoding: the outer encoding that was used ("none", "gzip", "grpc").
        annotations: dict of example values keyed by field path.
    """

    @property
    def message_json(self):
        # type: () -> str
        """Return the single message as a JSON object string (not array).

        Raises ValueError if there is more than one message.
        """
        parsed = json.loads(self.messages_json)
        if not isinstance(parsed, list) or len(parsed) != 1:
            raise ValueError(
                "JSONDecodeResult has multiple messages; use .messages_json directly"
            )
        return json.dumps(parsed[0], indent=2)


if six.PY3:
    import typing

    # Circular imports on Config if we don't check here
    if typing.TYPE_CHECKING:
        from typing import Any, Dict, List, Tuple, Optional, ByteString, Union
        from blackboxprotobuf.lib.pytypes import Message, TypeDefDict, FieldDefDict
        from blackboxprotobuf.lib.config import Config


# ---------------------------------------------------------------------------
# Primary API: decode() / encode() / decode_to_json() / encode_from_json()
# ---------------------------------------------------------------------------


def decode(data, message_type=None, encoding="auto", config=None):
    # type: (bytes, Optional[Union[str, Dict[str, Any], TypeDef]], str, Optional[Config]) -> DecodeResult
    """Decode a protobuf message payload, returning a DecodeResult.

    This is the primary entry point for decoding. It replaces the older
    decode_message / decode_wrapped_message pair by accepting an explicit
    encoding parameter.

    Args:
        data: bytes containing the (optionally wrapped) protobuf payload.
        message_type: Optional typedef hint - a TypeDef, a dict in typedef
            format, a known-type name string, or None to auto-detect.
        encoding: Outer wrapping to strip before decoding the protobuf bytes.
            "auto" (default) - try each algorithm in order.
            "none"           - raw protobuf bytes.
            "gzip"           - gzip-compressed single message.
            "grpc"           - gRPC framing (may contain multiple messages).
        config: Optional Config object. Defaults to the global default.
    Returns:
        DecodeResult with .messages (list), .typedef (TypeDef), .encoding,
        and .annotations.
    """
    if config is None:
        config = default_config

    if isinstance(data, bytearray):
        data = bytes(data)
    data = six.ensure_binary(data)

    typedef = _resolve_typedef(message_type, config)
    resolve_encoding = encoding if encoding != "auto" else None

    if resolve_encoding is None:
        decoders = payloads.find_decoders(data)
        detected_encoding = None
        for decoder in decoders:
            try:
                protobuf_datas, detected_encoding = decoder(data)
            except BlackboxProtobufException:
                continue
            try:
                values = []
                decoder_typedef = typedef  # type: _ImmutableTypeDef
                for protobuf_data in protobuf_datas:
                    (
                        value,
                        decoder_typedef,
                        _,
                        _,
                    ) = blackboxprotobuf.lib.types.length_delim.decode_message(
                        protobuf_data, config, decoder_typedef
                    )
                    values.append(value)
                public_typedef = _to_public_typedef(decoder_typedef)
                annotations = _collect_annotations(public_typedef.to_dict(), values[0])
                return DecodeResult(
                    messages=values,
                    typedef=public_typedef,
                    encoding=detected_encoding,
                    annotations=annotations,
                )
            except BlackboxProtobufException as exc:
                if detected_encoding == "none":
                    six.raise_from(
                        DecoderException(
                            "Unable to decode protobuf message with any encoding algorithm"
                        ),
                        exc,
                    )
                continue
        raise DecoderException(
            "Unable to decode protobuf message with any encoding algorithm"
        )
    else:
        protobuf_datas, detected_encoding = payloads.decode_payload(
            data, resolve_encoding
        )
        values = []
        decoder_typedef = typedef
        for protobuf_data in protobuf_datas:
            (
                value,
                decoder_typedef,
                _,
                _,
            ) = blackboxprotobuf.lib.types.length_delim.decode_message(
                protobuf_data, config, decoder_typedef
            )
            values.append(value)
        public_typedef = _to_public_typedef(decoder_typedef)
        annotations = _collect_annotations(public_typedef.to_dict(), values[0])
        return DecodeResult(
            messages=values,
            typedef=public_typedef,
            encoding=detected_encoding,
            annotations=annotations,
        )


def encode(message, message_type, encoding="none", config=None):
    # type: (object, Union[str, Dict[str, Any], TypeDef], str, Optional[Config]) -> bytes
    """Encode one or more message dicts as a protobuf payload.

    This is the primary entry point for encoding. It replaces the older
    encode_message / encode_wrapped_message pair.

    Args:
        message: A single message dict, or a list of message dicts (for grpc).
        message_type: TypeDef, dict typedef, or known-type name string.
        encoding: Outer wrapping to apply after encoding.
            "none" (default) - raw protobuf bytes.
            "gzip"           - gzip-compressed single message.
            "grpc"           - gRPC framing.
        config: Optional Config object. Defaults to the global default.
    Returns:
        Encoded bytes with the outer encoding applied.
    """
    if config is None:
        config = default_config

    typedef = _resolve_typedef(message_type, config)
    if typedef.is_empty() and (
        (isinstance(message, list) and any(len(m) > 0 for m in message))
        or (isinstance(message, dict) and len(message) > 0)
    ):
        raise TypedefException("A typedef is required to encode non-empty messages")

    messages = message if isinstance(message, list) else [message]
    encoded = [
        bytes(
            blackboxprotobuf.lib.types.length_delim.encode_message(msg, config, typedef)
        )
        for msg in messages
    ]

    if len(encoded) == 1 and encoding != "grpc":
        return payloads.encode_payload(encoded[0], encoding)
    return payloads.encode_payload(encoded, encoding)


def decode_to_json(data, message_type=None, encoding="auto", indent=2, config=None):
    # type: (bytes, Optional[Union[str, Dict[str, Any], TypeDef]], str, int, Optional[Config]) -> JSONDecodeResult
    """Decode a protobuf payload and return a JSON string.

    Thin wrapper around decode() that applies _json_safe_transform and
    _sort_output so bytes fields are represented as latin1 strings and fields
    are sorted by field number for readability.

    Args:
        data: bytes containing the (optionally wrapped) protobuf payload.
        message_type: Optional typedef hint. See decode() for details.
        encoding: Outer encoding ("auto", "none", "gzip", "grpc").
        indent: JSON indentation level (default 2). Pass None for compact.
        config: Optional Config object.
    Returns:
        JSONDecodeResult with .messages_json (always a JSON array), .typedef,
        .encoding, and .annotations. Use .message_json for the single-message
        JSON object string.
    """
    if config is None:
        config = default_config

    result = decode(data, message_type, encoding=encoding, config=config)
    typedef_dict = result.typedef.to_dict()

    json_messages = []
    for msg in result.messages:
        msg = _json_safe_transform(msg, typedef_dict, False, config=config)
        msg = _sort_output(msg, typedef_dict, config=config)
        json_messages.append(msg)

    return JSONDecodeResult(
        messages_json=json.dumps(json_messages, indent=indent),
        typedef=result.typedef,
        encoding=result.encoding,
        annotations=result.annotations,
    )


def encode_from_json(json_str, message_type, encoding="none", config=None):
    # type: (str, Union[str, Dict[str, Any], TypeDef], str, Optional[Config]) -> bytes
    """Re-encode a JSON string as a protobuf payload.

    Accepts the JSON string produced by decode_to_json (or any compatible
    format). The JSON may be a single message object or an array of messages.

    Args:
        json_str: JSON string to re-encode. May be an object or array.
        message_type: TypeDef, dict typedef, or known-type name string.
        encoding: Outer encoding to apply ("none", "gzip", "grpc").
        config: Optional Config object.
    Returns:
        Encoded bytes with the outer encoding applied.
    """
    if config is None:
        config = default_config

    typedef = _resolve_typedef(message_type, config)
    typedef_dict = typedef.to_dict()

    value = json.loads(json_str)
    values = value if isinstance(value, list) else [value]
    if typedef.is_empty() and any(len(v) > 0 for v in values):
        raise TypedefException("A typedef is required to encode non-empty messages")

    values = [_json_safe_transform(msg, typedef_dict, True) for msg in values]
    encoded = [
        bytes(
            blackboxprotobuf.lib.types.length_delim.encode_message(msg, config, typedef)
        )
        for msg in values
    ]

    if len(encoded) == 1 and encoding != "grpc":
        return payloads.encode_payload(encoded[0], encoding)
    return payloads.encode_payload(encoded, encoding)


def _collect_annotations(typedef_dict, message):
    # type: (Dict[str, Any], Any) -> Dict[str, Any]
    """Collect example values from the first decoded message, keyed by field path.

    This replaces the old _annotate_typedef approach of embedding example
    values directly in the typedef dict. The annotations are now returned
    as a separate dict from decode().
    """
    annotations = {}  # type: Dict[str, Any]
    _collect_annotations_recursive(typedef_dict, message, annotations, [])
    return annotations


def _collect_annotations_recursive(typedef_dict, message, annotations, path):
    # type: (Dict[str, Any], Any, Dict[str, Any], List[str]) -> None
    for field_number, field_def in typedef_dict.items():
        field_name = field_def.get("name", "") or field_number
        if field_name not in message:
            field_name = field_number
        if field_name not in message:
            continue
        field_value = message[field_name]
        current_path = path + [field_name]
        if field_def.get("type") == "message" and "message_typedef" in field_def:
            sub_typedef = field_def["message_typedef"]
            if isinstance(field_value, list):
                for item in field_value:
                    if isinstance(item, dict):
                        _collect_annotations_recursive(
                            sub_typedef, item, annotations, current_path
                        )
            elif isinstance(field_value, dict):
                _collect_annotations_recursive(
                    sub_typedef, field_value, annotations, current_path
                )
        else:
            annotations[".".join(current_path)] = field_value


def export_protofile(message_types, output_filename):
    # type: (Dict[str, TypeDefDict], str) -> None
    """This function attempts to export a set of message type definitions to a
    `.proto` file for use with other tools.

    Args:
        message_types: Python dictionary containing the type definitions to
            export. The dictionary should contain the message type name as the
            key and the type definition as the value.
        output_filename: String representing the filename to output the
            protobuf definition file to.
    """
    blackboxprotobuf.lib.protofile.export_proto(
        message_types, output_filename=output_filename
    )


def import_protofile(input_filename, save_to_known=True, config=None):
    # type: (str, bool, Optional[Config]) -> Dict[str, TypeDefDict] | None
    """This function attempts to import a set of message type definitions from a
    `.proto` file.

    This is a convenience function for `blackboxprotobuf.lib.protofile`. The
    protobuf file import support is not complete and may fail for some type
    definitions.

    Args:
        input_filename: Filename to read the protobuf definitions from.
        save_to_known: If True, this function will save the message type
            definitions to `config.known_types`. Otherwise, it will return them
            to the caller. Defaults to `True`.
        config: Optional config object which stores the `known_types` map.
            Defaults to `blackboxprotobuf.lib.config.default`.
    Returns:
        If `save_to_known` is False, then the type definitions read from the
        file are returned as a dictionary, with the type names as the keys and
        type definitions as the values.
    """
    if config is None:
        config = default_config

    new_typedefs = blackboxprotobuf.lib.protofile.import_proto(
        config, input_filename=input_filename
    )
    if save_to_known:
        config.known_types.update(new_typedefs)
        return None
    else:
        return new_typedefs


_NAME_REGEX = re.compile(r"\A[a-zA-Z][a-zA-Z0-9_]*\Z")


def validate_typedef(typedef, old_typedef=None, config=None, _path=None):
    # type: (TypeDefDict, Optional[TypeDefDict], Optional[Config], Optional[List[str]]) -> None
    """Attempt to validate a type definition object is valid.

    This function attempts to ensure a type definition is valid before it is
    used to encode/decode a message. This will make sure the field names are
    valid and field names/numbers are consistent. It is intended to be called
    after a user has edited the type definition to ensure the edits are valid.

    Args:
        typedef: The type definition object to validate. This should be a
            python dict derived from the dict returned  by a decode function.
        old_typedef: Optionally provide a old version of the type definition to
            compare the new type definnition to. If provided, this function
            will ensure any type changes are valid. For example, a field with a
            varint type can be changed to other varint types, but not a string
            or float.
        config: Optionally provide a config object which contains the
            `known_types` map to map message type names to known type definitions.
            Defaults to `blackboxprotobuf.lib.config.default`.
    Raises:
        TypedefException: Raises a TypedefException if the provided type
            definition is not valid.
    """
    if _path is None:
        _path = []
    if config is None:
        config = default_config

    int_keys = set()
    field_names = set()
    for field_number, field_typedef in typedef.items():
        alt_field_number = None
        if "-" in str(field_number):
            field_number, alt_field_number = field_number.split("-")

        # Validate field_number is a number
        if not str(field_number).isdigit():
            raise TypedefException("Field number must be a digit: %s" % field_number)
        field_number = six.ensure_text(str(field_number))

        field_path = _path[:]
        field_path.append(field_number)

        # Check for duplicate field numbers
        if field_number in int_keys:
            raise TypedefException(
                "Duplicate field number: %s" % field_number, field_path
            )
        int_keys.add(field_number)

        # Must have a type field
        if "type" not in field_typedef:
            raise TypedefException(
                "Field number must have a type value: %s" % field_number, field_path
            )
        if alt_field_number is not None:
            if field_typedef["type"] != "message":
                raise TypedefException(
                    "Alt field number (%s) specified for non-message field: %s"
                    % (alt_field_number, field_number),
                    field_path,
                )

        valid_type_fields = [
            "type",
            "name",
            "message_typedef",
            "message_type_name",
            "alt_typedefs",
            "seen_repeated",
        ]
        for key, value in field_typedef.items():
            # Check field keys against valid values
            if key not in valid_type_fields:
                raise TypedefException(
                    'Invalid field key "%s" for field number %s' % (key, field_number),
                    field_path,
                )
            if (
                key in ["message_typedef", "message_type_name"]
                and not field_typedef["type"] == "message"
            ):
                raise TypedefException(
                    'Invalid field key "%s" for field number %s' % (key, field_number),
                    field_path,
                )
            if key == "group_typedef" and not field_typedef["type"] == "group":
                raise TypedefException(
                    'Invalid field key "%s" for field number %s' % (key, field_number),
                    field_path,
                )

            # Validate type value
            if key == "type":
                if value not in blackboxprotobuf.lib.types.type_maps.WIRETYPES:
                    raise TypedefException(
                        'Invalid type "%s" for field number %s' % (value, field_number),
                        field_path,
                    )
            # Check for duplicate names
            if key == "name":
                if not isinstance(value, six.string_types):
                    raise TypedefException(
                        "Invalid type for name field in typedef: %r. Field number %s"
                        % (value, field_number),
                        field_path,
                    )
                if value.strip() == "":
                    continue

                if value.lower() in field_names:
                    raise TypedefException(
                        ('Duplicate field name "%s" for field ' "number %s")
                        % (value, field_number),
                        field_path,
                    )
                if not _NAME_REGEX.match(value):
                    raise TypedefException(
                        (
                            'Invalid field name "%s" for field '
                            "number %s. Should match %s"
                        )
                        % (value, field_number, "[a-zA-Z_][a-zA-Z0-9_]*"),
                        field_path,
                    )
                field_names.add(value.lower())

            # Check if message type name is known
            if key == "message_type_name":
                if value not in config.known_types:
                    raise TypedefException(
                        (
                            'Message type "%s" for field number'
                            " %s is not known. Known types: %s"
                        )
                        % (value, field_number, config.known_types.keys()),
                        field_path,
                    )

            # Recursively validate inner typedefs
            if key == "message_typedef":
                if isinstance(value, dict):
                    if (
                        old_typedef is not None
                        and field_number in old_typedef
                        and key in old_typedef[field_number]
                    ):
                        validate_typedef(
                            value,
                            old_typedef=old_typedef[field_number]["message_typedef"],
                            _path=field_path,
                            config=config,
                        )
                    else:
                        validate_typedef(value, _path=field_path, config=config)
            if key == "alt_typedefs":
                for alt_field_number, alt_typedef in field_typedef[
                    "alt_typedefs"
                ].items():
                    if isinstance(alt_typedef, dict):
                        validate_typedef(alt_typedef, _path=field_path, config=config)

    if old_typedef is not None:
        wiretype_map = {}
        for field_number, value in old_typedef.items():
            wiretype_map[
                int(field_number)
            ] = blackboxprotobuf.lib.types.type_maps.WIRETYPES[value["type"]]
        for field_number, value in typedef.items():
            field_path = _path[:]
            field_path.append(str(field_number))
            if int(field_number) in wiretype_map:
                old_wiretype = wiretype_map[int(field_number)]
                if (
                    old_wiretype
                    != blackboxprotobuf.lib.types.type_maps.WIRETYPES[value["type"]]
                ):
                    raise TypedefException(
                        (
                            "Wiretype for field number %s does"
                            " not match old type definition"
                        )
                        % field_number,
                        field_path,
                    )


def _json_safe_transform(values, typedef, toBytes, config=None):
    # type: (Message, TypeDefDict, bool, Optional[Config]) -> Message
    # Python's JSON doesn't have a default way to handle 'bytes' types. To
    # handle this, we want some string like encoding which JSON can handle but
    # can also handle arbitrary bytes. This method get's more complicated than
    # just converting all bytes since on re-encoding we need to know which ones
    # were transformed and which are supposed to actually be strings

    # A built-for binary encoding method like hex or base64 would be 'proper',
    # but doesn't really give any information to a reader. In some cases, a
    # binary blob may have embedded strings or integer values that would be
    # beneficial to quickly skim.

    # This uses latin1 encoding because it can handle arbitrary bytes, prints
    # ASCII characters and can be decoded back to the same exact byte string.
    # It's possible I missed another encoding method that matches these
    # properties across python2.7 and python3.9, but had issues with some other
    # backslash escape mechanisms parsing back to bytes.

    if config is None:
        config = default_config
    name_map = {
        item["name"]: number
        for number, item in typedef.items()
        if ("name" in item and item["name"] != "")
    }
    if not isinstance(values, dict):
        # this function should only ever be called on a message, error out if
        # it is not one. This usually means a type got swapped around
        raise EncoderException(
            "Error performing _json_safe_transform on message. Field was expected to be a message but was not: %r"
            % values
        )
    for name, value in values.items():
        if isinstance(name, int):
            name = six.ensure_text(str(name))
        alt_number = None
        base_name = name
        if "-" in name:
            base_name, alt_number = name.split("-")

        if base_name in name_map:
            field_number = name_map[base_name]
        else:
            field_number = base_name

        if field_number not in typedef or "type" not in typedef[field_number]:
            raise EncoderException(
                "Field %r not found in typedef or does not have type attribute."
                % field_number
            )

        field_type = typedef[field_number]["type"]  # type: str | TypeDefDict
        if field_type == "message":
            field_typedef = _get_typedef_for_message(typedef[field_number], config)
            if alt_number is not None:
                # if we have an alt type, then let's look that up instead
                if alt_number not in typedef[field_number].get("alt_typedefs", {}):
                    raise TypedefException(
                        (
                            "Provided alt field name/number "
                            "%s is not valid for field_number %s"
                        )
                        % (alt_number, field_number)
                    )
                field_type = typedef[field_number]["alt_typedefs"][alt_number]
                if isinstance(field_type, dict):
                    field_typedef = field_type
                    field_type = "message"

        is_list = isinstance(value, list)
        field_values = value if is_list else [value]
        for i, field_value in enumerate(field_values):
            if field_type == "bytes":
                if toBytes:
                    field_values[i] = field_value.encode("latin1")
                else:
                    field_values[i] = field_value.decode("latin1")
            elif field_type == "message":
                field_values[i] = _json_safe_transform(
                    field_value,
                    field_typedef,
                    toBytes,
                    config=config,
                )

        # convert back to single value if needed
        if not is_list:
            values[name] = field_values[0]
        else:
            values[name] = field_values
    return values


def _get_typedef_for_message(field_typedef, config):
    # type: (FieldDefDict, Config) -> TypeDefDict
    assert field_typedef["type"] == "message"
    if "message_typedef" in field_typedef:
        return field_typedef["message_typedef"]
    elif field_typedef.get("message_type_name"):
        if field_typedef["message_type_name"] not in config.known_types:
            raise TypedefException(
                "Got 'message_type_name' not in known_messages: %s"
                % field_typedef["message_type_name"]
            )
        return config.known_types[field_typedef["message_type_name"]]
    else:
        raise TypedefException(
            "Got 'message' type without typedef or type name: %s" % field_typedef
        )


def _sort_output(value, typedef, config=None):
    # type: (Message, TypeDefDict, Optional[Config]) -> Message
    # Sort output by the field number in the typedef. Helps with readability in
    # a JSON dump
    output_dict = collections.OrderedDict()  # type: Message
    if config is None:
        config = default_config

    # Make a list of all the field names we have, aggregate together the alt fields as well
    field_names = {}  # type: Dict[str, List[tuple[str, str | None]]]
    for field_name in value.keys():
        if isinstance(field_name, int):
            field_name = six.ensure_text(str(field_name))
        if "-" in field_name:
            field_name_base, alt_number = field_name.split("-")
        else:
            field_name_base = field_name
            alt_number = None
        field_names.setdefault(field_name_base, []).append((field_name, alt_number))

    for field_number, field_def in sorted(typedef.items(), key=lambda t: int(t[0])):
        field_number = six.ensure_text(str(field_number))
        seen_field_names = field_names.get(field_number, [])

        # Try getting matching fields by name as well
        if field_def.get("name", "") != "":
            field_name = field_def["name"]
            seen_field_names.extend(field_names.get(field_name, []))

        for field_name, alt_number in seen_field_names:
            field_type = field_def["type"]
            field_message_typedef = None
            if field_type == "message":
                field_message_typedef = _get_typedef_for_message(field_def, config)

            if alt_number is not None:
                if alt_number not in field_def["alt_typedefs"]:
                    raise TypedefException(
                        (
                            "Provided alt field name/number "
                            "%s is not valid for field_number %s"
                        )
                        % (alt_number, field_number)
                    )
                alt_field_type = field_def["alt_typedefs"][alt_number]
                if isinstance(alt_field_type, dict):
                    field_message_typedef = alt_field_type
                    field_type = "message"
                else:
                    field_type = alt_field_type

            if field_type == "message":
                if field_message_typedef is None:
                    raise TypedefException(
                        'Message does not have an associated typedef: "%s"' % field_name
                    )
                field_value = value.get(field_name)
                if isinstance(field_value, list):
                    output_dict[field_name] = []
                    for field_value_item in field_value:
                        if not isinstance(field_value_item, dict):
                            raise TypedefException(
                                'Message values must be a dictionary type. Field name: "%s"'
                                % field_name
                            )
                        output_dict[field_name].append(
                            _sort_output(field_value_item, field_message_typedef)
                        )
                else:
                    if not isinstance(field_value, dict):
                        raise TypedefException(
                            'Message values must be a dictionary type. Field name: "%s"'
                            % field_name
                        )
                    output_dict[field_name] = _sort_output(
                        field_value, field_message_typedef
                    )
            else:
                output_dict[field_name] = value[field_name]

    return output_dict


def sort_typedef(typedef):
    # type: (TypeDefDict) -> TypeDefDict
    """Apply special sorting rules to the type definition to improve readability.

    Sorts the fields of a type definition so that important fields such as the
    'type' or 'name' are at the top and don't get buried beneath longer fields
    like 'message_typedef'. This will also sort the keys of the
    'message_typedef' based on the field number.
    Args:
        typedef - dictionary representing a Blackboxprotobuf type definition
    Returns:
        A new OrderedDict object containing the contents of the typedef
        argument sorted for readability.
    """

    # Sort output by field number and sub_keys so name then type is first

    TYPEDEF_KEY_ORDER = [
        "name",
        "type",
        "message_type_name",
        "seen_repeated",
        "message_typedef",
        "alt_typedefs",
    ]
    output_dict = collections.OrderedDict()  # type: Any

    for field_number, field_def in sorted(
        typedef.items(), key=lambda t: int(t[0])
    ):  # Sort by type number
        output_field_def = collections.OrderedDict()  # type: Any
        for key, value in sorted(
            field_def.items(), key=lambda t: (TYPEDEF_KEY_ORDER.index(t[0]), t[1])
        ):  # sort by special keys, then value
            if key == "message_typedef":
                output_field_def[key] = sort_typedef(value)  # type: ignore[arg-type]
            else:
                output_field_def[key] = value

        output_dict[field_number] = output_field_def
    if six.PY3 and typing.TYPE_CHECKING:
        return typing.cast(
            TypeDefDict, output_dict
        )  # Cast because typing doesn't like the ordered dict
    return output_dict


def _resolve_typedef(message_type, config):
    # type: (Optional[str | TypeDefDict | TypeDef], Config) -> TypeDef
    # Takes a message_type which is either None, a dictionary representing a typedef, or a string referencing `Config`, and return the correct typedef
    # Raises an exception if message_type is str and not in Config
    # Returns an empty typedef if message_type is None or empty string

    if isinstance(message_type, TypeDef):
        return message_type
    elif message_type is None or message_type == "":
        return TypeDef()
    elif isinstance(message_type, dict):
        return TypeDef.from_dict(message_type)
    elif isinstance(message_type, six.string_types):
        if message_type in config.known_types:
            return TypeDef.from_dict(config.known_types[message_type])
        else:
            raise TypedefException(
                "message_type (%s) is not in config.known_types" % message_type
            )
    else:
        raise TypedefException(
            "message_type is not a valid type definition: %s" % message_type
        )
