# Nasdaq-100 Agentic Research and Local-LLM Extension Plan

Date: 2026-08-07
Workspace: `D:\Quant-Stocks`
Status: companion research and implementation plan; does not modify the frozen qme v0.1 strategy

## Executive Decision

Build a hybrid system, not an LLM-first trading system.

- Keep qme's deterministic data, signal, accounting, portfolio, validation, and execution layers as the system of record.
- Use TradingAgents only as a bounded evidence-synthesis and challenge layer.
- Process the entire Nasdaq-100 universe with deterministic features first.
- Run a lightweight agent review on a shortlist and the full debate/risk graph only on finalists and every current holding.
- Never let a free-form LLM response place an order, choose unrestricted position size, alter historical data, or silently substitute for a failed structured response.

The best practical local model for one 32 GB NVIDIA GPU is `Qwen3.6-35B-A3B` at a validated 4-bit quantization, served through vLLM or SGLang with the model's official tool parser. The safer 24 GB option is `Mistral-Small-3.2-24B-Instruct-2506` at 4-bit. On a high-memory local server, `Mistral-Small-4-119B-2603` is the preferred deep-reasoning candidate for the Research Manager and Portfolio Manager, but it should be used only after schema and tool-call evaluation.

The existing workspace is not an implementation checkout of TradingAgents or qme. It contains research plans, audits, utility scripts, and Webull/Alpha Vantage prototypes. Therefore this document specifies the next build; it does not claim the Nasdaq-100 runner already exists.

## 1. Current-System Evaluation

### 1.1 qme design in this workspace

The qme plan is directionally strong:

- Alpha Vantage is the canonical research-data source.
- Raw responses are cached before parsing.
- Backtests are local, versioned, deterministic, and network-free.
- Corporate actions, identity, delisting behavior, costs, taxes, sample splits, and holdout governance are explicit.
- Webull is isolated to account state, preview, execution, and reconciliation.
- Live order placement requires explicit owner confirmation.

Material limitations:

- The `qme/` package described by `IMPLEMENTATION_PLAN.md` is not present yet.
- The current plans do not include a Nasdaq-100 membership service or batch-analysis runner.
- Alpha Vantage `LISTING_STATUS` cannot by itself prove historical Nasdaq-100 membership.
- Alpha Vantage fundamentals and company overview data need filing-availability controls before historical use.
- Official delisting-return and historical security-identity gaps remain.
- There is no LLM evidence store, model router, model evaluation harness, or structured-output production gate.

### 1.2 Upstream TradingAgents capability

The current upstream graph contains:

1. Market, Sentiment, News, and Fundamentals analysts.
2. Bull and Bear researchers.
3. A Research Manager.
4. A Trader.
5. Aggressive, Neutral, and Conservative risk debaters.
6. A Portfolio Manager.
7. Deterministic final signal processing and persistent reflection/checkpoint support.

Important operational facts:

- The four analyst branches are connected in sequence, not executed concurrently.
- The graph exposes only two generative model slots: `quick_think_llm` and `deep_think_llm`.
- The quick model serves all analysts, both researchers, the Trader, and all three risk debaters.
- The deep model serves the Research Manager and Portfolio Manager.
- Per-agent models require a code change or an OpenAI-compatible routing proxy.
- The Sentiment Analyst, Research Manager, Trader, and Portfolio Manager use Pydantic structured output in upstream commit `a33fd4c0f134485a43553a2c23a63cb14adbd88f`.
- Current upstream behavior falls back to free text when structured invocation fails. This is useful for an interactive research demo but is unsafe as a production decision contract.
- `benchmark_ticker` can be overridden to `QQQ`; the US default otherwise remains `SPY`.
- Data-vendor chains are explicit and do not silently use an unconfigured fallback.
- Checkpoint/resume is available but must be enabled.

### 1.3 Fit assessment

