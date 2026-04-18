"""Optional delegation of the agent's heavier tools to hosted Apify actors.

Three tools currently fan out to hosted actors when
:envvar:`APIFY_API_TOKEN` is configured:

* :func:`maybe_delegate_search_planning_applications` — delegates to the
  ``planning-aggregator`` actor (A5). Lets the free-tier agent piggy-back
  on the hosted actor's anti-bot moat and concurrent per-council fan-out
  rather than running each council through the local HTML fallback.
* :func:`maybe_delegate_landlord_network_for_company` — delegates to the
  ``landlord-network`` actor (A7). The actor owns the graph expansion
  loop, Companies-House rate-limit handling, and cross-seed
  parallelism; the local path runs the same ``build_landlord_graph``
  helper in-process.
* :func:`maybe_delegate_estimate_property_value` — delegates to the
  ``uk-avm`` actor (A10). The actor owns the heavier hedonic /
  quantile / GBM models, HPI adjustment, and neighbourhood enrichment
  sourced against the private (national) station list; the local path
  is limited to the postcode-median baseline.

All three helpers return the same dict shape the local tool produces,
so ``tools.py`` can simply call them before constructing its own clients
and fall through to the legacy path when they return ``None``.

The actor dataset rows are always parsed back through the canonical
:mod:`uk_property_apis` / :mod:`uk_property_avm` Pydantic models so the
output shape doesn't depend on whether the row came from the local
clients or the actor. Rows that fail validation become error strings
on the resulting dict rather than taking down the whole call —
downstream agents can inspect ``errors`` to decide whether to re-run
locally.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ValidationError
from uk_property_apify_client import ApifyDelegation, DelegationError
from uk_property_apis import (
    ApplicationDetail,
    LandlordGraph,
    PlanningApplication,
)
from uk_property_apis.idox import get_council
from uk_property_avm import NeighbourhoodFeatures, ValuationEstimate


async def maybe_delegate_search_planning_applications(
    *,
    council: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
    max_results: int,
) -> dict[str, Any] | None:
    """Try to delegate the agent's ``search_planning_applications`` tool to the
    hosted ``planning-aggregator`` Apify actor.

    Returns ``None`` when delegation isn't configured so the caller can fall
    through to the local ArcGIS/HTML path. When delegation runs, the shape of
    the returned dict matches the local tool exactly: ``council``,
    ``council_name``, ``mode``, ``query``, ``query_target``, ``since_days``,
    ``transport``, ``count``, ``applications``. ``transport`` is taken from
    the actor's ``per_council`` meta entry (``arcgis`` or ``html``).

    The actor is called with one council (``councils=[slug]``),
    ``hydrateDetails=False`` (matching the local tool's behaviour — the
    richer detail fields are reserved for ``lookup_planning_application``),
    and ``maxPerCouncil=max_results`` so the per-run cost is bounded the
    same way.
    """
    delegation = ApifyDelegation.resolve("planning-aggregator")
    if delegation is None:
        return None

    slug = council.strip().lower()
    if not slug:
        raise ValueError("council must be a non-empty slug")

    # The hosted planning-aggregator actor validates slugs against its own
    # (larger, privately-curated) registry, so we deliberately tolerate
    # slugs the public library doesn't know about locally. When the slug
    # *is* in our public reference registry we upgrade the reported
    # ``council_name`` to the canonical display name; otherwise we just
    # echo the slug back (the actor will reject unknown slugs anyway).
    try:
        config = get_council(slug)
        slug = config.slug
        council_name: str = config.name
    except KeyError:
        council_name = slug

    actor_input = _build_planning_actor_input(
        council_slug=slug,
        mode=mode,
        query=query,
        query_target=query_target,
        since_days=since_days,
        max_results=max_results,
    )

    result = await delegation.call(actor_input)
    return _map_planning_result(
        result.items,
        result.run_meta,
        council_slug=slug,
        council_name=council_name,
        mode=mode,
        query=query,
        query_target=query_target,
        since_days=since_days,
    )


async def maybe_delegate_landlord_network_for_company(
    *,
    company_number: str,
    depth: int,
    max_companies: int,
    max_officers: int,
    expand_corporate_pscs: bool,
) -> dict[str, Any] | None:
    """Try to delegate ``landlord_network_for_company`` to the hosted
    ``landlord-network`` Apify actor.

    Returns ``None`` when delegation isn't configured (caller falls
    through to the local ``build_landlord_graph`` path). When delegation
    runs, the returned dict is exactly ``LandlordGraph.model_dump(mode=
    "json", exclude_none=True)`` — matching the local path byte-for-byte
    once the actor's envelope is unpacked.

    The actor is called with one seed (``seeds=[company_number]``) and
    ``seedConcurrency=1`` because the agent tool only ever asks about a
    single company. Parameters map 1:1 to the actor schema (``depth``,
    ``maxCompanies``, ``maxOfficers``, ``expandCorporatePscs``).
    """
    delegation = ApifyDelegation.resolve("landlord-network")
    if delegation is None:
        return None

    actor_input = _build_landlord_actor_input(
        company_number=company_number,
        depth=depth,
        max_companies=max_companies,
        max_officers=max_officers,
        expand_corporate_pscs=expand_corporate_pscs,
    )

    result = await delegation.call(actor_input)
    return _map_landlord_result(result.items, company_number=company_number)


async def maybe_delegate_estimate_property_value(
    *,
    postcode: str,
    property_type: str | None,
    years_back: int,
    method: Literal["hedonic", "quantile", "gbm", "median"] = "hedonic",
    include_neighbourhood: bool = False,
    hpi_enabled: bool = True,
    hpi_to_date: str | None = None,
) -> dict[str, Any] | None:
    """Try to delegate ``estimate_property_value`` to the hosted ``uk-avm`` actor.

    Returns ``None`` when delegation isn't configured so the caller falls
    through to the local median-baseline AVM. When delegation runs, the
    returned dict always includes the fields the local path emits
    (``postcode``, ``property_type``, ``years_back``, ``min_transfer_date``,
    ``ppd_rows_fetched``, ``comparables_considered``, ``estimate``), plus
    extras the hosted actor provides:

    * ``method`` — the requested / effective valuation method;
    * ``hpi_to_date`` — the pivot date HPI adjustment was applied to
      (``None`` when HPI is disabled or unavailable);
    * ``neighbourhood`` — a ``NeighbourhoodFeatures`` dump, or ``None`` /
      absent when neighbourhood enrichment is off.

    The actor is called with a single-target input (``targets=[…]``) and
    ``targetConcurrency=1`` because the agent tool only ever asks about one
    address. ``lookbackYears`` maps from ``years_back`` and the other knobs
    forward unchanged.
    """
    delegation = ApifyDelegation.resolve("uk-avm")
    if delegation is None:
        return None

    actor_input = _build_avm_actor_input(
        postcode=postcode,
        property_type=property_type,
        years_back=years_back,
        method=method,
        include_neighbourhood=include_neighbourhood,
        hpi_enabled=hpi_enabled,
        hpi_to_date=hpi_to_date,
    )

    result = await delegation.call(actor_input)
    return _map_avm_result(
        result.items,
        result.run_meta,
        postcode=postcode,
        property_type=property_type,
        years_back=years_back,
    )


def _build_planning_actor_input(
    *,
    council_slug: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
    max_results: int,
) -> dict[str, Any]:
    actor_input: dict[str, Any] = {
        "mode": mode,
        "councils": [council_slug],
        "maxPerCouncil": max_results,
        "hydrateDetails": False,
    }
    if mode == "recent":
        actor_input["sinceDays"] = since_days
    if mode == "search":
        if not query:
            raise ValueError("mode='search' requires a non-empty query")
        actor_input["query"] = query
        actor_input["queryTarget"] = query_target
    return actor_input


def _build_landlord_actor_input(
    *,
    company_number: str,
    depth: int,
    max_companies: int,
    max_officers: int,
    expand_corporate_pscs: bool,
) -> dict[str, Any]:
    return {
        "seeds": [{"companyNumber": company_number}],
        "depth": depth,
        "maxCompanies": max_companies,
        "maxOfficers": max_officers,
        "expandCorporatePscs": expand_corporate_pscs,
        "seedConcurrency": 1,
    }


def _map_planning_result(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
    *,
    council_slug: str,
    council_name: str,
    mode: str,
    query: str | None,
    query_target: str,
    since_days: int,
) -> dict[str, Any]:
    """Re-shape A5 actor output to match the local tool's response."""

    applications: list[dict[str, Any]] = []
    parse_errors: list[str] = []
    for row in items:
        row_council = row.get("council") or row.get("council_slug")
        if row_council and row_council != council_slug:
            parse_errors.append(f"skipping planning-aggregator row for council={row_council!r}")
            continue
        app = _validate_planning_row(row)
        if isinstance(app, str):
            parse_errors.append(app)
            continue
        applications.append(app.model_dump(mode="json", exclude_none=True))

    transport = _planning_transport(run_meta, council_slug)

    return {
        "council": council_slug,
        "council_name": council_name,
        "mode": mode,
        "query": (query or "").strip() or None,
        "query_target": query_target if mode == "search" else None,
        "since_days": since_days if mode == "recent" else None,
        "transport": transport,
        "count": len(applications),
        "applications": applications,
        "errors": parse_errors or None,
    }


