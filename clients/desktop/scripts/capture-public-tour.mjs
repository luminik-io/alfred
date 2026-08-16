import { spawn } from "node:child_process";
import { copyFile, mkdir, readdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const desktopDir = fileURLToPath(new URL("..", import.meta.url));
const repoRoot = path.resolve(desktopDir, "../..");
const outputDir = path.join(desktopDir, "test-results", "public-tour");
const captureMp4 = path.join(outputDir, "alfred-tour.mp4");
const capturePoster = path.join(outputDir, "alfred-tour-poster.png");
const mediaTargets = [
  path.join(repoRoot, "docs", "media"),
  path.join(repoRoot, "site", "public", "media"),
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

async function findVideos(directory) {
  const videos = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const entryPath = path.join(directory, entry.name);
    if (entry.isDirectory()) videos.push(...(await findVideos(entryPath)));
    if (entry.isFile() && entry.name === "video.webm") videos.push(entryPath);
  }
  return videos;
}

await rm(outputDir, { recursive: true, force: true });
await run(
  process.execPath,
  [
    path.join(desktopDir, "scripts", "run-contract-tests.mjs"),
    "e2e/public-tour.spec.ts",
    "--config=playwright.tour.config.ts",
  ],
  {
    cwd: desktopDir,
    env: { ...process.env, ALFRED_TOUR_PAUSE_MS: "1800" },
  },
);

const videos = await findVideos(outputDir);
if (videos.length !== 1) {
  throw new Error(`Expected one public-tour video, found ${videos.length}.`);
}

await run("ffmpeg", [
  "-y",
  "-i",
  videos[0],
  "-an",
  "-vf",
  "fps=30,scale=1440:900:flags=lanczos,format=yuv420p",
  "-c:v",
  "libx264",
  "-crf",
  "24",
  "-preset",
  "medium",
  "-movflags",
  "+faststart",
  captureMp4,
]);
await run("ffmpeg", [
  "-y",
  "-ss",
  "00:00:01",
  "-i",
  captureMp4,
  "-frames:v",
  "1",
  "-update",
  "1",
  capturePoster,
]);

for (const target of mediaTargets) {
  await mkdir(target, { recursive: true });
  await copyFile(captureMp4, path.join(target, "alfred-tour.mp4"));
  await copyFile(capturePoster, path.join(target, "alfred-tour-poster.png"));
}

console.log("Updated the fixture-only public tour in docs/media and site/public/media.");
