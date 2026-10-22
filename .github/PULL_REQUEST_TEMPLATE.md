## What this changes

<!-- One or two sentences. If it fixes an issue, write "Fixes #123". -->

## Why

<!-- The reason, not a restatement of the diff. -->

## Does it touch the safety gate?

- [ ] No — this change cannot delete, move, or modify a file
- [ ] Yes — and every new path goes through `safety.validate_target`, or is a
      documented exception

If yes, describe what bounds the new deletion:

<!-- allowed_roots, admin gate, fixed argument list, etc. -->

## Checklist

- [ ] Tests added or updated, and the full suite passes
- [ ] No new dependency, or the reason is explained above
- [ ] `docs/ARCHITECTURE.md` updated if a public signature or an item changed
- [ ] `docs/USER_GUIDE.md` updated if user-visible behaviour changed
- [ ] `docs/SAFETY.md` updated if a safety rule changed
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No real file outside a temporary directory is touched by any test
