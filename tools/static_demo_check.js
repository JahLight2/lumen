/* Offline check of the exported static demo (jsdom, no network at all).
   Verifies the snapshot boots from fixtures and refuses writes.
   Run: npm install --no-save jsdom && node tools/static_demo_check.js            */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const DIR = path.join(__dirname, "..", "demo-static");
let pass = 0, fail = 0;
const ok = (cond, label) => { if (cond) { pass++; console.log("  ok   " + label); }
                              else { fail++; console.log("  FAIL " + label); } };

(async () => {
  const errors = [];
  const html = fs.readFileSync(path.join(DIR, "index.html"), "utf8");
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    url: "https://demo.example/",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.addEventListener("error", e => errors.push(String(e.message || e.error)));
      w.fetch = async () => { throw new Error("network used: snapshot must be offline"); };
    },
  });
  const w = dom.window;
  await new Promise(r => setTimeout(r, 900));

  console.log("static demo (offline, fixtures only)");
  ok(w.document.getElementById("demo-banner") !== null, "read-only banner rendered");
  ok(/Read-only demo snapshot/.test(w.document.body.textContent), "banner text visible");

  const body = w.document.body.textContent;
  ok(/A001_C003\.mp4/.test(body), "captured asset appears in the UI");

  // fixtures path works
  const health = await w.fetch("/api/health");
  ok(health.ok && (await health.json()).ok === true, "GET /api/health answered from fixtures");

  // a browse call that only exists in fixtures
  const browse = await w.fetch("/api/browse?root_id=BOGUS&path=");
  ok(browse.status === 404 && (await browse.json()).error === "not_in_snapshot",
     "unknown endpoint returns explicit not_in_snapshot (no silent empty)");

  // search is a labelled client-side filter, not a fake AI claim
  const s = await (await w.fetch("/api/search?q=a001")).json();
  ok(s.count >= 1 && /snapshot/.test(s.demo_note || ""), "search labels itself as a snapshot filter");

  // writes must be refused, visibly
  const w1 = await w.fetch("/api/assets/x/comments", { method: "POST", body: "{}" });
  const wj = await w1.json();
  ok(w1.status === 403 && wj.error === "read_only_demo", "POST refused with read_only_demo");
  ok(/read-only snapshot/i.test(wj.message), "refusal explains itself in plain words");

  // markers export still works from the captured text fixture
  const csv = await w.fetch("/api/assets/" + Object.keys(JSON.parse(
      fs.readFileSync(path.join(DIR, "fixtures.json"), "utf8")).__text__ || {})[0].split("/")[3] + "/markers.csv");
  ok(csv.ok && /(TIME|Marker|marker)/i.test(await csv.text()), "marker CSV served from snapshot text");

  // guest page
  const ghtml = fs.readFileSync(path.join(DIR, "guest.html"), "utf8");
  const gdom = new JSDOM(ghtml, { runScripts: "dangerously", url: "https://demo.example/guest.html",
    beforeParse(x) { x.addEventListener("error", e => errors.push(String(e.message || e.error))); } });
  await new Promise(r => setTimeout(r, 900));
  const gbody = gdom.window.document.body.textContent;
  ok(/Read-only demo snapshot/.test(gbody), "guest page carries the snapshot banner");
  ok(!/127\.0\.0\.1|localhost/.test(gbody), "guest page leaks no local host address");

  ok(errors.length === 0, "no JS runtime errors" + (errors.length ? ": " + errors.slice(0, 3).join(" | ") : ""));

  console.log(`\n${pass}/${pass + fail} checks passed`);
  process.exit(fail ? 1 : 0);
})();
