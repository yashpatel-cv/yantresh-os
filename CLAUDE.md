# Agent rules — read before editing

Any AI agent (Claude Code, Cursor, etc.) **must** follow these rules on every
edit in this repo. They are non-negotiable.

## Standing rules

1. **Minimalist, anti-bloat.** No over-engineering, no unnecessary
   dependencies, no speculative abstraction. Lean, readable, simple. Prefer
   deleting code to adding it. Justify every new dependency.
2. **Zero hardcoded domains or secrets.** Domains, addresses, API keys,
   models, owners live only in `.env` / `.env.example` / `yantresh.toml`.
   Code reads them as variables. Never inline a real value.
3. **Small, atomic commits.** One logical change per commit. Commit messages
   in Conventional Commits style, subject **under 20 words**, no fluff.
4. **Strict LF line endings.** `.gitattributes` enforces `eol=lf`. Verify LF
   before committing (dev is Windows, deploy is Linux ARM64).

## This repo

`yantresh-os` — bounded Amatya/Karta agent + FastAPI seam. ~1,100 LOC, 9
flat modules. Models are TOML rows, never code. Termination proof
(`bounds.py`), write-ahead ledger + daily fuse (`ledger.py`), router seam
(`router.py`). Offline tests must stay green: `pytest -q`.
