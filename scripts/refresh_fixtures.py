"""Refresh the live scraper test fixtures from production.

Companion to ``check_fixture_freshness.py``. The freshness script yells
when fixtures age out; this script is what a maintainer runs to fix the
yellow/red rows on their laptop.

Scope
-----

Three portal captures (HTML) and four Allsop JSON captures, plus a
"re-run the parsers" verification step:

* Rightmove: ``search_cambridge_<YYYY-MM>.html``,
  ``torent_cambridge_<YYYY-MM>.html``, ``detail_<id>_<YYYY-MM>.html``.
* Zoopla: ``search_cambridgeshire_<YYYY-MM>.html``,
  ``torent_cambridgeshire_<YYYY-MM>.html``, ``detail_<id>_<YYYY-MM>.html``.
* OnTheMarket: ``search_cambridge_<YYYY-MM>.html``,
  ``detail_<id>_<YYYY-MM>.html``.
* Allsop: ``search_<YYYY-MM>_slice5.json``, ``auction_<YYYY-MM>.json``,
  ``lot_detail_<YYYY-MM>.json`` (plus the undated
  ``search_range_slice.json`` slice, only when the live feed happens to
  contain a range-guide lot).

The detail listing IDs are pinned to the ones the conftest fixture file
names encode, so a refresh overwrites the file for the *currently
canonical* detail page rather than spawning a new ID every quarter.

CLI
---

::

    uv run scripts/refresh_fixtures.py --help
    uv run scripts/refresh_fixtures.py all                # every portal + allsop (needs --cloudflare-ok)
    uv run scripts/refresh_fixtures.py allsop             # json-only, safe, no CF
    uv run scripts/refresh_fixtures.py rightmove --cloudflare-ok
    uv run scripts/refresh_fixtures.py zoopla --cloudflare-ok --headless
    uv run scripts/refresh_fixtures.py --verify           # skip capture, just rerun parser tests

By default portal captures run a headful Playwright browser so a
Cloudflare challenge is visible + manually resolvable. Use ``--headless``
once you've proven the site is clean.

Cloudflare / block handling
---------------------------

Every portal sits behind a WAF (Cloudflare / Imperva / similar). The
script runs one fetch per URL and inspects the resulting HTML for the
usual block markers (``cloudflare``, ``attention required``, ``just a
moment``, ``captcha``). If it spots one, it refuses to overwrite the
existing fixture and prints a clear "blocked, try again with
``--cloudflare-ok`` and solve the challenge manually" message.

Verification
-----------

After any successful capture the script invokes ``pytest`` against the
matching parser test file. If the new fixture breaks the parser, the
old fixture is *restored* (the new capture lands at ``*.new`` beside
it for inspection) so the committed tree always matches the committed
tests.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "packages" / "scrapers" / "tests" / "fixtures"
SCRAPERS_TESTS_ROOT = REPO_ROOT / "packages" / "scrapers" / "tests"

_DEFAULT_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6_1) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15"
)
# Matches the set in smoke.probe_listings_live - anything in this list
# on a sub-500-byte page strongly suggests a WAF challenge rather than
# real content.
_BLOCK_MARKERS = ("cloudflare", "attention required", "just a moment", "captcha")

# Minimum HTML payload size (bytes) that's plausibly a real listings
# page. Block pages are typically well under 20 KB; a normal Rightmove /
# Zoopla / OnTheMarket page is 500 KB+. 50 KB is a conservative floor.
_MIN_PAYLOAD_BYTES = 50_000

# Stealth init script taken verbatim from
# uk-property-apify/shared/src/uk_property_apify_shared/crawler/browser_fetcher.py.
# Duplicated rather than imported so this script stays runnable without
# pulling the apify sibling into the dep graph.
_STEALTH_INIT_JS = """
() => {
  Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
  Object.defineProperty(navigator, 'languages', { get: () => ['en-GB', 'en'] });
  Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3, 4, 5].map((_, i) => ({ name: `plugin-${i}` })),
  });
  Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
  window.chrome = window.chrome || { runtime: {}, loadTimes: () => ({}), csi: () => ({}) };
  const origQuery = (navigator.permissions || {}).query;
  if (origQuery) {
    navigator.permissions.query = (params) =>
      params.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : origQuery(params);
  }
  const getParameter = WebGLRenderingContext.prototype.getParameter;
  WebGLRenderingContext.prototype.getParameter = function (p) {
    if (p === 37445) return 'Intel Inc.';
    if (p === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter.call(this, p);
  };
}
"""


@dataclass(frozen=True)
class PortalCapture:
    """One ``(url, dest filename)`` pair for a portal refresh."""

    url: str
    filename_template: str  # filename with ``{stamp}`` placeholder

    def dest(self, *, portal_dir: str, stamp: str) -> Path:
        return FIXTURES_ROOT / portal_dir / self.filename_template.format(stamp=stamp)


# IDs pinned to the existing fixture filenames. Keep these in sync with
# packages/scrapers/tests/conftest.py - the tests always read the canonical
# "<stamp>" fixture, so the refresh writes under the same filenames.
_RIGHTMOVE_DETAIL_ID = "173261858"
_ZOOPLA_DETAIL_ID = "72228361"
_OTM_DETAIL_ID = "18999957"


RIGHTMOVE_CAPTURES: tuple[PortalCapture, ...] = (
    PortalCapture(
        url="https://www.rightmove.co.uk/property-for-sale/Cambridge.html",
        filename_template="search_cambridge_{stamp}.html",
    ),
    PortalCapture(
        url="https://www.rightmove.co.uk/property-to-rent/Cambridge.html",
        filename_template="torent_cambridge_{stamp}.html",
    ),
    PortalCapture(
        url=f"https://www.rightmove.co.uk/properties/{_RIGHTMOVE_DETAIL_ID}",
        filename_template=f"detail_{_RIGHTMOVE_DETAIL_ID}_{{stamp}}.html",
    ),
)

ZOOPLA_CAPTURES: tuple[PortalCapture, ...] = (
    PortalCapture(
        url="https://www.zoopla.co.uk/for-sale/property/cambridgeshire/",
        filename_template="search_cambridgeshire_{stamp}.html",
    ),
    PortalCapture(
        url="https://www.zoopla.co.uk/to-rent/property/cambridgeshire/",
        filename_template="torent_cambridgeshire_{stamp}.html",
    ),
    PortalCapture(
        url=f"https://www.zoopla.co.uk/for-sale/details/{_ZOOPLA_DETAIL_ID}/",
        filename_template=f"detail_{_ZOOPLA_DETAIL_ID}_{{stamp}}.html",
    ),
)

OTM_CAPTURES: tuple[PortalCapture, ...] = (
    PortalCapture(
        url="https://www.onthemarket.com/for-sale/property/cambridge/",
        filename_template="search_cambridge_{stamp}.html",
    ),
    PortalCapture(
        url=f"https://www.onthemarket.com/details/{_OTM_DETAIL_ID}/",
        filename_template=f"detail_{_OTM_DETAIL_ID}_{{stamp}}.html",
    ),
)


@dataclass(frozen=True)
class CaptureOutcome:
    """Result of one capture attempt.

    ``candidate`` points at the ``.new`` file that was staged beside the
    canonical fixture. It's ``None`` when the capture was blocked or
    errored (we write a ``.blocked`` body in the block case so a human
    can still inspect what came back, but that file is *not* a valid
    candidate for the committed slot).
    """

    label: str
    status: str  # "ok" | "blocked" | "error"
    detail: str
    canonical: Path | None
    candidate: Path | None


def _current_stamp() -> str:
    """Return the UTC capture month in ``YYYY-MM`` form."""

    now = datetime.now(UTC)
    return f"{now.year:04d}-{now.month:02d}"


def _write_candidate_text(path: Path, data: str) -> Path:
    """Write ``data`` next to ``path`` as ``<path>.new`` and return the new path.

    Two-stage write is the whole reason this script is safe to run
    repeatedly. The committed fixture stays on disk untouched; the
    refreshed capture lands as a sibling ``.new`` file. We only swap the
    two files in :func:`_swap_in` *after* the parser tests have
    confirmed the refreshed fixtures still match the parsers.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    new_side = path.with_suffix(path.suffix + ".new")
    new_side.write_text(data, encoding="utf-8")
    return new_side