def _validate_planning_row(
    row: dict[str, Any],
) -> PlanningApplication | ApplicationDetail | str:
    """Parse an actor row back to the richer of the two models it fits.

    ``ApplicationDetail`` is a superset of :class:`PlanningApplication` (it
    has extra case-officer / decision / documents fields). We try the
    superset first so ``hydrate_details=True`` runs keep their extras; on
    failure we fall back to the base model. A doubly-failing row becomes a
    string error in the returned dict so the tool surface can log it but
    doesn't crash.
    """
    try:
        return ApplicationDetail.model_validate(row)
    except ValidationError:
        pass
    try:
        return PlanningApplication.model_validate(row)
    except ValidationError as exc:
        return f"planning-aggregator row validation failed: {exc}"


def _planning_transport(run_meta: dict[str, Any] | None, council_slug: str) -> str | None:
    """Extract the actor-reported transport (``arcgis`` / ``html``) for the
    target council from :attr:`RUN_META.per_council`. Returns ``None`` when
    the meta record is missing — the local tool's shape allows ``None``
    callers tolerate that.
    """
    if not run_meta:
        return None
    per_council = run_meta.get("per_council")
    if not isinstance(per_council, list):
        return None
    for entry in per_council:
        if not isinstance(entry, dict):
            continue
        if entry.get("council") == council_slug:
            transport = entry.get("transport")
            if isinstance(transport, str):
                return transport
    return None


