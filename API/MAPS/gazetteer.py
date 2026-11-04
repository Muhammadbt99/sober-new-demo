"""A small offline list of major cities, so place search still finds them when
every online geocoder is unreachable (it happens: Photon and Nominatim both
refused connections from the demo machine on 2026-11-03).

Coordinates are city centres, to about 100 m. Names match without accents or
case, on a word prefix: "sao" finds Sao Paulo, "rio" finds Rio de Janeiro.
"""
from __future__ import annotations

import unicodedata

# name, ISO 3166-1 alpha-2, country, lat, lon
CITIES: list[tuple[str, str, str, float, float]] = [
    ("New York", "US", "United States", 40.7128, -74.0060),
    ("Los Angeles", "US", "United States", 34.0522, -118.2437),
    ("San Francisco", "US", "United States", 37.7749, -122.4194),
    ("Chicago", "US", "United States", 41.8781, -87.6298),
    ("Washington", "US", "United States", 38.9072, -77.0369),
    ("Boston", "US", "United States", 42.3601, -71.0589),
    ("Miami", "US", "United States", 25.7617, -80.1918),
    ("Toronto", "CA", "Canada", 43.6532, -79.3832),
    ("Vancouver", "CA", "Canada", 49.2827, -123.1207),
    ("Montreal", "CA", "Canada", 45.5017, -73.5673),
    ("Mexico City", "MX", "Mexico", 19.4326, -99.1332),
    ("Bogota", "CO", "Colombia", 4.7110, -74.0721),
    ("Lima", "PE", "Peru", -12.0464, -77.0428),
    ("Santiago", "CL", "Chile", -33.4489, -70.6693),
    ("Buenos Aires", "AR", "Argentina", -34.6037, -58.3816),
    ("Sao Paulo", "BR", "Brazil", -23.5505, -46.6333),
    ("Rio de Janeiro", "BR", "Brazil", -22.9068, -43.1729),
    ("London", "GB", "United Kingdom", 51.5074, -0.1278),
    ("Manchester", "GB", "United Kingdom", 53.4808, -2.2426),
    ("Dublin", "IE", "Ireland", 53.3498, -6.2603),
    ("Paris", "FR", "France", 48.8566, 2.3522),
    ("Berlin", "DE", "Germany", 52.5200, 13.4050),
    ("Munich", "DE", "Germany", 48.1351, 11.5820),
    ("Madrid", "ES", "Spain", 40.4168, -3.7038),
    ("Barcelona", "ES", "Spain", 41.3874, 2.1686),
    ("Lisbon", "PT", "Portugal", 38.7223, -9.1393),
    ("Rome", "IT", "Italy", 41.9028, 12.4964),
    ("Milan", "IT", "Italy", 45.4642, 9.1900),
    ("Amsterdam", "NL", "Netherlands", 52.3676, 4.9041),
    ("Brussels", "BE", "Belgium", 50.8503, 4.3517),
    ("Zurich", "CH", "Switzerland", 47.3769, 8.5417),
    ("Vienna", "AT", "Austria", 48.2082, 16.3738),
    ("Prague", "CZ", "Czechia", 50.0755, 14.4378),
    ("Warsaw", "PL", "Poland", 52.2297, 21.0122),
    ("Budapest", "HU", "Hungary", 47.4979, 19.0402),
    ("Copenhagen", "DK", "Denmark", 55.6761, 12.5683),
    ("Stockholm", "SE", "Sweden", 59.3293, 18.0686),
    ("Oslo", "NO", "Norway", 59.9139, 10.7522),
    ("Helsinki", "FI", "Finland", 60.1699, 24.9384),
    ("Athens", "GR", "Greece", 37.9838, 23.7275),
    ("Istanbul", "TR", "Turkey", 41.0082, 28.9784),
    ("Kyiv", "UA", "Ukraine", 50.4501, 30.5234),
    ("Moscow", "RU", "Russia", 55.7558, 37.6173),
    ("Cairo", "EG", "Egypt", 30.0444, 31.2357),
    ("Casablanca", "MA", "Morocco", 33.5731, -7.5898),
    ("Lagos", "NG", "Nigeria", 6.5244, 3.3792),
    ("Nairobi", "KE", "Kenya", -1.2921, 36.8219),
    ("Johannesburg", "ZA", "South Africa", -26.2041, 28.0473),
    ("Cape Town", "ZA", "South Africa", -33.9249, 18.4241),
    ("Dubai", "AE", "United Arab Emirates", 25.2048, 55.2708),
    ("Abu Dhabi", "AE", "United Arab Emirates", 24.4539, 54.3773),
    ("Sharjah", "AE", "United Arab Emirates", 25.3463, 55.4209),
    ("Doha", "QA", "Qatar", 25.2854, 51.5310),
    ("Riyadh", "SA", "Saudi Arabia", 24.7136, 46.6753),
    ("Jeddah", "SA", "Saudi Arabia", 21.4858, 39.1925),
    ("Mecca", "SA", "Saudi Arabia", 21.3891, 39.8579),
    ("Medina", "SA", "Saudi Arabia", 24.4672, 39.6024),
    ("Kuwait City", "KW", "Kuwait", 29.3759, 47.9774),
    ("Manama", "BH", "Bahrain", 26.2285, 50.5860),
    ("Muscat", "OM", "Oman", 23.5880, 58.3829),
    ("Tehran", "IR", "Iran", 35.6892, 51.3890),
    ("Karachi", "PK", "Pakistan", 24.8607, 67.0011),
    ("Lahore", "PK", "Pakistan", 31.5204, 74.3587),
    ("Islamabad", "PK", "Pakistan", 33.6844, 73.0479),
    ("Delhi", "IN", "India", 28.6139, 77.2090),
    ("Mumbai", "IN", "India", 19.0760, 72.8777),
    ("Bangalore", "IN", "India", 12.9716, 77.5946),
    ("Dhaka", "BD", "Bangladesh", 23.8103, 90.4125),
    ("Bangkok", "TH", "Thailand", 13.7563, 100.5018),
    ("Singapore", "SG", "Singapore", 1.3521, 103.8198),
    ("Kuala Lumpur", "MY", "Malaysia", 3.1390, 101.6869),
    ("Jakarta", "ID", "Indonesia", -6.2088, 106.8456),
    ("Manila", "PH", "Philippines", 14.5995, 120.9842),
    ("Hong Kong", "HK", "Hong Kong", 22.3193, 114.1694),
    ("Shanghai", "CN", "China", 31.2304, 121.4737),
    ("Beijing", "CN", "China", 39.9042, 116.4074),
    ("Seoul", "KR", "South Korea", 37.5665, 126.9780),
    ("Tokyo", "JP", "Japan", 35.6762, 139.6503),
    ("Osaka", "JP", "Japan", 34.6937, 135.5023),
    ("Taipei", "TW", "Taiwan", 25.0330, 121.5654),
    ("Sydney", "AU", "Australia", -33.8688, 151.2093),
    ("Melbourne", "AU", "Australia", -37.8136, 144.9631),
    ("Auckland", "NZ", "New Zealand", -36.8485, 174.7633),
]


