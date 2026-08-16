# Day 1 Prospective Transport

The real Day 1 transport creates at most one file per day at
`anchors/prospective/<commitment>.json` on the approved `main` branch. Existing
files are never updated or deleted. Equal content is idempotent; different
content at the same path is a conflict.

The public file contains only the prospective commitment contract. Race date,
venue, race number, racer identity, probabilities, raw input, hashes of input,
salt, prediction package, odds, results, and payouts remain local.

The GitHub HTTP `Date` response must be strictly before the private package
cutoff. A missing, equal, or late timestamp is rejected. Filesystem timestamps
are not evidence.

Run after the next eligible B file is placed locally:

```powershell
py -3.13 scripts\run_day1_prospective_v2.py --b-root data\raw\official\entries
```

The runner is limited to one venue, twelve races, one package, one external
create, and no retry. Payment, betting, production adoption, overwrite, update,
and delete remain disabled.
