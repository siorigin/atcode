// start.js — Wraps Next.js standalone server with WebSocket proxy support.
//
// The standalone server.js does not proxy WebSocket upgrade requests for
// `rewrites` entries. This wrapper intercepts `upgrade` events whose path
// starts with /ws-proxy/ and tunnels them to the FastAPI backend, while
// letting Next.js handle everything else (HTTP, HMR, etc.).
//
// Usage (standalone prod):
//   node start.js          # instead of node server.js

'use strict';

const http = require('http');

const WS_PROXY_PREFIX = '/ws-proxy/';

// Resolve backend host from env (same vars used by api-config.ts server-side)
function getBackendTarget() {
  const serverUrl = process.env.SERVER_API_URL;
  if (serverUrl) {
    try {
      const u = new URL(serverUrl);
      return {
        hostname: u.hostname,
        port: parseInt(u.port, 10) || 8008,
      };
    } catch { /* fall through */ }
  }
  return {
    hostname: 'localhost',
    port: parseInt(process.env.API_PORT || '8008', 10),
  };
}

const backend = getBackendTarget();

// Monkey-patch http.createServer so we can attach an `upgrade` listener to
// whatever server the Next.js standalone entry point creates internally.
const _createServer = http.createServer.bind(http);
http.createServer = function patchedCreateServer(...args) {
  const server = _createServer(...args);

  server.on('upgrade', (req, clientSocket, head) => {
    if (!req.url || !req.url.startsWith(WS_PROXY_PREFIX)) {
      // Not ours — let Next.js or other handlers deal with it.
      return;
    }

    // /ws-proxy/api/tasks/ws  →  /api/tasks/ws
    const targetPath = req.url.slice(WS_PROXY_PREFIX.length - 1);

    const proxyReq = http.request({
      hostname: backend.hostname,
      port: backend.port,
      path: targetPath,
      method: 'GET',
      headers: {
        ...req.headers,
        host: `${backend.hostname}:${backend.port}`,
      },
    });

    proxyReq.on('upgrade', (_res, backendSocket, backendHead) => {
      // Relay the raw 101 Switching Protocols handshake back to the browser.
      let handshake = 'HTTP/1.1 101 Switching Protocols\r\n';
      for (let i = 0; i < _res.rawHeaders.length; i += 2) {
        handshake += `${_res.rawHeaders[i]}: ${_res.rawHeaders[i + 1]}\r\n`;
      }
      handshake += '\r\n';

      clientSocket.write(handshake);
      if (backendHead && backendHead.length) clientSocket.write(backendHead);

      // Bi-directional pipe
      backendSocket.pipe(clientSocket);
      clientSocket.pipe(backendSocket);

      backendSocket.on('error', () => clientSocket.destroy());
      clientSocket.on('error', () => backendSocket.destroy());
      clientSocket.on('close', () => backendSocket.destroy());
      backendSocket.on('close', () => clientSocket.destroy());
    });

    proxyReq.on('response', (res) => {
      // Backend responded with a non-101 status (e.g. 403) — relay and close.
      let response = `HTTP/1.1 ${res.statusCode} ${res.statusMessage}\r\n`;
      for (let i = 0; i < res.rawHeaders.length; i += 2) {
        response += `${res.rawHeaders[i]}: ${res.rawHeaders[i + 1]}\r\n`;
      }
      response += '\r\n';
      clientSocket.end(response);
    });

    proxyReq.on('error', (err) => {
      console.error(`[WS Proxy] Backend connection failed: ${err.message}`);
      clientSocket.end('HTTP/1.1 502 Bad Gateway\r\n\r\n');
    });

    proxyReq.end();
  });

  return server;
};

// Load the original Next.js standalone server — it will call our patched
// http.createServer, so the upgrade handler is automatically attached.
require('./server.js');
