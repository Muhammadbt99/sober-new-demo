import { gunzipSync } from "node:zlib";

const TILE = "https://tiles.openfreemap.org/planet/20261025_080012_pt";
const z = 14, lat = 40.758, lon = -73.9855;
const n = 2 ** z;
const x = Math.floor(((lon + 180) / 360) * n);
const rad = (lat * Math.PI) / 180;
const y = Math.floor(((1 - Math.log(Math.tan(rad) + 1 / Math.cos(rad)) / Math.PI) / 2) * n);

const res = await fetch(`${TILE}/${z}/${x}/${y}.pbf`);
let buf = Buffer.from(await res.arrayBuffer());
if (buf[0] === 0x1f && buf[1] === 0x8b) buf = gunzipSync(buf);

const all = buf.toString("latin1").match(/[\x20-\x7e]{2,40}/g) ?? [];
const freq = new Map();
for (const s of all) freq.set(s, (freq.get(s) ?? 0) + 1);

// Short, lowercase, word-like strings are the enum vocabulary.
const words = [...freq.entries()]
  .filter(([s]) => /^[a-z][a-z_]{2,24}$/.test(s))
  .sort((a, b) => b[1] - a[1]);

console.log("total strings", all.length, "distinct words", words.length);
console.log(words.map(([s, c]) => `${s}:${c}`).join("  "));
