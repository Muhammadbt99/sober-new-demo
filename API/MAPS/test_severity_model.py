"""Tests for the retrained crash-severity model and its scenario endpoint.

The old model could not beat the majority class because its training data had
no vehicle type or contributing factor. These tests pin the replacement: the
feature contract is complete, the model is served honestly, and the strongest
known severity signal (a bicycle) actually moves the prediction.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import app  # noqa: E402
from severity_features import CLASS_NAMES, SEVERITY_FEATURES, build_row_from_parts  # noqa: E402


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as client:
        yield client


class TestSeverityFeatures:
    def test_row_covers_every_feature_in_order(self):
        row = build_row_from_parts(
            hour=2,
            month=7,
            is_weekend=1,
            lat=40.75,
            lon=-73.99,
            borough="BROOKLYN",
            vehicles=["Bike"],
            factors=["Unsafe Speed"],
        )
        assert set(row) == set(SEVERITY_FEATURES)
        assert row["is_bicycle"] == 1.0
        assert row["is_sedan"] == 0.0
        assert row["factor_unsafe_speed"] == 1.0
        assert row["borough_code"] > 0

    def test_unknown_vehicle_maps_to_other_instead_of_raising(self):
        row = build_row_from_parts(
            hour=12,
            month=1,
            is_weekend=0,
            lat=40.75,
            lon=-73.99,
            borough=None,
            vehicles=["Hovercraft"],
            factors=[],
        )
        assert row["is_other"] == 1.0
        assert row["borough_code"] == 0.0

    def test_placeholder_factors_are_not_counted(self):
        row = build_row_from_parts(
            hour=12,
            month=1,
            is_weekend=0,
            lat=40.75,
            lon=-73.99,
            borough="QUEENS",
            vehicles=["Sedan", "Unspecified"],
            factors=["Unspecified"],
        )
        assert row["n_vehicles"] == 1.0
        assert row["n_factors"] == 0.0


class TestMlStats:
    def test_stats_exposes_the_severity_features(self, client):
        response = client.get("/ml/stats")
        assert response.status_code == 200
        data = response.get_json()
        assert data["model"] != "Unavailable"
        assert "is_bicycle" in data["features"]
        assert data["macro_f1"] is not None
        assert data["baseline_macro_f1"] is not None

    def test_stats_never_claims_the_model_scores_risk(self, client):
        data = client.get("/ml/stats").get_json()
        assert data["used_for_risk_scoring"] is False
        assert "excluded from /risk" in data["status"]

    def test_stats_reports_the_context_only_ablation(self, client):
        data = client.get("/ml/stats").get_json()
        ablation = data["ablation_context_only"]
        assert ablation is not None
        assert "is_bicycle" not in ablation["features"]

    def test_stats_discloses_probability_calibration(self, client):
        data = client.get("/ml/stats").get_json()
        calibration = data["calibration"]
        assert calibration is not None
        fatal = calibration["per_class"]["fatal_risk"]
        assert fatal["ratio"] is not None
        # Class weighting is the reason the fatal score is usable at all, and the
        # reason it must never be presented as a real-world fatality rate.
        assert fatal["ratio"] > 1
        assert len(calibration["fatal_reliability"]) == 10


class TestSeverityScenario:
    def test_default_scenario_returns_a_normalised_prediction(self, client):
        response = client.get("/ml/severity")
        assert response.status_code == 200
        data = response.get_json()
        assert data["predicted"] in CLASS_NAMES
        assert 0.0 <= data["confidence"] <= 1.0
        assert set(data["probabilities"]) == set(CLASS_NAMES)
        assert abs(sum(data["probabilities"].values()) - 1.0) < 0.01
        assert data["used_for_risk_scoring"] is False

    def test_a_bicycle_scenario_is_more_likely_fatal_than_a_sedan(self, client):
        bike = client.get("/ml/severity?hour=2&vehicle=Bike&factor=Unsafe%20Speed").get_json()
        sedan = client.get("/ml/severity?hour=2&vehicle=Sedan").get_json()
        assert (
            bike["probabilities"]["fatal_risk"] > sedan["probabilities"]["fatal_risk"]
        ), "the model did not learn the strongest severity signal in the data"

    def test_invalid_hour_is_rejected(self, client):
        assert client.get("/ml/severity?hour=99").status_code == 400

    def test_invalid_month_is_rejected(self, client):
        assert client.get("/ml/severity?month=13").status_code == 400

    def test_unknown_vehicle_is_accepted(self, client):
        assert client.get("/ml/severity?vehicle=Hovercraft").status_code == 200

    def test_response_says_the_probabilities_are_not_calibrated(self, client):
        data = client.get("/ml/severity").get_json()
        assert data["probability_semantics"] == "class_weighted"
        assert "class-weighted" in data["note"]
