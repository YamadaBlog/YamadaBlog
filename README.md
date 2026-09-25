<div align="center">

<img src="./assets/banner.svg" alt="mao@lab -- systems builder: research infrastructure, agent orchestration, security research" width="100%" />

<samp>
  <a href="https://yamadablog.github.io/YamadaBlog/">site</a> ·
  <a href="#-uptime">uptime</a> ·
  <a href="#-ls-work">work</a> ·
  <a href="#-ls-instruments">instruments</a> ·
  <a href="#-cat-domainsmd">domains</a> ·
  <a href="#-tail--f-labnotebook">notebook</a> ·
  <a href="https://github.com/YamadaBlog/pulse-player">pulse-player</a>
</samp>

</div>

---

I build systems that have to be **right**, not just run — research platforms where one look-ahead bug silently
invalidates months of work, a UI component that must behave identically in six frameworks, and orchestration
that has to survive being interrupted halfway through.

Most of what I build is private. This page is the public index: enough to show the shape of the work,
not enough to spoil it.

<!-- telemetry -->
<samp>calibrated 2026-09-25 . 3,756 contributions . 236 active days . longest streak 32d . load avg 16.00 / 27.53 / 18.02</samp>
<!-- /telemetry -->

---

## `$ uptime`

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./assets/seismo-dark.svg">
  <img alt="Seismograph of daily contributions over the last 365 days" src="./assets/seismo-light.svg" width="100%">
</picture>

<sub>No hosted stat widgets. Rendered from the GitHub API by <a href="./scripts/forge.py"><code>scripts/forge.py</code></a> (stdlib Python only) and re-forged nightly.</sub>

---

## `$ ls ~/work`

```text
~/work
├── pulse-player/       public    one core, six frameworks, seven npm packages
├── research-platform/  private   ~1k commits, 9 months . data -> features -> RL -> replay -> execution
├── control-plane/      private   ~700 commits . multi-agent job queue, model router, human gates
├── integrity-tools/    private   temporal-leakage linter, sealed datasets, execution receipts
├── security-work/      private   authorized assessments . binary & protocol analysis
└── anima/              private   embodied AI desktop companion (Tauri/Rust + TS)
```

