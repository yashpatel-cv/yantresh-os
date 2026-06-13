# Yantresh — Current Deployment Setup (living doc)

> Update this file every time the architecture, repo layout, or deploy topology changes.
> Last updated: 2026-06-13.

## 1. Repo topology — three repos, NOT one folder

| Repo | Location | Role | Contains compose/Caddy? |
|---|---|---|---|
| `yantresh-os` | `Y:\claude\yantresh-os` (this repo) | Amatya/Karta agent + `api.py` (FastAPI seam) | No — only `Dockerfile` for the API image |
| `patel-portfolio` | `Y:\claude\patel-portfolio` | Astro 5 static site (yashpatel.dev) | Yes, but **standalone**: its own `Dockerfile` + `docker-compose.yml` serve it directly on `:8080` with no TLS/Caddy |
| `yantresh` (ops repo) | scaffolded in scratchpad only, **not yet a real repo on disk** | Glue layer: Caddyfile + compose that fronts both services with TLS + multi-domain routing | Yes — this is its whole job |

**Does everything need to be in the same folder?** Only the *ops-repo checkout on the VPS* needs all three trees present side by side (because Compose build-contexts and Caddy both need local paths). On your dev machine the three repos stay exactly where they are now — separate. The standard way to assemble the VPS tree without duplicating code is git submodules: the `yantresh` ops repo has `services/yantresh-os` and `services/patel-portfolio` as submodules pointing at the real repos' GitHub URLs. `git clone --recurse-submodules` on the VPS materializes the single directory Compose expects; each app repo is still developed and versioned independently.

## 2. Multi-domain architecture

```
                         DNS A/AAAA → VPS (Oracle Ampere A1, ARM64)
                                    │
                              Caddy (TLS, auto-cert)
                 ┌──────────────────┴───────────────────┐
        {$DOMAIN}  (yashpatel.dev)              api.{$DOMAIN}  (api.yashpatel.dev)
                 │                                       │
   handle_path {$API_BASE_PATH}/*  ──► yantresh-api:8000   yantresh-api:8000
   (everything else) ──► portfolio:8080                  (private lane only)
        │  demo lane: /v1/demo/missions                       │  private lane: /v1/missions
        │  no auth, profile=demo, rate-limited                │  X-API-Key required, profile=default
```

- **`{$DOMAIN}`** serves the portfolio (static Astro build via nginx-unprivileged on `:8080`) AND, via Caddy's `handle_path {$API_BASE_PATH}/*`, same-origin proxies to `yantresh-api:8000` for the **public demo lane** (`/v1/demo/missions` — no API key, `profile="demo"`, tiny `Bounds`, `YANTRESH_TOOL_ALLOWLIST=read_file`, rate-limited 6 req/min/IP). This is what the portfolio's mission-console island calls.
- **`api.{$DOMAIN}`** is a separate Caddy site block that reverse-proxies the same `yantresh-api:8000` container, but only the **private lane** (`/v1/missions`, `X-API-Key` required, `profile="default"`, full `Bounds`/tool access). This is for your own tooling/automation, not the public demo.
- Both subdomains terminate on the same Caddy instance — one A/AAAA record per subdomain to the VPS IP, Caddy auto-TLS via SNI handles both certs.
- The only environment-specific value the frontend needs is `API_BASE_PATH` (default `/api`). It flows: `.env` → Compose env on `caddy` + `portfolio` → Caddy `handle_path {$API_BASE_PATH}/*` matcher, and → nginx-rendered `/config.js` (`window.__YANTRESH__.apiBase`) → `mission-console.js` builds relative URLs off `location.origin`. No hostnames are ever hardcoded in the frontend.

## 3. Component status