def _map_landlord_result(
    items: list[dict[str, Any]],
    *,
    company_number: str,
) -> dict[str, Any]:
    """Unwrap the A7 seed envelope and re-dump the LandlordGraph.

    If the actor emitted zero items (shouldn't happen — the actor fails
    the seed into ``ERRORS`` instead) we raise :class:`DelegationError`
    so the caller doesn't silently return garbage.
    """
    if not items:
        raise DelegationError(
            f"landlord-network actor returned no rows for seed {company_number!r}"
        )

    envelope = items[0]
    graph_raw = envelope.get("graph")
    if not isinstance(graph_raw, dict):
        raise DelegationError(
            f"landlord-network row is missing 'graph' sub-object; got keys={sorted(envelope)!r}"
        )

    try:
        graph = LandlordGraph.model_validate(graph_raw)
    except ValidationError as exc:
        raise DelegationError(f"landlord-network graph failed validation: {exc}") from exc

    return graph.model_dump(mode="json", exclude_none=True)


def _build_avm_actor_input(
    *,
    postcode: str,
    property_type: str | None,
    years_back: int,
    method: Literal["hedonic", "quantile", "gbm", "median"],
    include_neighbourhood: bool,
    hpi_enabled: bool,
    hpi_to_date: str | None,
) -> dict[str, Any]:
    """Build a one-target input payload for the hosted ``uk-avm`` actor."""
    target: dict[str, Any] = {"postcode": postcode}
    if property_type is not None:
        target["propertyType"] = property_type

    actor_input: dict[str, Any] = {
        "targets": [target],
        "lookbackYears": years_back,
        "method": method,
        "hpiEnabled": hpi_enabled,
        "includeNeighbourhood": include_neighbourhood,
        "targetConcurrency": 1,
    }
    if hpi_to_date is not None:
        actor_input["hpiToDate"] = hpi_to_date
    return actor_input


