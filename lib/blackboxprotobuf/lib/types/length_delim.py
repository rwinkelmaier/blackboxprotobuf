"""Module for encoding and decoding length delimited fields"""

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

import binascii
import string
import copy
import sys
import six
import logging

import blackboxprotobuf.lib
from blackboxprotobuf.lib.types import varint, wiretypes
from blackboxprotobuf.lib.exceptions import (
    EncoderException,
    DecoderException,
    TypedefException,
    BlackboxProtobufException,
)
from blackboxprotobuf.lib.typedef import (
    _ImmutableTypeDef,
    _MutableInternalTypeDef,
    TypeDef,
    _ImmutableFieldDef,
    _MutableInternalFieldDef,
    FieldDef,
)

if six.PY3:
    import typing

    if typing.TYPE_CHECKING:
        from blackboxprotobuf.lib.config import Config
        from typing import Any, Callable, Dict, Tuple, Optional, List, ByteString
        from blackboxprotobuf.lib.pytypes import Message

logger = logging.getLogger(__name__)


def encode_string(value):
    # type: (Any) -> bytearray
    """Encode a string as a length delimited byte array"""
    try:
        value = six.ensure_text(value)
    except TypeError as exc:
        six.raise_from(
            EncoderException("Error encoding string to message: %r" % value), exc
        )
    return encode_bytes(value)


def encode_bytes(value):
    # type: (Any) -> bytearray
    """Encode a length delimited byte array"""
    if isinstance(value, six.text_type):
        try:
            value = six.ensure_binary(value)
        except TypeError as exc:
            six.raise_from(
                EncoderException("Error encoding bytes to message: %r" % value), exc
            )

    if not isinstance(value, (bytes, bytearray)):
        raise EncoderException(
            "encode_bytes must receive a bytes or bytearray value: %s %r"
            % (type(value), value)
        )
    encoded_length = varint.encode_varint(len(value))
    return encoded_length + value


def decode_bytes(buf, pos):
    # type: (bytes, int) -> Tuple[bytes, int]
    """Decode a length delimited bytes array from buf"""
    length, pos = varint.decode_uvarint(buf, pos)
    end = pos + length
    try:
        return buf[pos:end], end
    except IndexError as exc:
        six.raise_from(
            DecoderException(
                (
                    "Error decoding bytes. Decoded length %d is longer than bytes"
                    " available %d"
                )
                % (length, len(buf) - pos)
            ),
            exc,
        )


def encode_bytes_hex(value):
    # type: (Any) -> bytearray
    """Encode a length delimited byte array represented by a hex string"""
    try:
        return encode_bytes(binascii.unhexlify(value))
    except (TypeError, binascii.Error) as exc:
        six.raise_from(
            EncoderException("Error encoding hex bytestring %s" % value), exc
        )


def decode_bytes_hex(buf, pos):
    # type: (bytes, int) -> Tuple[bytes, int]
    """Decode a length delimited byte array from buf and return a hex encoded string"""
    value, pos = decode_bytes(buf, pos)
    return binascii.hexlify(value), pos


def decode_string(value, pos):
    # type: (bytes, int) -> Tuple[str, int]
    """Decode a length delimited byte array as a string"""
    length, pos = varint.decode_uvarint(value, pos)
    end = pos + length
    try:
        # backslash escaping isn't reversible easily
        return value[pos:end].decode("utf-8"), end
    except (TypeError, UnicodeDecodeError) as exc:
        six.raise_from(
            DecoderException("Error decoding UTF-8 string %r" % value[pos:end]), exc
        )


def encode_tag(field_number, wire_type):
    # type: (int, int) -> bytearray
    # Not checking bounds here, should be check before
    tag_number = (field_number << 3) | wire_type
    return varint.encode_uvarint(tag_number)


def decode_tag(buf, pos):
    # type: (bytes, int) -> Tuple[int, int, int]
    tag_number, pos = varint.decode_uvarint(buf, pos)
    field_number = tag_number >> 3
    wire_type = tag_number & 0x7
    return field_number, wire_type, pos


