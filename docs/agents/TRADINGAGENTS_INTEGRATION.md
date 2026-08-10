# TradingAgents integration boundary

## Audited upstream

QME is pinned to TradingAgents commit
[`a33fd4c0f134485a43553a2c23a63cb14adbd88f`](https://github.com/tauricresearch/tradingagents/commit/a33fd4c0f134485a43553a2c23a63cb14adbd88f),
audited on 2026-08-08. The package still reports version `0.3.1`, so QME records
the source as `0.3.1+git.a33fd4c`. The source is Apache-2.0 licensed.

Current upstream has four structured-output roles: Sentiment Analyst, Research
Manager, Trader, and Portfolio Manager. Its helper still retries failed structured
calls as free text, and its rating parser can turn an unparseable result into
`Hold`. Neither behavior is an eligible QME decision path.

Primary upstream contracts:

- [Graph setup](https://github.com/tauricresearch/tradingagents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/setup.py)
- [Graph runtime](https://github.com/tauricresearch/tradingagents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/graph/trading_graph.py)
- [Structured-output fallback](https://github.com/tauricresearch/tradingagents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/structured.py)
- [Sentiment live prefetch](https://github.com/tauricresearch/tradingagents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/analysts/sentiment_analyst.py)
- [Rating parser](https://github.com/tauricresearch/tradingagents/blob/a33fd4c0f134485a43553a2c23a63cb14adbd88f/tradingagents/agents/utils/rating.py)

## What is implemented

- An installable `qme` package and exact optional upstream Git dependency.
- A strict, immutable evidence-packet schema with canonical SHA-256 lineage.
- Rejection of post-cutoff or stale mandatory sources, remote/unsafe source URIs,
  extra schema fields, and source bytes that do not match their SHA-256 declarations.
- A typed packet-only tool gateway with strict per-tool argument schemas, exact
  ticker/date/selector identity, and explicit untrusted-evidence framing.
- An `AgentReviewArtifact` envelope that can never be trade eligible.
- A backend capability contract requiring packet-only tools, network denial,
  strict structured outputs, isolated memory/configuration/processes, and safe
  checkpoint behavior.
- Validation of typed Research Manager, Trader, Portfolio Manager, and optional
  Sentiment outputs without using upstream `SignalProcessor`.
- Replayable packet-tool receipts that bind arguments, response hashes, and cited
  source IDs; every mandatory source must be called and cited for a valid report.
- A no-clobber CLI for packet validation and fail-closed readiness checks.

## Evidence-contract blockers still open

The current schema checks a source against the `max_age_hours` declared inside
that packet. This is not yet an authoritative freshness decision: a future packet
compiler must apply a qme-owned, versioned source-class policy, record its hash,
and reject packet values that relax the policy. No production agent run is
approved from packet-declared freshness alone.

Likewise, source files are byte-verified but model-visible tool text is currently
inline packet content. Response hashes prove that inline text was not changed
after packet construction; they do not prove how it was derived from the cited
raw source. Each tool block still needs a verified derived-artifact record with
its output-content hash, source IDs/spans, transform version, and transform-config
hash. This work is tracked in Linear NEE-149 and blocks runtime activation.

## Why runtime is disabled

Unmodified upstream does not expose a supported immutable-evidence seam:

- graph tools and analyst factories are hardcoded;
- instrument identity calls yfinance;
- Sentiment directly fetches Yahoo, StockTwits, and Reddit;
- construction mutates process-global dataflow configuration;
- memory/reflection can read and write prior outcomes;
- checkpoint identity omits evidence, model, prompt/schema, and source revisions;
- structured failures fall back to free text.

Accordingly, `TradingAgentsAdapter` does not instantiate the upstream graph. Even
if the optional dependency is installed, a runtime-enabled request without a
packet-native backend returns `BLOCKED_UNSAFE_UPSTREAM`.

The shipped adapter also accepts no executable backend object. Typed backend data
normalization is private and executes no model code. A concrete, revision-attested
subprocess supervisor must be implemented and tested before any runtime seam is
added.

## Packet-native backend requirements

The narrow fork or isolated service must:

1. Inject packet-backed tool nodes and an instrument-context resolver.
2. Replace Sentiment live prefetch with packet blocks or omit that analyst.
3. Raise or return typed failure metadata on every structured-output error.
4. Disable reflection and persistent memory for replay/historical evaluation.
5. Run one ticker/config per subprocess; threads are not safe because upstream
   configuration is global.
6. Disable checkpoints or include upstream/model/prompt/tool/evidence/run hashes in
   checkpoint identity.
7. Block all network access after packet freeze.
8. Return typed objects before Markdown rendering plus immutable packet-tool
   receipts and cited packet source IDs.
9. Send evidence through the gateway's untrusted-data envelope; evidence content
   can never define instructions, tools, or permission to fetch external data.

Until those controls pass frozen tests, TradingAgents remains a pinned but
non-executable integration dependency. This is an intentional production gate,
not an installation error.

## Authority boundary

TradingAgents reports are advisory. They must not be imported by deterministic
ranking, accounting, portfolio, execution, or Webull order modules. No rating,
prose sizing recommendation, completion order, or confidence statement may alter
rank or target-weight hashes while influence mode is `report_only`.
