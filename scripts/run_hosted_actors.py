"""Exercise every hosted Apify actor through the intel-repo delegation client.

Proves the dual-mode plumbing in ``uk-property-apify-client`` works end-to-end
from the public ``uk-property-intel`` tree: for each of the 14 actor slugs
declared in ``KNOWN_ACTOR_SLUGS`` we resolve a delegation, fire a minimal but
realistic input, await completion through ``ApifyDelegation.call``, and print
a structured per-actor verdict.

Run:

    export APIFY_API_TOKEN=...
    export APIFY_USERNAME=oscillating_binoculars
    uv run scripts/run_hosted_actors.py            # everything
    uv run scripts/run_hosted_actors.py zoopla-listings rightmove-listings

Useful extras:

* ``--listings-only`` limits to A1/A2/A3/A14b (the portal surface).
* ``--timeout-s`` overrides the ``UK_PROPERTY_APIFY_TIMEOUT_S`` default
  (600 s) for each call.
* ``--zoopla-proxy residential``/``datacenter``/``none`` picks the
  ``proxy`` block passed to A1 (Residential GB is what beat Cloudflare).

Verdicts:

* **PASS**    — run SUCCEEDED, dataset non-empty (or expected-empty), all
                required fields present on the sample record.
* **PARTIAL** — SUCCEEDED but dataset empty for a should-have-data actor
                (the typical signal for "Cloudflare intermittent" or
                "no residential coverage right now").
* **FAIL**    — ``DelegationError`` from ``.call``, or missing fields on
                the sample record.
* **SKIP**    — delegation not resolved (token/username unset for that slug).

Every verdict is SUCCEEDED-gated by the client itself (see
``ApifyDelegation.call`` - non-SUCCEEDED statuses raise). Exit code is
non-zero if anything lands on ``FAIL``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any

from uk_property_apify_client import (
    KNOWN_ACTOR_SLUGS,
    ActorCallResult,
    ApifyDelegation,
    DelegationError,
)


@dataclass(frozen=True)
class ActorSpec:
    key: str
    payload: dict[str, Any]
    expects_items: bool
    required_fields: tuple[str, ...]
    note: str = ""


def _listings_query(location: str, **extra: Any) -> dict[str, Any]:
    """A ``SearchQuery``-shaped dict with sensible defaults per run."""
    base: dict[str, Any] = {
        "location": location,
        "transaction": "sale",
        "minBeds": 1,
    }
    base.update(extra)
    return base


RIGHTMOVE_QUERY = _listings_query("Cambridge", maxPrice=500_000)
"""Rightmove's URL builder prefers city / town names it can resolve to a
Rightmove location identifier. Postcodes go through the free-text search,
which 200s with an empty grid."""

ONTHEMARKET_QUERY = _listings_query("Cambridge", maxPrice=500_000)
"""OTM treats postcodes and place-names equally well; stick with Cambridge
so the 3 listings actors share a directly-comparable query."""

ZOOPLA_QUERY = _listings_query("SW1A 1AA")
"""Zoopla's slug dictionary doesn't cover every UK place — a canonical
postcode slug (`for-sale/property/sw1a-1aa/`) always lands on a populated
page, while bare place-names (`cambridge`) occasionally serve an empty
``Property for sale in -`` shell."""


def build_specs(*, zoopla_proxy: str) -> list[ActorSpec]:
    zoopla_payload: dict[str, Any] = {
        "queries": [ZOOPLA_QUERY],
        "maxPagesPerQuery": 1,
        "queryConcurrency": 1,
    }
    if zoopla_proxy == "residential":
        zoopla_payload["proxy"] = {
            "useApifyProxy": True,
            "apifyProxyGroups": ["RESIDENTIAL"],
            "apifyProxyCountry": "GB",
        }
    elif zoopla_proxy == "datacenter":
        zoopla_payload["proxy"] = {"useApifyProxy": True}

    return [
        ActorSpec(
            key="zoopla-listings",
            payload=zoopla_payload,
            expects_items=True,
            required_fields=("source", "source_id", "source_url"),
            note=f"proxy={zoopla_proxy}; WAF-sprint verified 2026-04-23",
        ),
        ActorSpec(
            key="rightmove-listings",
            payload={
                "queries": [RIGHTMOVE_QUERY],
                "maxPagesPerQuery": 1,
                "queryConcurrency": 1,
            },
            expects_items=True,
            required_fields=("source", "source_id", "source_url"),
        ),
        ActorSpec(
            key="onthemarket-listings",
            payload={
                "queries": [ONTHEMARKET_QUERY],
                "maxPagesPerQuery": 1,
                "queryConcurrency": 1,
            },
            expects_items=True,
            required_fields=("source", "source_id", "source_url"),
        ),
        ActorSpec(
            key="epc-ct-ppd-unified",
            payload={
                "postcodes": ["CB1 1BS"],
                "includeEpc": False,
                "includeCouncilTax": True,
                "includePpd": True,
                "maxCouncilTaxPages": 2,
            },
            expects_items=True,
            required_fields=("postcode", "match_type"),
            note="EPC source skipped (no EPC_AUTH_EMAIL/TOKEN)",
        ),
        ActorSpec(
            key="planning-aggregator",
            payload={
                "mode": "recent",
                "sinceDays": 30,
                "councils": ["lambeth"],
                "maxPerCouncil": 10,
            },
            expects_items=True,
            required_fields=("council", "reference", "address"),
        ),
        ActorSpec(
            key="uk-auctions",
            payload={"discover": True, "maxAuctions": 1, "pageSize": 50},
            expects_items=True,
            required_fields=("auction_source", "lot_number", "address"),
        ),
        ActorSpec(
            key="landlord-network",
            payload={
                "seeds": [{"companyNumber": "00048839", "label": "Sainsbury's"}],
                "depth": 1,
                "maxCompanies": 5,
                "maxOfficers": 20,
            },
            expects_items=True,
            required_fields=("seed", "graph"),
            note="needs COMPANIES_HOUSE_API_KEY secret to emit items",
        ),
        ActorSpec(
            key="uk-tenders",
            payload={"sources": ["contracts-finder"], "limitPerSource": 5},
            expects_items=True,
            required_fields=("source", "source_id", "title"),
        ),
        ActorSpec(
            key="uk-demographics",
            payload={
                "areas": ["SW1A 1AA"],
                "sources": ["nomis", "census"],
                "censusTables": ["TS001"],
            },
            expects_items=True,
            required_fields=("resolved",),
        ),
        ActorSpec(
            key="uk-avm",
            payload={
                "targets": [
                    {"postcode": "CB1 1BS", "propertyType": "F", "floorAreaSqm": 65}
                ],
                "method": "median",
                "includeEpc": False,
                "includeNeighbourhood": False,
                "includeYield": False,
            },
            expects_items=True,
            required_fields=("target", "estimate"),
        ),
        ActorSpec(
            key="uk-climate-risk",
            payload={
                "points": ["SW1A 1AA"],
                "sources": ["flood", "radon"],
                "pointConcurrency": 1,
            },
            expects_items=True,
            required_fields=("resolved",),
        ),
        ActorSpec(
            key="uk-location-intel",
            payload={
                "points": ["SW1A 1AA"],
                "sources": ["amenities", "naptan"],
                "amenityRadiusM": 500,
                "naptanRadiusM": 500,
            },
            expects_items=True,
            required_fields=("resolved",),
        ),
        ActorSpec(
            key="uk-listings-hydrate",
            payload={
                "listingUrls": [
                    {
                        "url": "https://www.rightmove.co.uk/properties/173261858",
                        "transaction": "sale",
                    }
                ],
                "batchConcurrency": 1,
            },
            expects_items=True,
            required_fields=("source", "source_id", "source_url"),
        ),
        ActorSpec(
            key="uk-sold-prices",
            payload={"postcodes": ["CB1 1BS"], "maxPagesPerPostcode": 1},
            expects_items=True,
            required_fields=("postcode", "transaction_id", "price_gbp"),
        ),
    ]


@dataclass
class RunOutcome:
    spec: ActorSpec
    verdict: str
    status: str
    elapsed_s: float
    items: int
    run_id: str
    missing_fields: list[str]
    error: str | None
    sample_line: str


def _sample_line(
    result: ActorCallResult,
    required: tuple[str, ...],
) -> tuple[str, list[str]]:
    if not result.items:
        return "(empty dataset)", list(required)
    row = result.items[0]
    missing = [f for f in required if f not in row or row.get(f) in (None, "", [])]
    preview_keys = list(required) + ["title", "price", "sale_price"]
    preview = {k: row.get(k) for k in preview_keys if k in row}
    line = json.dumps(preview, default=str)[:240]
    return line, missing


async def run_one(spec: ActorSpec, *, timeout_s: float) -> RunOutcome:
    delegation = ApifyDelegation.resolve(spec.key)  # type: ignore[arg-type]
    if delegation is None:
        return RunOutcome(
            spec=spec,
            verdict="SKIP",
            status="(resolve returned None)",
            elapsed_s=0.0,
            items=0,
            run_id="",
            missing_fields=[],
            error=(
                "APIFY_API_TOKEN or APIFY_USERNAME missing for this slug"
            ),
            sample_line="",
        )
    delegation = ApifyDelegation(
        api_token=delegation.api_token,
        actor_id=delegation.actor_id,
        timeout_s=timeout_s,
        memory_mb=delegation.memory_mb,
        build=delegation.build,
    )
    t0 = time.monotonic()
    try:
        result = await delegation.call(spec.payload)
    except DelegationError as exc:
        return RunOutcome(
            spec=spec,
            verdict="FAIL",
            status="DelegationError",
            elapsed_s=time.monotonic() - t0,
            items=0,
            run_id="",
            missing_fields=[],
            error=str(exc),
            sample_line="",
        )
    elapsed = time.monotonic() - t0
    sample, missing = _sample_line(result, spec.required_fields)
    if spec.expects_items and not result.items:
        verdict = "PARTIAL"
    elif missing:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    return RunOutcome(
        spec=spec,
        verdict=verdict,
        status=result.status,
        elapsed_s=elapsed,
        items=len(result.items),
        run_id=result.run_id,
        missing_fields=missing,
        error=None,
        sample_line=sample,
    )


def _print_report(outcomes: list[RunOutcome]) -> None:
    print()
    print("=" * 78)
    print(f"{'ACTOR':<24} {'VERDICT':<8} {'ITEMS':>6} {'ELAPSED':>8}  RUN")
    print("-" * 78)
    for oc in outcomes:
        run_tag = oc.run_id[:14] if oc.run_id else "(no run)"
        print(
            f"{oc.spec.key:<24} {oc.verdict:<8} {oc.items:>6} "
            f"{oc.elapsed_s:>7.1f}s  {run_tag}"
        )
    print("=" * 78)
    for oc in outcomes:
        if oc.verdict == "PASS":
            continue
        print()
        print(f"[{oc.verdict}] {oc.spec.key}")
        if oc.spec.note:
            print(f"    note:   {oc.spec.note}")
        if oc.error:
            print(f"    error:  {oc.error[:300]}")
        if oc.missing_fields:
            print(f"    missing: {oc.missing_fields}")
        if oc.sample_line:
            print(f"    sample: {oc.sample_line}")
    print()


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Run hosted Apify actors via uk-property-apify-client delegation"
    )
    p.add_argument(
        "keys",
        nargs="*",
        help=(
            "actor slugs to exercise (default: all). Must be in "
            f"{sorted(KNOWN_ACTOR_SLUGS)}"
        ),
    )
    p.add_argument("--listings-only", action="store_true")
    p.add_argument("--timeout-s", type=float, default=600.0)
    p.add_argument(
        "--zoopla-proxy",
        choices=("residential", "datacenter", "none"),
        default="residential",
        help="proxy block passed to zoopla-listings (default: residential)",
    )
    p.add_argument(
        "--concurrent",
        type=int,
        default=1,
        help="max concurrent actor runs (default: 1 serial for clean logs)",
    )
    return p.parse_args()


async def _async_main() -> int:
    args = _parse_args()
    if not os.getenv("APIFY_API_TOKEN") or not os.getenv("APIFY_USERNAME"):
        print(
            "APIFY_API_TOKEN and APIFY_USERNAME must be set. "
            "Example: export APIFY_USERNAME=oscillating_binoculars",
            file=sys.stderr,
        )
        return 2

    specs = build_specs(zoopla_proxy=args.zoopla_proxy)
    if args.listings_only:
        listing_keys = {
            "zoopla-listings",
            "rightmove-listings",
            "onthemarket-listings",
            "uk-listings-hydrate",
        }
        specs = [s for s in specs if s.key in listing_keys]
    elif args.keys:
        wanted = set(args.keys)
        unknown = wanted - set(KNOWN_ACTOR_SLUGS)
        if unknown:
            print(f"Unknown actor slugs: {sorted(unknown)}", file=sys.stderr)
            return 2
        specs = [s for s in specs if s.key in wanted]

    print(
        f"Firing {len(specs)} actor(s) as {os.getenv('APIFY_USERNAME')} "
        f"with timeout={args.timeout_s}s, concurrency={args.concurrent}"
    )

    sem = asyncio.Semaphore(max(1, args.concurrent))

    async def _runner(spec: ActorSpec) -> RunOutcome:
        async with sem:
            print(f"  -> {spec.key:<24} starting...", flush=True)
            oc = await run_one(spec, timeout_s=args.timeout_s)
            tail = oc.sample_line[:90] if oc.sample_line else oc.error or ""
            print(
                f"     {spec.key:<24} {oc.verdict:<8} items={oc.items:<4} "
                f"{oc.elapsed_s:>6.1f}s  {tail}",
                flush=True,
            )
            return oc

    outcomes = await asyncio.gather(*(_runner(s) for s in specs))
    _print_report(outcomes)

    failed = [oc for oc in outcomes if oc.verdict == "FAIL"]
    return 1 if failed else 0


def main() -> int:
    return asyncio.run(_async_main())


if __name__ == "__main__":
    sys.exit(main())
