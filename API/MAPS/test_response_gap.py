"""The response-gap map: high crash risk, far from a mapped hospital."""
from __future__ import annotations

import pandas as pd
import pytest

from app import app
from global_model.build_hospitals import keep, merge_duplicates, uae_hospital
import response_gap
from response_gap import FAR_KM, HIGH_PERCENTILE, here, nearest, quadrant, summarise

HOSPITALS = pd.DataFrame(
    [
        {"region": "x", "lat": 25.00, "lon": 55.00, "name": "Central Hospital", "emergency": "yes"},
        {"region": "x", "lat": 25.50, "lon": 55.00, "name": "", "emergency": ""},
    ]
)


def cell(name, lat, percentile, lon=55.0):
    return {"cell": name, "lat": lat, "lon": lon, "name": name, "percentile": percentile}


class TestCleaning:
    @pytest.mark.parametrize(
        "name",
        [
            "Samaa Medical Centre",
            "Sharjah Medical Center",
            "Perfect Doctors Clinic",
            "Getchecked Clinic",
            "Dialysis Center Al Towar",
            "Kottakal Ayurvedic Centre",
            "Burjeel Day Surgery Center",
            "Gulf Diagnostic Centre",
            "Bright Smile Dental",
            "Skin and Dermatology Clinic",
            "Primary Healthcare Al Muhaisnah Center",
            "City IVF Hospital",
            "Al Noor Pharmacy",
            "تمت ازالته",
        ],
    )
    def test_places_that_are_not_general_hospitals_go(self, name):
        assert keep(name, None) is False

    @pytest.mark.parametrize("name", ["Rashid Hospital", "Cleveland Clinic Abu Dhabi", "Al Qassimi Hospital", ""])
    def test_real_hospitals_stay(self, name):
        assert keep(name, None) is True

    @pytest.mark.parametrize(
        "name",
        ["Brookdale Hospital Medical Center", "Jamaica Hospital Medical Center"],
    )
    def test_a_hospital_called_a_medical_center_stays(self, name):
        assert keep(name, None) is True

    def test_emergency_care_outweighs_the_name(self):
        assert keep("Wyckoff Heights Medical Center", "yes") is True

    def test_a_specialist_place_is_dropped_even_with_hospital_in_its_name(self):
        assert keep("Sunrise Eye Hospital", None) is False

    def test_a_place_marked_no_emergency_goes(self):
        assert keep("Rashid Hospital", "no") is False

    def test_one_place_mapped_twice_is_kept_once_with_its_best_record(self):
        rows = [
            {"lat": 25.0, "lon": 55.0, "name": "", "emergency": ""},
            {"lat": 25.0005, "lon": 55.0, "name": "Rashid Hospital", "emergency": "yes"},
        ]
        assert [row["name"] for row in merge_duplicates(rows)] == ["Rashid Hospital"]

    def test_a_hospital_name_beats_a_stray_label_on_the_same_spot(self):
        rows = [
            {"lat": 25.0, "lon": 55.0, "name": "HLG Umar", "emergency": ""},
            {"lat": 25.0005, "lon": 55.0, "name": "Fakeeh University Hospital", "emergency": ""},
        ]
        assert [row["name"] for row in merge_duplicates(rows)] == ["Fakeeh University Hospital"]

    def test_two_hospitals_apart_both_stay(self):
        rows = [
            {"lat": 25.0, "lon": 55.0, "name": "A", "emergency": ""},
            {"lat": 25.01, "lon": 55.0, "name": "B", "emergency": ""},
        ]
        assert len(merge_duplicates(rows)) == 2


DUBAI = (25.2, 55.3)
DOHA = (25.29, 51.53)
MUSCAT = (23.59, 58.4)