| Capability | Current fit | Decision |
|---|---:|---|
| Deterministic momentum research | Strong design, not implemented | Build first |
| Reproducible backtesting | Strong design, not implemented | Preserve as authority |
| Current single-ticker qualitative research | Good prototype fit | Use in shadow mode |
| Historical point-in-time agent analysis | Weak | Block until source cutoffs are enforced |
| Nasdaq-100 batch processing | Absent | Add external orchestrator |
| Cross-sectional portfolio ranking | Absent in TradingAgents | Keep in qme |
| Broker execution | Webull prototype/plan only | Keep separate from agents |
| Local-model compatibility | Supported through Ollama/OpenAI-compatible servers | Validate tool and schema behavior |
| Production decision safety | Insufficient | Add fail-closed contracts and human gate |

## 2. Nasdaq-100 Universe Contract

### 2.1 Do not hard-code “100 tickers”

The Nasdaq-100 represents companies, while eligible multiple share classes can each be constituents. The May 2026 methodology also allows Fast Entry and certain quarterly additions to temporarily raise the constituent count above 100. The runner must consume a dated security list of dynamic length.

### 2.2 Authoritative source order

1. Nasdaq Global Index Watch (GIW) component/weighting data or the authenticated GIW web service.
2. Nasdaq GIFFD delivery when historical/pro-forma files are licensed.
3. Official Nasdaq change announcements for reconciliation.
4. QQQ holdings or Alpha Vantage ETF holdings only as a secondary discrepancy check, never as the index authority.

GIW's public page exposes only limited data without login. Production automation should use the documented secure web service or a controlled manual-download workflow, not an undocumented page scraper.

### 2.3 Required membership schema

Each snapshot must include:

```text
index_symbol             # NDX
effective_at             # timestamp/date membership becomes active
announced_at             # source announcement timestamp when available
source_url
source_file_sha256
source_acquired_at
company_name
security_symbol
security_id              # internal stable identifier
cik                       # when mapped
share_class
index_weight             # nullable when source lacks it
change_type              # add/remove/retain
reason                   # scheduled, fast entry, corporate action, replacement
supersedes_snapshot_id
```

Store immutable snapshots and an explicit diff. A membership change must fail the scheduled run until the diff is either auto-verified against a Nasdaq announcement or manually approved.

### 2.4 Current methodology change to encode

The revised methodology became effective May 1, 2026. It adds rank-based quarterly review in March, June, and September, Fast Entry for sufficiently large new listings, and a low-float weighting cap. The first quarterly rebalance under those rules became effective June 22, 2026.

The June 2026 official change set was:

- Add: `ALAB`, `CRWV`, `NBIS`, `RKLB`, `TER`.
- Remove: `CHTR`, `CTSH`, `INSM`, `VRSK`, `ZS`.

This change set is a reconciliation fixture, not a substitute for the complete membership file.

### 2.5 Two distinct operating modes

- `current_membership`: analyzes the latest effective official basket. Appropriate for live research.
- `point_in_time_membership`: resolves membership effective on the requested historical date. Mandatory for backtests and historical agent evaluation.

Never run a 2011-2026 backtest using today's constituents. That creates survivorship and selection bias even if price data itself is historical.

## 3. Recommended Nasdaq-100 Analysis Architecture

```text
Official NDX membership snapshot
            |
            v
Identity + as-of-time + data-quality gates
            |
            v
Deterministic features for every constituent
  momentum | trend | volatility | liquidity | drawdown
  earnings proximity | QQQ beta | correlation | data freshness
            |
            v
Cross-sectional score and pre-registered screen
            |
       +----+------------------------+
       |                             |
       v                             v
All current holdings           Top 20-30 candidates
       |                             |
       +-------------+---------------+
                     v
          Lightweight evidence review
       market + filings + news + sentiment
                     |
                     v
             Top 5-10 finalists
                     |
                     v
          Full TradingAgents debate
      bull/bear -> manager -> trader -> risk
                     |
                     v
        Fail-closed structured decision
                     |
                     v
      Deterministic portfolio constraints
                     |
                     v
     Human-approved Webull preview/reconcile
```

