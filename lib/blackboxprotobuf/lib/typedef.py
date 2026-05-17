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

import six
from blackboxprotobuf.lib.exceptions import (
    BlackboxProtobufException,
    EncoderException,
    TypedefException,
)

if six.PY3:
    import typing

    if typing.TYPE_CHECKING:
        from typing import Dict, Optional, Any, List, TypedDict, Tuple, Union, Iterator
        from typing import ItemsView, KeysView, ValuesView
        from blackboxprotobuf.lib.config import Config
        from .pytypes import TypeDefDict, FieldDefDict


# ---------------------------------------------------------------------------
# _ImmutableTypeDef — read-only base used as the internal decoder parameter type.
# Decoder functions are typed to accept _ImmutableTypeDef, forcing them to call
# make_mutable() before writing. Users always hold the mutable TypeDef subclass.
# ---------------------------------------------------------------------------

class _ImmutableTypeDef(object):
    def __init__(self):
        # type: () -> None
        self._fields = {}  # type: Dict[str, FieldDef]
        self._field_names = {}  # type: Dict[str, str]  # name -> field_id

    # ------------------------------------------------------------------ #
    # Serialization (read side)
    # ------------------------------------------------------------------ #

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Serialise to a plain dict.

        Emits canonical string keys. Does NOT include internal decoder state
        (field_order) or annotations (example_value_ignored).
        """
        return {
            field_id: fielddef.to_dict()
            for field_id, fielddef in self._fields.items()
        }

    # ------------------------------------------------------------------ #
    # Key helpers
    # ------------------------------------------------------------------ #

    def _normalize_key(self, key):
        # type: (object) -> str
        return six.ensure_text(str(key))

    def _resolve_key(self, key):
        # type: (object) -> str
        """Resolve a field number (int or str) or field name to a field_id."""
        s = self._normalize_key(key)
        base = s.split("-", 1)[0]
        return self._field_names.get(base, base)

    # ------------------------------------------------------------------ #
    # Read-only mapping interface
    # ------------------------------------------------------------------ #

    def __getitem__(self, field_number):
        # type: (object) -> _ImmutableFieldDef
        field_id = self._resolve_key(field_number)
        if field_id in self._fields:
            return self._fields[field_id]
        raise KeyError(field_number)

    def __contains__(self, field_number):
        # type: (object) -> bool
        field_id = self._resolve_key(field_number)
        return field_id in self._fields

    def __iter__(self):
        # type: () -> Iterator[str]
        return iter(self._fields)

    def __len__(self):
        # type: () -> int
        return len(self._fields)

    def get(self, field_number, default=None):
        # type: (object, object) -> object
        try:
            return self[field_number]
        except KeyError:
            return default

    def items(self):
        # type: () -> ItemsView[str, _ImmutableFieldDef]
        return self._fields.items()

    def keys(self):
        # type: () -> KeysView[str]
        return self._fields.keys()

    def values(self):
        # type: () -> ValuesView[_ImmutableFieldDef]
        return self._fields.values()

    def is_empty(self):
        # type: () -> bool
        return len(self._fields) == 0

    # ------------------------------------------------------------------ #
    # Internal: used by the decoder
    # ------------------------------------------------------------------ #

    def make_mutable(self):
        # type: () -> TypeDef
        """Return a shallow mutable copy (copy-on-write pattern for the decoder)."""
        mutable = TypeDef()
        mutable._fields = self._fields.copy()
        mutable._field_names = self._field_names.copy()
        return mutable

    def lookup_fielddef(self, key):
        # type: (str) -> Optional[Tuple[str, _ImmutableFieldDef]]
        """Look up a FieldDef by field number string or name (with optional alt suffix)."""
        field_name = key.split("-", 1)[0]
        field_id = self._field_names.get(field_name, field_name)
        if field_id in self._fields:
            return field_id, self._fields[field_id]
        return None

    def lookup_fielddef_number(self, field_id):
        # type: (str) -> Optional[Tuple[str, _ImmutableFieldDef]]
        if field_id in self._fields:
            return field_id, self._fields[field_id]
        return None


# ---------------------------------------------------------------------------
# TypeDef — public mutable type. Extends _ImmutableTypeDef with write methods.
# Users always receive and manipulate TypeDef objects.
# ---------------------------------------------------------------------------

class TypeDef(_ImmutableTypeDef):

    # ------------------------------------------------------------------ #
    # Serialization (write side)
    # ------------------------------------------------------------------ #

    @staticmethod
    def from_dict(typedef_dict):
        # type: (Dict[str, Any]) -> TypeDef
        """Build a TypeDef from a dict.

        Accepts both the legacy format (message_typedef, alt_typedefs,
        message_type_name, seen_repeated, field_order, example_value_ignored)
        and the new format (alts, type_ref, repeated, inline sub-fields).
        Integer field-number keys are coerced to strings.
        String shorthand values (e.g. "string") expand to {"type": "string"}.
        Legacy state fields (field_order, example_value_ignored) are silently
        ignored — they are internal decoder state, not persisted schema.
        """
        typedef = TypeDef()
        for field_id, fielddef_dict in typedef_dict.items():
            field_id = six.ensure_text(str(field_id))
            if isinstance(fielddef_dict, six.string_types):
                fielddef_dict = {"type": fielddef_dict}
            fielddef = FieldDef.from_dict(fielddef_dict, field_id)
            typedef._fields[field_id] = fielddef
            if fielddef._name:
                typedef._field_names[fielddef._name] = field_id
        return typedef

    # ------------------------------------------------------------------ #
    # Mutable mapping interface
    # ------------------------------------------------------------------ #

    def __getitem__(self, field_number):
        # type: (object) -> FieldDef
        field_id = self._resolve_key(field_number)
        if field_id in self._fields:
            return self._fields[field_id]
        raise KeyError(field_number)

    def __setitem__(self, field_number, value):
        # type: (object, object) -> None
        """Assign a FieldDef to a field number.

        value may be:
          - a FieldDef instance
          - a dict (passed to FieldDef.from_dict)
          - a str (type shorthand, e.g. "string")
        """
        field_id = self._normalize_key(field_number)
        if isinstance(value, six.string_types):
            value = FieldDef.from_dict({"type": value}, field_id)
        elif isinstance(value, dict):
            value = FieldDef.from_dict(value, field_id)
        elif isinstance(value, FieldDef):
            value._field_id = field_id
        else:
            raise TypeError(
                "Expected FieldDef, dict, or str; got %s" % type(value).__name__
            )
        old = self._fields.get(field_id)
        if old and old._name:
            self._field_names.pop(old._name, None)
        self._fields[field_id] = value
        if value._name:
            self._field_names[value._name] = field_id

    def __delitem__(self, field_number):
        # type: (object) -> None
        field_id = self._resolve_key(field_number)
        if field_id not in self._fields:
            raise KeyError(field_number)
        fielddef = self._fields.pop(field_id)
        if fielddef._name:
            self._field_names.pop(fielddef._name, None)

    def items(self):
        # type: () -> ItemsView[str, FieldDef]
        return self._fields.items()

    def values(self):
        # type: () -> ValuesView[FieldDef]
        return self._fields.values()

    # ------------------------------------------------------------------ #
    # Internal write method used by the decoder (on mutable copies only)
    # ------------------------------------------------------------------ #

    def set_fielddef(self, field_number, fielddef):
        # type: (str, FieldDef) -> None
        field_id = six.ensure_text(str(field_number))
        self._fields[field_id] = fielddef
        if fielddef._name:
            self._field_names[fielddef._name] = field_id

    def lookup_fielddef(self, key):
        # type: (str) -> Optional[Tuple[str, FieldDef]]
        field_name = key.split("-", 1)[0]
        field_id = self._field_names.get(field_name, field_name)
        if field_id in self._fields:
            return field_id, self._fields[field_id]
        return None

    def lookup_fielddef_number(self, field_id):
        # type: (str) -> Optional[Tuple[str, FieldDef]]
        if field_id in self._fields:
            return field_id, self._fields[field_id]
        return None

    # ------------------------------------------------------------------ #
    # Convenience mutators (public API)
    # ------------------------------------------------------------------ #

    def set_type(self, field_number, type_name):
        # type: (object, str) -> None
        """Set the type of a field by number or name."""
        self[field_number].type = type_name

    def set_name(self, field_number, name):
        # type: (object, Optional[str]) -> None
        """Rename a field, keeping the name-lookup cache consistent."""
        field_id = self._normalize_key(field_number)
        fielddef = self[field_id]
        if fielddef._name:
            self._field_names.pop(fielddef._name, None)
        fielddef._name = name if name else None
        if name:
            self._field_names[six.ensure_text(str(name))] = field_id

    def update(self, field_number, **kwargs):
        # type: (object, **Any) -> None
        """Update one or more attributes on a FieldDef without replacing it.

        Supported kwargs: type, name, repeated, message_typedef, type_ref.
        Routing name changes through this method keeps _field_names consistent.
        """
        field_id = self._normalize_key(field_number)
        for key, value in kwargs.items():
            if key == "name":
                self.set_name(field_id, value)
            elif key == "type":
                self[field_id].type = value
            elif key == "repeated":
                self[field_id].repeated = value
            elif key == "message_typedef":
                self[field_id].message_typedef = value
            elif key == "type_ref":
                self[field_id].type_ref = value
            else:
                raise TypeError("Unknown FieldDef attribute: %s" % key)

    def copy(self):
        # type: () -> TypeDef
        """Shallow copy: new dict referencing the same FieldDef objects."""
        new = TypeDef()
        new._fields = self._fields.copy()
        new._field_names = self._field_names.copy()
        return new


# ---------------------------------------------------------------------------
# _ImmutableFieldDef — read-only base used as the internal decoder parameter
# type. Decoder functions accept _ImmutableFieldDef and must call make_mutable()
# to obtain a writable FieldDef before modifying it.
# ---------------------------------------------------------------------------

class _ImmutableFieldDef(object):
    def __init__(self, field_id=""):
        # type: (str) -> None
        self._name = None  # type: Optional[str]
        self._field_id = field_id  # type: str
        self._message_type_name = None  # type: Optional[str]
        # _types["0"] = primary type (str or TypeDef); "1", "2", ... = alts
        self._types = {}  # type: Dict[str, Union[str, TypeDef]]
        self._example_value = None  # type: Any
        self._seen_repeated = False  # type: bool
        self._field_order = None  # type: Optional[List[str]]

    # ------------------------------------------------------------------ #
    # Serialization (read side)
    # ------------------------------------------------------------------ #

    def to_dict(self):
        # type: () -> Dict[str, Any]
        """Serialise to a plain dict.

        Emits the legacy key names (message_typedef, alt_typedefs,
        message_type_name, seen_repeated) for compatibility with existing
        consumers. Does NOT emit field_order — that is internal decoder state
        embedded in the TypeDef object and not persisted across serialization.
        """
        fielddef_dict = {}  # type: Dict[str, Any]
        if self._name:
            fielddef_dict["name"] = self._name
        if self._message_type_name:
            fielddef_dict["message_type_name"] = self._message_type_name
        if self._seen_repeated:
            fielddef_dict["seen_repeated"] = self._seen_repeated

        field_type = self._types.get("0")
        if isinstance(field_type, TypeDef):
            fielddef_dict["type"] = "message"
            fielddef_dict["message_typedef"] = field_type.to_dict()
        elif field_type is not None:
            fielddef_dict["type"] = field_type

        if len(self._types) > 1:
            fielddef_dict["alt_typedefs"] = {
                alt_num: (
                    alt_val.to_dict() if isinstance(alt_val, TypeDef) else alt_val
                )
                for alt_num, alt_val in self._types.items()
                if alt_num != "0"
            }

        return fielddef_dict

    # ------------------------------------------------------------------ #
    # Read-only properties
    # ------------------------------------------------------------------ #

    @property
    def type(self):
        # type: () -> Optional[str]
        """The field's primary type name (e.g. 'string', 'int', 'message')."""
        t = self._types.get("0")
        if isinstance(t, TypeDef):
            return "message"
        return t

    @property
    def name(self):
        # type: () -> str
        """The field's name, falling back to the field number string."""
        if self._name:
            return self._name
        return self._field_id

    @property
    def repeated(self):
        # type: () -> bool
        """True if this field has been confirmed as a repeated (list) field."""
        return self._seen_repeated

    @property
    def message_typedef(self):
        # type: () -> Optional[TypeDef]
        """The inline sub-typedef for message fields; None for scalar fields."""
        t = self._types.get("0")
        if isinstance(t, TypeDef):
            return t
        return None

    @property
    def type_ref(self):
        # type: () -> Optional[str]
        """Reference to a named typedef in config.known_types (alias: message_type_name)."""
        return self._message_type_name

    # ------------------------------------------------------------------ #
    # Read-only navigation (delegates to message_typedef)
    # ------------------------------------------------------------------ #

    def __getitem__(self, field_number):
        # type: (object) -> FieldDef
        td = self.message_typedef
        if td is None:
            raise TypeError(
                "Field %r is not a message type; cannot index into it" % self._field_id
            )
        return td[field_number]

    # ------------------------------------------------------------------ #
    # Internal: used by the decoder (all reads)
    # ------------------------------------------------------------------ #

    def make_mutable(self):
        # type: () -> FieldDef
        """Return a shallow mutable copy for decoder use."""
        mutable = FieldDef(self._field_id)
        mutable._name = self._name
        mutable._message_type_name = self._message_type_name
        mutable._types = self._types.copy()
        mutable._example_value = self._example_value
        mutable._seen_repeated = self._seen_repeated
        mutable._field_order = self._field_order
        return mutable

    def lookup_field_type(self, key, config, field_path):
        # type: (str, Config, List[str]) -> Optional[Union[str, TypeDef]]
        if "-" in key:
            alt_type_id = key.split("-", 1)[1]
        else:
            alt_type_id = "0"
        return self.lookup_field_type_number(alt_type_id, config, field_path)

    def lookup_field_type_number(self, alt_type_id, config, field_path):
        # type: (str, Config, List[str]) -> Optional[Union[str, TypeDef]]
        if alt_type_id not in self._types:
            return None
        field_type = self._types[alt_type_id]
        if field_type == "message":
            return self.resolve_message_type_name(config, field_path)
        return field_type

    @property
    def field_order(self):
        # type: () -> Optional[List[str]]
        return self._field_order

    @property
    def seen_repeated(self):
        # type: () -> bool
        """Alias for repeated; kept for internal decoder compatibility."""
        return self._seen_repeated

    def field_key(self, alt_field_id):
        # type: (str) -> str
        if alt_field_id == "0":
            return self.name
        return self.name + "-" + alt_field_id

    def next_alt_type_id(self):
        # type: () -> str
        existing_ids = [int(alt_type_id) for alt_type_id in self._types.keys()]
        if not existing_ids:
            return "0"
        return six.ensure_text(str(max(existing_ids) + 1))

    def resolve_message_type_name(self, config, field_path):
        # type: (Config, List[str]) -> TypeDef
        if self._message_type_name not in config.known_types:
            raise TypedefException(
                "Message name '%s' has not been defined in known types"
                % self._message_type_name,
                field_path,
            )
        return TypeDef.from_dict(config.known_types[self._message_type_name])

    def resolve_types(self, config, field_path):
        # type: (Config, List[str]) -> Dict[str, Union[str, TypeDef]]
        field_types = self._types.copy()
        if field_types.get("0") == "message":
            field_types["0"] = self.resolve_message_type_name(config, field_path)
        return field_types

    def alt_id_for_type(self, field_type):
        # type: (object) -> Optional[str]
        for alt_id, alt_type in self._types.items():
            if not isinstance(alt_type, TypeDef) and field_type == alt_type:
                return alt_id
        return None


