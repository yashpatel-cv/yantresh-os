# Yantresh — AI Workforce OS

> **Yantresh** (સૂત્રધાર, *sūtradhāra*) — literally "the holder of the threads." In Sanskrit/Gujarati
> theatre it is the **director-narrator who orchestrates the whole performance**: introduces the
> players, keeps the scenes on track, intervenes when an actor strays. That is precisely what a
> supervisor-over-workers agent platform does. The name is unused by any major AI product (verified
> June 2026), is pronounceable, and carries the exact orchestration metaphor.
>
> Naming hierarchy used in this doc:
> **Yantresh** = the platform · **Amatya** (chief) = the Supervisor agent · **Karta** (worker) =
> the Worker agents. *(Alternates considered: Saarthi = charioteer/guide; Vyavastha = system/order.)*
>
> Rebuilt 2026-06-08. Supersedes the prior 12-week AI Workforce OS plan. This document is the single
> source of truth; if anything conflicts with memory or older notes, **this wins.**

---

## PART A — Does this actually work? (Read before building anything)

You asked the right question first. Here is the evidence-based verdict.

### A.1 The pattern you described is real, named, and proven
What you sketched — **one Supervisor that plans/reviews/keeps Workers on track, over one or two
Workers that do the task** — is the textbook **supervisor (orchestrator-worker) pattern**. It is not
speculative:

| Who | What they proved | Relevance to you |
|-----|------------------|------------------|
| **Anthropic** (multi-agent research system) | Orchestrator + parallel worker subagents beat single-agent Claude Opus 4 by **90.2%** on their internal research eval. | Proves the ceiling is real for decompose-and-delegate work. |
| **LangGraph** (`langgraph-supervisor`) | Ships the supervisor pattern as a library; their canonical example is literally **a supervisor coordinating a developer agent + reviewer agent + tester agent**. | This *is* your idea, productized. |
| **Cognition / Devin** | Published "Don't Build Multi-Agents" (Jun 2025) arguing single-agent is better for deep+narrow **coding**… then in **March 2026 shipped a coordinator that scopes work and assigns it to managed Devins in isolated VMs.** | Even the loudest skeptic adopted supervisor-over-coding-workers. Strong signal for your exact use case. |
| **CrewAI / MetaGPT / AutoGen** | Hierarchical "manager delegates to role agents" shipped in production frameworks since 2024. | The plumbing is mature; you are not inventing primitives. |

### A.2 The honest caveat — this is where most people get burned
Anthropic's own data: **multi-agent systems burn ~15× the tokens of a single chat** (≈4× for a normal
agent, ≈15× for multi-agent). Token usage alone explained ~80% of their performance variance. The
economics only justify multi-agent on **high-value, parallelizable** work.

Cognition's counter-point is equally important and applies *directly* to a coding workforce: when you
split a task across agents, **context fragments** — the Worker doesn't see what the Supervisor knows,
sub-decisions conflict, and the system gets *more* fragile, not less. For **deep, narrow, sequential**
work (most coding tasks), a single well-context-engineered agent often beats a naive crew.

### A.3 The synthesis (what to actually do)
Both camps are right about different shapes of work, which gives a clear rule:

> **Use the Supervisor not to parallelize, but to *bound and verify*.** Your Supervisor's job is not
> "split work across many devs." It is "give one Worker a tightly-scoped subtask, check the Worker
> isn't drifting or burning tokens, verify the output meets a definition-of-done, and decide
> continue / replan / escalate / stop." One Supervisor + **one** Worker, run as a bounded loop with a
> verification gate, captures ~80% of the value at ~20% of the token cost and almost none of the
> context-fragmentation risk. Add a second Worker only when you have a genuinely parallel subtask
> (e.g. research while another writes).

So: **your instinct to keep it simple is correct and is backed by the strongest skeptics in the
field.** Start with 1 Supervisor + 1 Worker. Prove it on a golden-task eval. Only then scale.

### A.4 Go / No-Go gate (do not skip)
Before any platform work, build **Phase 0** (Part D). It is one week. If the 1-Supervisor + 1-Worker
loop cannot pass **4 of 5 golden tasks** for **under a fixed token budget per task**, the platform
will not work either — stop and fix the loop, don't build dashboards on a broken engine. This is the
single most important decision in the whole plan.

---

## PART B — Critique of the existing plans

