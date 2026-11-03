import type { StyleSpecification } from "maplibre-gl";

/**
 * Two basemaps share one style.
 *
 * 1. `planet` - OpenFreeMap's global OpenMapTiles vector tiles. Every street on
 *    earth, z0-14, no API key and no rate limit. This is what fills the globe.
 * 2. `basemap` - the local `public/basemap/nyc.pmtiles` extract. It draws above
 *    the global stack inside the NYC bounding box, so the judged demo keeps its
 *    offline, higher-detail basemap and never depends on the network for the
 *    route view.
 *
 * The style deliberately has no symbol layers. MapLibre would need a glyph
 * endpoint for labels, and a glyph request is exactly the kind of network
 * dependency the offline demo must not have. Road hierarchy, water, parks and
 * borough boundaries carry the geography instead.
 *
 * The global stack is OpenMapTiles schema; the local extract is Protomaps
 * schema. That is why the two groups filter on different property names.
 */
export const BASEMAP_URL = "pmtiles:///basemap/nyc.pmtiles";
export const PLANET_TILEJSON = "https://tiles.openfreemap.org/planet";
export const BASEMAP_ATTRIBUTION =
  "© OpenStreetMap contributors · Protomaps · NYC extract";
export const PLANET_ATTRIBUTION =
  '<a href="https://openfreemap.org" target="_blank">OpenFreeMap</a> · ' +
  '<a href="https://www.openmaptiles.org/" target="_blank">© OpenMapTiles</a> · ' +
  'Data from <a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>';