### 3.1 Why the funnel is necessary

A full TradingAgents run is a serial, multi-turn tool-calling workflow. A reasonable engineering estimate is roughly 15-25 local generations per ticker, depending on tool loops and debate settings. A full daily run over the entire basket would therefore be on the order of 1,500-2,500 generations, plus market-data requests. That is slow, quota-heavy, difficult to reproduce, and unlikely to add proportional signal.

The funnel keeps the whole-universe comparison deterministic and reserves expensive reasoning for cases where qualitative evidence could change a decision.

### 3.2 Required coverage rule

Always include:

- every current holding;
- every pending target order;
- every name with a membership change;
- every name with a material corporate action or filing since the last run;
- deterministic top-ranked candidates;
- names near an exit threshold.

This prevents the screen from hiding deteriorating holdings or operational risks.

## 4. Data-Source Design

| Data | Authority | Refresh | Notes |
|---|---|---|---|
| NDX membership/weights | Nasdaq GIW/GIFFD | On official change/rebalance schedule plus daily check | Immutable dated snapshots |
| Raw daily OHLCV | Alpha Vantage | Daily after close | Cache raw; compute indicators locally |
| Dividends/splits | Alpha Vantage plus issuer/SEC validation for exceptions | Daily/event-driven | Self-compute adjusted research series |
| Filing metadata/documents | SEC EDGAR | Event-driven | Enforce `accepted_at <= as_of` |
| Comparable XBRL facts | SEC Company Facts | Event-driven/nightly bulk | Map taxonomy and fiscal periods carefully |
| Current news/sentiment | Alpha Vantage | Candidate/holding only | Store article timestamp, retrieval time, URL, hash |
| Macro | FRED | Release-driven | Preserve release/vintage semantics where needed |
| Current quotes/account/orders | Webull | Live/paper workflow | Never canonical backtest data |

### 4.1 Alpha Vantage scale decision

The free Alpha Vantage limit is 25 requests/day. It cannot support a daily Nasdaq-100 research refresh. Initial full daily-history retrieval is also premium-only. Use a premium tier, cache aggressively, and make the scheduler quota-aware.

Recommended request policy:

- Initial load: one full-history pull per constituent plus corporate actions.
- Daily maintenance: compact price refresh for all constituents.
- Fundamentals/news: holdings and shortlist only, with TTL and filing/event invalidation.
- Technical indicators: compute locally; do not spend one API request per indicator.
- Retry only idempotent reads, with exponential backoff and a global token bucket.
- Validate Alpha Vantage payload shape because throttles and business errors may arrive as HTTP 200.

### 4.2 Point-in-time filing control

Use SEC submissions metadata as the availability clock. A quarterly fact with fiscal period ending in March is not usable in a historical March analysis if the filing was accepted in May. Each extracted value must retain:

```text
cik
accession_number
form
filing_date
accepted_at
period_start
period_end
taxonomy
tag
unit
value
source_url
source_hash
```

SEC data APIs require no key and provide submissions and XBRL facts. Automated access must identify the client and stay within the SEC fair-access limit of 10 requests/second; bulk nightly archives are preferable for broad backfills.

## 5. Deterministic Research Profile for Nasdaq-100

Create a separate, pre-registered `ndx_v0_1` profile. Do not silently replace the broad-universe qme v0.1 baseline.

### 5.1 Candidate baseline