#: Other spellings of a listed city: open map data often has only one of them.
ALIASES = {
    "makkah": "mecca",
    "makka": "mecca",
    "makkah al mukarramah": "mecca",
    "madinah": "medina",
    "al madinah": "medina",
    "al madinah al munawwarah": "medina",
}


def _fold(text: str) -> str:
    """Lower case, accents removed, whitespace collapsed."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(plain.lower().split())


def canonical(text: str) -> str:
    """The folded name, with another spelling of a listed city resolved."""
    query = _fold(text)
    return ALIASES.get(query, query)


def search(text: str, limit: int = 6) -> list[dict]:
    """Cities whose name starts with the query, or has a word that does.

    Results have the shape the app reads from Nominatim, marked ``"source":
    "offline"`` so nobody mistakes them for a live geocoder answer.
    """
    query = canonical(text)
    if len(query) < 2:
        return []
    found = []
    for name, iso2, country, lat, lon in CITIES:
        folded = _fold(name)
        haystack = f"{folded} {_fold(country)}"
        if folded.startswith(query) or any(word.startswith(query) for word in folded.split()) or (
            " " in query and all(part in haystack for part in query.split())
        ):
            found.append(
                {
                    "place_id": f"offline-{iso2}-{folded.replace(' ', '-')}",
                    "display_name": f"{name}, {country}",
                    "name": name,
                    "lat": f"{lat:.4f}",
                    "lon": f"{lon:.4f}",
                    "type": "city",
                    "source": "offline",
                }
            )
    found.sort(key=lambda item: (not _fold(item["name"]).startswith(query), item["name"]))
    return found[:limit]
