# Current deployment setup

> Superseded — see `yantresh-hub/docs/SETUP.md` and `yantresh-hub/DEPLOY.md`,
> the living docs for the multi-repo deploy (Caddy + GHCR image-pull +
> pull-based systemd-timer CD).

## What changed (2026-06-13)

An earlier draft of this doc described a different plan: a
portfolio-embedded "mission console" (same-origin `/api/*` demo lane on
`{$DOMAIN}`, a separate `api.{$DOMAIN}` subdomain, and a `yantresh` ops
repo assembled via `git submodule`).

That plan was dropped in favor of:

- **Subdomain per project, no embedding** — `{$YANTRESH_ADDRESS}` routes
  straight to `yantresh-api:8000` (both lanes); the portfolio is a plain
  static site with no API awareness.
- **GHCR image pulls, no submodules** — each project's own CI builds and
  pushes its image; `yantresh-hub`'s compose only has `image:` refs.
- **Pull-based CD** — a systemd timer on the VPS runs
  `docker compose pull && up -d` every few minutes.

None of the dropped mission-console/submodule work was merged into this
repo or `patel-portfolio` — nothing to undo. `yantresh-hub` is the real,
committed ops repo (was "scaffolded in scratchpad only" when this doc was
first written).
