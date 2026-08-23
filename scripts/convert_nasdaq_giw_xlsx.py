"""Convert one exact Nasdaq GIW ExportWeightings XLSX to canonical CSV."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from qme.data.ndx.giw_xlsx import decode_giw_weightings_xlsx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()

    source = args.source.resolve(strict=True)
    destination = args.destination.resolve(strict=False)
    decoded = decode_giw_weightings_xlsx(source.read_bytes())
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    descriptor = os.open(destination, flags, 0o644)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(decoded.csv_bytes)
        handle.flush()
        os.fsync(handle.fileno())
    print(
        json.dumps(
            {
                "source": str(source),
                "destination": str(destination),
                "xlsx_sha256": decoded.xlsx_sha256,
                "xlsx_bytes": decoded.xlsx_bytes,
                "csv_sha256": decoded.csv_sha256,
                "csv_bytes": len(decoded.csv_bytes),
                "row_count": decoded.row_count,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