def encode_message(data, config, typedef, path=None, field_order=None):
    # type: (Message, Config, _ImmutableTypeDef, Optional[List[str]], Optional[List[str]]) -> bytearray
    """Encode a Python dictionary to a binary protobuf message"""
    output = bytearray()
    if path is None:
        path = []

    output_len = 0
    field_outputs = {}  # type: Dict[str, List[Tuple[ByteString, ByteString]]]
    for field_id, value in data.items():
        field_number, outputs = _encode_message_field(
            config, typedef, path, field_id, value
        )

        field_outputs.setdefault(field_number, []).extend(outputs)
        output_len += len(outputs)

    if output_len > 0:
        if field_order is not None and len(field_order) == output_len:
            # check for old typedefs which had field_order as a tuple
            if isinstance(field_order[0], tuple):
                field_order = [x[0] for x in field_order]
            for field_number in field_order:
                try:
                    tag, field_output = field_outputs[field_number].pop(0)
                    output += tag
                    output += field_output
                except (IndexError, KeyError):
                    # If these don't match up despite us checking the overall
                    # length, then we probably have something weird going on
                    # with field naming.
                    # This might mean ordering is off from the original, but
                    # should break real protobuf messages
                    logger.warning(
                        "The field_order list does not match the fields from _encode_message_field"
                    )
                    # If we're hitting a mismatch between the field order and
                    # what data we have, then just bail. We can encode the rest
                    # normally
                    break

        # Group  together elements in an array
        for values in field_outputs.values():
            for tag, value in values:
                output += tag
                output += value

    return output


def _encode_message_field(config, typedef, path, field_id, value):
    # type: (Config, _ImmutableTypeDef, List[str], str | int, Any) -> Tuple[str, List[Tuple[ByteString, ByteString]]]

    if not isinstance(field_id, six.text_type):
        field_key = six.text_type(field_id)  # type: str
    else:
        field_key = field_id

    fielddef_results = typedef.lookup_fielddef(field_key)

    if fielddef_results is None:
        raise EncoderException(
            "Provided field name/number %s is not valid" % (field_key),
            path,
        )
    field_number, fielddef = fielddef_results

    field_path = path[:]
    field_path.append(str(field_number))

    field_type = fielddef.lookup_field_type(field_key, config, field_path)

    if field_type is None:
        raise EncoderException(
            "Provided field name/number %s / %s is not valid"
            % (field_key, field_number),
            field_path,
        )

    field_encoder = None  # type: Callable[[Any], ByteString] | None
    if isinstance(field_type, _ImmutableTypeDef):
        field_typedef = field_type
        field_type = "message"
        field_encoder = lambda data: encode_lendelim_message(
            data,
            config,
            field_typedef,
            path=field_path,
            field_order=fielddef.field_order,
        )
    else:
        if field_type not in blackboxprotobuf.lib.types.ENCODERS:
            raise TypedefException("Unknown type: %s" % field_type)
        field_encoder = blackboxprotobuf.lib.types.ENCODERS[field_type]
        if field_encoder is None:
            raise TypedefException(
                "Encoder not implemented for %s" % field_type, field_path
            )

    # Encode the tag
    tag = encode_tag(
        int(field_number), blackboxprotobuf.lib.types.WIRETYPES[field_type]
    )

    outputs = []  # type: list[Tuple[ByteString, ByteString]]
    try:
        # Repeated values we'll encode each one separately and add them to the outputs list
        # Packed values take in a list, but encode them into a single length
        # delimited field, so we handle those as a non-repeated value
        if isinstance(value, list) and not field_type.startswith("packed_"):
            for repeated in value:
                outputs.append((tag, field_encoder(repeated)))
        else:
            outputs.append((tag, field_encoder(value)))

    except EncoderException as exc:
        exc.set_path(field_path)
        six.reraise(*sys.exc_info())

    return field_number, outputs


