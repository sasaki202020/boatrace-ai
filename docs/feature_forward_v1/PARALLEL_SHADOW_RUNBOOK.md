# Course/Start Parallel Shadow

## Scope

`course_start_residual_shadow_v1` is a fixed research-only challenger beside
the frozen `tree_15` champion. It never changes the existing prediction files,
settlements, collector store, BUY/EV logic, or production output.

The runner reads only:

- existing pre-race prediction JSON files
- the feature-forward SQLite store in read-only mode
- the frozen `tree_15` artifact for hash verification
- the fixed challenger config

It does not read the settlement directory. A race is eligible only when its
existing prediction and deadline are both still in the future at runner start.
There is no retrospective shadow creation.

## Output

The only write target is:

`data/research/feature_forward_v1/parallel_shadow/parallel_shadow.sqlite3`

The database is append-only. One race can be inserted once. Re-running with
the same input is idempotent; a different payload for the same race stops with
a conflict. The ledger chain is verified after each run.

Missing or invalid course/start data causes an explicit baseline fallback. It
does not invent feature values and it does not alter the champion prediction.
The runner does not freeze that fallback before the verified capture window
has closed at T-6 minutes; this prevents a scheduled-run race with the
collector from permanently hiding an otherwise available challenger snapshot.

## Fixed model

The coefficients, fixed center/scale preprocessing, residual scale, clip, and
seed are stored in `config/feature_forward_v1/parallel_shadow_config.json`.
They are not fitted by the forward runner and cannot use result data.

## Task registration

The existing collector, prediction, settlement, and OOF tasks are not changed.
The optional independent task is registered only by:

```powershell
pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File .\scripts\register_parallel_shadow_task_v1.ps1
```

Its wrapper uses the same local prediction and feature-store paths, but a
separate shadow database. It performs no network request, betting, payment, or
production write.