- Universe: point-in-time NDX securities.
- Signal date: month-end close.
- Primary signal: 12-1 momentum.
- Secondary diagnostics: 6-month momentum, 3-month momentum, trend quality, 63-day volatility, 6-month max drawdown, QQQ beta, correlation to QQQ.
- Selection variants: top 10 and top 20 only, declared before validation.
- Weighting: equal weight as the control; inverse-volatility is a separately labeled extension.
- Regime: QQQ above its 200-day moving average as the primary defensive variant.
- Execution: qme's deterministic T+1 research-fill policy.
- Costs: 5/10/25 bps per side.
- Benchmarks: QQQ total return, equal-weight point-in-time NDX universe, and a no-agent deterministic strategy.

The broad-universe plan's “top 50” should not be copied into a roughly 100-security universe; it would hold about half the basket and dilute the research question.

### 5.2 Portfolio controls

Apply after any agent opinion:

- maximum single-name weight;
- maximum issuer exposure across multiple share classes;
- sector/industry concentration report and optional cap;
- volatility and drawdown budget;
- correlation-cluster cap;
- minimum liquidity and price sanity gates;
- earnings/corporate-action risk flag;
- maximum turnover and cash buffer;
- no leverage in the baseline.

The LLM may recommend a bounded risk flag or veto for missing/contradictory evidence. It must not bypass these controls.

## 6. Ideal Local Models

### 6.1 Model selection criteria

For this system, tool behavior matters more than generic chat preference. A candidate model must demonstrate:

1. Correct multi-turn function calling.
2. Reliable Pydantic/JSON schema output.
3. Grounded use of supplied evidence and timestamps.
4. Numeric consistency and unit awareness.
5. Stable Buy/Overweight/Hold/Underweight/Sell semantics.
6. Acceptable latency at the intended context and batch size.
7. A permissive license suitable for local use.

### 6.2 Practical deployment tiers

| Local capacity | Recommended model | Best use | Decision |
|---|---|---|---|
| 16 GB VRAM | `gpt-oss-20b` or a validated 14B-class instruct model at 4-bit | Development, extraction, smoke tests | Not the preferred final manager |
| 24 GB VRAM | `Mistral-Small-3.2-24B-Instruct-2506` at 4-bit | Both current model slots; strong schema/tool baseline | Safest entry build |
| 32 GB VRAM | `Qwen3.6-35B-A3B` at 4-bit | Both slots or quick/research roles | Best overall consumer build |
| 32 GB VRAM, throughput-first | `GLM-4.7-Flash` at 4-bit | Fast analyst/researcher workload | Strong alternative; serving stack is less mature |
| 80-96+ GB accelerator/unified memory | `Mistral-Small-4-119B-2603` at 4-bit | Deep manager/portfolio decisions | Best high-end candidate after evaluation |

Do not make an 8B-class model the Portfolio Manager. Small models are appropriate for extraction, classification, summarization, and test fixtures, not for the final multi-source risk decision.

### 6.3 Model-by-role target after routing refactor

| Role | Preferred model | Reasoning mode | Notes |
|---|---|---:|---|
| Deterministic quant screen | No LLM | N/A | Python/Polars/Pandas |
| Market analyst/tool planner | Qwen3.6-35B-A3B or GLM-4.7-Flash | Low/medium | Strong tool orchestration |
| News/sentiment analyst | Mistral Small 3.2 or Qwen3.6 | Low | Require evidence IDs and timestamps |
| Fundamentals/filings analyst | Qwen3.6; Mistral Small 4 for finalists | Medium/high | Long documents and point-in-time controls |
| Bull/Bear researchers | Qwen3.6 or GLM-4.7-Flash | Medium/high | Diversity should come from prompts/evidence, not random temperature alone |
| Research Manager | Mistral Small 4; Qwen3.6 on 32 GB | High | Fail-closed schema |
| Trader proposal | Mistral Small 3.2 | Low | Constrained action/levels/sizing schema |
| Risk debaters | Qwen3.6 or GLM-4.7-Flash | Medium | Share the same immutable evidence packet |
| Portfolio Manager | Mistral Small 4; Qwen3.6 on 32 GB | High | Advisory decision only; deterministic limits win |
| Reflection | 14B-24B instruct model | Low | Not in the live decision path |
| Evidence retrieval | `BAAI/bge-m3` | N/A | Dense+sparse multilingual retrieval, 8192-token inputs |
| Evidence reranking | `BAAI/bge-reranker-v2-m3` | N/A | Cross-encoder relevance gate |