### B.1 The original 12-week plan — what was wrong (your own critique was right, plus more)
The earlier plan's self-identified flaws are valid and stay fixed here: two-language runtime
(Python+TS), no per-tenant cost caps, unencrypted OAuth tokens, no Postgres RLS, per-worker local ML
model for loop detection, no prompt-injection defense, undefined model router, no webhook idempotency,
missing billing/eval/circuit-breaker/cost-observability/prompt-versioning. **Additional flaws not on
that list:**

1. **Locked to one model vendor.** The stack hard-coded Claude Sonnet/Haiku. You explicitly want
   model-agnostic. Fixed in Part C via a **ModelRouter over a gateway (LiteLLM/OpenRouter)**.
2. **"Never gets stuck" was a hope, not a mechanism.** Loop detection alone does not guarantee
   termination. A system only "never gets stuck" if **every** task is bounded on multiple axes with a
   single guaranteed terminal state. Fixed in Part C.1 ("Five Bounds").
3. **No verification gate.** Nothing stopped a Worker from *declaring* success falsely. Fixed via a
   Supervisor definition-of-done check before any task is marked complete.
4. **Built platform before proving the loop.** 12 weeks of infra before a single eval. Inverted here:
   Phase 0 eval gate first.
5. **LangGraph for an MVP adds a heavy dependency you must debug when it breaks.** For the
   *validation* phase, a hand-rolled ~300-line Python state machine is more "unbreakable" because you
   own every line. Adopt LangGraph later only when you need durable checkpointing of long tasks.

### B.2 The reference 5-session lean plan — strengths and gaps
**Strengths (adopt these):** ruthless scope (5 sessions, ~30 files, 3 tools, 2 roles), golden-task
eval *before* code, `CLAUDE.md` as the architecture owner the agent cannot deviate from, tests gate
every session, no dashboard until 4/5 passes, single language, resumable via CURRENT STATE / RESUME
POINT. This is the right *shape* for Phase 0.

**Gaps (this plan fills them):**
- Hard-coded to raw Anthropic SDK → **not model-agnostic.** Swap for the ModelRouter.
- "2 agent roles" but no explicit **bounded supervisor loop** with replan/escalate ceilings.
- No **HITL escape hatch** — when all retries fail, what happens? Must be a structured escalation,
  never a silent hang or crash.
- No **token cost telemetry per task** — you can't prove "doesn't waste tokens" without measuring it.
- Security tests are good but **no prompt-injection defense** on tool outputs.

**Verdict:** Use the reference plan's discipline as the skeleton of Phase 0, hardened with the
never-stuck spine and made model-agnostic.

---

## PART C — Core architecture (the unbreakable spine)

### C.1 The "Five Bounds" doctrine — why Yantresh can never hang
A task is *guaranteed* to terminate because **every run is bounded on five independent axes, and every
bound resolves to exactly one terminal action: escalate-or-abort with a structured failure record.**
There is no code path that loops forever or hangs silently.

| # | Bound | Default | On breach |
|---|-------|---------|-----------|
| 1 | **Step budget** (Worker tool-calls per task) | 25 | Supervisor forced review → escalate |
| 2 | **Token budget** (per task, all calls) | configurable, e.g. 200k | Hard stop at 100%; soft warn at 80% |
| 3 | **Wall-clock budget** | 10 min | Cancel in-flight, checkpoint, escalate |
| 4 | **Retry ceiling** (per subtask) | 2 | Mark subtask blocked, move on or escalate |
| 5 | **Replan ceiling** (Supervisor re-plans per task) | 2 | Escalate to human, never replan a 3rd time |

Plus three independent guards layered on top:
- **Loop guard** — fingerprint every `(tool, params, output[:200])`; flag when cosine similarity of
  embeddings > 0.92 within a sliding window of 5. On flag → inject a hard recovery instruction and
  count it against the replan ceiling.
- **Circuit breaker** — per tool/connector: open after 3 consecutive failures, half-open after a
  timeout, fail fast while open (never hammer a dead endpoint).
- **Verification gate** — the Supervisor checks Worker output against an explicit definition-of-done
  before `complete`. Fail → retry (within ceiling) or escalate. A Worker can never self-certify.

> **The guarantee:** steps↑, tokens↑, time↑, retries↑, replans↑ — *every* counter is monotonic and
> capped, and the only thing past the cap is a clean `escalated` or `failed` state with a checkpoint.
> "Stuck" is unreachable.

