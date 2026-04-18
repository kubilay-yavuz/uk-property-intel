"""Tests for the held-out evaluation harness."""

from __future__ import annotations

import numpy as np
import pytest
from uk_property_avm import (
    EnrichedComparable,
    EvalReport,
    HedonicTarget,
    ValuationEstimate,
    evaluate_model,
    format_report_markdown,
    time_based_split,
)


def _row(
    idx: int,
    *,
    price: int,
    transfer_date: str = "2024-03-01",
    postcode: str = "SW2 5TN",
    property_type: str = "T",
    floor_area_sqm: float | None = 80.0,
    energy_efficiency: int | None = 70,
    age_band: str | None = "England and Wales: 1900-1929",
) -> EnrichedComparable:
    return EnrichedComparable(
        transaction_id=f"T{idx:05d}",
        price=price,
        transfer_date=transfer_date,
        property_type=property_type,
        tenure="F",
        paon=str(10 + idx),
        street="Main Road",
        postcode=postcode,
        floor_area_sqm=floor_area_sqm,
        energy_rating="C",
        energy_efficiency=energy_efficiency,
        built_form="Mid-Terrace",
        construction_age_band=age_band,
        epc_property_type="House",
        match_quality="exact_address",
    )


def _synthetic_market_dated(
    n: int,
    *,
    start_date: str = "2022-01-01",
    end_date: str = "2024-12-31",
    noise_sigma: float = 0.05,
    seed: int = 42,
) -> list[EnrichedComparable]:
    """Synthetic market with timestamps spread uniformly across a window."""

    rng = np.random.default_rng(seed)
    floors = rng.uniform(40, 150, size=n)
    noise = rng.normal(0, noise_sigma, size=n)
    log_prices = 10 + 0.8 * np.log(floors) + noise
    prices = np.round(np.exp(log_prices)).astype(int)

    start = np.datetime64(start_date)
    end = np.datetime64(end_date)
    span_days = (end - start).astype("timedelta64[D]").astype(int)
    day_offsets = rng.integers(0, span_days, size=n)
    dates = [(start + np.timedelta64(int(d), "D")).astype(str) for d in day_offsets]

    return [
        EnrichedComparable(
            transaction_id=f"T{i:05d}",
            price=int(prices[i]),
            transfer_date=dates[i],
            property_type="T",
            tenure="F",
            paon=str(10 + i),
            street="Main Road",
            postcode="SW2 5TN",
            floor_area_sqm=float(floors[i]),
            energy_rating="C",
            energy_efficiency=70,
            built_form="Mid-Terrace",
            construction_age_band="England and Wales: 1900-1929",
            epc_property_type="House",
            match_quality="exact_address",
        )
        for i in range(n)
    ]


class TestTimeBasedSplit:
    def test_defaults_to_six_month_trailing_holdout(self) -> None:
        rows = [
            _row(1, price=500_000, transfer_date="2022-01-01"),
            _row(2, price=550_000, transfer_date="2023-06-01"),
            _row(3, price=600_000, transfer_date="2024-10-01"),
            _row(4, price=620_000, transfer_date="2024-12-01"),
        ]

        train, test = time_based_split(rows, holdout_months=6)

        train_ids = {r.transaction_id for r in train}
        test_ids = {r.transaction_id for r in test}
        assert "T00001" in train_ids
        assert "T00002" in train_ids
        assert "T00003" in test_ids
        assert "T00004" in test_ids

    def test_explicit_cutoff_is_honoured(self) -> None:
        rows = [
            _row(1, price=500_000, transfer_date="2023-01-01"),
            _row(2, price=550_000, transfer_date="2024-01-01"),
            _row(3, price=600_000, transfer_date="2024-12-01"),
        ]

        train, test = time_based_split(rows, cutoff_date="2024-06-01")

        assert {r.transaction_id for r in train} == {"T00001", "T00002"}
        assert {r.transaction_id for r in test} == {"T00003"}

    def test_drops_unparseable_dates(self) -> None:
        rows = [
            _row(1, price=500_000, transfer_date="2023-01-01"),
            _row(2, price=550_000, transfer_date="not-a-date"),
        ]

        train, test = time_based_split(rows, holdout_months=6)

        total_ids = {r.transaction_id for r in train} | {r.transaction_id for r in test}
        assert "T00001" in total_ids
        assert "T00002" not in total_ids

    def test_empty_input_returns_empty_split(self) -> None:
        train, test = time_based_split([])
        assert train == []
        assert test == []

    def test_rejects_bad_cutoff_date(self) -> None:
        with pytest.raises(ValueError, match="cutoff_date must be"):
            time_based_split([_row(1, price=500_000)], cutoff_date="not-a-date")