### 6.4 No-code recommendation for current TradingAgents

Because upstream exposes only two names and one backend URL:

- 24 GB: use Mistral Small 3.2 for both `quick_think_llm` and `deep_think_llm`.
- 32 GB: use Qwen3.6-35B-A3B for both slots to avoid model swapping.
- High-memory server: use a routing proxy that maps the two model names to separate serving endpoints, or add per-role endpoints in code.

Using two different large models on constrained hardware can be slower than one good model because the server repeatedly evicts and reloads weights.

## 7. Serving and Hardware Guidance

### Recommended production-like workstation

- NVIDIA GPU with 32 GB VRAM.
- 128 GB system RAM.
- Modern 12-16 core CPU.
- 2 TB NVMe dedicated to models, caches, raw responses, and reports.
- Linux or WSL2 for the least-friction vLLM/SGLang path.

### Serving choice

- Prefer vLLM or SGLang for scheduled batch work, concurrency, and explicit tool parsers.
- Use Ollama for the simplest proof of concept.
- For Qwen3.6, enable the official Qwen reasoning and `qwen3_coder` tool-call parsers.
- For GLM-4.7-Flash, enable the `glm47` tool parser and matching reasoning parser.
- For Mistral, enable the Mistral tool parser.
- Start with 16K-32K maximum context. Do not allocate 256K merely because the model supports it; KV cache can consume the memory needed for throughput.

### AMD Ryzen AI Max+ 395 / Halo

The 128 GB unified-memory Halo platform is valuable when the goal is to fit a 119B-class 4-bit model locally and privacy/capacity matter more than throughput. For the daily Nasdaq-100 funnel, a 32 GB NVIDIA GPU is usually the better performance investment because the recommended 24B-35B models fit and the CUDA serving ecosystem is more mature. Buy Halo for large-model capacity; buy NVIDIA for faster repeated agent runs.

## 8. Production Safety and Model Evaluation Gates

### 8.1 Fail closed on decision schemas

Modify the TradingAgents adapter so that structured failure produces:

```text
status = DEGRADED_SCHEMA_FAILURE
trade_eligible = false
raw_response_hash = ...
error = ...
```

Do not automatically promote free text to a valid decision. Free-text fallback may remain for human-readable research, but it cannot enter portfolio construction.

### 8.2 Required evaluation suite

Build a frozen fixture set containing:

- correct and malformed tool calls;
- empty data and vendor error payloads;
- conflicting price sources;
- stale news presented beside newer news;
- fiscal-period end before filing availability;
- stock splits and large price discontinuities;
- multiple share classes;
- ticker changes;
- earnings and corporate-action dates;
- negative, neutral, and positive investment cases;
- adversarial prompt content inside news/filings.

Promotion thresholds:

- 100% valid schema on Research Manager, Trader, and Portfolio Manager fixtures.
- At least 99.5% valid tool calls across repeated tool-use fixtures.
- 100% rejection of post-`as_of` evidence.
- Zero fabricated source IDs in the evaluation set.
- Zero order-eligible outputs when any mandatory source is stale or missing.
- Decision stability and calibration reported across at least three repeated runs.
- Latency, tokens/second, prompt tokens, completion tokens, retries, and peak memory captured.

### 8.3 Agent influence policy

Start with `influence_mode = report_only`.

After shadow validation, consider one of two bounded modes:

- `veto_only`: agents may block a candidate for a documented event/data risk.
- `bounded_overlay`: a validated agent score may move the deterministic score by at most a pre-registered small amount.