| Component | Status |
|---|---|
| `yantresh-os` core (agent.py, bounds.py, router.py, sanitizer.py) | Done, unchanged this session |
| `api.py` v2 — dual-lane FastAPI seam, subprocess-per-mission dispatch | Done, smoke-tested |
| `config.py` / `cli.py` / `tools.py` / `ledger.py` — demo profile + allowlist + fuse lock | Done, re-verified |
| `yantresh.toml` demo profile sections | Done |
| `patel-portfolio` site itself (Astro, Tailwind 4, content-driven, `YantreshArchitecture.astro`) | Pre-existing, complete, untouched |
| `patel-portfolio` standalone Dockerfile/compose/nginx (`:8080`, no TLS) | Pre-existing, complete — works as a standalone deploy today |
| **`yantresh` ops repo** (Caddyfile, multi-service compose, `.env.example`, CI) | Scaffolded in scratchpad (`outputs/yantresh/`) — **not yet a real repo, not yet on a connected folder** |
| **Mission-console integration into `patel-portfolio`** (`mission-console.js`, `config.js.template`, `docker-entrypoint.d/10-render-config.sh`, Dockerfile/nginx patches, `<section id="mission-console">` markup) | Templates drafted in scratchpad **but NOT yet merged into the real `patel-portfolio` repo** — its Dockerfile/nginx.conf currently have none of this |

## 4. Outstanding decisions / known gaps

- `patel-portfolio`'s standalone `docker-compose.yml` (port 8080, no Caddy) has no `/api/*` to proxy to — if the mission-console island is added to this repo, it must degrade gracefully (hide/disable itself) when `API_BASE_PATH` has no live backend, e.g. when the site is run standalone outside the ops-repo stack.
- Where the mission console markup should live: `YantreshArchitecture.astro` is the natural home (it's already the "how Yantresh works" deep-dive section) — likely as a new small `MissionConsole.astro` component included at the end of that section.
- The `yantresh` ops repo needs to actually be created as a git repo and either pushed to GitHub (for submodule references) or placed in a connected folder so you can see/edit it directly — currently it only exists in my scratchpad and is only reachable via `present_files`.
- No `caddy` binary available in the sandbox to run `caddy validate` on the Caddyfile — do this manually before first deploy.

## 5. Action items (what you need to do)

1. **Decide where the `yantresh` ops repo lives** — new GitHub repo + connected local folder, or a subfolder of an existing connected folder. Until this exists for real, the Caddy/compose/multi-domain wiring is paper-only.
2. **DNS**: create A (and AAAA if using IPv6) records for `yashpatel.dev` → VPS IP and `api.yashpatel.dev` → same VPS IP.
3. **Provision the Oracle Ampere A1 VPS**: install Docker + Compose plugin, open ports 80/443 in the subnet security list.
4. **Merge mission-console templates into the real `patel-portfolio` repo** (or explicitly defer this and ship the portfolio standalone first, Yantresh API later) — Dockerfile COPY lines, `docker-entrypoint.d/10-render-config.sh`, `/config.js` nginx location, and the `<section id="mission-console">` component.
5. **Materialize the ops repo on disk**: Caddyfile, multi-service `docker-compose.yml`, `.env` (from `.env.example` — set `DOMAIN`, `API_BASE_PATH`, `YANTRESH_API_KEY`, `YANTRESH_MAX_*`), CI workflow for the Caddy image.
6. **`caddy validate`** the Caddyfile before first deploy (not possible in this sandbox — do locally or on the VPS).
7. **First deploy**: `git clone --recurse-submodules` the ops repo on the VPS, `docker compose up -d --build`, then smoke-test both lanes:
   - `curl -H "X-API-Key: $KEY" https://api.yashpatel.dev/v1/missions` (private)
   - `curl -X POST https://yashpatel.dev/api/v1/demo/missions -d '{"task":"read README.md"}'` (public demo, same-origin)

## Changelog

- **2026-06-13**: Doc created. Captured current state after api.py v2 + Caddy/compose wiring + portfolio config.js/island templates (scratchpad), and after reading the real `patel-portfolio` repo (standalone Astro site with its own complete Dockerfile/compose/nginx — not yet aware of the ops-repo/mission-console work).
