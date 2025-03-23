"""Tests focused on common API behavior"""

import pytest

import blackboxprotobuf
from blackboxprotobuf.lib.exceptions import TypedefException


def test_encode_empty_typedef():
    # Only allow empty typedef for empty message for an encoder

    empty_typedefs = [{}, "", None]
    for typedef in empty_typedefs:
        typedef = {}
        message = {}
        payload = blackboxprotobuf.encode_message(message, typedef)
        assert len(payload) == 0

        payload = blackboxprotobuf.encode_wrapped_message([message], typedef, "none")
        assert len(payload) == 0

        payload = blackboxprotobuf.protobuf_from_json("{}", typedef)
        assert len(payload) == 0

        message = {"1": 0}
        with pytest.raises(TypedefException):
            payload = blackboxprotobuf.encode_message(message, typedef)
        with pytest.raises(TypedefException):
            payload = blackboxprotobuf.protobuf_from_json('{"1": 0}', typedef)
        with pytest.raises(TypedefException):
            payload = blackboxprotobuf.encode_wrapped_message(
                [message], typedef, "none"
            )


def test_invalid_typedef_string():
    # String typedefs must exist in config

    message = {}
    typedef = "test123"
    with pytest.raises(TypedefException):
        payload = blackboxprotobuf.encode_message(message, typedef)
    with pytest.raises(TypedefException):
        payload = blackboxprotobuf.protobuf_from_json("{}", typedef)
    with pytest.raises(TypedefException):
        payload = blackboxprotobuf.encode_wrapped_message([message], typedef, "none")
