# Independent System Audit — Quant-Stocks Trading System

Date: 2026-08-07
Scope: full workspace — strategy specs, quant methodology, scoring math, trading code, data/analysis scripts, and operational state.
Method: document review plus line-level code audit of all ~10,050 lines of project Python. Selected findings were verified by executing code against synthetic data and by cross-checking real output files on disk.

Finding prefixes: S = system/operational, Q = quant methodology, W = Webull trading skill code, D = data/analysis scripts.

---

## Executive Summary

**The single biggest risk to profitability is not in any formula. It is that the deterministic core the plans depend on has never been built, while planning scope keeps expanding.**

- The `qme` package specified by `IMPLEMENTATION_PLAN.md` (May 31) does not exist. Ten weeks later, the newest plan layers a Nasdaq-100 agent funnel, local LLM serving, model routers, and evaluation harnesses on top of that unbuilt foundation.
- The workspace has **no working version control**: `.git/` is empty (zero objects, no HEAD). Every plan's lineage requirement ("code_revision", "pinned code", "bit-identical reruns") is currently unsatisfiable.
- The code that does exist splits into: a Webull trading rail whose safety layer is **advisory rather than enforced** (critical confirmation and risk-limit gaps, W1/W2/W17), and side-project scripts (insider scanner, XLF spread monitor, LEAPS hedge prototype) that appear in no plan.
- The composite scoring spec contains several genuine math defects (dead entry/exit thresholds, tail-destroying normalization, an ill-defined OBV factor, a point-in-time violation in the universe filter) that should be fixed on paper before any implementation.
- The planning documents themselves are unusually disciplined — holdout governance, pre-registration, cost/tax awareness, fail-closed LLM contracts are all correct instincts. The system's problem is sequencing and enforcement, not intent.

**Recommended course: freeze all new planning, cut scope to the deterministic NDX baseline, put the workspace under version control today, and do not point the Webull skill at a funded account until findings W1, W2, and W17 are fixed.**

---

## Part 1 — System State (S findings)

### What exists vs. what the plans assume

| Component | Planned in | Status |
|---|---|---|
| `qme/` package (data spine, backtest, validation) | IMPLEMENTATION_PLAN.md | **Not started** |
| NDX membership service, funnel, LLM layer | NASDAQ100 plan | Not started (plan is 0 days old) |
| Composite scoring engine v0.2 | composite_momentum_scoring_spec.md | Not started |
| Webull execution rail | Phase 5 tickets | Prototype + `webull_skill` package exist; unsafe (Part 3) |
| Alpha Vantage ingestion | Phase 1 tickets | Only `av_probe.py` (good) and the insider scanner (off-plan) |
| Insider transaction scanner | — (no plan) | Built; **never run beyond `--prepare-only`**; has a crash bug (D1) |
| XLF 53/56 call-spread monitor | — (no plan) | Built and ran 2026-06-09; math correct; quote-validation gap (D11) |
| QQQ put-spread hedge prototype | tools/webull/HANDOFF.md | Signing verified vs UAT; blocked on endpoint paths; no confirm gate (D19) |

### S1 — CRITICAL: No version control
`.git/` exists but is completely empty — no objects, no refs, no HEAD. Nothing in this workspace is versioned. This contradicts non-negotiable #6 ("backtests deterministic from pinned data versions and code") and every lineage field (`code_revision`, `strategy_config_hash`) in the plans. Any backtest run today would be unreproducible by construction.

### S2 — HIGH: Secret hygiene
- `ALPHA_VANTAGE_API_KEY` sits in plaintext `.env` at the repo root with **no `.gitignore`**. The first careless `git init && git add .` commits the key.
- `alpha_vantage_webull_combined_plan.md` records that the AV key was pasted into a chat and should be rotated "after the ingestion prototype is proven". `HANDOFF.md` records the same for the Webull App Key/Secret. Nothing indicates either rotation happened (D23).
- Three scripts read credentials from a hardcoded path in a *different* repo (`D:\Quant-futures-app\.env`), doubling the secret's blast radius and contradicting the repo-`.env` convention the other scripts follow.

### S3 — MEDIUM: Scope drift
The three pipelines that actually ran (insider scan prep, spread monitor, account report) plus the LEAPS-hedge mission in HANDOFF.md are absent from every strategy plan. Each consumed build/debug time and none feeds the momentum system. The insider scanner's universe even includes ~684 SPAC units/warrants/rights that every qme spec explicitly excludes. This is not fatal, but it is the pattern that explains S1: effort is going to side quests while the core ages as paper.

### S4 — MEDIUM: Plan-to-code ratio
Between 2026-05-31 and 2026-08-07 the workspace gained ~5 planning/audit documents (~100 KB of markdown, including two audits of specs that have no implementation) and effectively zero lines of the system they describe. The NDX plan's own execution order (§11) correctly puts "build qme foundation" first — follow it.

---

## Part 2 — Quant Methodology (Q findings)

