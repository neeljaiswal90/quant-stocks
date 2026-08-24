"""Deterministic asset classification with dated evidence (NEE-124, M1).

The rule engine lives in :mod:`qme.data.classification.rules_v1` and is imported
by its full module path. This package initializer deliberately imports nothing,
so ``import qme.data.classification`` pulls no rule table, no evidence model, and
no sibling data package into the process.
"""
