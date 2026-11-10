import { createHash } from 'node:crypto';
import { readdir, readFile, writeFile } from 'node:fs/promises';
import { deflateSync } from 'node:zlib';

// Small, original geometric icon. Build-time PNGs keep the source asset editable as SVG.
function crc32(data) {
  let value = 0xffffffff;
  for (const byte of data) {
    value ^= byte;
    for (let bit = 0; bit < 8; bit++) value = (value >>> 1) ^ (0xedb88320 & -(value & 1));
  }
  return (value ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const body = Buffer.concat([Buffer.from(type), data]);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(body));
  return Buffer.concat([length, body, crc]);
}
for (const size of [192, 512]) {
  const pixels = Buffer.alloc((size * 3 + 1) * size);
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const u = (x / size) * 64;
      const v = (y / size) * 64;
      const mark =
        (u >= 20 && u < 26 && v >= 20 && v < 48) ||
        (u >= 20 && u < 44 && v >= 20 && v < 26) ||
        (u >= 20 && u < 39 && v >= 32 && v < 38);
      const color = mark ? [244, 243, 238] : [24, 91, 70];
      const offset = y * (size * 3 + 1) + 1 + x * 3;
      pixels.set(color, offset);
    }
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(size, 0);
  header.writeUInt32BE(size, 4);
  header[8] = 8;
  header[9] = 2;
  await writeFile(
    `dist/icon-${size}.png`,
    Buffer.concat([
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
      chunk('IHDR', header),
      chunk('IDAT', deflateSync(pixels)),
      chunk('IEND', Buffer.alloc(0)),
    ]),
  );
}
const assets = (await readdir('dist/assets')).map((name) => `assets/${name}`);
const files = [
  'index.html',
  'icon.svg',
  'icon-192.png',
  'icon-512.png',
  'manifest.webmanifest',
  ...assets,
];
const hash = createHash('sha256');
for (const file of files) hash.update(await readFile(`dist/${file}`));
const version = hash.digest('hex').slice(0, 16);
await writeFile(
  'dist/precache.json',
  JSON.stringify({
    version,
    urls: files.map((file) => `/finevent/${file}`),
  }),
);
// Browsers detect worker updates by script bytes, not by the fetched precache manifest.
const worker = (await readFile('dist/sw.js', 'utf8')).replace(
  /^\/\/ finevent-precache-version: [a-f0-9]{16}\r?\n/,
  '',
);
await writeFile('dist/sw.js', `// finevent-precache-version: ${version}\n${worker}`);