def _write_candidate_json(path: Path, data: Any) -> Path:
    return _write_candidate_text(path, json.dumps(data, ensure_ascii=False, indent=2))


def _looks_blocked(body: str) -> tuple[bool, str]:
    lowered = body[:4096].lower()
    for marker in _BLOCK_MARKERS:
        if marker in lowered:
            return True, marker
    if len(body) < _MIN_PAYLOAD_BYTES:
        return True, f"payload<{_MIN_PAYLOAD_BYTES}B"
    return False, ""


async def _portal_fetch(
    context: BrowserContext,
    capture: PortalCapture,
    *,
    warm_session: bool,
) -> tuple[str, str]:
    """Navigate to ``capture.url`` and return ``(html, block_reason)``."""

    page = await context.new_page()
    try:
        if warm_session:
            try:
                host = f"https://{capture.url.split('/')[2]}/"
                await page.goto(host, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1_500)
            except Exception:  # noqa: BLE001 — warm-up errors are best-effort
                pass
        await page.goto(capture.url, wait_until="domcontentloaded", timeout=45_000)
        await page.wait_for_timeout(1_000)
        html = await page.content()
    finally:
        await page.close()
    blocked, reason = _looks_blocked(html)
    return html, (reason if blocked else "")


async def _refresh_portal(
    *,
    label: str,
    portal_dir: str,
    captures: tuple[PortalCapture, ...],
    stamp: str,
    headless: bool,
) -> list[CaptureOutcome]:
    """Drive Playwright to pull each capture and write atomic fixture files."""

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return [
            CaptureOutcome(
                label=label,
                status="error",
                detail=(
                    "playwright not installed. run `uv add --group dev playwright` "
                    "then `uv run playwright install chromium`."
                ),
                path=None,
            )
        ]

    outcomes: list[CaptureOutcome] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=_DEFAULT_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
        )
        await context.add_init_script(_STEALTH_INIT_JS)
        try:
            for cap in captures:
                dest = cap.dest(portal_dir=portal_dir, stamp=stamp)
                try:
                    html, block_reason = await _portal_fetch(
                        context, cap, warm_session=True
                    )
                except Exception as exc:  # noqa: BLE001 — surface every navigation error
                    outcomes.append(
                        CaptureOutcome(
                            label=f"{label}:{cap.filename_template.format(stamp=stamp)}",
                            status="error",
                            detail=f"{type(exc).__name__}: {exc}",
                            canonical=dest,
                            candidate=None,
                        )
                    )
                    continue

                if block_reason:
                    # Persist the blocked body alongside so a human can
                    # inspect the WAF response, but don't stage it as a
                    # candidate - we don't want to swap a challenge
                    # page into the committed slot even if tests happen
                    # to still pass (they won't, but belt-and-braces).
                    side_path = dest.with_suffix(dest.suffix + ".blocked")
                    side_path.parent.mkdir(parents=True, exist_ok=True)
                    side_path.write_text(html, encoding="utf-8")
                    outcomes.append(
                        CaptureOutcome(
                            label=f"{label}:{dest.name}",
                            status="blocked",
                            detail=(
                                f"block marker {block_reason!r}; saved body to "
                                f"{side_path.name} for inspection"
                            ),
                            canonical=dest,
                            candidate=None,
                        )
                    )
                    continue

                candidate = _write_candidate_text(dest, html)
                outcomes.append(
                    CaptureOutcome(
                        label=f"{label}:{dest.name}",
                        status="ok",
                        detail=f"{len(html):,} bytes (staged as {candidate.name})",
                        canonical=dest,
                        candidate=candidate,
                    )
                )
        finally:
            await context.close()
            await browser.close()
    return outcomes


