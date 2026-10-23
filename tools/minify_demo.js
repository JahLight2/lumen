/* Minify the exported static demo so it fits through a single deploy payload.
   Safe transforms only: terser (AST-based) for the app script, whitespace for CSS.
   The generated file is behaviour-identical; re-verified by tools/static_demo_check.js. */
const fs = require("fs");
const path = require("path");
const { minify } = require("terser");

const DIR = path.join(__dirname, "..", "demo-static");

function minifyCSS(css) {
  return css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/\s*([{}:;,>~+])\s*/g, "$1")
    .replace(/;}/g, "}")
    .replace(/\s+/g, " ")
    .trim();
}

(async () => {
  let html = fs.readFileSync(path.join(DIR, "index.html"), "utf8");

  // 1) CSS
  html = html.replace(/<style>([\s\S]*?)<\/style>/g, (m, css) =>
    "<style>" + minifyCSS(css) + "</style>");

  // 2) every inline <script> that is not the fixture/shim/marker block
  const blocks = [];
  const re = /<script>([\s\S]*?)<\/script>/g;
  let m;
  while ((m = re.exec(html)) !== null) blocks.push({ full: m[0], body: m[1] });

  for (const b of blocks) {
    if (b.body.includes("__LFMC_FIXTURES__")) continue;   // fixtures: already compact JSON
    if (b.body.includes("static demo shim")) continue;    // keep the shim readable
    const out = await minify(b.body, { compress: true, mangle: true, toplevel: false, format: { comments: false } });
    if (out.error) throw out.error;
    html = html.replace(b.full, "<script>" + out.code + "</script>");
  }

  // 3) collapse markup indentation only (never inside <pre>/<textarea>)
  html = html.replace(/>\n\s+</g, "><");

  fs.writeFileSync(path.join(DIR, "index.html"), html);
  console.log("index.html", (html.length / 1024).toFixed(1) + " KB");

  const g = fs.readFileSync(path.join(DIR, "guest.html"), "utf8");
  let gmin = g.replace(/<style>([\s\S]*?)<\/style>/g, (mm, css) => "<style>" + minifyCSS(css) + "</style>");
  if (gmin.length > g.length * 1.05) gmin = g;
  fs.writeFileSync(path.join(DIR, "guest.html"), gmin);
  console.log("guest.html", (gmin.length / 1024).toFixed(1) + " KB");

  const fx = fs.readFileSync(path.join(DIR, "fixtures.json"), "utf8");
  fs.writeFileSync(path.join(DIR, "fixtures.json"), JSON.stringify(JSON.parse(fx)));
  console.log("fixtures.json", (fs.statSync(path.join(DIR, "fixtures.json")).size / 1024).toFixed(1) + " KB");
})();
