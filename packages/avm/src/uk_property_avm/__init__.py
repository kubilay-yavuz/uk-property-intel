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
from uk_property_avm.features import (
    AmenityDensitySource,
    CrimeStatsSource,
    FloodWarningSource,
    NeighbourhoodFeatureExtractor,
    NeighbourhoodFeatures,
    PostcodeGeocoder,
    Station,
    StationDataset,
    haversine_km,
)
from uk_property_avm.gbm import (
    GBMHedonicModel,
    RegressorFactory,
    SklearnQuantileRegressor,
    default_regressor_factory,
    estimate_value_gbm_hedonic,
    make_lightgbm_regressor_factory,
)
from uk_property_avm.hedonic import (
    HedonicModel,
    HedonicTarget,
    estimate_value_hedonic,
    normalise_age_band,
    normalise_tenure,
)
from uk_property_avm.hpi import (
    HPIAdjuster,
    adjust_comparable_prices,
    adjust_enriched_prices,
    list_ons_regions,
    parse_month_key,
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
from uk_property_avm.quantile import (
    QuantileHedonicModel,
    estimate_value_quantile_hedonic,
)

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_LOOKBACK_YEARS",
    "AmenityDensitySource",
    "Comparable",
    "CrimeStatsSource",
    "EnrichedComparable",
    "EvalReport",
    "EvalSegment",
    "FloodWarningSource",
    "GBMHedonicModel",
    "HPIAdjuster",
    "HedonicModel",
    "HedonicTarget",
    "JoinConfig",
    "NeighbourhoodFeatureExtractor",
    "NeighbourhoodFeatures",
    "PostcodeGeocoder",
    "Predictor",
    "PropertyType",
    "QuantileHedonicModel",
    "RegressorFactory",
    "SklearnQuantileRegressor",
    "Station",
    "StationDataset",
    "ValuationEstimate",
    "__version__",
    "adjust_comparable_prices",
    "adjust_enriched_prices",
    "comparables_from_ppd",
    "default_regressor_factory",
    "enrich_comparables",
    "estimate_value",
    "estimate_value_gbm_hedonic",
    "estimate_value_hedonic",
    "estimate_value_quantile_hedonic",
    "evaluate_model",
    "format_report_markdown",
    "haversine_km",
    "list_ons_regions",
    "make_lightgbm_regressor_factory",
    "normalise_address_key",
    "normalise_age_band",
    "normalise_postcode",
    "normalise_tenure",
    "parse_floor_area",
    "parse_month_key",
    "time_based_split",
]