def decode_message(buf, config, typedef=None, pos=0, end=None, depth=0, path=None):
    # type: (bytes, Config, Optional[_ImmutableTypeDef], int, Optional[int], int, Optional[List[str]]) -> Tuple[Message, _MutableInternalTypeDef, List[str], int]
    """Decode a protobuf message with no length prefix"""
    if end is None:
        end = len(buf)

    if typedef is None:
        typedef = TypeDef()

    if path is None:
        path = []

    if isinstance(buf, bytearray):
        buf = bytes(buf)

    output = {}  # type: Message
    seen_repeated = {}  # type: Dict[str, bool]
    mut_typedef = typedef.make_mutable()

    grouped_fields, field_order, pos = _group_by_number(buf, pos, end, path)
    for field_number, (wire_type, field_starts) in grouped_fields.items():
        # wire_type should already be validated by _group_by_number

        field_path = path[:] + [field_number]

        fielddef_pair = mut_typedef.lookup_fielddef_number(
            field_number
        )  # type: Optional[Tuple[str, _ImmutableFieldDef]]

        fielddef = (
            FieldDef(field_number) if fielddef_pair is None else fielddef_pair[1]
        )  # type: _ImmutableFieldDef

        # Decode messages (which may have multiple typedefs)  or unknown length delimited fields
        if wire_type == wiretypes.LENGTH_DELIMITED and not isinstance(
            fielddef.lookup_field_type_number("0", config, field_path), six.string_types
        ):
            output_map, new_fielddef = _try_decode_lendelim_fields(
                buf, field_starts, fielddef, config, field_path
            )
            if len(field_starts) > 1:
                new_fielddef.mark_repeated()

            # Merge length delim field into the output map
            for field_key, field_outputs in output_map.items():
                output.setdefault(field_key, []).extend(field_outputs)
            seen_repeated[fielddef.name] = new_fielddef.seen_repeated
            mut_typedef.set_fielddef(field_number, new_fielddef)
        else:
            field_outputs, new_fielddef, field_alt_type_id = _decode_standard_field(
                buf, wire_type, field_starts, fielddef, config, path
            )

            field_key = new_fielddef.field_key(field_alt_type_id)
            output.setdefault(field_key, []).extend(field_outputs)
            seen_repeated[fielddef.name] = new_fielddef.seen_repeated

            # Save the field typedef/type back to the typedef
            mut_typedef.set_fielddef(field_number, new_fielddef)

    _simplify_output(output, seen_repeated)
    return output, mut_typedef, field_order, pos


def _decode_standard_field(buf, wire_type, field_starts, fielddef, config, field_path):
    # type: (bytes, int, List[int], _ImmutableFieldDef, Config, List[str]) -> Tuple[List[Any], _MutableInternalFieldDef, str]
    field_outputs = None
    field_alt_type_id = None
    for alt_type_id, field_type in fielddef.resolve_types(config, field_path).items():
        if isinstance(field_type, _ImmutableTypeDef):
            # Skip message types
            continue
        if (
            not isinstance(field_type, six.string_types)
            or blackboxprotobuf.lib.types.WIRETYPES[field_type] != wire_type
        ):
            raise DecoderException(
                "Type %s from typedef did not match wiretype %s"
                % (field_type, wire_type),
                path=field_path,
            )

        if field_type not in blackboxprotobuf.lib.types.DECODERS:
            raise TypedefException(
                "Type %s does not have a decoder" % (field_type),
                path=field_path,
            )
        decoder = blackboxprotobuf.lib.types.DECODERS[field_type]
        try:
            field_outputs = [
                decoder(buf, field_start)[0] for field_start in field_starts
            ]
            field_alt_type_id = alt_type_id
        except BlackboxProtobufException as exc:
            # Error decoding, try next one if we have one
            continue
        # Decoding worked
        break

    if field_outputs is None:
        field_type = config.get_default_type(wire_type)
        default_decoder = blackboxprotobuf.lib.types.DECODERS[field_type]

        field_outputs = [
            default_decoder(buf, field_start)[0] for field_start in field_starts
        ]

    mut_fielddef = fielddef.make_mutable()
    if field_alt_type_id is None:
        field_alt_type_id = mut_fielddef.next_alt_type_id()

    mut_fielddef.set_type(field_alt_type_id, field_type)

    if field_outputs is None:
        raise DecoderException(
            "Unable to decode wire_type %s" % (wire_type),
            path=field_path,
        )
    if isinstance(field_type, six.string_types) and field_type.startswith("packed_"):
        # Packed decoding will return a list of lists
        field_outputs = [y for x in field_outputs for y in x]
        mut_fielddef.mark_repeated()
    # Mark repeated if we have have more than one
    # Don't need to worry if it's already repeated
    elif len(field_outputs) > 1:
        mut_fielddef.mark_repeated()

    return field_outputs, mut_fielddef, field_alt_type_id


