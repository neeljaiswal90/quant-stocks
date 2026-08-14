"""Print the deterministic NEE-176 10,000-event compact known-answer fixture."""

from __future__ import annotations

import json

from qme.governance.sample_access_chain_v2 import known_answer_summary


def main() -> None:
    """Emit canonical, platform-independent fixture JSON to standard output."""

    print(json.dumps(known_answer_summary(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
