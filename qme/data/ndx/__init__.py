"""Nasdaq-100 (NDX) membership data.

The only membership source wired in this package is the owner's **manual**
Nasdaq Global Index Watch (GIW) component-file download
(``qme.data.ndx.giw_snapshot``). Nothing here performs network I/O.

Non-claims:

* no authoritative NDX membership is claimed until an owner-approved snapshot
  exists in the data root;
* historical membership before the first downloaded snapshot is not claimed and
  is never synthesised from a later basket.
"""

from __future__ import annotations