The v0.1 concept (12-1 cross-sectional momentum, monthly, equal weight, T+1 open, costed, holdout-governed) is academically defensible and conservatively specified. `momentum_strategy_audit.md` already covers the accounting, delisting, survivorship, and corporate-action risks well; those findings stand and are not repeated here. The following are **new** issues, mostly in `composite_momentum_scoring_spec.md`.

### Q1 — HIGH: The score entry/exit thresholds are dead rules
`final_score = percentile_rank(composite_raw) * 100`, so the score is a cross-sectional percentile by construction. Entry requires `score >= 75 AND rank <= 10/15/20`; exit at `score < 60 OR rank > 20`.

- In any universe of ≥ ~80 names, `rank <= 20` implies percentile ≥ 75, so `score >= 75` is always satisfied when the rank condition is — it never binds.
- Exit: `score < 60` means falling below the 60th percentile (rank ≈ #40 in a 100-name universe, #600 in a 1,500-name universe). `rank > 20` always fires first.

The entire scoring-threshold layer collapses into pure rank rules (enter top-N, exit past rank 20). If absolute-quality gating is intended — e.g., "don't buy anything in a weak tape even if it ranks #1" — thresholds must be applied to `composite_raw` or to raw factor values, not to a percentile. As written, the market filter is the *only* absolute defense, and the spec should say so explicitly.

### Q2 — HIGH: The normalization chain destroys information exactly where selection happens
`winsorize(5,95) → z-score → percentile_rank` has two problems:

1. z-scoring before percentile-ranking is a no-op (any monotone transform preserves ranks).
2. Winsorizing before percentile-ranking is *destructive*: clipping to the 5th/95th percentiles creates ties among the top 5% of names per factor. In a ~100-name NDX universe that ties the top 5 names; in a 1,500-name universe it ties the top 75 — and the strategy then selects the top 10 by rank. The flagship 12-1 factor loses all ordering among precisely the names being chosen. Tie-breaking is also undefined (QME-042 requires deterministic ties; the composite spec doesn't).

Fix: either use winsorized z-scores directly (keeps magnitude, needs winsorization) or plain percentile ranks (needs no winsorization). The current chain is the worst of both.

### Q3 — HIGH: OBV slope is mathematically ill-defined
`obv_slope = OBV / OBV_20d_ago - 1`. OBV is a cumulative sum from an arbitrary start date; its level has no meaning, can be zero (division blow-up) or negative (a *rise* from −1,000 to −500 computes as −50%, scored as a decline). Replace with a scale-free form, e.g. `(OBV − OBV_20d_ago) / avg_daily_volume_20d`, or drop it — `up_volume_ratio` already covers accumulation.

### Q4 — MEDIUM: Downside volatility definition is ambiguous and, read literally, wrong
`stdev(negative_daily_returns, 63)` — the standard deviation *of only the negative returns* measures dispersion of down days around their own mean: a stock that falls exactly −2% every down day has downside vol ≈ 0. The standard measure is semideviation: `sqrt(mean(min(r,0)^2))` over all 63 days. Pin the formula before implementation.

### Q5 — MEDIUM: `adjusted close >= $10` universe floor is a point-in-time violation
Adjusted prices restate retroactively with every subsequent split. A $100 stock in 2015 that later splits 20:1 has a 2015 *adjusted* close of $5 and gets retroactively excluded from the 2015 universe. Price floors must use raw close as of the signal date. (Same discipline applies to the market filter input — the existing audit's finding #14.)

### Q6 — LOW: `momentum_ir = R_6M / vol_126d` references an undefined factor
Only `vol_63d` and `downside_vol_63d` are defined in the risk sleeve. Define `vol_126d` or reuse `vol_63d`; either is fine, ambiguity is not.

### Q7 — MEDIUM: 1-month momentum is positively weighted against its own evidence
The 12-1 signal skips the last month *because* 1-month returns exhibit short-term reversal (Jegadeesh 1990). The absolute-momentum sleeve then adds `R_1M` back with +10% weight (and `R_3M` at 20% overlaps partially). This may still be right for large-cap NDX names post-2010 — but it contradicts the spec's stated rationale for merging sleeves ("reduce double-counting") and must be justified by the Phase A IC diagnostics, not assumed. Pre-register the possibility that `R_1M` enters with *negative* sign or weight zero.

### Q8 — MEDIUM: Effective momentum weight is far above the nominal 40%, and sleeve re-ranking amplifies noise
`R_6M` appears in absolute momentum, relative strength (RS_6M), and risk quality (momentum_ir numerator); trend quality (price vs 200DMA, high proximity) is strongly correlated with 12-1 momentum. Effective medium-term-momentum weight is plausibly 70%+. Separately, re-ranking each sleeve to uniform [0,100] forces *equal cross-sectional dispersion on every sleeve* — a sleeve with genuinely no signal gets amplified to the same spread as the strongest sleeve. That is the stated intent ("prevents compressed sleeves from becoming irrelevant") but it is backwards: a compressed sleeve *should* matter less. The planned factor-correlation matrix in Phase A will surface this — act on it.

### Q9 — MEDIUM: Position sizing under rank hysteresis is underspecified
Entry at rank ≤ 10, exit at rank > 20 means the holding count floats between 10 and 20. "Equal weight at entry, allow drift" leaves open: 1/10th of what (NAV? free cash?); what funds a new entry when nothing exits; how the 25% single-name cap is enforced (trim at rebalance triggers taxable gains and turnover); what happens at the cap boundary. These mechanics drive turnover, cash drag, and tax — specify them at QME-043/047 level before v0.2 is built. Also note: 25% in one name is a very large cap for a system marketed as risk-controlled; with top-10 equal entries, consider 15%.

### Q10 — LOW: 12-1 formula defined two ways
`IMPLEMENTATION_PLAN.md` uses `ln(adj_close[t-21]/adj_close[t-252])`; the composite spec uses the simple return. Rank-equivalent cross-sectionally, but IC and spread diagnostics computed on raw values will differ. Pin one.

### Q11 — MEDIUM: Wash sales are unmodeled
Monthly rank churn (exit past rank 20, re-enter at rank ≤ 10 weeks later) will routinely repurchase the same names within 30 days in a taxable account, deferring realized losses under wash-sale rules. No plan document mentions wash sales. The tax model (QME-047/estimated tax reporting) should track them, and the *strategy-level* answer — run this in a tax-advantaged account if at all possible — belongs in the launch decision (see Part 6).

### Q12 — MEDIUM: All three sample windows sit inside one secular tech bull
Dev 2011-2018, validation 2019-2021, holdout 2022+. The 2022 holdout is a real stress (momentum-in-tech unwound hard), but no window contains a 2000-02-style regime where the QQQ filter and momentum both fail for years. AV daily data reaches back to 1999; NDX membership that far back is hard, but even a rough pre-2010 stress run (clearly labeled non-PIT) would tell you more about crash behavior than any weight tweak. At minimum, document that the system has never been tested in a multi-year bear.

### Q13 — STRATEGIC: NDX-only cross-sectional momentum is a thin edge
Momentum premia are strongest in broad universes including mid/small caps; inside ~100 correlated mega-caps the cross-sectional spread is narrow and top-10 selections will frequently be a single-sector bet (the NDX plan's correlation-cluster cap in §5.2 is essential, not optional). The pre-registered benchmark set (QQQ, equal-weight PIT universe, no-agent control) is exactly right — trust it, and be prepared for the honest outcome that top-10 NDX momentum after costs and taxes does not beat holding QQQ. That is a legitimate research result, not a failure of engineering.

### Q14 — LOW: Sign conventions unpinned
`inverse_score(max_drawdown_6M)` flips direction depending on whether drawdown is stored as a negative or positive magnitude. Same class of issue as Q6 — pin conventions in the spec, with a worked numeric fixture per factor.

**Also verified correct:** all sleeve and composite weights sum to 1.0; the 12-1 window arithmetic (t−252 → t−21) is correct; the 75/60-style hysteresis structure (as rank 10/20) is a sound anti-churn design; cost tiers and T+1 adjusted-open fill derivation (`raw_open × adj_close/raw_close`) are appropriate.

---

## Part 3 — Trading Code: `webull_skill` package (W findings)

Full audit of `tools/webull/openapi-skills/`. Design layering (CLI → tools → SDK) is clean, region modeling thoughtful, formatting consistent. But the safety architecture is **advisory, not enforced**.

| # | Finding | Severity |
|---|---|---|
| W1 | Order confirmation exists only in SKILL.md prose. No code path requires a confirm flag, preview-first, or dry-run; `webull-skill trading --action place --order-file o.json` submits a live order immediately. | **Critical** |
| W2 | Quantity/notional/whitelist limits are bypassed for entire order classes: option single (`option_order.py:216-283`) and strategy orders, stock combo (`stock_order.py:705-730`), algo (`:737-803`), event orders, and cash-`AMOUNT` orders (`guards.py:92-94` skips quantity; no cap on `total_cash_amount` anywhere). Crypto `AMOUNT` orders skip validation entirely, including the whitelist. | **Critical** |
| W17 | Any `WEBULL_ENVIRONMENT` value other than exactly `"uat"` routes to **production** (`sdk_client.py:87-97`; no normalization or allowlist in `config.py`). `UAT`, `sandbox`, `paper`, or a trailing space silently sends live orders while the user believes they are sandboxed. | **High** |
| W6 | Futures notional = `quantity × limit_price`, no contract multiplier (`guards.py:207-217`): 2 ES @ 5000 counts as $10k against the cap; true exposure ≈ $500k. | **High** |
| W3 | `RiskEngine` runs only via explicit `--action local-check`; no place path calls it. Two parallel risk implementations that can drift. | High |
| W4 | Auto-generated `client_order_id` is not returned on submit failure (`stock_order.py:394,409-416` and siblings) — after a timeout the caller can't query/cancel or retry idempotently; a manual retry mints a new ID → possible double fill. | Med-High |
| W7 | MARKET/STOP orders have no notional check at all (both guards and RiskEngine key off `limit_price`): MARKET BUY 900 × $700 stock passes a $10k cap. | Medium |
| W14 | HTTP 200 with an error body is reported as success, exit 0 (`formatters.py:66-84`, `cli.py:242-247` detects errors only by string prefix). | Medium |
| W15 | Cancel 404/403 and market-data-subscription errors map to friendly strings with no error prefix → exit 0. A failed cancel of a live order looks successful to automation. | Medium |
| W18 | With exactly one account on the credentials, an explicitly passed *different* `--account-id` is silently overridden (`account.py:176-180`). | Medium |
| W20 | Documented features don't exist: `AuditLogger` is instantiated and never called (README claims "all order operations are logged" — none are); documented `mcp` CLI mode is an argparse error; `EnvRouter` is dead code; a second, even-less-validated `place_option_single_order` lives in `stock_order.py:578-676`. | Medium |
| W9 | Invalid risk-limit env values silently fall back to permissive defaults; `WEBULL_MAX_ORDER_QUANTITY=nan` silently disables the quantity cap (`quantity > NaN` is always False). | Medium |
| W8 | `SHORT` is a valid side with no long-only config, and no order path ever consults positions (no sell-vs-holdings or max-position check). | Medium |
| W12 | Option-replace leg scaling uses `int(quantity * ratio)` float truncation; detail-fetch failure is swallowed (`option_order.py:141-145, 359-360`). | Low-Med |
| W5/10/11/13/16/19/21 | Preview↔place unlinked; RiskEngine whitelist skips empty symbols; latent fail-open in per-market enum validation; float money end-to-end (no Decimal), `"10.0"` quantity strings; preview validation optional for library callers; empty account labels accepted; Unicode-alphanumeric client order IDs. | Low |

**Test coverage:** 1,726 test lines, but zero tests for quantity caps, notional limits, whitelist, `AMOUNT` behavior, any option/combo/algo/futures/crypto/event placement path, environment routing (W17), the single-account override (W18), or error-body handling (W14/15). `risk_engine.py` has no test file. Coverage concentrates on JP region rules and formatting — the safety-critical surface is untested.

**Must-fix before any funded use:** (1) an enforced confirmation/dry-run gate in code; (2) one mandatory risk check through which *every* place path flows, multiplier-aware for derivatives; (3) `WEBULL_ENVIRONMENT` allowlist that fails closed on anything but exact `uat`/`prod`.

---

## Part 4 — Data & Analysis Scripts (D findings)

### insider_scan.py — never run for real; would crash if it were
| # | Finding | Severity |
|---|---|---|
| D1 | `csv.DictWriter` raises on extra keys (`log_abs_transaction_value` etc. not in fieldnames) — **verified by execution**: the first run that fetches any in-window transaction crashes in report generation. It has only ever produced headers because zero data has been fetched (`reports/` contains only `universe.json`). | **Critical** |
| D2 | 4,614 symbols at 23 requests/day = ~201 days per pass, but reports filter on a rolling 28-day window: rankings reflect *fetch recency*, not insider activity. Symbols fetched in month 1 read as "no activity" in month 3. | **Critical (design)** |
| D3 | AV daily-limit responses arrive under the `"Information"` key; only `"Note"` is treated as throttling — the run burns the whole remaining batch on doomed calls. | High |
| D4 | Erroring symbols are retried every day at the same alphabetical position forever; ≥23 persistent errors (likely: the universe contains ~684 SPAC unit/warrant/right symbols) wedges the scan permanently. | High |
| D5 | `{"data": []}` short-circuits the dot→dash symbol fallback: `BRK.B`/`BF.B` are marked complete with zero transactions forever. | Medium |
| D6 | Windowing on `transaction_date` with no filing date = lookahead bias if this output ever feeds a historical signal. Live-watchlist use is fine; label it. | Medium |
| D7-D9 | No HTTP/JSON error handling in `call_av` (state lost mid-run); quota counts symbols not requests; `from` is not a real AV parameter (server-side windowing assumption is false). | Med/Low |

Math verified correct: buy/sell netting (A/D codes), median/MAD robust z-scores (1.4826 scale), percentile ranks, weight sums. Note: negative net-buy z-scores are clamped to zero — heavy insider *selling* never raises unusualness. Design choice, know it.

### monitor_option_spread.py — math right, one dangerous gap
- **D11 — HIGH:** zero/absent quotes pass validation (`finite_float` rejects only NaN): pre-market bid/ask of 0.0 on both legs yields spread_ask 0.0 ≤ threshold → false `ACCEPTABLE_ENTRY`. No bid>0 check; `lastTradeDate` is captured but never used for staleness. The 2026-06-09 log avoided this only because it ran entirely during RTH.
- D12/D13 — LOW: `==` boundary on do-not-chase classifies as WATCH (boundary value 0.84 actually occurs in the log); hardcoded defaults now reference an expired contract.
- Verified against the live log: spread bid/ask/mid, natural-pricing convention, breakeven = long strike + debit, max profit = width − debit, ET session handling — all correct.

### build_webull_account_report.py — best code in the repo, one real number wrong
- **D14 — MED-HIGH:** `filled_cash_flow` returns 0 for any status ≠ `FILLED`, excluding executed portions of `PARTIAL_FILLED` orders. **Verified in the real report data**: 4 partially filled SPXW option orders omitted → reported $40,228.11 cash flow overstates true signed flow by ≈ $4,350.
- D15-D18 — LOW: cash-flow ledger omits assignment/exercise/expiration and fees (disclosed in-report, but the sell-heavy +$48k premium-flow number is easy to misread as profit); `filled_price`→`limit_price`→0 silent fallback; unparseable balances silently drop out of headline totals; combo multiplier read from leg[0] only.
- Positives: Decimal throughout for money, masked identifiers, bounded retries, duplicate-cursor pagination guard, methodology disclosed in the report itself.

### webull_prototype.py / pull_quote.py
- **D19 — MED-HIGH:** `send-spread --env prod` places a live 3-lot QQQ put-spread with **no interactive confirmation**; preview/dry-run are opt-in. HANDOFF.md's own constraint ("anything that looks like 'press send for them' should be gated behind explicit confirmation") is unimplemented. Currently harmless only because endpoint paths 404 — the moment path discovery lands, this is a loaded fat-finger path. Invert the default: preview unless `--live` plus a confirmation phrase.
- D20-D22 — LOW/MED: signature computed over raw query values but URL sent percent-encoded (latent divergence); `--extend-hour` flag is dead (`or True`); `pull_quote.py` silently defaults to UAT data while its docstring promises real-time, and reads creds only from the legacy `D:\Quant-futures-app\.env`.

---

## Part 5 — Cross-Document Inconsistencies

1. **12-1 formula**: log return in IMPLEMENTATION_PLAN.md vs simple return in the composite spec (Q10).
2. **Market-filter registry**: IMPLEMENTATION_PLAN pre-registers QQQ-14d and QQQ-200d as baseline variants; the composite spec lists QQQ>SMA200 as default, SMA100 as "faster validation" (absent from the plan), and demotes SMA14 to research-only. One registry should own the variant list — this is exactly the "researcher degrees of freedom outside the grid" the internal audit warns about.
3. **Environment conventions**: `build_webull_account_report.py` force-sets `prod`; `webull_skill` defaults to `uat` with a fail-open exact-match (W17); `pull_quote.py` defaults to `uat` from a different repo's `.env`. Three scripts, three conventions, one of them dangerous.
4. **Universe hygiene**: every strategy spec excludes units/warrants/rights; the insider scanner's universe includes ~684 of them (D4).
5. **Lineage requirements vs reality**: every plan pins `code_revision`; the repo has no commits (S1).
6. **Stale fixture**: the MSFT dividend example flagged in the internal audit ($0.83 vs actual $0.91) remains uncorrected in the backlog.

---

## Part 6 — Honest Profitability Assessment

Requested goal: a profitable trading system. The audit's view of where profit will and will not come from:

1. **The deterministic baseline is the whole ballgame.** Until v0.1 runs end-to-end on pinned data, everything else — composite weights, agent debates, model selection — is decoration. A 100-name NDX universe is actually the *easier* first build (fewer identity/delisting problems than the broad universe); the NDX plan's funnel architecture is sound but its LLM layer should not be built until the deterministic control exists and shows an edge in validation.
2. **Expect the edge to be thin and the taxes to matter more than the factors.** Monthly-turnover momentum in a taxable account pays short-term capital gains rates plus wash-sale friction (Q11). Tax location (IRA/retirement account if available) will likely move the after-tax result more than any weight in the composite. The plan already requires after-tax reporting — treat that number, not pre-tax Sharpe, as the go/no-go metric vs simply holding QQQ.
3. **The benchmarks are the defense against self-deception.** QQQ total return, equal-weight PIT universe, and the no-agent control are pre-registered. If top-10/20 NDX momentum doesn't beat them after costs and taxes in validation, believe the result (Q13). Do not respond by widening the grid — the deflated-Sharpe/degrees-of-freedom machinery in the plan exists precisely to make that failure legible.
4. **The regime filter's value is concentrated in rare episodes** (2020, 2022). Dev-window results will barely exercise it (Q12). Judge it on drawdown behavior in validation/holdout, not on dev-period return.
5. **The LLM layer is a cost center until proven otherwise.** Its report-only → veto-only → bounded-overlay progression is the right design, but its realistic near-term contribution to returns is ~zero while its engineering and operational cost is large. Defer everything past NDX-009 until the deterministic system is live in paper trading.
6. **Execution safety is a profitability issue.** One unconfirmed fat-finger order (W1, D19) or one silent prod-routing accident (W17) can erase a year of thin factor edge. Safety fixes are not overhead; they are the strategy's risk budget.

---

## Part 7 — Prioritized Recommendations

### P0 — This week, before anything else
1. `git init`, add a `.gitignore` covering `.env`, `*.env`, logs, `.venv`, `__pycache__`; commit the workspace. (S1, S2)
2. Rotate the Alpha Vantage key and Webull App Key/Secret (both were pasted into chats per your own docs); consolidate credentials to the repo `.env`. (S2, D23)
3. Webull skill: add an enforced confirmation gate on every `place*` path; route all order types through one mandatory risk check (multiplier-aware); allowlist `WEBULL_ENVIRONMENT` and fail closed. (W1, W2, W17)
4. `webull_prototype.py`: invert `send-spread` to preview-by-default with `--live` + confirmation phrase. (D19)

### P1 — Build the core (next 4-8 weeks)
5. Freeze all new planning documents. Execute QME-000 → QME-050 with the NDX universe as the first target (defer the broad universe). The membership snapshot service (NDX-002/003) replaces `LISTING_STATUS` as the universe authority and shrinks the identity problem to ~100 names.
6. Before implementing v0.2 scoring, amend the spec per Q1-Q9 (dead thresholds, normalization chain, OBV, downside-vol definition, raw-close floor, sizing mechanics, tie-breaking). These are one-page spec edits now, or silent backtest bugs later.
7. Add wash-sale awareness to the tax model, and decide the account-location question before paper trading. (Q11)

### P2 — Fix or shelve the side tools
8. Insider scanner: fix D1 (one line: `extrasaction="ignore"` or sync fieldnames), D3, D4, D7; then either shrink the universe to something a 23/day quota can revisit monthly (≈700 names max) or shelve it — at 201 days per pass it cannot do its job by design (D2).
9. Spread monitor: require bid > 0 on both legs and a staleness check before any ENTRY status. (D11)
10. Account report: include PARTIAL_FILLED executed quantities in cash flow. (D14)

### P3 — Only after v0.1 validation is signed
11. Revisit the NDX LLM plan. Its gates (fail-closed schemas, report-only start, promotion thresholds) are well designed — keep them, but let the deterministic system earn its validation first.

---

## What Is Genuinely Good Here

- Raw-response caching before parsing; no network in backtest loops; T+1 fills; pre/post-cost reporting; deflated Sharpe; holdout manifests; owner-confirmation requirements (on paper); fail-closed LLM decision contracts; point-in-time membership discipline in the NDX plan.
- `av_probe.py` handles all three AV soft-error keys correctly — it should be the template for every AV client.
- `build_webull_account_report.py`: Decimal money, masked identifiers, in-report methodology disclosure.
- The internal `momentum_strategy_audit.md` is high quality; its accounting/delisting/corporate-action findings remain valid and unaddressed.

The intent and research discipline in the documents are well above hobbyist standard. The gap is that none of it is enforced by running, versioned code — and enforcement, not more planning, is what stands between this workspace and a profitable system.

---

# Addendum 2026-08-08 — Reconciliation with External Quantitative Review

A third independent review (methodology-focused, no code audit) was received after this audit. Its checkable claims were verified against the workspace. This addendum records what it confirms, what it adds, what it corrects, and the consolidated verdict. Where the external review and this audit conflict, the resolution below is authoritative for the consolidated action list.

## Verified new findings (adopted)

### A1 — The v0.2 relative-strength sleeve is *exactly* redundant (supersedes Q8's "overlap" framing)
On any fixed date the benchmark return is a cross-sectional constant `c`. Under the spec's own normalization, subtracting `c` shifts the winsorization thresholds and the cross-sectional mean by the same constant, so `z(x − c) = z(x)` — the factor scores are **identical**, not merely rank-equivalent:

```text
score(RS_QQQ_3M) = score(R_3M)
score(RS_SPY_3M) = score(R_3M)
score(RS_QQQ_6M) = score(R_6M)
⇒ relative_strength ≡ 0.75·score(R_3M) + 0.25·score(R_6M)
```

The entire 20% sleeve is a repackaging of two absolute-momentum components and contains zero benchmark-relative information. **QME-124 should be blocked** until the sleeve is removed or replaced with something genuinely relative: sector-relative momentum (needs PIT sectors), beta-residual momentum (pre-formation regression only), or an absolute excess-return *gate* that is not subsequently re-ranked.

### A2 — Forward-label leakage across fold boundaries
Phase A factor decay computes 1M/3M/6M forward returns on formation dates through the end of validation (2021). A 6M label formed in H2 2021 necessarily reads 2022 — the holdout window. Rule: a formation observation belongs to a fold only if `label_end <= fold_end`; purge the last `h` months of each fold for `h`-month labels, including the dev/validation boundary. Forward returns for tradable IC/spread must also align to the execution convention (T+1 open to T+1 open), with close-to-close reported separately as a diagnostic.

### A3 — The 2022+ "holdout" is retrospective, not pristine
The plans were written in 2026 and label 2022-onward as a one-time holdout; no pre-2022 registration artifact exists, and the designer has lived through the period. Reclassify 2022+ as a **retrospective external test window** (still governed by the manifest and read-once discipline), and establish the true out-of-sample evidence prospectively: a paper/forward period after the final spec freeze. Additionally, once v0.1 reads that window, it is spent — v0.2 cannot reuse it as an independent holdout.

### A4 — The top-20 variant has no hysteresis at all
Entry `rank <= 20` with exit `rank > 20` is a zero-width band. Only the top-10 and top-15 variants have real hysteresis. (Refines Q1; the external review's score algebra — rank 20 ≈ score 81, `score < 60` only past rank ≈ 40 in a 100-name universe — matches this audit's Q1 conclusion that the 75/60 thresholds are dead.)

### A5 — NDX/broad-universe eligibility contract conflicts
- **ADRs**: QME-021 excludes ADRs; official NDX methodology admits eligible ADRs. The NDX profile must override the generic exclusion or it will silently drop index members.
- **Fast Entry / short histories**: a recently listed Fast Entry member cannot have a 12-1 score. Required state machine: `index_member → data_available → feature_eligible → rank_eligible → portfolio_eligible`, with `INSUFFICIENT_HISTORY` reported in coverage rather than silent disappearance.
- **Issuer/share-class**: add a point-in-time `issuer_id` and decide *before testing* whether ranking is security-level, issuer-level with one preregistered tradable class, or issuer-weight-split — otherwise one issuer's multiple classes can occupy multiple equal-weight slots.

### A6 — Model-weight lookahead in any historical agent analysis
Even with perfectly cutoff-controlled evidence packets, a current LLM's weights encode post-date facts. Historical agent runs with current models are temporally contaminated by construction and must be labeled as such (or use date-vintaged checkpoints); persistent reflection/memory must be disabled or fold-scoped during replays. Agents therefore remain prospective and `report_only` — this hardens, with a mechanism, the deferral this audit recommended (P3/#11).

### A7 — Reliability thresholds need denominators
The NDX plan's "≥ 99.5% valid tool calls" requires ≈ 598 zero-failure independent trials for a one-sided 95% lower bound (n ≥ ln 0.05 / ln 0.995). Three repeated runs measure stability only. Promotion fixtures need registered sample sizes.

### A8 — Formal metric and portfolio-identity definitions
Adopted as stated in the external review: self-financing identities (`NAV⁺ = NAV⁻ − TC` at common marking prices), cost-aware target-weight solving (weights summing to 1 can go cash-negative after fees), `GTN` vs one-way turnover conventions, `K_t = min(50, ⌊0.20·N_t⌋)` with fail-closed breadth floor, explicit CAGR/Sharpe/Sortino/IC definitions with HAC or block-bootstrap inference for overlapping labels, and monthly date-level Spearman IC rather than pooled panel t-tests. These formalize this audit's Q9 and the internal audit's finding #3.

### A9 — Factor fixes, concretized
The external review's replacement formulas are adopted verbatim for the spec rewrite: bounded signed-volume flow for OBV (`Σ sign(ΔP)·V / Σ V`, in [−1,1]) replacing Q3's flagged ratio; true downside semideviation with registered MAR replacing Q4's ambiguity; `momentum_ir` renamed (it is return-over-realized-vol, not an information ratio) resolving Q6; positive-magnitude MDD with orientation fixtures resolving Q14; the "risk-adjusted quality" sleeve renamed `momentum_risk` (it contains no fundamental quality variables).

## Corrections to the record

- **The internal audit's "72-config grid" is stale.** QME-065's registered grid is `4 lookbacks × 3 holdings × 2 rebalance × 4 filters × 3 costs = 288` reported outputs (96 distinct strategies if costs are reporting-only scenarios never used for selection). The multiplicity ledger should use 96/288, not 72, and additionally count off-grid choices per QME-064.
- **This audit's suggested 15% single-name cap (Q9) is withdrawn as a parameter recommendation.** The external review is right that cap values (issuer/sector/vol/turnover/participation/cash) require a stated risk mandate, AUM, and execution evidence; until then they are scenario values. The *equations* and enforcement points still need defining now; the *numbers* do not.
- **QQQ 14-day filter**: demoted from "pre-registered baseline variant" (IMPLEMENTATION_PLAN) to an ordinary tested hypothesis consuming research degrees of freedom, resolving the cross-document conflict noted in Part 5 #2 in favor of the stricter treatment.

## What the external review does not cover

It is methodology-only. All code and operational findings of this audit stand unchanged and unaddressed by it: the Webull skill's enforcement gaps (W1/W2/W17 remain the top live-money risks), the prototype's ungated `send-spread` (D19), the insider scanner's crash and design mismatch (D1-D9), the monitor's quote-validation gap (D11), the account report's partial-fill omission (D14), and the S-series operational findings (empty git, unrotated secrets, scope drift). Both reviews independently confirmed S1 (no repository) and the absence of any implementation.

## Consolidated P0 list (supersedes Part 7's P0/P1 ordering)

Safety and hygiene (unchanged from Part 7):
1. Version control + `.gitignore` + commit (S1, S2).
2. Rotate AV and Webull credentials; consolidate to repo `.env` (S2, D23).
3. Webull skill: enforced confirmation gate, universal multiplier-aware risk check, environment allowlist (W1, W2, W17); prototype preview-by-default (D19).

Specification (before any implementation ticket is accepted):
4. Freeze the v0.1 mathematical spec: session-anchored offsets, `NOT_SCORABLE` on missing anchors, log-vs-simple pinned, "academically inspired long-only control" labeling, no-filter primary with filters as labeled extensions.
5. Rewrite the accounting coordinate: signals on total-return series; screens, fills, ledger, and marks on raw prices/shares/cash; split/dividend rules and volume-adjustment chain as specified in A8/Part 6 of the external review; fail closed on unsupported corporate actions (resolves internal-audit #3/#4 and QME-045/047 conflict).
6. v0.2: remove/replace the RS sleeve (A1), adopt the single-pass normalization (no winsorize-then-rank, no sleeve rerank), replace the four defective factors (A9), redesign entry/exit as rank hysteresis with `R_out > R_in` plus optional genuinely absolute gates (Q1/A4), and specify the survivor-weighting policy (re-equal-weight vs drift vs no-trade band).
7. Validation protocol: purged expanding folds within 2011-2021, one-shot 2019-2021 confirmation, 2022+ relabeled retrospective, prospective paper period as the real holdout (A2, A3); global append-only experiment registry with the 96/288 grid accounting and a preregistered primary economic objective.
8. NDX contracts: eligibility state machine, ADR override, `issuer_id` policy (A5).
9. Hand-calculated multi-rebalance fixtures before any full data pull.

Then, and only then: implement v0.1, validate, redesign v0.2 per the amended spec, run agents prospectively `report_only` with frozen evidence packets (A6), calibrate capacity/execution from paper fills, and consider capital last.

---

# Addendum 2026-08-14 — `tools/webull` findings remediated

Applied at owner request. All changes are in the untracked `tools/` tree (outside
the `qme` package and its CI by design). Verified by the package's own suite:
**205 passed** (68 pre-existing + 137 new/updated safety tests) and an offline
smoke test of the prototype's fail-closed paths.

| Finding | Disposition | Where |
|---|---|---|
| **W1** confirmation only in prose | **Fixed.** Every mutating action is preview-only unless `--confirm-live` AND exact `--confirm-phrase` (`"PLACE LIVE ORDER"` in uat; `"PLACE LIVE ORDER <account_id>"` in prod). No bypass flag; parser test asserts none exists. | `pretrade_gate.py`, `cli.py` |
| **W2** limits bypassed for options/combo/algo/event/AMOUNT/crypto-AMOUNT | **Fixed.** One universal gate normalizes every order class into priced legs; quantity, per-leg + aggregate notional, and whitelist enforced on all. Fails closed on any unpriceable payload. | `pretrade_gate.py` |
| **W3** RiskEngine opt-in only | **Superseded.** Gate runs unconditionally before dispatch; `local-check` retained as a diagnostic. | `cli.py` |
| **W4** generated `client_order_id` lost on submit failure | **Fixed.** `handle_submit_exception` surfaces the sent id with a do-not-resubmit warning at all 8 place paths. | `errors.py`, `trading/*.py` |
| **W6** futures notional ignored multiplier | **Fixed.** Root-symbol multiplier registry (ES 50, MES 5, NQ 20, …) or explicit `contract_multiplier`; unknown roots fail closed. Test: 2 ES @ 5000 → $500,000. | `pretrade_gate.py` |
| **W7** MARKET/STOP had no notional check | **Fixed.** Unpriced = unbounded → rejected unless `reference_price` supplied; STOP uses `stop_price`. | `pretrade_gate.py` |
| **W9** bad limit env values fell back to defaults; NaN disabled caps | **Fixed.** Set-but-invalid/NaN/inf/negative limits raise `ConfigError` at startup. | `config.py` |
| **W14/W15** 200-with-error and cancel 404/403 exited 0 | **Fixed.** Error markers detected anywhere in the result; mutating replies without an order id are treated as NOT PLACED. | `cli.py::_wrap_tool_result` |
| **W17** non-exact `WEBULL_ENVIRONMENT` routed to prod | **Fixed.** Exact allowlist `{uat, prod}` in `validate_config` and again at endpoint injection (defense in depth); UAT with no sandbox endpoints also refuses. | `config.py`, `sdk_client.py` |
| **W18** explicit `--account-id` overridden in single-account setups | **Fixed.** Explicit id is validated first and never substituted. | `trading/account.py` |
| **W20** audit logger dead; MCP mode documented but absent; duplicate less-validated option placement | **Fixed / disclosed.** Logger now records `ORDER_ATTEMPT` per priced leg, `ORDER_RESULT`, `VALIDATION_ERROR`; README marks MCP mode as not implemented; duplicate `place_option_single_order`/`preview_option_order`/`_build_option_order` removed from `stock_order.py`. | `cli.py`, `README.md`, `stock_order.py` |
| **D19** prototype `send-spread` placed live with no confirmation | **Fixed.** Preview-by-default; live requires `--live` + `--confirm-phrase "PLACE LIVE ORDER <account_id>"`; `resolve_host` fails closed on non-exact env. | `webull_prototype.py` |
| W5, W8, W10–W13, W16, W19, W21 (low) | **Open** — W8 (no long-only switch / no position-aware check) is the most material of these and should precede any funded use through this rail. | — |

Docs (`SKILL.md`, `README.md`) were rewritten so they describe only what the code
enforces. Credential rotation (S2) remains an owner action and is not evidenced
by this change.