### C.2 Model-agnostic by construction (connect any model)
Never call a provider SDK directly from agent logic. All inference goes through one seam:

```
Agent logic ──> ModelRouter ──> Gateway (LiteLLM self-hosted  OR  OpenRouter hosted) ──> any provider
                    │                                                                     (OpenAI, Anthropic,
                    └─ picks model per STEP TYPE, enforces budget, owns fallback chain      Google, DeepSeek,
                                                                                            Mistral, local vLLM…)
```

- **Recommendation:** **LiteLLM proxy (self-hosted)** as the gateway. One OpenAI-format endpoint in
  front of ~100 providers, with **built-in per-key/per-team budget caps, fallbacks, retries, and
  token cost logging** — which directly satisfies both "any model" and "don't waste tokens." Runs on a
  $5–10/mo VPS. Use **OpenRouter** instead if you'd rather not host (one hosted key, 300+ models,
  automatic fallback) — the `ModelRouter` interface is identical, only the base URL/key change.
- **`ModelRouter` picks the model by step type, not by hard-coding:**

  | Step type | Default model class | Why |
  |-----------|---------------------|-----|
  | Supervisor planning / review | strong reasoning (e.g. a frontier "thinking" model) | quality-sensitive, low frequency |
  | Worker tool-calling / execution | cheap fast model | high frequency, repetitive |
  | Embeddings (loop guard, memory) | shared embedding endpoint via API | never load a local model per worker |

  Every entry is a config row (`role → model_id`, plus an ordered `fallback: [...]`). Swapping
  GPT-class for DeepSeek-class for a local model is a config edit, **zero code change.**

### C.3 Minimal never-stuck loop (Phase 0 skeleton — hand-rolled, no framework)
```python
# core/agent.py  — illustrative skeleton; full version built in Phase 0
from dataclasses import dataclass, field

class Escalate(Exception): ...          # single terminal escape hatch

@dataclass
class Bounds:
    max_steps: int = 25
    max_tokens: int = 200_000
    max_seconds: int = 600
    max_retries: int = 2
    max_replans: int = 2

@dataclass
class RunState:
    task_id: str
    plan: list = field(default_factory=list)
    step: int = 0
    tokens_used: int = 0
    started_at: float = 0.0
    retries: int = 0
    replans: int = 0
    status: str = "planning"            # planning|executing|reviewing|escalated|complete|failed

class Agent:
    def __init__(self, router, tools, memory, loop_guard, breaker, bounds=Bounds()):
        self.router, self.tools = router, tools
        self.memory, self.loop_guard, self.breaker = memory, loop_guard, breaker
        self.b = bounds

    def run(self, task: str) -> dict:
        st = RunState(task_id=new_id(), started_at=now())
        try:
            st.plan = self.supervisor_plan(task, st)          # Amatya plans
            while st.step < len(st.plan):
                self._enforce_bounds(st)                       # bounds 1-3, raises Escalate
                subtask = st.plan[st.step]
                result = self._execute_with_breaker(subtask, st)  # Karta acts; circuit breaker
                if self.loop_guard.looping(result):            # loop guard
                    self._replan_or_escalate(st, reason="loop")
                    continue
                if not self.supervisor_verify(subtask, result, st):  # verification gate
                    if not self._retry(st):                    # bound 4
                        self._replan_or_escalate(st, reason="verify_fail")
                    continue
                self.memory.store(st.task_id, subtask, result)
                st.step += 1
            return self._complete(st)
        except Escalate as e:
            return self._escalate(st, reason=str(e))           # ALWAYS terminal, never hangs

    def _enforce_bounds(self, st):
        if st.step    >= self.b.max_steps:   raise Escalate("step_budget")
        if st.tokens_used >= self.b.max_tokens: raise Escalate("token_budget")
        if now() - st.started_at >= self.b.max_seconds: raise Escalate("wall_clock")

    def _replan_or_escalate(self, st, reason):
        if st.replans >= self.b.max_replans: raise Escalate(f"replan_ceiling:{reason}")
        st.replans += 1
        st.plan = self.supervisor_replan(st, reason)           # bounded re-plan with recovery context
        st.step = 0
```
The contract: **no branch returns to the loop without incrementing a bounded counter, and every cap
raises the one `Escalate` that lands in a terminal handler.** That is the entire "never gets stuck"
guarantee, in one screen.

