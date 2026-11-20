import pytest

from steward_boundary import PROTOCOL
from steward_boundary.canonical import canonical, sha256_hex


def test_canonical_is_sorted_compact_utf8():
    assert canonical({"b": 1, "a": ["é", None, True]}) == '{"a":["é",null,true],"b":1}'.encode()


def test_the_same_value_always_hashes_the_same():
    assert sha256_hex({"x": 1, "y": 2}) == sha256_hex({"y": 2, "x": 1})
    assert len(sha256_hex({})) == 64


def test_nan_is_refused():
    with pytest.raises(ValueError):
        canonical({"x": float("nan")})


def test_protocol_version():
    assert PROTOCOL == 3      # 3: a tool result's `ticked` (public issue #53)