# ---------------------------------------------------------------------------
# FieldDef — public mutable type. Extends _ImmutableFieldDef with property
# setters and write methods. Users always receive and manipulate FieldDef objects.
# ---------------------------------------------------------------------------

class FieldDef(_ImmutableFieldDef):

    # ------------------------------------------------------------------ #
    # Serialization (write side)
    # ------------------------------------------------------------------ #

    @staticmethod
    def from_dict(fielddef_dict, field_id=""):
        # type: (Dict[str, Any], str) -> FieldDef
        """Build a FieldDef from a dict.

        Accepts both the legacy format and the new inline-sub-fields format.
        Keys partitioned on isdigit(): digit keys are sub-message fields,
        non-digit keys are metadata (type, name, alts/alt_typedefs,
        type_ref/message_type_name, repeated/seen_repeated).

        Legacy state fields (field_order, example_value_ignored) are silently ignored;
        field_order is internal decoder state, not persisted across serialization.
        """
        fielddef = FieldDef(field_id)

        # Partition: digit keys -> sub-fields, non-digit keys -> metadata
        sub_fields = {}  # type: Dict[str, Any]
        meta = {}  # type: Dict[str, Any]
        for key, value in fielddef_dict.items():
            key_str = six.ensure_text(str(key))
            if key_str.isdigit():
                sub_fields[key_str] = value
            else:
                meta[key_str] = value

        # Legacy 'message_typedef' key: merge its contents as sub-fields
        has_message_typedef = "message_typedef" in meta
        if has_message_typedef:
            for k, v in meta["message_typedef"].items():
                sub_fields[six.ensure_text(str(k))] = v

        field_type = meta.get("type")

        if sub_fields:
            fielddef._types["0"] = TypeDef.from_dict(sub_fields)
        elif has_message_typedef:
            # message_typedef was explicitly present but empty: create empty TypeDef
            fielddef._types["0"] = TypeDef()
        elif field_type is not None:
            fielddef._types["0"] = field_type

        if "name" in meta:
            fielddef._name = meta["name"] or None

        # type_ref (new) or message_type_name (legacy)
        type_ref = meta.get("type_ref") or meta.get("message_type_name")
        if type_ref:
            fielddef._message_type_name = type_ref

        # alts (new) or alt_typedefs (legacy)
        alts = meta.get("alts") or meta.get("alt_typedefs")
        if alts:
            for alt_num, alt_val in alts.items():
                alt_num_s = six.ensure_text(str(alt_num))
                if isinstance(alt_val, dict):
                    fielddef._types[alt_num_s] = TypeDef.from_dict(alt_val)
                else:
                    fielddef._types[alt_num_s] = alt_val

        # repeated (new) or seen_repeated (legacy)
        if "repeated" in meta:
            fielddef._seen_repeated = bool(meta["repeated"])
        elif "seen_repeated" in meta:
            fielddef._seen_repeated = bool(meta["seen_repeated"])

        # field_order and example_value_ignored: silently ignored

        return fielddef

    # ------------------------------------------------------------------ #
    # Mutable properties (redeclared to add setters)
    # ------------------------------------------------------------------ #

    @property
    def type(self):
        # type: () -> Optional[str]
        t = self._types.get("0")
        if isinstance(t, TypeDef):
            return "message"
        return t

    @type.setter
    def type(self, value):
        # type: (str) -> None
        if value == "message" and isinstance(self._types.get("0"), TypeDef):
            pass  # preserve existing inline message_typedef
        else:
            self._types["0"] = value

    @property
    def name(self):
        # type: () -> str
        if self._name:
            return self._name
        return self._field_id

    @name.setter
    def name(self, value):
        # type: (Optional[str]) -> None
        """Set the field's name directly.

        Note: use TypeDef.set_name() to also update the parent typedef's
        name-lookup cache. Direct assignment here does NOT update that cache.
        """
        self._name = value if value else None

    @property
    def repeated(self):
        # type: () -> bool
        return self._seen_repeated

    @repeated.setter
    def repeated(self, value):
        # type: (bool) -> None
        self._seen_repeated = bool(value)

    @property
    def message_typedef(self):
        # type: () -> Optional[TypeDef]
        t = self._types.get("0")
        if isinstance(t, TypeDef):
            return t
        return None

    @message_typedef.setter
    def message_typedef(self, value):
        # type: (Optional[TypeDef]) -> None
        if value is None:
            if isinstance(self._types.get("0"), TypeDef):
                del self._types["0"]
        else:
            self._types["0"] = value

    @property
    def type_ref(self):
        # type: () -> Optional[str]
        return self._message_type_name

    @type_ref.setter
    def type_ref(self, value):
        # type: (Optional[str]) -> None
        self._message_type_name = value

    # ------------------------------------------------------------------ #
    # Mutable navigation
    # ------------------------------------------------------------------ #

    def __getitem__(self, field_number):
        # type: (object) -> FieldDef
        td = self.message_typedef
        if td is None:
            raise TypeError(
                "Field %r is not a message type; cannot index into it" % self._field_id
            )
        return td[field_number]

    def __setitem__(self, field_number, value):
        # type: (object, object) -> None
        if self.message_typedef is None:
            self._types["0"] = TypeDef()
        td = self.message_typedef
        assert td is not None
        td[field_number] = value

    # ------------------------------------------------------------------ #
    # Internal write methods used by the decoder (on mutable copies only)
    # ------------------------------------------------------------------ #

    def set_field_order(self, field_order):
        # type: (List[str]) -> None
        self._field_order = field_order

    def mark_repeated(self):
        # type: () -> None
        self._seen_repeated = True

    def set_type(self, alt_type_id, field_type):
        # type: (str, Union[str, TypeDef]) -> None
        self._types[alt_type_id] = field_type

    def set_types(self, types):
        # type: (Dict[str, Union[str, TypeDef]]) -> None
        self._types = types

    def add_type(self, field_type):
        # type: (Union[str, TypeDef]) -> str
        alt_type_id = None
        if not isinstance(field_type, TypeDef):
            alt_type_id = self.alt_id_for_type(field_type)
        if alt_type_id is None:
            alt_type_id = self.next_alt_type_id()
            self.set_type(alt_type_id, field_type)
        return alt_type_id