Never tune the overlay on the final holdout.

## 9. Batch Orchestration Contract

Each batch run must pin:

```text
run_id
analysis_as_of
membership_snapshot_id
data_snapshot_ids
strategy_config_hash
prompt_bundle_hash
model_id_and_revision
quantization_hash
serving_engine_and_version
temperature_and_reasoning_settings
tool_schema_version
code_revision
```

Operational requirements:

- One immutable evidence packet per ticker.
- Global semaphores for market-data and LLM calls.
- Separate concurrency limits for quick and deep models.
- Per-ticker checkpoint/resume and idempotent outputs.
- Retry budget by error class; never retry validation failures indefinitely.
- Candidate priority queue: holdings and exit risks first.
- Batch deadline and partial-completion status.
- A manifest listing completed, degraded, skipped, and failed tickers.
- No portfolio decision until every mandatory holding has a valid or explicitly degraded result.

## 10. Proposed Build Backlog

These are companion tickets and are not yet merged into `TICKET_BACKLOG.md`.

### NDX-001 - Pin integration repositories and versions

- Create the qme source package described by the existing plan.
- Add TradingAgents as a pinned dependency or isolated service.
- Record upstream commit, dependency lock, and license inventory.

### NDX-002 - Official Nasdaq membership provider

- Implement GIW/manual-download adapters.
- Store immutable snapshots, source hashes, effective dates, and diffs.
- Add the June 2026 change set as a reconciliation fixture.

### NDX-003 - Point-in-time NDX universe resolver

- Resolve membership by effective date.
- Support multiple share classes and temporary counts above 100.
- Hard-fail historical runs without dated membership.

### NDX-004 - Nasdaq universe audit

- Reconcile Nasdaq snapshot, security master, Alpha Vantage coverage, SEC CIK, and Webull instrument mapping.
- Report missing/ambiguous symbols before analysis.

### NDX-005 - SEC EDGAR data spine

- Ingest submissions, filing metadata, selected filings, and XBRL facts.
- Enforce accepted-time cutoffs and SEC fair-access controls.

### NDX-006 - Nasdaq batch data refresh

- Add AV premium-aware compact refresh, corporate-action invalidation, caching, and quota manifests.
- Compute indicators locally.

### NDX-007 - Deterministic NDX feature matrix

- Produce same-date, cross-sectional features for all eligible securities.
- Add freshness, completeness, and outlier audits.

### NDX-008 - Pre-registered NDX strategy profile

- Implement top-10/top-20 variants, QQQ regime, costs, benchmarks, and portfolio controls.
- Keep broad qme v0.1 unchanged.

### NDX-009 - Evidence packet and provenance store

- Normalize filings, news, price diagnostics, and macro evidence.
- Add stable source IDs, timestamps, hashes, deduplication, and prompt-injection sanitization.

### NDX-010 - Local model serving profile

- Provide vLLM/SGLang/Ollama configurations for selected models.
- Pin parser flags, context, quantization, and model revision.

### NDX-011 - Per-role model router

- Replace the two-object limitation with role-to-model and role-to-endpoint configuration.
- Keep a compatibility mode for unmodified upstream TradingAgents.

### NDX-012 - Fail-closed structured-output adapter

- Make schema errors explicit and trade-ineligible.
- Add bounded retries and raw-response hashes.

### NDX-013 - Model capability evaluation harness

- Run frozen tool, schema, grounding, numeric, leakage, and latency fixtures.
- Produce a comparable scorecard for every model/quantization/engine combination.

### NDX-014 - Funnel scheduler

- Screen all names deterministically.
- Analyze holdings and top 20-30 with the quick layer.
- Run full debate on top 5-10 and high-risk holdings.
- Support checkpoint/resume and global quotas.

### NDX-015 - Cross-sectional result aggregator

- Normalize outputs across tickers.
- Prevent one ticker's prose length/confidence style from affecting ranking.
- Produce candidate, holding-risk, and degraded-run tables.