### C.4 Final stack (single-language core, model-agnostic, security-first)
| Layer | Choice | Note vs old plan |
|-------|--------|------------------|
| Agent core + API | **Python 3.13 + FastAPI** | **single language** (kills the Python/TS split critique). LangGraph optional, added only when durable checkpointing is needed. |
| Model access | **ModelRouter → LiteLLM proxy** (or OpenRouter) | **model-agnostic**, per-key budget caps, fallback chains, token cost logging |
| Task queue | **BullMQ→** *use* **Celery/Redis** (Python) or keep a thin Node worker only if required | stay single-language; Redis either way |
| Working memory | Redis (TTL) | unchanged |
| Episodic / semantic | PostgreSQL + pgvector, **RLS + tenant_id on every table from day one** | fixes multi-tenant leak |
| Embeddings | **shared API embedding endpoint** (via gateway) | kills per-worker local 80MB model |
| Secrets / tokens | **AES-256-GCM at rest, per-tenant KDF**, or Vault/Secrets Manager | fixes CWE-312 plaintext tokens |
| Tool I/O safety | **prompt-injection sanitizer + trust boundary** on every tool output before it re-enters context | fixes verbatim injection |
| Billing (when monetizing) | Stripe + usage metering, **per-tenant hard spend cap** | not an afterthought |
| Dashboard | Next.js (separate frontend, talks to FastAPI) | a frontend is fine; it isn't a second *runtime* |
| Deploy | Docker + Railway/Fly, Cloudflare in front | unchanged |
| Observability | OpenTelemetry traces **+ cost_usd/input_tokens/output_tokens on every LLM call**, rolled up task→worker→tenant | fixes "can't see runaway spend" |

---

## PART D — Phase 0: prove the loop (Week 0, ~5 build sessions). THE GATE.

Goal: a CLI + REST agent with **1 Supervisor (Amatya) + 1 Worker (Karta)**, 3 tools, the Five
Bounds, loop guard, circuit breaker, verification gate, **model-agnostic via ModelRouter**, and a
golden-task eval. **No dashboard, no auth, no billing.** Ship nothing until **4/5 golden tasks pass
under the token budget.**

| Session | Deliverable | Done-test (binary) |
|---------|-------------|--------------------|
| **S1 — Core loop** | `ModelRouter`, `tools.py` (read_file/write_file/run_bash with security blocks), `Bounds`, `Agent` loop, `supervisor.py`, `worker.py` | `pytest` green; a task runs end-to-end and **terminates** even when handed an impossible task (lands in `escalated`, never hangs). |
| **S2 — Guards + memory** | `loop_guard.py` (embedding fingerprint via API), `circuit_breaker.py`, SQLite/pg episodic memory, verification gate | Loop guard flags a forced repeat within 5 steps; breaker opens after 3 tool failures; Worker output rejected by gate triggers bounded retry. |
| **S3 — Eval harness** | `eval/golden_tasks.py` (5 real coding tasks), `eval/runner.py` with **per-task token + cost report** | Runner prints pass/fail + tokens + $ per task; **≥4/5 pass**; every task under token budget. |
| **S4 — Security** | `tests/test_security.py` + tool-output **injection sanitizer** | Path traversal, absolute paths, `.exe`, `rm -rf /` all blocked; a tool result containing "ignore previous instructions…" is neutralized before re-entering context. |
| **S5 — Package + go/no-go** | `requirements.txt`, `Dockerfile`, `cli.py`, `README.md`, `VALIDATED.md` | `docker build` clean; `python cli.py "task"` end-to-end; **write `VALIDATED.md` with the real X/5 + token cost only if ≥4/5.** |

**Go/No-Go:** ≥4/5 under budget → proceed to Part E. <4/5 → fix the loop; do **not** build the
platform. This single gate is what separates this from the plans that over-build before proving.

---

## PART E — Phased platform build (only after Phase 0 passes)

Each item folds in a fix for a known flaw. Weeks are indicative; gate each phase on its done-test.

**Phase 1 — Foundations & safety (Wk 1–3)**
- Wk1: pnpm/uv monorepo, FastAPI skeleton, **Postgres schema with `tenant_id` + RLS on every table
  from day one**, multi-tenant auth (Clerk or Auth.js).
- Wk2: Role registry (worker definition schema), task queue + cron, **webhook receiver with
  idempotency keys** (Redis dedup — external triggers, not just BullMQ jobId), Stripe billing scaffold
  with per-agent metering.
