"""UK property automated valuation model (middle tier)."""

from __future__ import annotations

from uk_property_avm.baseline import comparables_from_ppd, estimate_value
from uk_property_avm.eval import (
    EvalReport,
    EvalSegment,
    Predictor,
    evaluate_model,
    format_report_markdown,
    time_based_split,
)
from uk_property_avm.hedonic import (
    HedonicModel,
    HedonicTarget,
    estimate_value_hedonic,
    normalise_age_band,
    normalise_tenure,
)
from uk_property_avm.join import (
    DEFAULT_LOOKBACK_YEARS,
    JoinConfig,
    enrich_comparables,
    normalise_address_key,
    normalise_postcode,
    parse_floor_area,
)
from uk_property_avm.models import (
    Comparable,
    EnrichedComparable,
    PropertyType,
    ValuationEstimate,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_LOOKBACK_YEARS",
    "Comparable",
    "EnrichedComparable",
    "EvalReport",
    "EvalSegment",
    "HedonicModel",
    "HedonicTarget",
    "JoinConfig",
    "Predictor",
    "PropertyType",
    "ValuationEstimate",
    "__version__",
    "comparables_from_ppd",
    "enrich_comparables",
    "estimate_value",
    "estimate_value_hedonic",
    "evaluate_model",
    "format_report_markdown",
    "normalise_address_key",
    "normalise_age_band",
    "normalise_postcode",
    "normalise_tenure",
    "parse_floor_area",
    "time_based_split",
]
