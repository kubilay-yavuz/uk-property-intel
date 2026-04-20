"""Audit scraper test fixtures for age / drift.

Portals (Rightmove, Zoopla, OnTheMarket) and auction houses (Allsop) all change
their HTML / JSON shape frequently. The parser tests in
``packages/scrapers/tests/`` pin against real payloads captured live from
each site - see ``tests/fixtures/`` for the captures. Once a fixture drifts
more than a quarter behind production, our parser tests start vouching for
a shape nobody serves any more.

This script is the cheap canary that runs in CI and prints a freshness
table so "refresh the scraper fixtures" becomes a calendar event rather
than an unpleasant surprise the day a parser silently stops matching.

How it works:

* Every fixture filename encodes the capture month as ``...YYYY-MM...``
  (e.g. ``search_cambridgeshire_2026-04.html``).
* The script walks ``packages/scrapers/tests/fixtures/`` and extracts that
  stamp for each file.
* Each fixture is tagged as ``fresh | warn | fail`` against two thresholds
  (see ``WARN_AFTER_DAYS`` / ``FAIL_AFTER_DAYS`` below).
* Files with no ``YYYY-MM`` stamp are reported as ``undated`` - the repo
  policy is to add one next time they're refreshed.

Exit codes:

* ``0`` - everything within thresholds (or only warns, and ``--strict`` not set).
* ``1`` - at least one fixture exceeded ``FAIL_AFTER_DAYS`` (stale beyond
  hard limit) or ``--strict`` was passed and something warns / is undated.
* ``2`` - misuse (bad CLI args, fixtures dir missing).

Run locally:

    uv run scripts/check_fixture_freshness.py          # human-readable table
    uv run scripts/check_fixture_freshness.py --strict # treat warnings + undated as fails
    uv run scripts/check_fixture_freshness.py --json   # machine-readable JSON
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "packages" / "scrapers" / "tests" / "fixtures"

# Calendar-month YYYY-MM stamp embedded anywhere in the filename. We accept a
# leading underscore/dash/period or a leading ``@`` boundary so partial date
# matches inside other tokens (like ``v2026-04-fix``) are still picked up.
_STAMP_RE = re.compile(r"(?<![0-9])(20\d{2})-(0[1-9]|1[0-2])(?![0-9])")

# We treat 3 months as "warn": refresh cadence should be quarterly. 6 months
# as "fail": at that age the fixture is demonstrably out-of-date for any
# fast-moving portal (Rightmove etc. change often mid-year) and the parser
# tests are rubber-stamping ancient HTML.
WARN_AFTER_DAYS = 92
FAIL_AFTER_DAYS = 184


@dataclass(frozen=True)
class FixtureInfo:
    """One row of the freshness report."""

    path: str
    month: str | None  # "2026-04" or None
    age_days: int | None
    status: str  # "fresh" | "warn" | "fail" | "undated"


def _stamp_from_name(name: str) -> tuple[int, int] | None:
    """Extract ``(year, month)`` from the first ``YYYY-MM`` in ``name``.

    The fixture layout encodes the capture month directly in the filename so
    it survives moves, copies, and git operations that would otherwise strip
    mtimes. We deliberately don't fall back to filesystem mtime - a recent
    `git checkout` would make every file look fresh.
    """

    m = _STAMP_RE.search(name)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    return year, month


def _age_days(year: int, month: int, *, today: date) -> int:
    """Days between ``today`` and the first-of-month for the fixture stamp.

    We use the first of the stamped month as a conservative "capture date"
    proxy - the real capture could have been mid-month, but this never
    over-estimates freshness.
    """

    captured = date(year, month, 1)
    return (today - captured).days


def _classify(age_days: int | None) -> str:
    if age_days is None:
        return "undated"
    if age_days >= FAIL_AFTER_DAYS:
        return "fail"
    if age_days >= WARN_AFTER_DAYS:
        return "warn"
    return "fresh"


def _collect(today: date) -> list[FixtureInfo]:
    """Walk the fixtures tree and build one FixtureInfo per file."""

    rows: list[FixtureInfo] = []
    if not FIXTURES_ROOT.is_dir():
        return rows
    for path in sorted(FIXTURES_ROOT.rglob("*")):
        if not path.is_file():
            continue
        # Skip OS junk and things that aren't fixtures proper.
        if path.name.startswith(".") or path.suffix in {".pyc"}:
            continue
        stamp = _stamp_from_name(path.name)
        if stamp is None:
            rows.append(
                FixtureInfo(
                    path=str(path.relative_to(REPO_ROOT)),
                    month=None,
                    age_days=None,
                    status="undated",
                )
            )
            continue
        year, month = stamp
        age = _age_days(year, month, today=today)
        rows.append(
            FixtureInfo(
                path=str(path.relative_to(REPO_ROOT)),
                month=f"{year:04d}-{month:02d}",
                age_days=age,
                status=_classify(age),
            )
        )
    return rows


def _render_table(rows: list[FixtureInfo]) -> str:
    if not rows:
        return "(no fixtures found)"
    # Column widths: path wants to dominate; everything else is tiny.
    path_w = max(len(r.path) for r in rows)
    lines: list[str] = []
    header = f"{'fixture':<{path_w}}  {'month':<7}  {'age_d':>5}  status"
    lines.append(header)
    lines.append("-" * len(header))
    for r in rows:
        month = r.month or "-"
        age = f"{r.age_days}" if r.age_days is not None else "-"
        lines.append(f"{r.path:<{path_w}}  {month:<7}  {age:>5}  {r.status}")
    lines.append("")
    lines.append(_summary_line(rows))
    return "\n".join(lines)


def _summary_line(rows: list[FixtureInfo]) -> str:
    counts: dict[str, int] = {"fresh": 0, "warn": 0, "fail": 0, "undated": 0}
    for r in rows:
        counts[r.status] += 1
    return (
        f"total={len(rows)}  fresh={counts['fresh']}  warn={counts['warn']}  "
        f"fail={counts['fail']}  undated={counts['undated']}  "
        f"(warn>={WARN_AFTER_DAYS}d, fail>={FAIL_AFTER_DAYS}d)"
    )


def _exit_code(rows: list[FixtureInfo], *, strict: bool) -> int:
    if any(r.status == "fail" for r in rows):
        return 1
    if strict and any(r.status in ("warn", "undated") for r in rows):
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat 'warn' and 'undated' as failures (quarterly cadence guard).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of a human table.",
    )
    parser.add_argument(
        "--today",
        default=None,
        help=(
            "Override today's date (YYYY-MM-DD). Used by tests and "
            "reproducible CI runs. Defaults to the UTC calendar date."
        ),
    )
    args = parser.parse_args()

    if args.today:
        try:
            today = datetime.strptime(args.today, "%Y-%m-%d").date()
        except ValueError as exc:
            print(f"invalid --today: {exc}", file=sys.stderr)
            return 2
    else:
        today = datetime.now(UTC).date()

    if not FIXTURES_ROOT.is_dir():
        print(f"fixtures directory not found: {FIXTURES_ROOT}", file=sys.stderr)
        return 2

    rows = _collect(today)
    rc = _exit_code(rows, strict=args.strict)

    if args.as_json:
        print(
            json.dumps(
                {
                    "today": today.isoformat(),
                    "warn_after_days": WARN_AFTER_DAYS,
                    "fail_after_days": FAIL_AFTER_DAYS,
                    "strict": args.strict,
                    "summary": {
                        k: sum(1 for r in rows if r.status == k)
                        for k in ("fresh", "warn", "fail", "undated")
                    },
                    "fixtures": [asdict(r) for r in rows],
                    "exit_code": rc,
                },
                indent=2,
                sort_keys=True,
            )
        )
    else:
        print(_render_table(rows))

    return rc


if __name__ == "__main__":
    sys.exit(main())
