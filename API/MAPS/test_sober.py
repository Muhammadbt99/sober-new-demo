import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import (validate_coordinates, get_time_features, get_crash_hotspots,
                  sample_waypoints)




class TestValidateCoordinates:

    def test_valid_nyc_coordinates(self):
        valid, error = validate_coordinates(40.758, -73.985)
        assert valid is True
        assert error is None
    
    def test_valid_dubai_coordinates(self):
        valid, error = validate_coordinates(25.2048, 55.2708)
        assert valid is True
    
    def test_invalid_latitude_too_high(self):
        valid, error = validate_coordinates(999, -73.985)
        assert valid is False
        assert "Latitude" in error
    
    def test_invalid_latitude_too_low(self):
        valid, error = validate_coordinates(-999, -73985)
        assert valid is False
        assert "Latitude" in error 

    def test_invalid_longitude_too_high(self):
        valid, error = validate_coordinates(40.758, 999)
        assert valid is False
        assert "Longitude" in error

    def test_non_numeric_latitude(self):
        valid, error = validate_coordinates("abc", -73.985)
        assert valid is False
        assert "numbers" in error
    
    def test_non_numeric_longitude(self):
        valid, error = validate_coordinates(40.758, "xyz")
        assert valid is False
        assert "numbers" in error

    def test_zero_coordinates_rejected(self):
        valid, error = validate_coordinates(0, 0)
        assert valid is False
        assert "ocean" in error

    def test_boundary_values(self):
        valid, _ = validate_coordinates(90, 180)
        assert valid is True
        valid, _ = validate_coordinates(-90, -180)
        assert valid is True
    
    def test_string_numbers_accepted(self):
        valid, _ = validate_coordinates("40.758", "-73.985")
        assert valid is True

#GET_TIME_FEATURES TESTS

class TestGetTimeFeatures:

    def test_returns_three_values(self):
        result = get_time_features()
        assert len(result) == 3
    
    def test_hour_in_valid_range(self):
        hour, _, _ = get_time_features()
        assert 0 <= hour <= 23

    def test_day_of_week_in_valid_range(self):
        _, day, _ = get_time_features()
        assert 0 <= day <= 6
    
    def test_is_weekend_is_binary(self):
        _, _, is_weekend = get_time_features()
        assert is_weekend in [0, 1]

# GET_CRASH_HOTSPOTS TESTS

class TestGetCrashHotspots:

    def test_returns_list(self):
        result = get_crash_hotspots(top_k=5)
        assert isinstance(result, list)
    
    def test_returns_correct_count(self):
        result = get_crash_hotspots(top_k=5)
        assert len(result) == 5

    def test_default_returns_10(self):
        result = get_crash_hotspots()
        assert len(result) == 10
    
    def test_hotspot_has_required_fields(self):
        result = get_crash_hotspots(top_k=1)
        hotspot = result[0]
        assert "rank" in hotspot
        assert "lat" in hotspot
        assert "lon" in hotspot
        assert "crash_count" in hotspot
        assert "risk_level" in hotspot

    def test_ranks_are_sequential(self):
        result = get_crash_hotspots(top_k=5)
        ranks = [h["rank"] for h in result]
        assert ranks == [1, 2, 3, 4, 5]
    
    def test_sorted_by_crash_count_descending(self):
        result = get_crash_hotspots(top_k=5)
        counts = [h["crash_count"] for h in result]
        assert counts == sorted(counts, reverse=True)
    
    def test_no_zero_coordinates(self):
        result = get_crash_hotspots(top_k=10)
        for h in result:
            assert not (h["lat"] == 0 and h["lon"] == 0), "Zero coordinates should be filtered"

    def test_coordinates_within_nyc_bounds(self):
        result = get_crash_hotspots(top_k=10)
        for h in result:
            assert 40.4 <= h["lat"] <= 41.0, f"Lat {h['lat']} outside NYC bounds"
            assert -74.5 <= h["lon"] <=-73.5, f"Lon {h['lon']} outside NYC bounds"
    
    def test_risk_levels_are_valid(self):
        result = get_crash_hotspots(top_k=10)
        valid_levels = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
        for h in result:
            assert h["risk_level"] in valid_levels
    
    def test_crash_count_positive(self):
        result = get_crash_hotspots(top_k=10)
        for h in result:
            assert h["crash_count"] > 0
    
    def test_top_k_zero_returns_empty(self):
        result = get_crash_hotspots(top_k=0)
        assert result == []
    
    def test_high_crash_count_is_critical_or_high(self):
        result = get_crash_hotspots(top_k=1)
        top = result[0]
        assert top["risk_level"] in {"HIGH", "CRITICAL"}
    
#ADDITIONAL TESTS FOR EDGE CASES

