"""`core/build_cache.py`: the entry and byte limits every cartograph build
cache shares, and the digest its keys are made with.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

import pytest

pytest.importorskip(
    "qgis.core",
    reason="core/build_cache.py watches QGIS layers; this file runs in any QGIS Python",
)

from nika_onlymap_exporter.core.build_cache import (
    BoundedCache,
    UnvouchedValueError,
    value_digest,
)


class TestBoundedCache:
    def test_a_slot_answers_only_for_the_key_it_holds(self) -> None:
        cache: BoundedCache[str] = BoundedCache(4, 100)
        cache.put("frame", "key-1", "one", 1)
        assert cache.get("frame", "key-1") == "one"
        assert cache.get("frame", "key-2") is None
        assert cache.get("other", "key-1") is None

    def test_storing_a_slot_again_replaces_it(self) -> None:
        cache: BoundedCache[str] = BoundedCache(4, 100)
        cache.put("frame", "key-1", "one", 10)
        cache.put("frame", "key-2", "two", 20)
        assert len(cache) == 1
        assert cache.total_bytes == 20
        assert cache.get("frame", "key-1") is None
        assert cache.get("frame", "key-2") == "two"

    def test_past_the_entry_limit_the_least_recently_used_goes(self) -> None:
        cache: BoundedCache[str] = BoundedCache(2, 100)
        cache.put("a", "k", "a", 1)
        cache.put("b", "k", "b", 1)
        assert cache.get("a", "k") == "a"  # now the most recently used
        cache.put("c", "k", "c", 1)
        assert cache.get("b", "k") is None
        assert cache.get("a", "k") == "a"
        assert cache.get("c", "k") == "c"

    def test_past_the_byte_budget_the_oldest_go_until_it_fits(self) -> None:
        cache: BoundedCache[str] = BoundedCache(10, 100)
        cache.put("a", "k", "a", 40)
        cache.put("b", "k", "b", 40)
        cache.put("c", "k", "c", 40)
        assert cache.get("a", "k") is None
        assert cache.total_bytes == 80
        cache.put("d", "k", "d", 90)
        assert len(cache) == 1
        assert cache.get("d", "k") == "d"

    def test_one_result_over_the_whole_budget_is_not_kept(self) -> None:
        cache: BoundedCache[str] = BoundedCache(10, 100)
        cache.put("a", "k", "a", 10)
        cache.put("frame", "k", "small", 10)
        cache.put("frame", "k2", "huge", 101)
        assert cache.get("frame", "k2") is None
        assert cache.get("frame", "k") is None  # replaced, not kept beside it
        assert cache.get("a", "k") == "a"
        assert cache.total_bytes == 10

    def test_clear_empties_it(self) -> None:
        cache: BoundedCache[str] = BoundedCache(10, 100)
        cache.put("a", "k", "a", 10)
        cache.clear()
        assert len(cache) == 0
        assert cache.total_bytes == 0
        assert cache.get("a", "k") is None


class Kind(enum.Enum):
    ONE = 1
    TWO = 2


@dataclass(frozen=True)
class Part:
    name: str
    kind: Kind
    values: tuple[float, ...] = ()


class TestValueDigest:
    def test_equal_content_gives_equal_digests(self) -> None:
        assert value_digest(Part("a", Kind.ONE, (1.0, 2.0))) == value_digest(
            Part("a", Kind.ONE, (1.0, 2.0))
        )

    @pytest.mark.parametrize(
        "other",
        [
            Part("b", Kind.ONE, (1.0, 2.0)),
            Part("a", Kind.TWO, (1.0, 2.0)),
            Part("a", Kind.ONE, (2.0, 1.0)),
            Part("a", Kind.ONE, (1.0, 2.0000001)),
            Part("a", Kind.ONE, (1, 2)),
        ],
    )
    def test_any_difference_gives_another_digest(self, other) -> None:
        assert value_digest(Part("a", Kind.ONE, (1.0, 2.0))) != value_digest(other)

    def test_a_mapping_is_read_in_its_own_order(self) -> None:
        assert value_digest({"a": 1, "b": 2}) != value_digest({"b": 2, "a": 1})

    def test_a_set_is_read_in_no_order(self) -> None:
        assert value_digest(frozenset({"a", "b"})) == value_digest(
            frozenset({"b", "a"})
        )

    def test_a_value_it_cannot_see_into_gives_no_digest(self) -> None:
        assert value_digest((1, object())) is None
        assert value_digest({"set": frozenset({object()})}) is None

    def test_special_encodes_what_it_knows_and_may_refuse(self) -> None:
        class Known:
            text = "known"

        def special(part, add) -> bool:
            if isinstance(part, Known):
                add(part.text)
                return True
            if isinstance(part, bytearray):
                raise UnvouchedValueError("bytearray")
            return False

        assert value_digest((Known(),), special) == value_digest((Known(),), special)
        assert value_digest((Known(),), special) is not None
        assert value_digest((bytearray(),), special) is None
