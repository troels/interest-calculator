"""Tests for the disk cache."""

from __future__ import annotations

from loan.data.cache import Cache


def test_put_get_roundtrip(tmp_path):
    cache = Cache(tmp_path)
    assert cache.get("ns", "k") is None
    assert cache.has("ns", "k") is False
    cache.put("ns", "k", '{"a": 1}')
    assert cache.has("ns", "k") is True
    assert cache.get("ns", "k") == '{"a": 1}'


def test_key_with_slash_is_sanitised(tmp_path):
    cache = Cache(tmp_path)
    cache.put("dfbf", "2026/06/15", "x")
    # stored under a slash-free name, still retrievable by the same key
    assert cache.get("dfbf", "2026/06/15") == "x"


def test_namespaces_are_isolated(tmp_path):
    cache = Cache(tmp_path)
    cache.put("a", "k", "1")
    assert cache.get("b", "k") is None