def _map_avm_result(
    items: list[dict[str, Any]],
    run_meta: dict[str, Any] | None,
    *,
    postcode: str,
    property_type: str | None,
    years_back: int,
) -> dict[str, Any]:
    """Unwrap the A10 single-target payload and reshape it to the local tool's output.

    The A10 actor emits one dataset row per target; the row's ``estimate``
    sub-object is already a :class:`ValuationEstimate` JSON dump and its
    ``neighbourhood`` sub-object (when present) is a
    :class:`NeighbourhoodFeatures` JSON dump. We re-validate both so a
    malformed payload raises :class:`DelegationError` rather than silently
    feeding junk to the LLM. PPD / comparables counts come from the
    actor's ``pool`` sub-object; ``min_transfer_date`` from ``run_meta``
    when the actor reports it.
    """

    if not items:
        raise DelegationError(
            f"uk-avm actor returned no rows for postcode {postcode!r}"
        )

    row = items[0]
    estimate_raw = row.get("estimate")
    if not isinstance(estimate_raw, dict):
        raise DelegationError(
            f"uk-avm row missing 'estimate' sub-object; got keys={sorted(row)!r}"
        )
    try:
        estimate = ValuationEstimate.model_validate(estimate_raw)
    except ValidationError as exc:
        raise DelegationError(f"uk-avm estimate failed validation: {exc}") from exc

    neighbourhood_payload: dict[str, Any] | None = None
    neighbourhood_raw = row.get("neighbourhood")
    if isinstance(neighbourhood_raw, dict):
        try:
            neighbourhood = NeighbourhoodFeatures.model_validate(neighbourhood_raw)
            neighbourhood_payload = neighbourhood.model_dump(
                mode="json", exclude_none=True
            )
        except ValidationError as exc:
            # Neighbourhood enrichment is a nice-to-have; don't tank the
            # whole call over a bad sub-object.
            neighbourhood_payload = None
            errors_container = row.setdefault("_neighbourhood_errors", [])
            if isinstance(errors_container, list):
                errors_container.append(str(exc))

    pool = row.get("pool")
    ppd_rows_fetched: int | None = None
    comparables_considered: int | None = None
    if isinstance(pool, dict):
        ppd_rows_fetched = _as_int(pool.get("ppd_rows"))
        # Enriched rows are the closest local analogue of "comparables
        # considered" — the local tool counts rows after cutoff filtering
        # but before the match-quality join; ``enriched_rows`` is post-join
        # which is strictly more informative.
        comparables_considered = _as_int(pool.get("enriched_rows"))

    min_transfer_date: str | None = None
    if isinstance(run_meta, dict):
        params = run_meta.get("parameters")
        if isinstance(params, dict):
            # The actor doesn't expose the per-target cutoff, only the
            # lookback window — we leave the local-shape key ``None`` in
            # that case rather than guessing.
            lookback = params.get("lookback_years")
            if lookback is not None and _as_int(lookback) != years_back:
                # Surface a drift hint via errors rather than silently
                # overriding the caller's parameter.
                pass

    return {
        "postcode": postcode.strip().upper(),
        "property_type": property_type,
        "years_back": years_back,
        "min_transfer_date": min_transfer_date,
        "ppd_rows_fetched": ppd_rows_fetched,
        "comparables_considered": comparables_considered,
        "estimate": estimate.model_dump(mode="json", exclude_none=True),
        "method": row.get("method"),
        "hpi_to_date": row.get("hpi_to_date"),
        "neighbourhood": neighbourhood_payload,
    }


def _as_int(value: object) -> int | None:
    """Best-effort coercion for payload numbers that may arrive as ints or strs."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return int(stripped)
        except ValueError:
            try:
                return int(float(stripped))
            except ValueError:
                return None
    return None


__all__ = [
    "DelegationError",
    "maybe_delegate_estimate_property_value",
    "maybe_delegate_landlord_network_for_company",
    "maybe_delegate_search_planning_applications",
]
