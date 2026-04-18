# uk-property-avm

Automated Valuation Model — the **middle tier** between back-of-envelope
£/sqft multiples and institutional-grade AVMs used by lenders.

## What ships today (Phase C MVP)

Four composable modules:

| Module | Role |
|---|---|
| `uk_property_avm.baseline` | Rolling-median baseline keyed by postcode + type (transparent fallback). |
| `uk_property_avm.join` | PPD + EPC join in DuckDB: `(postcode, paon, street)` key, time-nearest EPC selection, optional postcode fallback. |
| `uk_property_avm.hedonic` | Log-price linear hedonic with OHE features + IQR-residual bands. Falls back to the median baseline when the pool is thin. |
| `uk_property_avm.eval` | Time-based train/test split, MAPE / coverage / per-(area × type) breakdown, Markdown rendering. |

## Feature set

- **PPD**: `price`, `transfer_date`, `property_type` (D/S/T/F/O), `tenure`,
  `paon`/`street`/`postcode`.
- **EPC** (via the join): `total_floor_area` → sqm, `current_energy_rating`
  (A-G), `current_energy_efficiency` (0-100), `built_form`,
  `construction_age_band`, EPC's own `property-type`.
- **Derived**: postcode area OHE, age-band decadal OHE, log(floor_area)
  continuous, tenure canonicalised to FREEHOLD/LEASEHOLD/UNKNOWN.

## Quick start

```python
from uk_property_apis import EPCClient, LandRegistryClient
from uk_property_avm import (
    HedonicTarget,
    enrich_comparables,
    estimate_value_hedonic,
)

async with LandRegistryClient() as ppd, EPCClient() as epc:
    ppd_rows = await ppd.search_by_postcode("SW2 5TN")
    epc_page = await epc.search_domestic(postcode="SW2 5TN")
    enriched = enrich_comparables(ppd_rows, epc_page.rows)

estimate = estimate_value_hedonic(
    HedonicTarget(
        postcode="SW2 5TN",
        property_type="T",
        floor_area_sqm=82,
    ),
    enriched,
)
print(estimate.estimate_gbp, estimate.low_gbp, estimate.high_gbp)
```

## Evaluation

```python
from uk_property_avm import evaluate_model, format_report_markdown, time_based_split

train, test = time_based_split(enriched, holdout_months=6)
report = evaluate_model(train, test)
print(format_report_markdown(report))
```

The harness is model-agnostic — you can drop in any
`(HedonicTarget, Iterable[EnrichedComparable]) -> ValuationEstimate`
callable and score it on the same hold-out split.

## Roadmap

- Quantile regression for tighter, asymmetric bands.
- Gradient-boosted trees (XGBoost / LightGBM) with monotonic constraints on
  floor area and beds.
- HPI-adjusted comparables (nationwide + regional index).
- Distance-to-station, school quality, crime score, flood risk,
  planning-permission velocity as additional features.