**[pulse-player](https://github.com/YamadaBlog/pulse-player)** — *public, on npm.* A drop-in music player shipped as seven
`@pulse-music/*` packages: one framework-agnostic core with thin wrappers for Vue, React, Svelte, Angular,
Web Components and React Native. The part I care about is the release engineering — npm provenance
(sigstore-attested, built in CI), the Vue reference checked byte-for-byte in CI, clean-consumer installs tested
end to end, and a post-deploy smoke job on the [live demo](https://yamadablog.github.io/pulse-player/) that
fails on a single console error.

**research-platform** — *private.* Nine months, ~1,000 commits. Cross-validated ingestion, feature pipelines,
reinforcement-learning agents trained on a local-GPU-first / cloud-burst policy, a model factory
(*train → select → replay → qualify*), and guarded execution with drift and out-of-distribution kill-switches.
Backtest, replay and live all call the same `process_bar()`, so they cannot diverge — that is not a test,
it is the architecture.

**control-plane** — *private, in progress.* A personal control plane for multi-agent workflows: a transactional
job queue with atomic claim and idempotent enqueue, bounded retry into a dead-letter table, per-job budget
ceilings checked *before* a billable call, an append-only hash-chained event journal, and containerised worker
pools. Plus a policy-driven router across several model providers with primary/fallback cascades, quarantine
of degraded providers, and a deterministic effect key that refuses to re-issue a call whose outcome is unknown.
Autonomy is **fail-safe by default**: a transition auto-continues only if explicitly whitelisted and within
budget — everything else lands in a human review queue. Scheduled and resumable; deliberately operated
attended for now.

**integrity-tools** — *private, experimental.* What fell out of the above: a static linter for temporal leakage,
a bitemporal as-of store, dataset sealing with Merkle/SHA-256 manifests, and signed execution receipts.
Tools that turn *"this result is real"* into a checkable claim instead of a feeling.

<sub>Private work is described in general terms on purpose — shape, not contents. Architecture walkthroughs on request.</sub>

---

## `$ ls /instruments`

The bench. What is actually installed, wired and used — not a list of things I have heard of.

```text
/instruments
├── bench/          Win11 + WSL2 . agent CLI + MCP servers + hooks + subagents . tmux . ripgrep . just
├── languages/      python 3.12 . typescript . rust (tauri) . c# (reading, mostly) . lua . bash . pwsh
├── data/           pandas . numpy . duckdb . parquet . sqlite (wal, fts5) . polars
├── ml/             pytorch . ppo / rl . mlflow . multi-seed consensus . walk-forward . bootstrap . fdr
├── orchestration/  docker compose . temporal . task queues . scheduled ticks . dstack . cloud gpu
├── models/         multi-provider routing . cost ledgers . json-contract validation . eval harnesses
├── ui/             vue 3 . react . svelte . angular . lit / web components . react native . vite
├── observability/  prometheus . grafana . sli/slo + error budgets . structured alerting . watchdogs
├── delivery/       github actions . gitlab ci . canary deploys . semgrep . gitleaks . locked deps
├── verification/   pytest . hypothesis . vitest . playwright (a11y) . golden traces . replay harnesses
└── analysis/       decompilers . disassemblers . protocol analysis . dynamic instrumentation . proxies
```

**Automations that earn their keep**

- A **git pre-commit canary gate** that blocks any commit which regresses an established fact in my
  knowledge base. A deterministic gate is the only kind an agent cannot talk its way past.
- **Markdown as source of truth, index as cache** — a rebuildable full-text index over notes, plus a
  bitemporal fact table. If the index burns down, nothing is lost.
- **Append-only ADRs** where every claim is classified *proven / logical / hypothesis / rejected* —
  and rejections are kept, with the evidence that killed them.
- **Atomic writes everywhere** (`tmp → fsync → replace`), offsite continuity, restore drills that actually run.
- **SLO reporting that returns `unavailable`** rather than computing a metric from zero observations.

---

## `$ cat domains.md`

Where I am comfortable, and roughly how deep. Deliberately short on detail.

| Domain | What that means here |
|---|---|
| **Research infrastructure** | Making results trustworthy: no look-ahead, sealed test sets, replayable execution, honest statistics under multiple testing. |
| **Agent orchestration** | Durable state machines, idempotency, budget governance, human-in-the-loop gates, multi-provider routing. Persistence over cleverness. |
| **Systems & automation** | Long-running processes that survive interruption, reboot and their own bugs. Schedulers, watchdogs, recovery drills. |
| **Security research** | Authorized assessment only — written scope, rules of engagement, recognized methodology (PTES / OWASP WSTG / ASVS), CVSS-scored findings, timestamped evidence, prioritized remediation. Web/API surface and desktop-client posture: transport and header hardening, cross-origin policy, auth-flow logic, update-channel and code-signing integrity, supply-chain exposure. |
| **Reverse engineering** | Reading things that do not want to be read: obfuscated and virtualized managed binaries, packed interpreters, network protocol and serialization layers. Static analysis first, dynamic only in a controlled lab, and *"do not guess"* when there is no execution oracle. |
| **Applied ML** | Reinforcement learning under non-stationarity, ensembles over point estimates, drift and OOD detection as a safety layer rather than a metric. |
| **Frontend engineering** | Component libraries that must behave identically across framework boundaries, accessibility tested rather than claimed, size budgets enforced in CI. |
| **Document & report engineering** | Templated, QA-gated pipelines that compile documents and audit their own conformance. |

<sub>Security and reverse-engineering work is conducted under explicit written authorization, within a defined
scope, and reported privately to the owner of the system. Targets, findings, evidence and tooling stay
private — permanently. Nothing operational is published here, and nothing ever will be.</sub>

---

## `$ tail -f lab/notebook`

Most experiments end in a written *no*. That is the lab working, not failing.

```diff
- trailing / dynamic exits          cut exactly the tail winners that paid for everything
- mean-reversion on a thin edge     real at zero cost, dead after realistic spreads
- vector DBs, graph RAG and three   evaluated for the memory engine, rejected with reasons.
  agent-memory frameworks           markdown + a rebuildable index outlived all of them
- a "great" backtest                an indicator compared against the wrong-timeframe volatility
+ entry at next-bar open            edge survived moving off the signal bar's close
+ rolling walk-forward + FDR        kept only what survived multiple-testing correction
+ replay engine                     backtest == live, proven: incremental run == batch run
+ idempotent effect keys            a crashed worker can no longer double-charge a provider
! temporal-leakage linter           running. catch look-ahead before it reaches a backtest
! de-virtualizing a protected       partially mapped, then blocked without an execution oracle
  managed binary                    and documented as blocked instead of guessed
```

---

## `$ cat invariants.py`

```python
assert result.data_seen_at <= decision.time          # no look-ahead, ever
assert backtest.process_bar is live.process_bar      # one code path, not two that "should match"
assert out_of_sample.opened == 1                     # the test set is read once, then it is spent
assert sha256(dataset) == manifest[dataset.name]     # results point at the exact bytes that made them
assert transition in contract or requires_human()    # anything unlisted stops and asks
assert effect_key not in ledger or not retried       # never pay twice for an unknown outcome
assert scope.authorized and scope.written            # no target without a signed scope
assert "no edge" in notebook                         # killing ideas is output, not waste
```

---

## `$ whois mao`

Interested in research infrastructure, correctness-critical systems, agent orchestration and security
engineering. The fastest route is GitHub.

```sh
curl -sL yamadablog.github.io/YamadaBlog/card.txt
```

<div align="center">
<sub><samp>this README re-forges itself nightly · press <code>~</code> on the <a href="https://yamadablog.github.io/YamadaBlog/">site</a> for a shell</samp></sub>
</div>
