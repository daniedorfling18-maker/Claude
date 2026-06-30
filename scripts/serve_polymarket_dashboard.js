const fs = require("fs");
const http = require("http");
const path = require("path");

const root = path.resolve(process.argv[2] || path.join(process.cwd(), "outputs", "polymarket_dashboard"));
const port = Number(process.argv[3] || process.env.DASHBOARD_PORT || 8765);
const host = process.argv[4] || process.env.DASHBOARD_HOST || "0.0.0.0";

const contentTypes = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
};

function send(res, status, body, contentType = "text/plain; charset=utf-8") {
  res.writeHead(status, {
    "Content-Type": contentType,
    "Cache-Control": "no-store",
    "Access-Control-Allow-Origin": "*",
  });
  res.end(body);
}

function safePath(urlPath) {
  const parsed = new URL(urlPath, "http://dashboard.local");
  const requested = parsed.pathname === "/" ? "/index.html" : parsed.pathname;
  const decoded = decodeURIComponent(requested);
  const filePath = path.resolve(root, "." + decoded);
  return filePath.startsWith(root + path.sep) || filePath === root ? filePath : null;
}

const server = http.createServer((req, res) => {
  const filePath = safePath(req.url || "/");
  if (!filePath) {
    send(res, 403, "Forbidden");
    return;
  }
  fs.readFile(filePath, (err, data) => {
    if (err) {
      send(res, err.code === "ENOENT" ? 404 : 500, err.code === "ENOENT" ? "Not found" : "Server error");
      return;
    }
    send(res, 200, data, contentTypes[path.extname(filePath).toLowerCase()] || "application/octet-stream");
  });
});

server.on("error", (err) => {
  console.error(`Polymarket dashboard server failed on ${host}:${port}: ${err.message}`);
  process.exit(1);
});

server.listen(port, host, () => {
  console.log(`Polymarket dashboard server listening on http://${host}:${port}/ from ${root}`);
});