class TestEvaluateModel:
    def test_hedonic_model_scores_well_on_clean_synthetic_data(self) -> None:
        pool = _synthetic_market_dated(400, noise_sigma=0.05)
        train, test = time_based_split(pool, holdout_months=6)

        assert len(train) > 0
        assert len(test) > 0

        report = evaluate_model(train, test)

        assert isinstance(report, EvalReport)
        assert report.n_train == len(train)
        assert report.n_test == len(test)
        assert report.n_scored > 0
        # Clean data + log-linear generator: median APE < 10%.
        assert report.mape < 0.10
        # Coverage for IQR residual-based bands should land around 50%.
        assert 0.3 <= report.coverage <= 0.7

    def test_segments_cover_all_distinct_area_type_pairs(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)
        mixed_test = [
            _row(1001, price=500_000, postcode="SW2 5TN", property_type="T"),
            _row(1002, price=450_000, postcode="SW2 5TN", property_type="F"),
            _row(1003, price=700_000, postcode="E1 1AA", property_type="T"),
        ]

        report = evaluate_model(train, mixed_test)

        keys = {(s.postcode_area, s.property_type) for s in report.segments}
        assert ("SW", "T") in keys
        assert ("SW", "F") in keys
        assert ("E", "T") in keys

    def test_custom_predictor_is_honoured(self) -> None:
        train = [_row(i, price=500_000) for i in range(30)]
        test = [_row(100 + i, price=500_000) for i in range(5)]

        def always_500k(
            target: HedonicTarget, _pool: object
        ) -> ValuationEstimate:
            return ValuationEstimate(
                estimate_gbp=500_000,
                low_gbp=450_000,
                high_gbp=550_000,
                confidence="medium",
                comparables_used=0,
                basis="national",
                postcode=target.postcode,
                property_type=target.property_type,
            )

        report = evaluate_model(train, test, predictor=always_500k)

        assert report.n_scored == 5
        assert report.mape == pytest.approx(0.0)
        assert report.coverage == pytest.approx(1.0)

    def test_empty_test_set_returns_zero_metrics(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)

        report = evaluate_model(train, [])

        assert report.n_scored == 0
        assert report.mape == 0.0
        assert report.coverage == 0.0
        assert report.segments == []

    def test_skips_rows_with_zero_price(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)
        test = [
            _row(1, price=0, transfer_date="2024-12-01"),
            _row(2, price=500_000, transfer_date="2024-12-01"),
        ]

        report = evaluate_model(train, test)

        assert report.n_scored == 1

    def test_skips_rows_without_postcode(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)
        bad = _row(1, price=500_000, transfer_date="2024-12-01")
        bad.postcode = None
        report = evaluate_model(train, [bad])

        assert report.n_scored == 0


class TestFormatReportMarkdown:
    def test_renders_headline_and_segment_tables(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)
        test = [
            _row(1, price=500_000, postcode="SW2 5TN", property_type="T"),
            _row(2, price=600_000, postcode="SW2 5TN", property_type="T"),
        ]
        report = evaluate_model(train, test)

        md = format_report_markdown(report)

        assert "MAPE" in md
        assert "Coverage" in md
        assert "Postcode area" in md
        assert "| SW | T |" in md

    def test_report_is_pydantic_round_trippable(self) -> None:
        train = _synthetic_market_dated(200, noise_sigma=0.05)
        test = [_row(1, price=500_000)]

        report = evaluate_model(train, test)
        dumped = report.model_dump(mode="json")
        restored = EvalReport.model_validate(dumped)

        assert restored.mape == report.mape
        assert restored.coverage == report.coverage
        assert len(restored.segments) == len(report.segments)