- Wk3: Memory engine (Redis working + pgvector semantic, STORE/RETRIEVE/CONSOLIDATE as **idempotent,
  resumable** jobs with alerting on missed windows), **encrypted TokenStore (AES-256-GCM, per-tenant
  KDF).**
- *Milestone:* spin up a worker, give it a task, see encrypted memory written under RLS; runaway task
  hits a bound and escalates.

**Phase 2 — Runtime hardening (Wk 4–6)**
- Wk4: Promote the Phase 0 loop to the durable runtime (LangGraph + Postgres checkpointer **only if**
  long-task resumability is needed); wire **ModelRouter** with per-step routing + fallback chains +
  **token cost telemetry middleware on every call.**
- Wk5: Loop guard via shared embedding API (no local model) + **tool-output injection sanitizer /
  trust boundary.**
- Wk6: HITL gateway (confidence scoring, escalation rules, Slack/email notify) + **bounded Supervisor
  pattern that may spawn a *second* Worker only for genuinely parallel subtasks** (research‖draft).
- *Milestone:* agent runs a 20-step task, hits a loop, self-recovers within the replan ceiling,
  escalates one decision to a human, resumes after approval.

**Phase 3 — Connectors & evals (Wk 7–8)**
- Wk7: Gmail/Calendar/Slack OAuth + token refresh, each wrapped in a **circuit breaker (opossum-style)
  with backoff + fallback routing** and per-call audit log.
- Wk8: HubSpot/Notion/web-search connectors with capability tags + **agent eval harness** (golden set,
  expected tool sequences, LLM-judge scoring) wired into CI.
- *Milestone:* an "AI SDR" researches a prospect, drafts outreach, **awaits approval**, logs to CRM —
  fully bounded, fully audited.

**Phase 4 — Surface & ship (Wk 9–12)**
- Wk9: Dashboard (roster, real-time task feed via SSE) + **per-worker/tenant cost dashboard** (token
  spend, task cost, spend-vs-cap).
- Wk10: Approval queue, memory inspector, KPI charts, **prompt/role version registry with diff +
  one-click rollback** (so updating a prompt can't corrupt in-flight v1 tasks).
- Wk11: Worker-creation wizard (role templates), tool-permission grant UI, **per-worker/tenant spend
  caps (hard stop at $X, warn at 80%) enforced in the job processor.**
- Wk12: Seed demo, rehearse 3-min demo, **OWASP ZAP scan + pen-test checklist for the API and agent
  tool-execution paths**, deploy with Cloudflare.
- *Milestone (investor moment):* "Hire an AI worker → it researches, drafts, pauses for approval,
  acts, logs — bounded, costed, auditable, on the model of your choice."

---

## PART F — Open decisions to lock before Phase 0
1. **Gateway:** LiteLLM self-hosted (recommended, owns budget+fallback+logging) vs OpenRouter hosted.
2. **Default model classes** per step type (planning / execution / embeddings) and the fallback order.
3. **Token budget per task** for the Phase 0 golden eval (the number that defines "doesn't waste
   tokens").
4. **The 5 golden tasks** — pick real, representative coding tasks with file-existence/҂behavioral checks.
5. **Framework now or later:** hand-rolled loop for Phase 0 (recommended) vs LangGraph from the start.
6. **Name sign-off:** Yantresh (platform) / Amatya (supervisor) / Karta (worker) — or Saarthi / Vyavastha.

---

## Sources
- Anthropic — *How we built our multi-agent research system* (orchestrator-worker, +90.2%, ~15× tokens): https://www.anthropic.com/engineering/multi-agent-research-system
- Cognition — *Don't Build Multi-Agents* (context engineering, single-agent case): https://cognition.ai/blog/dont-build-multi-agents
- Cognition — *Multi-Agents: What's Actually Working* (their later coordinator-over-Devins): https://cognition.ai/blog/multi-agents-working
- LangGraph — *Multi-Agent Supervisor* library (developer+reviewer+tester example): https://github.com/langchain-ai/langgraph-supervisor-py
- LiteLLM — AI gateway, 100+ providers, budget caps & fallbacks: https://github.com/BerriAI/litellm
- LiteLLM vs OpenRouter (2026 comparison): https://www.truefoundry.com/blog/litellm-vs-openrouter
