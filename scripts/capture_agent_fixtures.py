"""Capture one branch-page fixture per portal (Zoopla / Rightmove / OTM).

Not wired into the main fixture-refresh workflow because agent-branch
pages churn on a different cadence from listing pages. We pin three
mainstream Cambridgeshire branches — one per portal — so tests exercise
each portal's branch-page shape without requiring all three to be the
*same* trading name (Rightmove and Zoopla use different branch-id pools,
so a common URL slug does not map to the same branch). Each branch has
live sales + lettings stock and a non-empty team/trade-body block.

Run from the repo root:

    uv run --project uk-property-intel/packages/scrapers \
        python uk-property-intel/scripts/capture_agent_fixtures.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_ROOT = REPO_ROOT / "packages" / "scrapers" / "tests" / "fixtures"

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

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Each capture is a live estate-agent branch in Cambridgeshire. Rightmove's
# branch-id URL scheme is a `${CompanyName}/${Town}-${branchId}` slug where
# the numeric id is authoritative — the string portion is decorative and
# stale slugs redirect to whatever branch currently owns that id. So we
# name fixtures after the branch the page actually resolves to, not after
# the slug we used to request it.
AGENT_FIXTURES: tuple[tuple[str, Path], ...] = (
    (
        "https://www.zoopla.co.uk/find-agents/branch/connells-cambourne-cambridge-1855/",
        FIXTURES_ROOT / "zoopla" / "agent_connells_cambourne_2026-04.html",
    ),
    (
        "https://www.rightmove.co.uk/estate-agents/agent/Hockeys/Cambridge-211166.html",
        FIXTURES_ROOT / "rightmove" / "agent_hockeys_cambridge_2026-04.html",
    ),
    (
        "https://www.onthemarket.com/agents/branch/abbotts-cambridge/",
        FIXTURES_ROOT / "onthemarket" / "agent_abbotts_cambridge_2026-04.html",
    ),
)


async def _capture(url: str, dest: Path) -> tuple[str, int]:
    from playwright.async_api import async_playwright

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=_UA,
            viewport={"width": 1440, "height": 900},
            locale="en-GB",
            extra_http_headers={"Accept-Language": "en-GB,en;q=0.9"},
        )
        await context.add_init_script(_STEALTH_INIT_JS)
        try:
            page = await context.new_page()
            host = f"https://{url.split('/')[2]}/"
            try:
                await page.goto(host, wait_until="domcontentloaded", timeout=30_000)
                await page.wait_for_timeout(1_500)
            except Exception:  # noqa: BLE001 — best-effort warm-up
                pass
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            # Allow hydration + any dynamic content (agent stats) to settle.
            await page.wait_for_timeout(3_000)
            html = await page.content()
        finally:
            await context.close()
            await browser.close()

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(html, encoding="utf-8")
    return (url, len(html))


async def main() -> None:
    for url, dest in AGENT_FIXTURES:
        try:
            captured_url, size = await _capture(url, dest)
            print(f"[ok] {captured_url} -> {dest} ({size:,} bytes)")
        except Exception as exc:  # noqa: BLE001 — report and continue to next
            print(f"[err] {url}: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