class TestUaeRule:
    """The strict UAE rule: a blank name, a point outside the UAE, or junk goes."""

    @pytest.mark.parametrize("name", ["", "   "])
    def test_a_blank_name_goes(self, name):
        assert uae_hospital(name, *DUBAI) is False

    def test_a_hospital_in_qatar_or_oman_goes_even_if_named_one(self):
        assert uae_hospital("Hamad Hospital", *DOHA) is False
        assert uae_hospital("Royal Hospital", *MUSCAT) is False

    @pytest.mark.parametrize(
        "name",
        ["Family Care Centre Dubai", "Abu Dhabi Outpatient Hospital", "HR Building Hospital", "مستشفى"],
    )
    def test_junk_names_go(self, name):
        assert uae_hospital(name, *DUBAI) is False

    @pytest.mark.parametrize(
        "name",
        [
            "Burjeel Medical City", "Cleveland Clinic Abu Dhabi", "Danat Al Emarat", "THUMBAY MEDI City",
            "Doctor Sulaiman Al Habib",
        ],
    )
    def test_major_hospitals_without_the_word_stay(self, name):
        assert uae_hospital(name, *DUBAI) is True

    @pytest.mark.parametrize("name", ["Rashid Hospital", "مستشفى الامارات"])
    def test_a_named_hospital_stays(self, name):
        assert uae_hospital(name, *DUBAI) is True

    @pytest.mark.parametrize("name", ["Samaa Medical Centre", "Getchecked Clinic", "مركز شرطة هيلي", "Asd.saadiat"])
    def test_a_clinic_or_police_station_goes_without_the_word(self, name):
        assert uae_hospital(name, *DUBAI) is False


class TestTheHospitalListItself:
    """A hospital missing from the list shows up as a gap that does not exist."""

    LIST = pd.read_csv(response_gap.HOSPITALS_FILE, keep_default_na=False)

    @pytest.mark.parametrize(
        "name",
        [
            "Brookdale", "Wyckoff", "Interfaith", "Jamaica", "Flushing", "Woodhull", "Elmhurst",
            "Bellevue", "Kings County", "Maimonides", "Lincoln", "Jacobi", "Montefiore",
        ],
    )
    def test_new_york(self, name):
        names = self.LIST[self.LIST["region"] == "nyc"]["name"]
        assert names.str.contains(name, case=False).any()

    @pytest.mark.parametrize(
        "name",
        [
            "Rashid Hospital", "Latifa", "Dubai Hospital", "Qassimi", "Kuwaiti", "Sagar", "Tawam",
            "University Hospital Sharjah", "Sheikh Khalifa", "Cleveland Clinic",
            "Mediclinic Parkview", "Mediclinic City Hospital", "Fakeeh University Hospital",
            "Zulekha Hospital",
        ],
    )
    def test_the_uae(self, name):
        names = self.LIST[self.LIST["region"] == "uae"]["name"]
        assert names.str.contains(name, case=False).any()


class TestNotHospitals:
    """A place wrongly listed makes somewhere look closer to help than it is."""

    LIST = pd.read_csv(response_gap.HOSPITALS_FILE, keep_default_na=False)

    @pytest.mark.parametrize("name", ["Multi-Service Center", "Linden Orthopaedics", "Perfect Doctors", "Getchecked"])
    def test_these_stay_off(self, name):
        assert not self.LIST["name"].str.contains(name, case=False).any()


class TestDistance:
    def test_a_known_distance(self):
        km, _ = nearest([25.009], [55.0], HOSPITALS)
        assert km[0] == pytest.approx(1.0, abs=0.01)

    def test_the_nearest_of_several_is_chosen(self):
        _, which = nearest([25.4], [55.0], HOSPITALS)
        assert which[0] == 1


class TestQuadrants:
    @pytest.mark.parametrize(
        "km, percentile, kind",
        [
            (FAR_KM, HIGH_PERCENTILE, "gap"),
            (FAR_KM - 0.1, HIGH_PERCENTILE, "covered"),
            (FAR_KM, HIGH_PERCENTILE - 0.1, "remote"),
            (1.0, 10.0, "near"),
        ],
    )
    def test_the_lines(self, km, percentile, kind):
        assert quadrant(km, percentile) == kind


