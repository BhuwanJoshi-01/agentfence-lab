# Data retention

How long AgentFence Lab keeps run data and how `agentfence cleanup` removes it
(docs/07 section 11, AF-0505).

## What is stored

| Data | Location | Removed by cleanup |
|---|---|---|
| Run metadata, attempts, events, verdicts, evidence links | `<state>/agentfence.sqlite3` | yes, in one transaction per cleanup |
| Saved JSON report | `<state>/runs/<run-id>/report.json` | yes, after the database rows |
| Run folders with no database record (an interrupted save) | `<state>/runs/<run-id>/` | yes, once the folder is more than an hour old |
| Attempt workspaces left by a crash | `<state>/work/<run-id>/` | yes (also reconciled before every `agentfence test`) |
| HTML reports and export bundles written with `--output` | wherever you put them | no |

`<state>` is `$AGENTFENCE_STATE_DIR`, else `$XDG_STATE_HOME/agentfence`, else
`~/.local/state/agentfence`.

## Retention period

`storage.retention_days` in `agentfence.yaml` (default 30). A run is expired when its start
time is older than that many days. `--older-than DAYS` overrides it for one invocation;
`--older-than 0` selects every run.

## Using cleanup

```console
$ agentfence cleanup                 # dry run: lists what would be deleted
$ agentfence cleanup --apply         # asks for confirmation on a terminal
$ agentfence cleanup --apply --yes   # non-interactive (scripts, CI)
```

Without a terminal and without `--yes`, `--apply` refuses with exit code 2.

## Safety rules

- Events are append-only. The retention purge is the only code path that deletes them: it
  drops the `events_no_delete` trigger, deletes the expired runs' rows child-first and
  recreates the trigger inside the same transaction. Any failure rolls all of it back,
  trigger included, and cleanup exits 4 without touching run folders.
- Database rows go first, folders second. If removing a folder fails, the next cleanup finds
  it as an orphan and removes it then.
- Only entries named like a canonical run UUID are considered. A symlinked entry is unlinked,
  never followed; directories must belong to the current user. Removal uses
  file-descriptor based traversal, so a symlink swapped in mid-delete is not followed.
- A run folder without a database record is left alone for an hour, because
  `agentfence test` writes its folder a moment before it saves the row.

## Limits

Cleanup only removes what AgentFence itself manages. Copies in backups, filesystem
snapshots, files you exported or rendered elsewhere, and terminal scrollback are outside
its control and are not affected.
