/* Package the exported static demo into a single compressed payload + a tiny
   loader page, so it can be pushed to a static host in one upload.
   The loader inflates with the inlined fflate gunzip (pure JS, no browser API
   assumptions) and swaps in the captured page. */
const fs = require("fs");
const path = require("path");
const { gzipSync } = require("fflate");

const DIR = path.join(__dirname, "..", "demo-static");
const SRC = path.join(DIR, "src");

(async () => {
  fs.mkdirSync(SRC, { recursive: true });
  const index = fs.readFileSync(path.join(DIR, "index.html"), "utf8");
  const guest = fs.readFileSync(path.join(DIR, "guest.html"), "utf8");
  fs.writeFileSync(path.join(SRC, "index.html"), index);
  fs.writeFileSync(path.join(SRC, "guest.html"), guest);

  const bundle = JSON.stringify({ index, guest });
  const gz = gzipSync(Buffer.from(bundle, "utf8"), { level: 9 });
  const b64 = Buffer.from(gz).toString("base64");
  fs.writeFileSync(path.join(DIR, "payload.b64"), b64);
  console.log("payload.b64", (b64.length / 1024).toFixed(1) + " KB",
              "(raw bundle " + (bundle.length / 1024).toFixed(1) + " KB)");

  // no vendored decompressor: the loader uses the browser's own gzip stream
  const loader = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Lumen — read-only demo snapshot</title>
<style>html,body{margin:0;height:100%;background:#0e0f11;color:#e8e6e1;
font:14px/1.6 -apple-system,Segoe UI,Roboto,sans-serif}
#boot{display:flex;align-items:center;justify-content:center;height:100%;flex-direction:column;
gap:14px;text-align:center;padding:24px}#boot small{opacity:.55;max-width:34em;display:block}
.bar{width:180px;height:3px;background:#26282c;border-radius:2px;overflow:hidden}
.bar i{display:block;height:100%;width:40%;background:#c9a227;animation:sl 1.1s ease-in-out infinite}
@keyframes sl{0%{transform:translateX(-100%)}100%{transform:translateX(320%)}}</style>
</head><body>
<div id="boot"><div><b>Lumen</b> — read-only demo snapshot</div>
<div class="bar"><i></i></div>
<small>Unpacking the captured interface. No backend, no drive, no writes.</small></div>
<script>
(function(){
  var wantGuest = /guest/i.test(location.hash + location.search) ||
                  /^\\/guest/.test(location.pathname);
  function fail(m){
    document.getElementById("boot").innerHTML =
      '<div><b>Could not open the snapshot</b></div><small>' + m + '</small>' +
      '<small>Run the real host locally for the live product.</small>';
  }
  if (typeof DecompressionStream === "undefined") {
    return fail("This browser cannot decompress the snapshot. "
              + "Use a current Chrome, Firefox, Safari or Edge.");
  }
  var paths = ["payload.b64", "src/payload.b64", "/payload.b64"], i = 0;
  function grab(){
    if (i >= paths.length) throw new Error("payload not available");
    var p = paths[i++];
    return fetch(p, { cache: "force-cache" })
      .then(function(r){ if (!r.ok) throw new Error(r.status); return r.text(); })
      .catch(function(){ return grab(); });
  }
  Promise.resolve().then(grab)
    .then(function(b64){
      var bin = atob(b64.replace(/\\s+/g, ""));
      var bytes = new Uint8Array(bin.length);
      for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      var stream = new Blob([bytes]).stream()
                     .pipeThrough(new DecompressionStream("gzip"));
      return new Response(stream).text();
    })
    .then(function(json){
      var pack = JSON.parse(json);
      var html = wantGuest ? pack.guest : pack.index;
      if (!html) throw new Error("page missing from payload");
      document.open(); document.write(html); document.close();
    })
    .catch(function(e){ fail(String((e && e.message) || e)); });
})();
</script>
</body></html>`;
  fs.writeFileSync(path.join(DIR, "loader.html"), loader);
  console.log("loader.html", (loader.length / 1024).toFixed(1) + " KB");
  console.log("deploy total",
    ((loader.length + b64.length + 400) / 1024).toFixed(1) + " KB");
})();
