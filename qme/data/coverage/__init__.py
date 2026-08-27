"""Coverage audit and source-aware delisting policy (NEE-128, M1).

Two modules, imported by their full paths:

* :mod:`qme.data.coverage.delisting_v1` -- the base layer: the delisting / exit
  vocabulary, the five owner-gated registries (timing, benchmark-treatment
  change, and missing-mark policy still ship EMPTY; unknown-adverse haircuts
  and the recovery sensitivity range are owner-registered), the sourced versus
  fallback type wall, held-position marks, and P&L attribution by outcome type.
* :mod:`qme.data.coverage.audit_v1` -- the eight-class coverage audit, the
  missingness / exclusion ledger, the coverage-threshold registry (seven 100%
  minima owner-registered; held-position coverage remains hard-wired at 1),
  and the gate.

This package initializer deliberately imports nothing, so
``import qme.data.coverage`` pulls no registry, no vocabulary, and no sibling
data package into the process.
"""
