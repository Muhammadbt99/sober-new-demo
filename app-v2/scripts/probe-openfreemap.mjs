// MVT layer names, keys and values are length-delimited strings in the protobuf,
// so a printable-run scan of the decompressed tile is a valid way to read the
// vocabulary without a decoder. pbf@5 renamed its exports, so the full decoder
// path is not worth fighting here.
import { gunzipSync } from "node:zlib";

const TILE = "https://tiles.openfreemap.org/planet/20261025_080012_pt";
const z = Number(process.argv[2] ?? 14);
const lat = Number(process.argv[3] ?? 40.758);
const lon = Number(process.argv[4] ?? -73.9855);
const label = process.argv[5] ?? "NYC";

const n = 2 ** z;
const x = Math.floor(((lon + 180) / 360) * n);
const rad = (lat * Math.PI) / 180;
const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n);
const url = `${TILE}/${z}/${x}/${y}.pbf`;

const res = await fetch(url);
let buf = Buffer.from(await res.arrayBuffer());
if (buf[0] === 0x1f && buf[1] === 0x8b) buf = gunzipSync(buf);
const strings = new Set(buf.toString("latin1").match(/[\x20-\x7e]{3,}/g) ?? []);

const ROAD = ["motorway","trunk","primary","secondary","tertiary","minor","service","track","path","rail","transit","cable_car","gondola","ferry","pier","bridge","tunnel","raceway","busway","bus_guideway","highway"];
const LAYERS = ["aeroway","boundary","building","landcover","landuse","park","place","poi","transportation","water","waterway","housenumber","mountain_peak","transportation_name"];
const LANDCOVER = ["wood","grass","farmland","ice","rock","sand","wetland","snow"];

const hit = (list) => list.filter((s) => strings.has(s));

console.log(`--- ${label}  z${z} ${x}/${y}  bytes=${buf.length}`);
console.log("layers       ", hit(LAYERS).join(", ") || "(none)");
console.log("road classes ", hit(ROAD).join(", ") || "(none)");
console.log("landcover    ", hit(LANDCOVER).join(", ") || "(none)");
console.log("has 'class'  ", strings.has("class"), "| has 'kind'", strings.has("kind"));