### NDX-016 - Deterministic portfolio policy

- Apply issuer, sector, volatility, correlation, turnover, and liquidity constraints.
- Treat agent output as report-only initially.

### NDX-017 - Observability and incident controls

- Capture source freshness, failures, retries, latency, throughput, model memory, and schema rates.
- Add kill switch, batch deadline, and degraded-mode policy.

### NDX-018 - Shadow and walk-forward validation

- Compare deterministic-only, veto-only, and bounded-overlay variants.
- Use point-in-time membership/evidence.
- Freeze design before the governed holdout.

### NDX-019 - Paper-trading handoff

- Generate broker-neutral targets and Webull previews only after all gates pass.
- Preserve explicit owner confirmation and reconciliation.

## 11. Recommended Execution Order

1. Build qme foundation and Alpha Vantage mini-spine from the existing backlog.
2. Add NDX membership snapshots and point-in-time resolver.
3. Build the deterministic all-universe feature/screening run.
4. Add SEC filing availability and evidence provenance.
5. Stand up one local model and pass tool/schema evaluation.
6. Add the quick shortlist workflow in report-only mode.
7. Add the full debate workflow for finalists and holdings.
8. Add cross-sectional aggregation and deterministic portfolio constraints.
9. Run shadow/walk-forward comparisons.
10. Only then connect outputs to Webull preview and paper reconciliation.

## 12. Acceptance Criteria for “Nasdaq-100 Ready”

The system is not Nasdaq-100 ready until:

- The complete latest official basket can be reproduced from a dated source file.
- A historical date resolves the membership effective on that date.
- Every security maps through internal security ID, Alpha Vantage symbol, SEC CIK where applicable, and Webull instrument or an explicit exception.
- One batch produces deterministic features for every constituent at the same `as_of` cutoff.
- Every holding is analyzed even if it misses the candidate screen.
- The run resumes safely after interruption without duplicating outputs or calls.
- Structured decision schemas pass the promotion threshold.
- Missing/stale evidence makes an output trade-ineligible.
- Deterministic-only results remain available as the control.
- The agent overlay shows incremental value in shadow/walk-forward validation.
- No live order can bypass owner confirmation.

## Sources

- TradingAgents graph setup: https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/graph/setup.py
- TradingAgents default configuration: https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/default_config.py
- TradingAgents structured-output helper: https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/agents/utils/structured.py
- TradingAgents schemas: https://github.com/TauricResearch/TradingAgents/blob/main/tradingagents/agents/schemas.py
- Nasdaq-100 methodology: https://indexes.nasdaqomx.com/docs/methodology_NDX.pdf
- Nasdaq June 2026 quarterly changes: https://www.nasdaq.com/press-release/nasdaq-100-indexr-june-2026-quarterly-changes-2026-06-12
- Nasdaq Global Index Watch weighting page: https://indexes.nasdaqomx.com/Index/Weighting/NDX
- Alpha Vantage documentation: https://www.alphavantage.co/documentation/
- Alpha Vantage premium limits: https://www.alphavantage.co/premium/
- SEC EDGAR APIs: https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC developer resources and fair access: https://www.sec.gov/about/developer-resources
- Qwen3.6-35B-A3B model card: https://huggingface.co/Qwen/Qwen3.6-35B-A3B
- GLM-4.7-Flash model card: https://huggingface.co/zai-org/GLM-4.7-Flash
- Mistral Small 3.2 24B model card: https://huggingface.co/mistralai/Mistral-Small-3.2-24B-Instruct-2506
- Mistral Small 4 119B model card: https://huggingface.co/mistralai/Mistral-Small-4-119B-2603
- BGE-M3 embedding model: https://huggingface.co/BAAI/bge-m3
- BGE reranker v2 M3: https://huggingface.co/BAAI/bge-reranker-v2-m3
