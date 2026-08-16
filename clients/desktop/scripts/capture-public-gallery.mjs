import { spawn } from "node:child_process";
import { copyFile, mkdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const desktopDir = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(desktopDir, "../..");
const sourceDir = path.join(desktopDir, "test-results", "public-gallery-source");
const outputDir = path.join(desktopDir, "test-results", "public-gallery");
const names = [
  "alfred-gallery-work.png",
  "alfred-gallery-agents.png",
  "alfred-gallery-approval.png",
];
const mediaTargets = [
  path.join(repoRoot, "docs", "media", "gallery"),
  path.join(repoRoot, "site", "public", "media", "gallery"),
];

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: "inherit", ...options });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (signal) {
        reject(new Error(`${command} exited after signal ${signal}.`));
      } else if (code !== 0) {
        reject(new Error(`${command} exited with status ${code ?? "unknown"}.`));
      } else {
        resolve();
      }
    });
  });
}

await rm(outputDir, { recursive: true, force: true });
await rm(sourceDir, { recursive: true, force: true });
await run(
  process.execPath,
  [
    path.join(desktopDir, "scripts", "run-contract-tests.mjs"),
    "e2e/public-gallery.spec.ts",
    "--config=playwright.gallery.config.ts",
  ],
  { cwd: desktopDir, env: process.env },
);

await mkdir(outputDir, { recursive: true });
for (const name of names) {
  await run("ffmpeg", [
    "-y",
    "-i",
    path.join(sourceDir, name),
    "-vf",
    "scale=1270:760:flags=lanczos",
    "-frames:v",
    "1",
    "-update",
    "1",
    path.join(outputDir, name),
  ]);
}

for (const target of mediaTargets) {
  await rm(target, { recursive: true, force: true });
  await mkdir(target, { recursive: true });
  for (const name of names) {
    await copyFile(path.join(outputDir, name), path.join(target, name));
  }
}

console.log("Updated the fixture-only light-mode gallery in docs/media and site/public/media.");
