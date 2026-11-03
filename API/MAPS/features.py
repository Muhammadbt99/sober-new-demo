"""Coverage limits and time features shared by serving and training.

The learned crash-risk model owns its own feature list and artifact schema in
``crash_model/``. This module only holds the NYC coverage box and the small,
deterministic time helpers used by both the API and the severity model.
"""

# The crash dataset (crashes.csv) only covers New York City.
# lat_min, lat_max, lon_min, lon_max
NYC_BBOX = (40.4, 41.0, -74.5, -73.5)


def time_features(hour):
    """Return (time_of_day, is_night, is_rush_hour) for an hour 0-23."""
    hour = int(hour) % 24

    if 5 <= hour < 12:
        time_of_day = 0
    elif 12 <= hour < 17:
        time_of_day = 1
    elif 17 <= hour < 22:
        time_of_day = 2
    else:
        time_of_day = 3

    is_night = 1 if (hour >= 22 or hour <= 5) else 0
    is_rush_hour = 1 if ((7 <= hour <= 9) or (17 <= hour <= 19)) else 0
    return time_of_day, is_night, is_rush_hour


def in_model_area(lat, lon):
    """True when the offline crash dataset covers this point."""
    lat_min, lat_max, lon_min, lon_max = NYC_BBOX
    return lat_min <= lat <= lat_max and lon_min <= lon <= lon_max
