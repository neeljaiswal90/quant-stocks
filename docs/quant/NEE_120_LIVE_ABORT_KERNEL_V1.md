# NEE-120 bounded deterministic live-abort kernel v1

Status: deterministic trigger mechanics only; not an operational promotion contract.

The kernel implements only the source-faithful live-abort rows in the protected
S0a crosswalk V2. It computes each ledger's current drawdown from the running NAV
peak since prospective inception, then evaluates:

```text
strategy_current_drawdown  = max(0, 1 - strategy_nav / strategy_running_peak_nav)
benchmark_current_drawdown = max(0, 1 - benchmark_nav / benchmark_running_peak_nav)
excess_current_drawdown    = max(0, strategy_current_drawdown - benchmark_current_drawdown)
```

- Excess drawdown must be strictly greater than `0.10` for five consecutive,
  explicitly ordered session ordinals before aborting. Equality clears and resets
  the persistence count.
- Strategy current drawdown strictly greater than `0.40` aborts immediately.
  Equality does not.
- Reconciliation failure, schema-invalid input, a missing mandatory input, or a
  non-contiguous session ordinal fails safe to `ABORTED`.
- `ABORTED` is sticky. This slice intentionally has no resume or restart method.

Status comparisons use exact rational arithmetic, so an arbitrarily precise
value immediately above either strict boundary cannot round down to equality.
String inputs must be canonical ASCII decimals; binary floats and alternate
spellings fail safe. The state is immutable and only the pristine initial state
is publicly constructible. Each transition stores non-decreasing ledger peaks
and a domain-separated SHA-256 evidence chain.

Synthetic fixtures prove boundary and state-machine behavior only; they are not
production evidence. The caller supplies the running-peak coordinates. This
slice enforces that each peak is positive, not below current NAV, and never
decreases between processed sessions, but it cannot prove that the first supplied
peak contains complete prospective-inception history; production lineage remains
outside this kernel.

Authority is crosswalk
`NEE-172-S0A-1-CONTRACT-MATERIALIZATION-CROSSWALK-V2`, SHA-256
`11f1de4d:51816cad:7d958fe9:2946e18f:e968d9de:7537006e:00f80577:c11942d1`,
rows `S0A1-120-018`, `036`–`040`, `113`–`115`, and `125`. Row `125`
authorizes sticky abort state while its restart/resume policies remain explicit
non-executable context in this bounded slice.

The kernel does not create the still-unregistered economic-promotion V2 identity,
compute a confidence interval, implement Politis–White or Newey–West, decide
promotion, execute a restart, load data, place orders, or establish alpha,
capacity, prospective sufficiency, or production readiness.