async def _refresh_allsop(*, stamp: str) -> list[CaptureOutcome]:
    """Capture the four Allsop JSON fixtures from the live Angular API.

    Allsop has no Cloudflare / anti-bot in front of the JSON endpoints,
    so this path is safe to run unattended (and is the one the CI job
    exercises if we ever wire it up - HTML portals still need a human).
    """

    outcomes: list[CaptureOutcome] = []
    try:
        from uk_property_apis.auctions.allsop_client import AllsopClient
    except ImportError as exc:
        return [
            CaptureOutcome(
                label="allsop",
                status="error",
                detail=f"uk_property_apis not importable: {exc}",
                canonical=None,
                candidate=None,
            )
        ]

    dest_search = FIXTURES_ROOT / "auctions" / "allsop" / f"search_{stamp}_slice5.json"
    dest_auction = FIXTURES_ROOT / "auctions" / "allsop" / f"auction_{stamp}.json"
    dest_lot_detail = FIXTURES_ROOT / "auctions" / "allsop" / f"lot_detail_{stamp}.json"

    async with AllsopClient() as client:
        try:
            upcoming = await client.list_upcoming_auctions()
            if not upcoming.residential:
                return [
                    CaptureOutcome(
                        label="allsop",
                        status="error",
                        detail="allsop /api/auctions/upcoming returned no residential auctions",
                        canonical=None,
                        candidate=None,
                    )
                ]
            auction_id = upcoming.residential[0].auction_id

            auction_payload = await client.get_auction(auction_id)
            candidate = _write_candidate_json(dest_auction, auction_payload)
            outcomes.append(
                CaptureOutcome(
                    label=f"allsop:{dest_auction.name}",
                    status="ok",
                    detail=f"auction_id={auction_id} (staged as {candidate.name})",
                    canonical=dest_auction,
                    candidate=candidate,
                )
            )

            page = await client.search_page(auction_id=auction_id, size=5, page=1)
            if len(page.results) < 5:
                outcomes.append(
                    CaptureOutcome(
                        label=f"allsop:{dest_search.name}",
                        status="error",
                        detail=(
                            f"search returned {len(page.results)} results, expected 5 "
                            "(auction may still be pre-publishing)"
                        ),
                        canonical=dest_search,
                        candidate=None,
                    )
                )
            else:
                # The client returns a dataclass; the committed fixtures are
                # the raw wire payload. Re-assemble the {"data": {"results":
                # [...], "total": N}, "search-uuid": ...} envelope verbatim.
                payload = {
                    "data": {
                        "results": list(page.results),
                        "total": page.total,
                    },
                    "search-uuid": "refreshed",
                }
                candidate = _write_candidate_json(dest_search, payload)
                outcomes.append(
                    CaptureOutcome(
                        label=f"allsop:{dest_search.name}",
                        status="ok",
                        detail=(
                            f"{len(page.results)} lots, total={page.total} "
                            f"(staged as {candidate.name})"
                        ),
                        canonical=dest_search,
                        candidate=candidate,
                    )
                )

            # Lot detail: use the first result's reference so the gallery
            # fixture always corresponds to a real current lot.
            first = page.results[0] if page.results else None
            reference = (
                (first.get("reference") if isinstance(first, dict) else None)
                or ""
            ).strip()
            if reference:
                try:
                    lot_detail = await client.get_lot_detail(reference)
                except Exception as exc:  # noqa: BLE001 — non-fatal, log and move on
                    outcomes.append(
                        CaptureOutcome(
                            label=f"allsop:{dest_lot_detail.name}",
                            status="error",
                            detail=f"get_lot_detail({reference!r}): {exc}",
                            canonical=dest_lot_detail,
                            candidate=None,
                        )
                    )
                else:
                    candidate = _write_candidate_json(dest_lot_detail, lot_detail)
                    outcomes.append(
                        CaptureOutcome(
                            label=f"allsop:{dest_lot_detail.name}",
                            status="ok",
                            detail=f"reference={reference!r} (staged as {candidate.name})",
                            canonical=dest_lot_detail,
                            candidate=candidate,
                        )
                    )
            else:
                outcomes.append(
                    CaptureOutcome(
                        label=f"allsop:{dest_lot_detail.name}",
                        status="error",
                        detail="no reference on first search result; lot_detail skipped",
                        canonical=dest_lot_detail,
                        candidate=None,
                    )
                )
        except Exception as exc:  # noqa: BLE001 — guardrail around the whole block
            outcomes.append(
                CaptureOutcome(
                    label="allsop",
                    status="error",
                    detail=f"{type(exc).__name__}: {exc}",
                    canonical=None,
                    candidate=None,
                )
            )
    return outcomes