class TestValidateCoordinatesEdgeCass:

    def test_negative_valid_coordinates(self):
        valid, _ = validate_coordinates(-33.8688, 151.2093)
        assert valid is True
    
    def test_valid_sharjah_coordinates(self):
        valid, _ = validate_coordinates(25.3463, 55.4209)
        assert valid is True
    
    def test_empty_string_rejected(self):
        valid, error = validate_coordinates("", -73.985)
        assert valid is False
    
    def test_none_rejected(self):
        valid, error = validate_coordinates(None, -73.985)
        assert valid is False
    
    def test_exactly_minus_90_lat_valid(self):
        valid, _ = validate_coordinates(-90, 0)
        assert valid is True
    
    def test_exactly_90_lat_valid(self):
        valid, _ = validate_coordinates(90, 0)
        assert valid is True
    
    def test_exactly_180_lon_valid(self):
        valid, _ = validate_coordinates(0, 180)
        assert valid is True
    
    def test_just_over_90_lat_invalid(self):
        valid, _ = validate_coordinates(90.001, 0)
        assert valid is False
    
    def test_just_over_180_lon_invalid(self):
        valid, _ = validate_coordinates(0, 180.001)
        assert valid is False
    
    def test_whitespace_string_rejected(self):
        valid, error = validate_coordinates("", -73.985)
        assert valid is False

    
#SAMPLE_WAYPOINTS TESTS

class TestSampleWaypoints:

    def test_returns_list(self):
        geometry = {"coordinates": [[-73.985, 40.758], [-73.990, 40.760], [-73.995, 40.762]]}
        result = sample_waypoints(geometry)
        assert isinstance(result, list)

    def test_each_waypoint_has_lat_lon(self):
        geometry = {"coordinates": [[-73.985, 40.758], [-73.990, 40.760], [-73.995, 40.762]]}
        result = sample_waypoints(geometry)
        for wp in result:
            assert "lat" in wp
            assert "lon" in wp
    
    def test_empty_geometry_returns_empty(self):
        geometry = {"coordinates": []}
        result = sample_waypoints(geometry)
        assert result == []
    
    def test_empty_dict_returns_empty(self):
        result = sample_waypoints({})
        assert result == []
    
    def test_coordinates_are_flipped(self):
        geometry = {"coordinates": [[-73.985, 40.758]]}
        result = sample_waypoints(geometry)
        assert result[0]["lat"] == 40.758
        assert result[0]["lon"] == -73.985
    
    def test_samples_correct_number_of_points(self):
        coords = [[-73.0 + i*0.001, 40.0 + i*0.001] for i in range(100)]
        geometry = {"coordinates": coords}
        result = sample_waypoints(geometry, num_points=6)
        assert len(result) == 6
    
    def test_small_geometry_returns_all_points(self):
        coords = [[-73.985, 40.758], [-73.990, 40.760]]
        geometry = {"coordinates": coords}
        result = sample_waypoints(geometry, num_points=6)
        assert len(result) == 2
    
    def test_single_coordinate(self):
        geometry = {"coordinates": [[-73.985, 40.758]]}
        result = sample_waypoints(geometry)
        assert len(result) == 1
        assert result[0]["lat"] == 40.758
        assert result[0]["lon"] == -73.985

class TestSampleWaypointsAdditional:

    def test_large_geometry_sampled_correctly(self):
        coords = [[-73.0 + i*0.001, 40.0 + i*0.001] for i in range(200)]
        geometry = {"coordinates": coords}
        result = sample_waypoints(geometry, num_points=6)
        assert len(result) == 6
    
    def test_waypoints_within_original_bound(self):
        coords = [[-74.0 + i*0.01, 40.0 + i*0.01] for i in range(50)]
        geometry = {"coordinates": coords}
        result = sample_waypoints(geometry, num_points=6)
        lats = [wp["lat"] for wp in result]
        lons = [wp["lon"] for wp in result]
        assert min(lats) >= 40.0
        assert max(lats) <= 40.49
        assert min(lons) >= -74.0
        assert max(lons) <= -73.51

    def test_num_points_1_returns_1(self):
        coords = [[-73.985, 40.785], [-73.990, 40.760], [-73.995, 40.762]]
        geometry = {"coordinates": coords}
        result = sample_waypoints(geometry, num_points=1)
        assert len(result) == 1

#GET_TIME_FEATURES_ADDITIONAL TESTS

class TestGetTimeFeaturesAdditional:

    def test_weekend_correct_for_saturday(self):
        import datetime
        _, day, is_weekend = get_time_features()
        if day >= 5:
            assert is_weekend == 1
        else:
            assert is_weekend ==0
    
    def test_all_values_are_integers(self):
        hour, day, is_weekend = get_time_features()
        assert isinstance(hour, int)
        assert isinstance(day, int)
        assert isinstance(is_weekend, int)
    
    def test_consistent_results(self):
        result1 = get_time_features()
        result2 = get_time_features()
        assert result1[0] == result2[0]
        assert result1[1] == result2[1]

#VALIDATE_COORDINATES_STRESS TESTS

class TestValidateCoordinatesStress:

    def test_many_valid_world_cities(self):
        cities = [
            (51.5074, -0.1278),
            (48.8566, 2.3522),
            (35.6762, 139.6503),
            (-33.8688, 151.2093),
            (55.7558, 37.6173),
            (1.3521, 103.8198),
            (-23.5505, -46.6333),
            (19.4326, -99.1332)
        ]
        for lat, lon in cities:
            valid, error = validate_coordinates(lat, lon)
            assert valid is True, f"City ({lat}, {lon}) should be valid: {error}"
    
    def test_many_invalid_coordinates(self):
        invalid = [
            (91, 0), (-91, 0), (0, 181), (0, -181),
            (999, 999), (-999, -999), (float('inf'), 0)
        ]
        for lat, lon in invalid:
            valid, _ = validate_coordinates(lat, lon)
            assert valid is False, f"({lat}, {lon}) should be invalid"
    
