# Yantresh — Phase 0 spine

The bounded 1 Supervisor (Amatya) + 1 Worker (Karta) loop from the
[Crucible plan](Yantresh-CRUCIBLE-PLAN.md). ~1,100 LOC, 9 modules, flat.
Termination is enforced by a runtime ranking-function proof (`bounds.py`),
spend by a write-ahead ledger + daily fuse (`ledger.py`), and vendor access
by one router seam (`router.py`) — models are TOML rows, never code.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # add your key, then: source .env  (or use direnv)
pytest -q                       # 17 offline tests must be green first
python cli.py "create hello.txt containing the word namaste"
```

Exit 0 = `complete`, exit 2 = `escalated` — there is no third outcome and no hang.

## Extending (the low-code contract)

| Want | Do | Lines |
|------|----|-------|
| New tool | one type-hinted function with `@tool` in `tools.py` | ~5–15 |
| New worker role | `roles/<name>.md` + `[models.<name>]` in TOML | 0 code |
| Swap every model vendor | edit strings in `yantresh.toml` | 0 code |
| Tighter budgets | edit `[bounds]` / `[budget]` in TOML | 0 code |
| Kill all spend NOW | set `"state": "blown"` in `.yantresh/fuse.json` | 0 code |

## Walls (cost defense in depth)

L1 here (clamp + ledger + fuse) · L2 gateway per-key `max_budget`
(LiteLLM proxy/OpenRouter — add when self-hosting) · L3 provider prepaid
monthly cap (set in the vendor dashboard — your true maximum loss).

## API & deploy

`api.py` is a FastAPI seam over the same agent (dual-lane: private
`/v1/missions` with `X-API-Key`, public read-only `/v1/demo/missions`).
`Dockerfile` builds it; `.github/workflows/build-push.yml` publishes
`ghcr.io/<owner>/yantresh-os` on every push to `main`.

Production deploy (Caddy, TLS, GHCR pull, pull-based CD) lives in the
`yantresh-hub` repo — see its `DEPLOY.md`. This repo only needs
`YANTRESH_API_KEY` / `DEEPSEEK_API_KEY` set in that hub's `.env`;
everything else (bounds, budgets, demo profile) is `yantresh.toml`.