def _simplify_output(output, seen_repeated):
    # type: (Message, Dict[str, bool]) -> None
    # If any outputs only have one element, convert them from a list to solo
    # Mutates output
    for field_key, field_outputs in output.items():
        if isinstance(field_outputs, list) and len(field_outputs) == 1:
            field_name = (
                field_key.split(six.u("-"), 1)[0]
                if isinstance(field_key, six.string_types)
                else six.text_type(field_key)
            )
            if not seen_repeated[field_name]:
                output[field_key] = field_outputs[0]


def _group_by_number(buf, pos, end, path):
    # type: (bytes, int, int, List[str]) -> Tuple[Dict[str, Tuple[int, List[int]]], List[str], int]
    # Parse through the whole message and split into buffers based on wire
    # type and organized by field number. This forces us to parse the whole
    # message at once, but I think we're doing that anyway. This catches size
    # errors early as well, which is usually the best indicator of if it's a
    # protobuf message or not.
    # Returns a dictionary like:
    #     {
    #         "2": (<wiretype>, [<data>])
    #     }

    output_map = {}  # type: Dict[str, Tuple[int, List[int]]]
    field_order = []
    while pos < end:
        # Read in a field
        field_number, wire_type, pos = decode_tag(buf, pos)
        if pos == end:
            # Every wire type except groups require some bytes after the tag
            raise DecoderException(
                "Bytestring does not have sufficient bytes after protobuf tag"
            )

        # We want field numbers as strings everywhere
        field_id = six.text_type(field_number)

        field_path = path[:] + [field_id]

        if field_id in output_map and output_map[field_id][0] != wire_type:
            # This should never happen
            raise DecoderException(
                "Field %s has mistmatched wiretypes. Previous: %s Now: %s"
                % (field_id, output_map[field_id][0], wire_type),
                path=field_path,
            )

        length = None
        if wire_type == wiretypes.VARINT:
            byte_pos = pos
            max_pos = min(pos + varint.MAX_VARINT_LEN, end)
            while six.indexbytes(buf, byte_pos) & 0x80:
                byte_pos += 1
                if byte_pos >= max_pos:
                    raise DecoderException(
                        "Byte position exceeded message length while decoding. Protobuf message is invalid"
                    )
            byte_pos += 1
            length = byte_pos - pos
        elif wire_type == wiretypes.FIXED32:
            length = 4
        elif wire_type == wiretypes.FIXED64:
            length = 8
        elif wire_type == wiretypes.LENGTH_DELIMITED:
            # Read the length from the start of the message
            # add on the length of the length tag as well
            bytes_length, new_pos = varint.decode_uvarint(buf, pos)
            length = bytes_length + (new_pos - pos)
        elif wire_type in [
            wiretypes.START_GROUP,
            wiretypes.END_GROUP,
        ]:
            raise DecoderException("GROUP wire types not supported", path=field_path)
        else:
            raise DecoderException(
                "Got unknown wire type: %d" % wire_type, path=field_path
            )
        if pos + length > end:
            raise DecoderException(
                "Decoded length for field %s goes over end: %d > %d"
                % (field_id, pos + length, end),
                path=field_path,
            )

        if field_id in output_map:
            output_map[field_id][1].append(pos)
        else:
            output_map[field_id] = (wire_type, [pos])
        field_order.append(field_id)

        assert length >= 0
        pos += length
    return output_map, field_order, pos


