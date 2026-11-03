import pytest
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app import app

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

#SAFE_ROUTE TESTS

class TestSafeRouteEndpoint:

    def test_safe_route_missing_params_returns_400(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855')
        assert response.status_code == 400
    
    def test_safe_route_invalid_origin_returns_400(self, client):
        response = client.get('/safe_route?olat=999&olon=-73.9855&dlat=40.6892&dlon=-74.0445')
        assert response.status_code == 400
    
    def test_safe_route_invalid_destination_returns_400(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=999&dlon=-74.0445')
        assert response.status_code == 400
    
    def test_safe_route_valid_returns_200(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=40.689&dlon=-74.0445')
        assert response.status_code == 200
    
    def test_safe_route_has_routes_field(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=40.6892&dlon=-74.0445')
        data = response.get_json()
        assert "routes" in data
    
    def test_safe_routes_have_rank(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=40.6892&dlon=-74.0445')
        data = response.get_json()
        if data.get("routes"):
            assert "rank" in data["routes"][0]
    
    def test_safe_route_routes_ranked_by_expected_harm(self, client):
        """Routes are ranked by expected injury crashes per trip, lowest first.

        That is the PRD's "ranked by expected harm". It is not the order of
        ``average_risk``: a long route through quiet cells can have a lower
        average score and still expose the driver to more crashes in total.
        """
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=40.6892&dlon=-74.0445')
        data = response.get_json()
        routes = data.get("routes", [])
        if len(routes) > 1:
            totals = [r["expected_crashes_total"] for r in routes]
            assert totals == sorted(totals)
            assert [r["rank"] for r in routes] == list(range(1, len(routes) + 1))
    
    def test_safe_route_has_recommendation(self, client):
        response = client.get('/safe_route?olat=40.7580&olon=-73.9855&dlat=40.6892&dlon=-74.0445')
        data = response.get_json()
        assert "recommendation" in data

    def test_safe_route_scores_each_fake_provider_route(self, client, monkeypatch):
        """The endpoint, not just the engine, must run every provider route."""
        import app as app_module

        calls = {}

        def fake_routes(olat, olon, dlat, dlon):
            calls["origin"] = (olat, olon)
            calls["destination"] = (dlat, dlon)
            return [
                {
                    "geometry": {
                        "coordinates": [
                            [-73.9900, 40.7500],
                            [-73.9855, 40.7580],
                            [-73.9800, 40.7650],
                        ]
                    },
                    "distance": 1800.0,
                    "duration": 720.0,
                },
                {
                    "geometry": {
                        "coordinates": [
                            [-73.9800, 40.7500],
                            [-73.9750, 40.7580],
                            [-73.9700, 40.7650],
                        ]
                    },
                    "distance": 2100.0,
                    "duration": 840.0,
                },
            ], "fake"

        monkeypatch.setattr(app_module, "get_routes", fake_routes)
        monkeypatch.setattr(app_module, "get_cached_route", lambda route_id: None)
        monkeypatch.setattr(app_module, "cache_route", lambda *args, **kwargs: None)

        response = client.get(
            '/safe_route?olat=40.7500&olon=-73.9900'
            '&dlat=40.7650&dlon=-73.9700&hour=22&day=5'
        )
        assert response.status_code == 200
        data = response.get_json()

        assert calls["origin"] == (40.75, -73.99)
        assert calls["destination"] == (40.765, -73.97)
        assert data["routing_source"] == "fake"
        assert data["provider_routes"] == 2
        assert data["detour_synthesis"]["attempted"] is False
        # A stubbed provider must not reach the real routing service for the
        # waypoint alternatives either.
        assert data["via_alternatives"]["enabled"] is False
        assert len(data["routes"]) == 2
        assert data["recommended_route"] == 0

        totals = [route["expected_crashes_total"] for route in data["routes"]]
        assert totals == sorted(totals)
        for route in data["routes"]:
            assert route["segments"]
            assert all(segment["length_m"] > 0 for segment in route["segments"])

class TestNearestSafeEndpointFull:

    def test_nearest_safe_valid_returns_200(self, client):
        response = client.get('/nearest_safe?lat=40.7580&lon=-73.9855')
        assert response.status_code == 200
    
    def test_nearest_safe_has_stops_field(self, client):
        response = client.get('/nearest_safe?lat=40.7580&lon=-73.9855')
        data = response.get_json()
        assert "stops" in data 
    
    def test_nearest_safe_stops_sorted_by_distance(self, client):
        """`stops` is ordered by distance and `stops[0]` is `nearest_stop`.

        The endpoint publishes `nearest_stop` and a "Nearest safe stop" message,
        so the list must agree with it: ordering the payload by safety priority
        instead would advertise a stop that is not the nearest one.
        """
        response = client.get('/nearest_safe?lat=40.7580&lon=-73.9855')
        data = response.get_json()
        stops = data.get("stops", [])
        if len(stops) > 1:
            distances = [s["distance_m"] for s in stops]
            assert distances == sorted(distances)
            assert stops[0] == data["nearest_stop"]
    
    def test_nearest_safe_has_distance_field(self, client):
        response = client.get('/nearest_safe?lat=40.7580&lon=-73.9855')
        data = response.get_json()
        stops = data.get("stops", [])
        if stops:
            assert "distance_m" in stops[0]
            assert "distance_km" in stops[0]
    
class TestHeatmapEndpoint:

    def test_heatmap_valid_returns_200(self, client):
        response = client.get('/heatmap?lat1=40.70&lon1=-74.02&lat2=40.72&lon2=-73.98&grid=2')
        assert response.status_code == 200
    
    def test_heatmap_has_heatmap_field(self, client):
        response = client.get('/heatmap?lat1=40.70&lon1=-74.02&lat2=40.72&lon2=-73.98&grid=2')
        data = response.get_json()
        assert "heatmap" in data
    
    def test_heatmap_grid_size_capped_at_8(self, client):
        response = client.get('/heatmap?lat1=40.70&lon1=-74.02&lat2=40.72&lon2=-73.98&grid=20')
        data = response.get_json()
        assert data["grid_size"] <= 8

class TestMLChartEndpoints:

    def test_confusion_matrix_returns_image(self, client):
        response = client.get('/ml/confusion_matrix')
        assert response.status_code == 200
        assert response.content_type == 'image/png'
    
    def test_feature_importance_returns_image(self, client):
        response = client.get('/ml/feature_importance')
        assert response.status_code == 200
        assert response.content_type == 'image/png'
    