def _run_parser_tests(tests: list[str]) -> tuple[int, str]:
    """Run targeted parser tests and return ``(returncode, stdout_tail)``."""

    if not tests:
        return 0, "(no parser tests selected)"
    cmd = ["uv", "run", "pytest", "-q", *tests]
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-30:])
    return proc.returncode, tail


def _swap_in(candidates: list[tuple[Path, Path]]) -> list[tuple[Path, Path]]:
    """Move each ``.new`` into the canonical slot, backing up the previous file.

    Returns the list of ``(backup_path, canonical_path)`` pairs so the
    caller can call :func:`_swap_out` on parser-test failure.
    """

    backups: list[tuple[Path, Path]] = []
    for new_side, canonical in candidates:
        if not new_side.exists():
            continue
        backup = canonical.with_suffix(canonical.suffix + ".bak")
        if canonical.exists():
            canonical.replace(backup)
            backups.append((backup, canonical))
        new_side.replace(canonical)
    return backups


def _swap_out(backups: list[tuple[Path, Path]], candidates: list[tuple[Path, Path]]) -> None:
    """Restore the pre-swap state: canonical files back from ``.bak``, ``.new`` re-staged.

    The result is byte-identical to the working tree before the refresh
    ran: committed fixtures where they were, refreshed captures as
    ``<path>.new`` sidecar files for a human to diff.
    """

    for backup, canonical in backups:
        # Move the refreshed canonical file back out to <canonical>.new so
        # the maintainer can still diff it against the original.
        if canonical.exists():
            staged = canonical.with_suffix(canonical.suffix + ".new")
            canonical.replace(staged)
        backup.replace(canonical)

    # For candidates that had no prior committed file (brand-new capture
    # slot), _swap_in already moved the .new into place with no backup;
    # move it back out to preserve the pre-run tree.
    backed_up_canonicals = {c for _, c in backups}
    for new_side, canonical in candidates:
        if canonical in backed_up_canonicals:
            continue
        if canonical.exists():
            canonical.replace(new_side)


