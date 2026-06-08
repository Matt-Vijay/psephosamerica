from __future__ import annotations

from src.graph.entity_resolution.ids import stable_id


def test_stable_id_is_deterministic() -> None:
    assert stable_id(["a", "b"], "xx") == stable_id(["a", "b"], "xx")


def test_stable_id_has_prefix_and_is_lowercase_base32() -> None:
    out = stable_id(["a"], "ce")
    assert out.startswith("ce-")
    body = out[3:]
    assert body == body.lower()
    assert set(body) <= set("abcdefghijklmnopqrstuvwxyz234567")


def test_stable_id_separator_is_unambiguous() -> None:
    # The "|" join means ["a","b"] and ["a|b"] are NOT distinguished, but
    # differently-grouped real inputs that never contain "|" stay distinct.
    assert stable_id(["ab", "c"], "p") != stable_id(["a", "bc"], "p")


def test_stable_id_order_sensitive() -> None:
    assert stable_id(["a", "b"], "p") != stable_id(["b", "a"], "p")
