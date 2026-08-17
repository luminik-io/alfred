import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import test from "node:test";
import { inflateSync } from "node:zlib";

const siteRoot = resolve(import.meta.dirname, "..");
const generatorPath = resolve(siteRoot, "scripts/generate-og.mjs");
const imagePath = resolve(siteRoot, "public/brand/alfred-og.png");

function paeth(left, up, upperLeft) {
  const estimate = left + up - upperLeft;
  const leftDistance = Math.abs(estimate - left);
  const upDistance = Math.abs(estimate - up);
  const upperLeftDistance = Math.abs(estimate - upperLeft);
  if (leftDistance <= upDistance && leftDistance <= upperLeftDistance) return left;
  return upDistance <= upperLeftDistance ? up : upperLeft;
}

function inspectRgbPng(png) {
  assert.deepEqual(png.subarray(0, 8), Buffer.from("89504e470d0a1a0a", "hex"));

  let offset = 8;
  let width;
  let height;
  const imageChunks = [];
  while (offset < png.length) {
    const length = png.readUInt32BE(offset);
    const type = png.toString("ascii", offset + 4, offset + 8);
    const data = png.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      width = data.readUInt32BE(0);
      height = data.readUInt32BE(4);
      assert.equal(data[8], 8, "Open Graph PNG must use 8-bit channels");
      assert.equal(data[9], 2, "Open Graph PNG must use RGB pixels");
      assert.equal(data[12], 0, "Open Graph PNG must not be interlaced");
    } else if (type === "IDAT") {
      imageChunks.push(data);
    }
    offset += length + 12;
  }

  assert.ok(width && height);
  const bytesPerPixel = 3;
  const rowLength = width * bytesPerPixel;
  const encoded = inflateSync(Buffer.concat(imageChunks));
  assert.equal(encoded.length, (rowLength + 1) * height);

  const previous = Buffer.alloc(rowLength);
  const decoded = Buffer.alloc(rowLength);
  const totals = [0, 0, 0];
  let inputOffset = 0;
  for (let row = 0; row < height; row += 1) {
    const filter = encoded[inputOffset];
    inputOffset += 1;
    for (let column = 0; column < rowLength; column += 1) {
      const raw = encoded[inputOffset + column];
      const left = column >= bytesPerPixel ? decoded[column - bytesPerPixel] : 0;
      const up = previous[column];
      const upperLeft = column >= bytesPerPixel ? previous[column - bytesPerPixel] : 0;
      const predictor =
        filter === 0
          ? 0
          : filter === 1
            ? left
            : filter === 2
              ? up
              : filter === 3
                ? Math.floor((left + up) / 2)
                : filter === 4
                  ? paeth(left, up, upperLeft)
                  : assert.fail(`unsupported PNG filter ${filter}`);
      decoded[column] = (raw + predictor) & 0xff;
      totals[column % bytesPerPixel] += decoded[column];
    }
    decoded.copy(previous);
    inputOffset += rowLength;
  }

  const pixelCount = width * height;
  return { width, height, means: totals.map((total) => total / pixelCount) };
}

test("the public Open Graph card is generated in light mode", async () => {
  const source = await readFile(generatorPath, "utf8");
  const image = inspectRgbPng(await readFile(imagePath));

  assert.match(source, /const publicMediaMode = "light";/);
  assert.equal(image.width, 1200);
  assert.equal(image.height, 630);
  assert.ok(image.means.every((mean) => mean > 180));
});
