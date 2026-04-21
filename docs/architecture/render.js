// Mantis verdict-report PDF renderer.
//
// Dev-only. Invoked by docs/architecture/generate.py when --out ends in .pdf.
// Loads puppeteer from docs/assets/node_modules (the single dev-dep registry).
// Never imported from plugin runtime code.
//
// Usage:
//   node docs/architecture/render.js <input.html> <output.pdf>

const fs = require("fs");
const path = require("path");

const [,, inputArg, outputArg] = process.argv;
if (!inputArg || !outputArg) {
  console.error("usage: node render.js <input.html> <output.pdf>");
  process.exit(2);
}

const inputPath = path.resolve(inputArg);
const outputPath = path.resolve(outputArg);
const configPath = path.join(__dirname, "puppeteer.config.json");
const assetsModules = path.join(__dirname, "..", "assets", "node_modules");

let puppeteer;
try {
  puppeteer = require(path.join(assetsModules, "puppeteer"));
} catch (err) {
  console.error("ERROR: puppeteer not installed. Run `cd docs/assets && npm install`.");
  console.error(err.message);
  process.exit(3);
}

const config = JSON.parse(fs.readFileSync(configPath, "utf8"));

(async () => {
  const browser = await puppeteer.launch(config.launch);
  try {
    const page = await browser.newPage();
    if (config.page && config.page.emulateMediaFeatures) {
      await page.emulateMediaFeatures(config.page.emulateMediaFeatures);
    }
    if (config.page && config.page.viewport) {
      await page.setViewport(config.page.viewport);
    }
    await page.goto("file://" + inputPath, { waitUntil: "networkidle0" });
    await page.pdf({ path: outputPath, ...config.pdf });
    console.log("wrote " + outputPath);
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error("render failed:", err);
  process.exit(1);
});