_PRINTABLE_CHARS = set(string.digits + string.ascii_letters + string.punctuation)


def _is_printable_py2(value):
    # type: (str) -> bool
    return all(c in _PRINTABLE_CHARS for c in value)


# string.isprintable is much quicker in python3, but not available in python2 or jython
_is_printable = (
    _is_printable_py2 if six.PY2 else lambda x: x.isprintable()
)  # type: Callable[[str], bool]


def _try_decode_lendelim_fields(buf, field_starts, fielddef, config, path):
    # type: (bytes, List[int], _ImmutableFieldDef, Config, List[str]) -> Tuple[Message, _MutableInternalFieldDef]

    # Goals:
    #   Try to enforce a consistent type (but not typedef) across all fields we know about
    #   Allow different typedefs, as long as all fields are valid 'message' types
    #   Prefer printable strings before anonymous typedefs

    # Does not set seen_repeated on the field, caller must set this flag
    previous_message_types = [
        field_type
        for field_type in fielddef.resolve_types(config, path).items()
        if isinstance(field_type[1], _ImmutableTypeDef)
    ]  # type: List[Tuple[str, str | _ImmutableTypeDef]]

    # Step 1: Try decoding as printable string based on `isprintable()`. Note that this does not allow whitespace
    string_fields = None  # type: Optional[List[str]]
    string_decoding_failed = False
    if not previous_message_types:
        string_fields = []
        try:
            # No previous message types, so lets check if they're all text
            for field_start in field_starts:
                output, _ = decode_string(buf, field_start)
                string_fields.append(output)
            if all(_is_printable(field) for field in string_fields):
                # Everything is a printable string, return those strings
                output_fielddef = fielddef.make_mutable()
                string_alt_type_id = output_fielddef.add_type("string")

                field_key = output_fielddef.field_key(string_alt_type_id)
                message_output = {field_key: string_fields}  # type: Message

                # All fields successfully decoded as printable
                return message_output, output_fielddef
        except DecoderException as exc:
            # Error decoding one of the fields as a string, we mark it as
            # failed so we don't try again later
            string_fields = None
            string_decoding_failed = True

    # Step 2: Try decoding as message
    try:
        message_output = {}
        output_fielddef = fielddef.make_mutable()
        for field_start in field_starts:
            message_field_output = None  # type: Optional[Message]
            field_typedef = None
            alt_type_id = None
            # Step 2.1 Try decoding with existing message type
            for alt_type_id, field_type in previous_message_types:
                if not isinstance(field_type, _ImmutableTypeDef):
                    # shouldn't happen because we filtered earlier
                    continue
                try:
                    (
                        message_field_output,
                        field_typedef,
                        _,  # We ignore field order if we're going based on an existing typedef
                        _,
                    ) = decode_lendelim_message(
                        buf, config, field_type, pos=field_start, path=path
                    )
                    break
                except Exception as exc:
                    assert isinstance(
                        exc, DecoderException
                    )  # TODO should always get decoder exceptions, but maybe we'll get some surprises we want to catch
                    # If we get an exception, then this isn't the right typedef, try the next
                    continue

            if message_field_output is None:
                (
                    message_field_output,
                    field_typedef,
                    field_order,
                    _,
                ) = decode_lendelim_message(
                    buf, config, None, pos=field_start, path=path
                )
                # Save the field typedef
                alt_type_id = output_fielddef.add_type(field_typedef)
                output_fielddef.set_field_order(field_order)

                # Add this to previous_message_types so other fields can use it
                previous_message_types.append((alt_type_id, field_typedef))

            field_key = output_fielddef.field_key(alt_type_id)
            message_output.setdefault(field_key, []).append(message_field_output)

        # All the fields decoded as a message
        return message_output, output_fielddef

    except DecoderException as exc:
        # we hit an error decoding with an anonymous typedef, therefore field
        # is not a message type
        pass

    # Step 3: Fallback to string and binary types

    # Step 3.1: Check for string type
    try:
        if not string_decoding_failed:
            if not string_fields:
                string_fields = []
                for field_start in field_starts:
                    output, _ = decode_string(buf, field_start)
                    string_fields.append(output)
            output_fielddef = fielddef.make_mutable()
            alt_type_id = output_fielddef.add_type("string")
            field_key = output_fielddef.field_key(alt_type_id)
            message_output = {field_key: string_fields}
            return message_output, output_fielddef

    except DecoderException:
        # String decoding failed for at least one string
        pass

    # Step 3.2: Check config.default_binary_type
    try:
        outputs = []
        target_type = config.default_binary_type
        decoder = blackboxprotobuf.lib.types.DECODERS[target_type]
        for field_start in field_starts:
            output, _ = decoder(buf, field_start)
            outputs.append(output)
        output_fielddef = fielddef.make_mutable()
        alt_type_id = output_fielddef.add_type(target_type)

        field_key = output_fielddef.field_key(alt_type_id)
        output_message_binary = {field_key: outputs}  # type: Message

        return output_message_binary, output_fielddef

    except DecoderException:
        # The decoder failed for at least one field, try the next decoder
        pass

    # Step 3.3: Fall back to bytes as last resort
    # In most cases, bytes will already by tested via
    # `config.default_binary_type`
    outputs_bytes = []  # type: List[bytes]
    for field_start in field_starts:
        output_bytes, _ = decode_bytes(buf, field_start)
        outputs_bytes.append(output_bytes)
    output_fielddef = fielddef.make_mutable()
    alt_type_id = output_fielddef.add_type("bytes")

    field_key = output_fielddef.field_key(alt_type_id)
    message_output_bytes = {field_key: outputs_bytes}  # type: Message

    return message_output_bytes, output_fielddef