class TestSummary:
    def result(self, cells, centre=(25.0, 55.0)):
        return summarise(cells, HOSPITALS, centre=centre)

    def test_counts_cover_every_cell(self):
        cells = [cell("a", 25.2, 90.0), cell("b", 25.01, 90.0), cell("c", 25.2, 10.0), cell("d", 25.01, 10.0)]
        assert self.result(cells)["counts"] == {"gap": 1, "covered": 1, "remote": 1, "near": 1}

    def test_gaps_rank_by_risk_then_distance(self):
        cells = [cell("low", 25.2, 80.0), cell("top", 25.2, 95.0), cell("mid", 25.15, 90.0)]
        assert [row["cell"] for row in self.result(cells)["ranked"]] == ["top", "mid", "low"]

    def test_gaps_push_everything_else_off_the_list(self):
        ranked = self.result([cell("gap", 25.2, 80.0), cell("covered", 25.01, 99.0)])["ranked"]
        assert [row["cell"] for row in ranked] == ["gap"]

    def test_with_no_gaps_it_lists_the_furthest_high_risk_places(self):
        cells = [cell("close", 25.01, 99.0), cell("further", 25.03, 80.0), cell("calm", 25.03, 10.0)]
        result = self.result(cells)
        assert result["counts"]["gap"] == 0
        assert [row["cell"] for row in result["ranked"]] == ["further", "close"]

    def test_the_list_is_short(self):
        cells = [cell(str(i), 25.2, 90.0 + i / 100) for i in range(30)]
        assert len(self.result(cells)["ranked"]) == response_gap.RANKED

    def test_the_map_only_gets_cells_around_the_view(self):
        far_away = cell("far", 25.0, 90.0, lon=56.0)
        assert [row["cell"] for row in self.result([cell("near", 25.0, 90.0), far_away])["cells"]] == ["near"]

    def test_the_ranking_is_not_limited_to_the_view(self):
        far_away = cell("far", 25.2, 95.0, lon=56.0)
        assert [row["cell"] for row in self.result([far_away])["ranked"]] == ["far"]

    def test_an_unnamed_hospital_reads_properly(self):
        assert self.result([cell("a", 25.45, 90.0)], centre=(25.45, 55.0))["cells"][0]["hospital"] == "an unnamed hospital"

    def test_no_hospitals_means_no_claims(self):
        assert summarise([cell("a", 25.0, 90.0)], pd.DataFrame(), centre=(25.0, 55.0))["cells"] == []


class TestHere:
    def test_the_nearest_hospital_to_a_place(self):
        found = here(25.009, 55.0, HOSPITALS)
        assert found == {"km": 1.0, "hospital": "Central Hospital", "emergency_tagged": True}

    def test_most_hospitals_are_not_claimed_to_have_emergency_care(self):
        assert here(25.49, 55.0, HOSPITALS)["emergency_tagged"] is False


class TestEndpoint:
    @pytest.fixture
    def client(self):
        with app.test_client() as client:
            yield client

    def test_new_york(self, client):
        body = client.get("/response_gap?lat=40.758&lon=-73.9855&hour=18&day=4").get_json()
        assert body["available"] is True and body["region"] == "nyc"
        assert sum(body["counts"].values()) > 1000
        assert body["far_km"] == FAR_KM and body["high_percentile"] == HIGH_PERCENTILE
        assert "straight line" in body["note"]

    def test_every_place_listed_is_high_risk_and_labelled_honestly(self, client):
        body = client.get("/response_gap?lat=25.29458&lon=55.45975&hour=18&day=4").get_json()
        assert body["ranked"]
        for row in body["ranked"]:
            assert row["percentile"] >= HIGH_PERCENTILE
            assert (row["quadrant"] == "gap") == (row["km"] >= FAR_KM)

    def test_the_uae(self, client):
        body = client.get("/response_gap?lat=25.29458&lon=55.45975&hour=18&day=4").get_json()
        assert body["region"] == "uae" and body["here"]["hospital"]
        assert body["hospitals"] > 100

    def test_the_answer_moves_with_the_hour(self, client):
        night = client.get("/response_gap?lat=40.758&lon=-73.9855&hour=4&day=2").get_json()
        evening = client.get("/response_gap?lat=40.758&lon=-73.9855&hour=18&day=4").get_json()
        assert night["counts"] != evening["counts"]

    def test_outside_the_data_it_says_so(self, client):
        body = client.get("/response_gap?lat=48.85&lon=2.35&hour=18&day=4").get_json()
        assert body["available"] is False and body["reason"]

    def test_bad_input_is_refused(self, client):
        assert client.get("/response_gap?lat=999&lon=2").status_code == 400
        assert client.get("/response_gap?lat=40.7&lon=-73.9&hour=99").status_code == 400
