# metrics

`traffic.json` is an append-only snapshot of this repo's GitHub traffic, written
daily by [`.github/workflows/traffic.yml`](../.github/workflows/traffic.yml).

GitHub's traffic API only retains **14 days**. This file is the durable history.

## Reading it honestly

**`windows[]`** — quote this. Each entry is GitHub's own 14-day aggregate at the
time of the snapshot. GitHub deduplicates within a window, so `clones_uniques`
is a real unique-cloner count for those 14 days.

**`daily{}`** — raw per-day rows. `count` is safe to sum (that's what
`totals.clones_all_time` does). **`uniques` is NOT** — a person who clones on
Monday and again on Thursday appears in both days, so summing daily uniques
overstates reach, sometimes by a lot. The temptation to sum them is exactly the
kind of thing that turns a real number into an indefensible one.

## Setup

The traffic API requires repo admin/push scope. Actions' built-in `GITHUB_TOKEN`
**cannot** provide it — `administration` isn't a grantable permission for that
token — so a PAT is required, not a fallback:

1. **Required.** Create a fine-grained PAT scoped to this repo with **Administration:
   Read-only**.
2. Add it as the `METRICS_TOKEN` repository secret.

Run it by hand any time with `workflow_dispatch`, or locally:

```bash
GITHUB_TOKEN="$(gh auth token)" python scripts/snapshot_traffic.py
```