def encode_lendelim_message(data, config, typedef, path=None, field_order=None):
    # type: (Message, Config, _ImmutableTypeDef, Optional[List[str]], Optional[List[str]]) -> ByteString
    """Encode data as a length delimited protobuf message"""
    message_out = encode_message(
        data, config, typedef, path=path, field_order=field_order
    )
    length = varint.encode_varint(len(message_out))
    return length + message_out


def decode_lendelim_message(buf, config, typedef=None, pos=0, depth=0, path=None):
    # type: (bytes, Config, Optional[_ImmutableTypeDef], int, int, Optional[List[str]]) -> Tuple[Message, _MutableInternalTypeDef, List[str], int]
    """Deocde a length delimited protobuf message from buf"""
    length, pos = varint.decode_uvarint(buf, pos)
    end = pos + length
    if end > len(buf):
        raise DecoderException(
            "Bytestring is an invalid lenght delimited messages. The length prefix (%s) is longer than the buffer (%s)"
            % (end, len(buf))
        )
    ret = decode_message(buf, config, typedef, pos, end, depth=depth, path=path)
    return ret


def generate_packed_encoder(wrapped_encoder):
    # type: (Callable[[Any], ByteString]) -> Callable[[List[Any]], bytearray]
    """Generate an encoder for a packed type based on a base type encoder"""

    def length_wrapper(values):
        # type: (List[Any]) -> bytearray
        # Encode repeat values and prefix with the length
        output = bytearray()
        for value in values:
            output += wrapped_encoder(value)
        length = varint.encode_varint(len(output))
        return length + output

    return length_wrapper


def generate_packed_decoder(wrapped_decoder):
    # type: (Callable[[bytes, int], Tuple[Any, int]]) -> Callable[[bytes, int], Tuple[List[Any], int]]
    """Generate an decoder for a packed type based on a base type decoder"""

    def length_wrapper(buf, pos):
        # type: (bytes, int) -> Tuple[List[Any], int]
        # Decode repeat values prefixed with the length
        length, pos = varint.decode_uvarint(buf, pos)
        end = pos + length
        output = []
        while pos < end:
            value, pos = wrapped_decoder(buf, pos)
            output.append(value)
        if pos > end:
            raise DecoderException(
                (
                    "Error decoding packed field. Packed length larger than"
                    " buffer: decoded = %d, left = %d"
                )
                % (length, len(buf) - pos)
            )
        return output, pos

    return length_wrapper