def _print_outcomes(outcomes: list[CaptureOutcome]) -> None:
    for o in outcomes:
        mark = {
            "ok": "[OK  ]",
            "blocked": "[BLOK]",
            "error": "[ERR ]",
            "restored": "[RSTD]",
        }.get(o.status, "[?   ]")
        print(f"{mark} {o.label:60s} {o.detail}")


async def _amain(args: argparse.Namespace) -> int:
    stamp = args.stamp or _current_stamp()
    targets: set[str] = set(args.targets)
    if not targets or "all" in targets:
        targets = {"rightmove", "zoopla", "onthemarket", "allsop"}

    all_outcomes: list[CaptureOutcome] = []
    tests_to_run: list[str] = []

    portal_needs_cf = targets & {"rightmove", "zoopla", "onthemarket"}
    if portal_needs_cf and not args.cloudflare_ok and not args.verify_only:
        print(
            "Portal refresh would hit Cloudflare-protected sites. Re-run with "
            "--cloudflare-ok after confirming a headful browser can clear "
            "challenges (or drop portal targets to run allsop-only).",
            file=sys.stderr,
        )
        return 2

    if not args.verify_only:
        if "allsop" in targets:
            all_outcomes.extend(await _refresh_allsop(stamp=stamp))
            tests_to_run.append(
                str(SCRAPERS_TESTS_ROOT / "test_auctions_allsop_parser.py")
            )
        if "rightmove" in targets:
            all_outcomes.extend(
                await _refresh_portal(
                    label="rightmove",
                    portal_dir="rightmove",
                    captures=RIGHTMOVE_CAPTURES,
                    stamp=stamp,
                    headless=args.headless,
                )
            )
            tests_to_run.append(str(SCRAPERS_TESTS_ROOT / "test_rightmove_parser.py"))
        if "zoopla" in targets:
            all_outcomes.extend(
                await _refresh_portal(
                    label="zoopla",
                    portal_dir="zoopla",
                    captures=ZOOPLA_CAPTURES,
                    stamp=stamp,
                    headless=args.headless,
                )
            )
            tests_to_run.append(str(SCRAPERS_TESTS_ROOT / "test_zoopla_parser.py"))
        if "onthemarket" in targets:
            all_outcomes.extend(
                await _refresh_portal(
                    label="onthemarket",
                    portal_dir="onthemarket",
                    captures=OTM_CAPTURES,
                    stamp=stamp,
                    headless=args.headless,
                )
            )
            tests_to_run.append(
                str(SCRAPERS_TESTS_ROOT / "test_onthemarket_parser.py")
            )
    else:
        # Verify-only mode: rerun all scraper parsers against what's on disk.
        tests_to_run = [
            str(SCRAPERS_TESTS_ROOT / "test_auctions_allsop_parser.py"),
            str(SCRAPERS_TESTS_ROOT / "test_rightmove_parser.py"),
            str(SCRAPERS_TESTS_ROOT / "test_zoopla_parser.py"),
            str(SCRAPERS_TESTS_ROOT / "test_onthemarket_parser.py"),
        ]

    _print_outcomes(all_outcomes)

    candidates: list[tuple[Path, Path]] = [
        (o.candidate, o.canonical)
        for o in all_outcomes
        if o.status == "ok" and o.candidate is not None and o.canonical is not None
    ]

    if args.verify_only:
        print()
        print("-> re-running parser tests against committed fixtures …")
        rc, tail = _run_parser_tests(tests_to_run)
        print(tail)
        return rc

    if not all_outcomes:
        print("(no targets selected)", file=sys.stderr)
        return 2

    if not candidates:
        # Every capture was blocked or errored out. Don't run the parser
        # tests - they'd just pass against unchanged committed fixtures
        # and hide the fact that nothing actually refreshed.
        print("(no captures committed; every target blocked or errored)", file=sys.stderr)
        return 1

    print()
    print(f"-> staging {len(candidates)} refreshed fixture(s) and running parser tests …")
    backups = _swap_in(candidates)
    rc: int = 1
    tail: str = "(parser tests did not run)"
    try:
        rc, tail = _run_parser_tests(tests_to_run)
    finally:
        if rc != 0:
            print(
                "parser tests FAILED against the refreshed fixtures — rolling back "
                "and leaving new captures as *.new sidecars for diff",
                file=sys.stderr,
            )
            _swap_out(backups, candidates)
        else:
            # Tests passed: delete backups, canonical paths already hold
            # the refreshed content.
            for backup, _ in backups:
                with contextlib.suppress(FileNotFoundError):
                    backup.unlink()
    print(tail)

    any_fail = any(o.status in ("blocked", "error") for o in all_outcomes)
    if rc != 0:
        return rc
    return 1 if any_fail else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "targets",
        nargs="*",
        choices=["all", "rightmove", "zoopla", "onthemarket", "allsop"],
        help="Which capture set(s) to refresh. Default (no arg): all.",
    )
    parser.add_argument(
        "--stamp",
        default=None,
        help=(
            "Override the YYYY-MM stamp used in output filenames. "
            "Defaults to the current UTC month."
        ),
    )
    parser.add_argument(
        "--cloudflare-ok",
        action="store_true",
        help=(
            "Acknowledge that portal targets (rightmove/zoopla/onthemarket) "
            "hit Cloudflare-protected sites and that a headful browser may "
            "need manual challenge-solving."
        ),
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Launch Playwright headless (default: headful, so CF challenges are visible).",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help=(
            "Skip capture entirely, just rerun the scraper parser tests against "
            "what's already on disk. Useful for CI / a fast drift canary."
        ),
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_amain(args)))


if __name__ == "__main__":
    main()
