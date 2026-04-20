"""Unit tests for ``scripts/check_fixture_freshness.py``.

The freshness auditor is loaded via ``importlib`` - ``scripts/`` sits
outside the uv workspace packages so a regular ``from scripts.check_... ``
import doesn't work.

These tests cover three things:

1. ``_stamp_from_name`` correctly extracts ``YYYY-MM`` stamps and ignores
   malformed ones (no cross-year substrings, no bare ``MM-DD``).
2. ``_classify`` maps ages to ``fresh / warn / fail / undated`` around
   the configured thresholds - exact boundaries matter because one day
   of slop turns a warn into a fail.
3. End-to-end: ``_collect`` walks the real fixtures dir and returns one
   row per file; ``_exit_code`` flips the process exit correctly under
   ``--strict``.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_fixture_freshness.py"


@pytest.fixture(scope="module")
def freshness_module() -> object:
    """Load ``check_fixture_freshness.py`` as a module for direct function access."""

    spec = importlib.util.spec_from_file_location(
        "_check_fixture_freshness", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestStampFromName:
    def test_extracts_basic_stamp(self, freshness_module: object) -> None:
        assert freshness_module._stamp_from_name("search_2026-04.html") == (2026, 4)

    def test_extracts_first_stamp_when_multiple(
        self, freshness_module: object
    ) -> None:
        # A filename could accidentally carry two stamps (e.g. after a rename
        # like ``2026-04_backup_2026-10.html``). We anchor on the *first*
        # stamp because that's the canonical capture month.
        assert freshness_module._stamp_from_name(
            "2026-04_backup_2026-10.html"
        ) == (2026, 4)

    def test_ignores_bare_day_stamp(self, freshness_module: object) -> None:
        # ``slice5`` must not be parsed as 2020-05 or similar; there's no
        # year/month in "search_range_slice.json".
        assert freshness_module._stamp_from_name("search_range_slice.json") is None

    def test_ignores_partial_year(self, freshness_module: object) -> None:
        # "1999-04" is before 2000 - the regex explicitly requires ``20\d{2}``
        # because the earliest real fixture capture is 2024+.
        assert freshness_module._stamp_from_name("archive_1999-04.html") is None

    def test_ignores_mm_dd_only(self, freshness_module: object) -> None:
        # ``04-20`` alone (without a year prefix) must not match.
        assert freshness_module._stamp_from_name("daily_04-20.html") is None

    def test_rejects_non_months(self, freshness_module: object) -> None:
        # Month must be 01-12; we must not accept "00" or "13".
        assert freshness_module._stamp_from_name("search_2026-00.html") is None
        assert freshness_module._stamp_from_name("search_2026-13.html") is None

    def test_rejects_stamp_inside_longer_digit_run(
        self, freshness_module: object
    ) -> None:
        # A date accidentally embedded in a numeric ID (``20260433`` -> wouldn't
        # accidentally match as 2026-04) must be rejected.
        assert freshness_module._stamp_from_name("id_20260433.html") is None


class TestClassify:
    @pytest.mark.parametrize(
        ("age_days", "expected"),
        [
            (None, "undated"),
            (0, "fresh"),
            (91, "fresh"),
            (92, "warn"),
            (183, "warn"),
            (184, "fail"),
            (365, "fail"),
        ],
    )
    def test_boundary_behaviour(
        self,
        freshness_module: object,
        age_days: int | None,
        expected: str,
    ) -> None:
        assert freshness_module._classify(age_days) == expected


class TestAgeDays:
    def test_same_month_is_days_into_month(
        self, freshness_module: object
    ) -> None:
        # Fixture captured for month=2026-04; today=2026-04-20 -> 19 days
        # (because the "captured" proxy is the 1st of the month).
        assert (
            freshness_module._age_days(2026, 4, today=date(2026, 4, 20))
            == 19
        )

    def test_next_year(self, freshness_module: object) -> None:
        assert (
            freshness_module._age_days(2026, 4, today=date(2027, 4, 1))
            == 365  # 2026 → 2027 = 365 days (not a leap span)
        )


class TestCollectE2E:
    def test_real_fixtures_return_fresh_on_capture_date(
        self, freshness_module: object
    ) -> None:
        # On 2026-04-20 every real fixture should be <= 19 days old - the
        # whole fixture set was captured for 2026-04.
        rows = freshness_module._collect(date(2026, 4, 20))
        assert rows, "expected real fixtures on disk under tests/fixtures"
        statuses = {r.status for r in rows}
        # `undated` is allowed (search_range_slice.json); nothing should warn/fail.
        assert "warn" not in statuses
        assert "fail" not in statuses


class TestExitCode:
    def _row(self, module: object, status: str) -> object:
        return module.FixtureInfo(path="x", month=None, age_days=None, status=status)

    def test_fresh_only_exits_zero(self, freshness_module: object) -> None:
        rows = [self._row(freshness_module, "fresh")]
        assert freshness_module._exit_code(rows, strict=False) == 0
        assert freshness_module._exit_code(rows, strict=True) == 0

    def test_warn_default_exits_zero_strict_exits_one(
        self, freshness_module: object
    ) -> None:
        rows = [self._row(freshness_module, "warn")]
        assert freshness_module._exit_code(rows, strict=False) == 0
        assert freshness_module._exit_code(rows, strict=True) == 1

    def test_undated_default_exits_zero_strict_exits_one(
        self, freshness_module: object
    ) -> None:
        rows = [self._row(freshness_module, "undated")]
        assert freshness_module._exit_code(rows, strict=False) == 0
        assert freshness_module._exit_code(rows, strict=True) == 1

    def test_any_fail_exits_one_regardless_of_strict(
        self, freshness_module: object
    ) -> None:
        rows = [
            self._row(freshness_module, "fresh"),
            self._row(freshness_module, "fail"),
        ]
        assert freshness_module._exit_code(rows, strict=False) == 1
        assert freshness_module._exit_code(rows, strict=True) == 1
