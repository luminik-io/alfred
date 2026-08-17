import { readFileSync, statSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { gzipSync } from "node:zlib";

export const ENTRY_RAW_LIMIT = 550_000;
export const ENTRY_GZIP_LIMIT = 170_000;

export function assertEntryBudget({ rawBytes, gzipBytes }) {
  if (rawBytes > ENTRY_RAW_LIMIT) {
    throw new Error(
      `Raw entry bundle is ${rawBytes.toLocaleString("en-US")} bytes; limit is ${ENTRY_RAW_LIMIT.toLocaleString("en-US")} bytes.`,
    );
  }
  if (gzipBytes > ENTRY_GZIP_LIMIT) {
    throw new Error(
      `Gzip entry bundle is ${gzipBytes.toLocaleString("en-US")} bytes; limit is ${ENTRY_GZIP_LIMIT.toLocaleString("en-US")} bytes.`,
    );
  }
}

export function checkBuiltEntry(distDir) {
  const indexPath = join(distDir, "index.html");
  const html = readFileSync(indexPath, "utf8");
  const entryMatch = html.match(/<script[^>]+type="module"[^>]+src="([^"]+\.js)"/);
  if (!entryMatch) {
    throw new Error(`No module entry script found in ${indexPath}.`);
  }

  const entryPath = join(distDir, entryMatch[1].replace(/^\//, ""));
  const rawBytes = statSync(entryPath).size;
  const gzipBytes = gzipSync(readFileSync(entryPath)).length;
  assertEntryBudget({ rawBytes, gzipBytes });
  return { entryPath, rawBytes, gzipBytes };
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : "";
if (invokedPath === import.meta.url) {
  const scriptDir = dirname(fileURLToPath(import.meta.url));
  const result = checkBuiltEntry(resolve(scriptDir, "../dist"));
  console.log(
    `Desktop entry ${basename(result.entryPath)}: ${result.rawBytes.toLocaleString("en-US")} bytes raw, ${result.gzipBytes.toLocaleString("en-US")} bytes gzip.`,
  );
}