export const basemapStyle: StyleSpecification = {
  version: 8,
  name: "S.O.B.E.R. Night",
  projection: { type: "globe" },
  sky: {
    "sky-color": "#03060f",
    "horizon-color": "#1d4f74",
    "fog-color": "#04080f",
    "fog-ground-blend": 0.6,
    "horizon-fog-blend": 0.72,
    "sky-horizon-blend": 0.6,
    "atmosphere-blend": [
      "interpolate",
      ["linear"],
      ["zoom"],
      0,
      1,
      5,
      0.62,
      9,
      0,
    ],
  },
  sources: {
    planet: {
      type: "vector",
      url: PLANET_TILEJSON,
      attribution: PLANET_ATTRIBUTION,
      minzoom: 0,
      maxzoom: 14,
    },
    basemap: {
      type: "vector",
      url: BASEMAP_URL,
      attribution: BASEMAP_ATTRIBUTION,
      minzoom: 0,
      maxzoom: 14,
    },
    world: {
      type: "geojson",
      data: "/geo/world-countries.geojson",
    },
  },
  layers: [
    {
      // Land reads as the base surface; the global `water` fill paints the
      // oceans over it. The `sky` block keeps the void outside the globe dark,
      // so lifting the background here does not lighten space.
      id: "background",
      type: "background",
      // Land, not paper. At globe zoom it is the continents seen in daylight;
      // by z9 it has settled back into the slate the street layers expect, so
      // there is no visible step where the local extract takes over.
      paint: {
        "background-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#4a5a3c",
          3,
          "#33473a",
          6,
          "#152f3f",
          9,
          "#0d2f45",
        ],
      },
    },
    {
      // Offline fallback for the globe. Fades out as the global tiles arrive,
      // so the bundled outline still carries the planet with no network.
      id: "world-land",
      type: "fill",
      source: "world",
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#54663f",
          3,
          "#3a5140",
          6,
          "#152f3f",
        ],
        "fill-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          1,
          2.5,
          1,
          4,
          0,
        ],
      },
    },
    {
      id: "world-coast",
      type: "line",
      source: "world",
      paint: {
        "line-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#d3f0f9",
          5,
          "#6fc2d8",
        ],
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          0.7,
          5,
          1.2,
        ],
        "line-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          0.9,
          3.5,
          0.6,
          5,
          0,
        ],
      },
    },
    {
      id: "world-borders",
      type: "line",
      source: "world",
      paint: {
        "line-color": "#5f93ad",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          0.6,
          4,
          1.1,
        ],
        "line-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          0.7,
          2.5,
          0.55,
          4,
          0,
        ],
      },
    },
    // ---------------------------------------------------------------- planet
    {
      // Oceans, seas, lakes and wide rivers, worldwide.
      id: "planet-water",
      type: "fill",
      source: "planet",
      "source-layer": "water",
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#15517d",
          4,
          "#0d3857",
          8,
          "#061a2b",
        ],
      },
    },
    {
      id: "planet-landcover-wood",
      type: "fill",
      source: "planet",
      "source-layer": "landcover",
      // From z0: the continents have to read as forest from orbit, not only
      // once the camera is close enough for a city.
      minzoom: 0,
      filter: ["match", ["get", "class"], ["wood"], true, false],
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#2f6137",
          6,
          "#0c3b2a",
        ],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.45, 6, 0.85],
      },
    },
    {
      id: "planet-landcover-grass",
      type: "fill",
      source: "planet",
      "source-layer": "landcover",
      minzoom: 1,
      filter: [
        "match",
        ["get", "class"],
        ["grass", "farmland", "scrub", "wetland"],
        true,
        false,
      ],
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          1,
          "#56763c",
          8,
          "#11462f",
        ],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 1, 0.35, 8, 0.7],
      },
    },
    {
      // Warm counterpoint to the cool oceans: the Sahara, Arabia and Australia
      // read as sand instead of more blue-green.
      id: "planet-landcover-sand",
      type: "fill",
      source: "planet",
      "source-layer": "landcover",
      minzoom: 0,
      filter: ["match", ["get", "class"], ["sand"], true, false],
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#a6874f",
          6,
          "#6a5a38",
        ],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.55, 6, 0.62, 10, 0.48],
      },
    },
    {
      // Ice caps and glaciers: the brightest surface on the planet at night,
      // which is what makes the poles read at globe zoom.
      id: "planet-landcover-ice",
      type: "fill",
      source: "planet",
      "source-layer": "landcover",
      minzoom: 0,
      filter: ["match", ["get", "class"], ["ice"], true, false],
      paint: {
        "fill-color": [
          "interpolate",
          ["linear"],
          ["zoom"],
          0,
          "#eaf6fc",
          6,
          "#c9e2ee",
        ],
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 0, 0.5, 6, 0.55, 10, 0.4],
      },
    },
    {
      id: "planet-park",
      type: "fill",
      source: "planet",
      "source-layer": "park",
      minzoom: 3,
      paint: {
        "fill-color": "#0c3124",
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.3, 9, 0.8],
      },
    },
    {
      id: "planet-landuse-urban",
      type: "fill",
      source: "planet",
      "source-layer": "landuse",
      minzoom: 6,
      filter: [
        "match",
        ["get", "class"],
        ["residential", "commercial", "retail", "industrial", "railway"],
        true,
        false,
      ],
      paint: {
        "fill-color": "#0c1119",
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 6, 0.25, 10, 0.55],
      },
    },
    {
      id: "planet-waterway",
      type: "line",
      source: "planet",
      "source-layer": "waterway",
      minzoom: 6,
      paint: {
        "line-color": "#0a2c42",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          6,
          0.3,
          11,
          0.9,
          15,
          2.2,
        ],
      },
    },
    {
      id: "planet-boundary",
      type: "line",
      source: "planet",
      "source-layer": "boundary",
      minzoom: 2,
      filter: ["!=", ["get", "disputed"], 1],
      paint: {
        "line-color": "#5f93ad",
        "line-width": ["interpolate", ["linear"], ["zoom"], 2, 0.5, 8, 0.8],
        "line-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          2,
          0.35,
          6,
          0.25,
          10,
          0,
        ],
        "line-dasharray": [3, 3],
      },
    },
    {
      // Casing under the trunk network keeps major roads legible over parks.
      id: "planet-road-casing",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 5,
      filter: [
        "all",
        [
          "match",
          ["get", "class"],
          ["motorway", "trunk", "primary", "secondary"],
          true,
          false,
        ],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#03121c",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          5,
          0.7,
          9,
          1.6,
          13,
          5,
          15,
          9.6,
        ],
        "line-opacity": 0.9,
      },
    },
    {
      id: "planet-road-motorway",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 4,
      filter: [
        "all",
        ["match", ["get", "class"], ["motorway", "trunk"], true, false],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#49657f",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          4,
          0.5,
          8,
          1.3,
          12,
          3.6,
          15,
          8,
        ],
      },
    },
    {
      id: "planet-road-primary",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 6,
      filter: [
        "all",
        ["match", ["get", "class"], ["primary", "secondary"], true, false],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#334b62",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          6,
          0.5,
          9,
          1,
          12,
          3,
          15,
          7,
        ],
      },
    },
    {
      id: "planet-road-tertiary",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 8,
      filter: [
        "all",
        ["match", ["get", "class"], ["tertiary"], true, false],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#24374a",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          8,
          0.4,
          11,
          1,
          15,
          5.4,
        ],
      },
    },
    {
      // Every residential street on the planet, from z11 down to the tile limit.
      id: "planet-road-minor",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 11,
      filter: [
        "all",
        [
          "match",
          ["get", "class"],
          ["minor", "street", "street_limited"],
          true,
          false,
        ],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#1a2838",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          11,
          0.35,
          13,
          1.1,
          15,
          2.8,
        ],
        "line-opacity": 0.85,
      },
    },
    {
      id: "planet-road-service",
      type: "line",
      source: "planet",
      "source-layer": "transportation",
      minzoom: 13,
      filter: [
        "all",
        ["match", ["get", "class"], ["service", "track"], true, false],
        ["!=", ["get", "brunnel"], "tunnel"],
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#141f2c",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          13,
          0.3,
          15,
          1.4,
        ],
        "line-opacity": 0.8,
      },
    },
    {
      id: "planet-building",
      type: "fill",
      source: "planet",
      "source-layer": "building",
      minzoom: 13,
      paint: {
        "fill-color": "#0e1824",
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.28, 15, 0.62],
      },
    },
    // ------------------------------------------------------- local NYC extract
    {
      // Protomaps keeps land in the `earth` layer. It carries the accurate
      // coastline from street zoom all the way out to the z0 world tile.
      id: "earth",
      type: "fill",
      source: "basemap",
      "source-layer": "earth",
      minzoom: 9,
      paint: {
        // Deliberately darker than the planet's `background`: at street zoom
        // the land is the backdrop for the risk hexes, so it gives up the
        // globe's colour to keep their contrast.
        "fill-color": "#0f2334",
        "fill-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          9,
          0,
          10.5,
          1,
        ],
      },
    },
    {
      id: "water",
      type: "fill",
      source: "basemap",
      "source-layer": "water",
      minzoom: 9,
      paint: {
        "fill-color": "#071a28",
        "fill-opacity": [
          "interpolate",
          ["linear"],
          ["zoom"],
          9,
          0,
          10.5,
          1,
        ],
      },
    },
    {
      id: "landuse-parks",
      type: "fill",
      source: "basemap",
      "source-layer": "landuse",
      minzoom: 9,
      filter: [
        "match",
        ["get", "kind"],
        ["park", "forest", "grass", "garden", "nature_reserve"],
        true,
        false,
      ],
      paint: { "fill-color": "#0c3124", "fill-opacity": 0.7 },
    },
    {
      id: "landuse-industrial",
      type: "fill",
      source: "basemap",
      "source-layer": "landuse",
      minzoom: 9,
      filter: ["match", ["get", "kind"], ["industrial", "railway"], true, false],
      paint: { "fill-color": "#0c1119", "fill-opacity": 0.7 },
    },
    {
      id: "buildings",
      type: "fill",
      source: "basemap",
      "source-layer": "buildings",
      minzoom: 13,
      paint: {
        "fill-color": "#0e1824",
        "fill-opacity": ["interpolate", ["linear"], ["zoom"], 13, 0.28, 15, 0.62],
      },
    },
    {
      id: "waterways",
      type: "line",
      source: "basemap",
      "source-layer": "water",
      minzoom: 11,
      filter: ["==", ["geometry-type"], "LineString"],
      paint: { "line-color": "#0a2c42", "line-width": 1.2 },
    },
    {
      id: "road-casing",
      type: "line",
      source: "basemap",
      "source-layer": "roads",
      minzoom: 9,
      filter: [
        "match",
        ["get", "kind"],
        ["highway", "major_road", "medium_road"],
        true,
        false,
      ],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#03121c",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          8,
          0.8,
          12,
          3.3,
          15,
          9,
        ],
        "line-opacity": 0.9,
      },
    },
    {
      id: "road-minor",
      type: "line",
      source: "basemap",
      "source-layer": "roads",
      minzoom: 11,
      filter: ["match", ["get", "kind"], ["minor_road", "path"], true, false],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#1a2838",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          10,
          0.35,
          13,
          1.1,
          15,
          2.8,
        ],
        "line-opacity": 0.85,
      },
    },
    {
      id: "road-medium",
      type: "line",
      source: "basemap",
      "source-layer": "roads",
      minzoom: 10,
      filter: ["match", ["get", "kind"], ["medium_road"], true, false],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#24374a",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          9,
          0.55,
          12,
          2.1,
          15,
          5.4,
        ],
      },
    },
    {
      id: "road-major",
      type: "line",
      source: "basemap",
      "source-layer": "roads",
      minzoom: 9,
      filter: ["match", ["get", "kind"], ["major_road"], true, false],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#334b62",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          7,
          0.7,
          12,
          3,
          15,
          7,
        ],
      },
    },
    {
      id: "road-highway",
      type: "line",
      source: "basemap",
      "source-layer": "roads",
      minzoom: 9,
      filter: ["match", ["get", "kind"], ["highway"], true, false],
      layout: { "line-cap": "round", "line-join": "round" },
      paint: {
        "line-color": "#49657f",
        "line-width": [
          "interpolate",
          ["linear"],
          ["zoom"],
          6,
          0.6,
          12,
          3.6,
          15,
          8,
        ],
      },
    },
    {
      id: "borough-boundaries",
      type: "line",
      source: "basemap",
      "source-layer": "boundaries",
      minzoom: 9,
      paint: {
        "line-color": "#3b5164",
        "line-width": 0.8,
        "line-opacity": 0.45,
        "line-dasharray": [4, 4],
      },
    },
  ],
};
