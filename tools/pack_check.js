/* Verify the packed demo exactly as a browser would run it: loader -> payload.b64
   -> native gunzip -> captured page. Uses jsdom, with the sandbox's own
   DecompressionStream/Response/Blob injected (they exist in Node 20).
   Run: npm install --no-save jsdom fflate terser && node tools/pack_check.js */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const DIR = path.join(__dirname, "..", "demo-static");
const payload = fs.readFileSync(path.join(DIR, "payload.b64"), "utf8");
const loader = fs.readFileSync(path.join(DIR, "loader.html"), "utf8");

let pass = 0, fail = 0;
const ok = (c, l) => { c ? (pass++, console.log("  ok   " + l)) : (fail++, console.log("  FAIL " + l)); };

function boot(url) {
  return new Promise((resolve) => {
    const errs = [];
    const dom = new JSDOM(loader, {
      runScripts: "dangerously",
      url,
      pretendToBeVisual: true,
      beforeParse(w) {
        w.DecompressionStream = DecompressionStream;
        w.Response = Response;
        w.Blob = Blob;
        w.addEventListener("error", e => errs.push(String(e.message || e.error)));
        w.fetch = async (u) => {
          if (String(u).includes("payload.b64")) {
            return { ok: true, status: 200, text: async () => payload };
          }
          throw new Error("unexpected network call: " + u);
        };
      },
    });
    setTimeout(() => resolve({ dom, errs }), 1200);
  });
}

(async () => {
  console.log("packed demo (loader -> gunzip -> captured UI)");
  const { dom, errs } = await boot("https://demo.example/");
  const w = dom.window, d = w.document, body = d.body.textContent;

  ok(/Read-only demo snapshot/.test(body), "loader unpacked the captured interface");
  ok(d.getElementById("demo-banner") !== null, "read-only banner rendered");
  ok(/A001_C003\.mp4/.test(body), "captured asset present");
  ok(errs.length === 0, "no JS runtime errors" + (errs.length ? ": " + errs.slice(0, 2).join(" | ") : ""));
  const rel = await w.fetch("/api/health");
  ok(rel.ok && (await rel.json()).ok === true, "fixture shim still answers after unpacking");
  const wr = await w.fetch("/api/assets/x/comments", { method: "POST", body: "{}" });
  ok(wr.status === 403, "writes still refused after unpacking");

  console.log("packed demo (client review page)");
  const g = await boot("https://demo.example/#guest");
  const gb = g.dom.window.document.body.textContent;
  ok(/Read-only demo snapshot/.test(gb), "guest route unpacked");
  ok(/A001_C003\.mp4|Approved|comment/i.test(gb), "guest page shows review content");
  ok(g.errs.length === 0, "guest page has no JS runtime errors" +
     (g.errs.length ? ": " + g.errs.slice(0, 2).join(" | ") : ""));

  console.log(`\n${pass}/${pass + fail} checks passed`);
  process.exit(fail ? 1 : 0);
})();
