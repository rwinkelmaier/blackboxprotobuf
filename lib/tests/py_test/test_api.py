"""Tests focused on common API behavior"""

import pytest

import blackboxprotobuf
from blackboxprotobuf import decode, encode, encode_from_json
from blackboxprotobuf.lib.exceptions import TypedefException


def test_encode_empty_typedef():
    # Only allow empty typedef for empty message for an encoder

    empty_typedefs = [{}, "", None]
    for typedef in empty_typedefs:
        typedef = {}
        message = {}
        payload = encode(message, typedef)
        assert len(payload) == 0

        payload = encode([message], typedef, encoding="none")
        assert len(payload) == 0

        payload = encode_from_json("{}", typedef)
        assert len(payload) == 0

        payload = encode_from_json("{}", typedef, encoding="none")
        assert len(payload) == 0

        message = {"1": 0}
        with pytest.raises(TypedefException):
            payload = encode(message, typedef)
        with pytest.raises(TypedefException):
            payload = encode_from_json('{"1": 0}', typedef)
        with pytest.raises(TypedefException):
            payload = encode([message], typedef, encoding="none")
        with pytest.raises(TypedefException):
            payload = encode_from_json('{"1": 0}', typedef, encoding="none")


def test_invalid_typedef_string():
    # String typedefs must exist in config

    message = {}
    typedef = "test123"
    with pytest.raises(TypedefException):
        payload = encode(message, typedef)
    with pytest.raises(TypedefException):
        payload = encode_from_json("{}", typedef)
    with pytest.raises(TypedefException):
        payload = encode([message], typedef, encoding="none")
    with pytest.raises(TypedefException):
        payload = encode_from_json("{}", typedef, encoding="none")
