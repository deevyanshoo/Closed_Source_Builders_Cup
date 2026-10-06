# Planning workspace

React Router SPA for the local `/planning` workflow. Start the gateway separately, then run `npm install` and `npm run dev` here. Vite listens on `127.0.0.1:5316` and proxies `/v2` to `GATEWAY_URL` (default `http://127.0.0.1:8816`).

Run `npm run typecheck`, `npm test`, and `npm run build` before integration. The material catalog and plans come only from the gateway. Decimal amount fields remain strings across form state and JSON requests. Plan versions are shown as snapshot metadata; this workspace has no apply or approval operation.
