#!/usr/bin/env node
/**
 * UI smoke test: boots the real index.html in jsdom against the live host and
 * asserts that the shell actually rendered and that interactions bind.
 *
 * Dev-only tool. Needs `npm install --no-save jsdom` and a running server:
 *   python3 -m lfmc.cli serve --port 8765 --demo
 *   node tools/ui_smoke.js
 *
 * It is NOT part of the Python test suite (node is not a product dependency)
 * and it does not prove visual fidelity — only that the app boots, fetches, and
 * wires up without a blank screen.
 */
const { JSDOM, VirtualConsole } = require("jsdom");

const BASE = process.env.LFMC_BASE || "http://127.0.0.1:8765";
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

// Watchdog: never let a hung jsdom hang the caller.
setTimeout(() => {
  console.error("FAIL: smoke test timed out after 60s");
  process.exit(2);
}, 60000).unref();

function fail(msg) {
  console.error("FAIL: " + msg);
  process.exitCode = 1;
}
function ok(msg) {
  console.log("  ok  " + msg);
}

(async () => {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on("jsdomError", (e) => errors.push(e.message));
  vc.on("error", (m) => errors.push(String(m)));

  const dom = await JSDOM.fromURL(BASE + "/", {
    runScripts: "dangerously",
    // NB: do NOT use resources:"usable" here — it waits on subresource loads and
    // hangs the run. The app only uses inline CSS/JS and data: URIs.
    pretendToBeVisual: true,
    virtualConsole: vc,
    beforeParse(window) {
      window.fetch = (input, init) =>
        fetch(new URL(input, BASE).href, init);
      window.URL.createObjectURL = () => "blob:stub";
      window.navigator.clipboard = { writeText: async () => {} };
      window.open = () => null;
    },
  });

  const { window } = dom;
  const doc = window.document;

  // boot() + route() are async: give the app time to fetch and render.
  await wait(1800);

  // --- shell ---
  doc.getElementById("drives").textContent.includes("Demo Camera Drive")
    ? ok("sidebar lists the registered drive")
    : fail("drive tree did not render: " + doc.getElementById("drives").textContent.trim().slice(0, 120));

  // The drive root holds folders, not loose files, so open one first.
  const folderCards = doc.querySelectorAll("[data-folder]");
  folderCards.length > 0 ? ok(`${folderCards.length} folder tile(s) rendered at the drive root`)
                         : fail("no folder tiles at the drive root");
  if (folderCards.length) {
    folderCards[0].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
    await wait(900);
  }
  const cards = doc.querySelectorAll("[data-asset]");
  cards.length > 0 ? ok(`${cards.length} file card(s) rendered after opening a folder`)
                   : fail("no asset cards rendered");

  doc.querySelectorAll("#tabs button").length === 5
    ? ok("inspector shows 5 tabs (Details/Comments/Transcript/Similar/Share)")
    : fail("inspector tabs missing");

  doc.querySelectorAll("#nav a").length === 6
    ? ok("sidebar navigation rendered")
    : fail("sidebar nav missing");

  // --- interaction: select an asset ---
  doc.querySelectorAll("[data-asset]")[0]
    .dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(900);
  const inspName = doc.getElementById("inspName").textContent.trim();
  inspName && inspName !== "Select an asset"
    ? ok(`clicking a card opens the inspector for "${inspName}"`)
    : fail("inspector did not populate after selecting an asset");

  const bodyText = doc.getElementById("inspBody").textContent;
  bodyText.includes("Asset ID")
    ? ok("Details tab shows asset identity (stable id, path, fingerprint)")
    : fail("Details tab content missing");

  // --- interaction: Comments tab ---
  const commentsTab = [...doc.querySelectorAll("#tabs button")]
    .find((b) => b.dataset.tab === "comments");
  commentsTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(500);
  const commentEls = doc.querySelectorAll(".comment");
  commentEls.length > 0
    ? ok(`Comments tab rendered ${commentEls.length} threaded comment(s)`)
    : fail("comments did not render");
  doc.querySelector(".tc-chip")
    ? ok("timecode chips rendered on comments")
    : fail("no timecode chips");
  doc.getElementById("cBody")
    ? ok("comment composer is present")
    : fail("comment composer missing");

  // --- interaction: Share tab ---
  const shareTab = [...doc.querySelectorAll("#tabs button")]
    .find((b) => b.dataset.tab === "share");
  shareTab.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(400);
  doc.getElementById("lkCreate")
    ? ok("Share tab offers review-link creation")
    : fail("share controls missing");

  // --- routes ---
  for (const route of ["selects", "nle", "mount", "ai"]) {
    window.location.hash = "#" + route;
    await wait(700);
    const txt = doc.getElementById("content").textContent.trim();
    txt.length > 40 ? ok(`route #${route} rendered`)
                    : fail(`route #${route} rendered empty`);
  }

  // honesty checks: planned features must be labelled, not faked
  window.location.hash = "#ai";
  await wait(600);
  const aiTxt = doc.getElementById("content").textContent;
  aiTxt.includes("not running") || aiTxt.includes("not indexed")
    ? ok("AI route states the index is not running (no faked results)")
    : fail("AI route does not disclose that the index is absent");

  window.location.hash = "#nle";
  await wait(600);
  const nleTxt = doc.getElementById("content").textContent;
  nleTxt.includes("unverified")
    ? ok("NLE route labels integrations as unverified")
    : fail("NLE route does not disclose unverified status");

  if (errors.length) {
    console.error("\nJS runtime errors:");
    errors.slice(0, 8).forEach((e) => console.error("  - " + e.split("\n")[0]));
    process.exitCode = 1;
  } else {
    console.log("\nNo JS runtime errors.");
  }
  window.close();
  // The page runs a setInterval for transfer stats, which would keep node alive.
  process.exit(process.exitCode || 0);
})().catch((e) => {
  console.error("SMOKE CRASHED:", e.message);
  process.exitCode = 1;
});
