# A4 handoff addendum

## Environment

Reviewed worktree: /workspace/QME-external-review/A4
Commit: d890078803c58f3ca995ff80004b025583fe6b2e
Tree:   0d00c7b1ac87409c67ec32cbd0cde29c316d8334

This sandbox has CPython 3.10.20, not 3.12.10. The registered isolated
generator lock is a CPython 3.12 Linux/Windows wheel set. Independent
regeneration that claims byte-identity is valid only if you actually
install the pinned generator on a matching interpreter. If you cannot,
do the remaining checks and state the regeneration gap honestly.

Write only under /workspace/QME-external-review/outputs/A4/
Do not dirty the worktree.

Official-case dates that must be independently checked (from the
registered evidence / official-case fixture):

- Hurricane Sandy closures: 2012-10-29, 2012-10-30
- National day-of-mourning closures: 2018-12-05, 2025-01-09
- Thanksgiving early close 13:00: 2018-11-23
- Published 13:00 early closes: 2026-11-27, 2026-12-24

Protected-main Linux workflow run cited by the owner-decision record:
`31922669149`.

## What this addendum is not

- Not a recommended verdict
- Not an instruction to approve or reject
- Not authority to flip
  `linux_generator_hash_lock_available` or
  `windows_linux_byte_replay_verified`
- Not a review of any other artifact
