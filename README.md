# DraftKings contest-field dataset (anonymized)

Real DraftKings contest standings (every entry's lineup and final points) for NHL, NFL and NBA classic-style slates,
so you can backtest your own lineups against the *actual* field: where would this lineup have ranked, and what would it have paid?

Rebuild with `python3 build_dataset.py` (source data is only ever read; see "Reproducing").

Built for the free DFS lineup optimizers at https://nhl-lineup-optimizer.kindoflost.com

## Download

The large files are not in the git repo (GitHub's 100 MB file limit). Download them from the release assets:
https://github.com/kindoflost/dk-contest-fields/releases

- `entries_nhl.csv.gz`, `entries_nfl.csv.gz`, `entries_nba.csv.gz`: the `entries.csv` rows for each sport (same columns, gzip).
- `players_all.csv.gz`: `players.csv` for all sports.

`contests.csv` and `salaries/` are in the repo. `pandas.read_csv('entries_nhl.csv.gz')` reads the gzip directly.

## Source and terms

The data are DraftKings contest standings exports, downloaded manually by the author. Usernames are replaced with user1, user2, ...
(see "Anonymization"). Provided as-is for research and backtesting, with no warranty. No affiliation with DraftKings;
DraftKings is a trademark of its owner. The salary files are the slate's public DKSalaries exports, included for backtesting.

## Coverage

| Sport | Contests | Dates | Field sizes |
|-------|---------:|-------|-------------|
| NBA | 55 | 2026-10-13 .. 2026-11-16 (30 slate dates) | 118 .. 95,124 |
| NFL | 16 | 2026-10-18 .. 2026-11-15 (5 slate dates) | 4,879 .. 416,171 |
| NHL | 138 | 2026-10-02 .. 2026-11-16 (45 slate dates) | 713 .. 23,781 |

Total 209 contests, about 2.41 million entries, 110,702 distinct (anonymized) users. Dates come from the source folder
name (the day the data was collected), which can differ from the game date for late slates.
Slates the source folders label "big"/"small" carry that label in `contests.slate`.

## Files

### contests.csv (one row per contest)
`contest_id, sport, date, slate, source_stem, field_size, max_entries_per_user_observed, entry_fee, paid_places, prize_pool, payout_table, payout_match, salary_file`
- `source_stem`: `contest-standings-<contest_id>` (any trailing text from the original filename is dropped).
- `field_size`: number of entries in the standings file.
- `max_entries_per_user_observed`: largest "m" seen in the "(n/m)" suffix (see entries.csv); a lower bound on the contest's entry cap.
- `entry_fee`: always blank. The payout files do not contain it.
- `payout_table`: `1=500.00|2=250.00|6-7=30.00|...` (position range = dollars per entry). `paid_places`, `prize_pool` are derived from it.
- `payout_match`: `id` = payout file named with the contest id; `heuristic` = three NHL 2026-10-02 contests whose payout files had no id
  and were matched by field size (the "5900 entries" file to the ~5.9k-entry contest, "7000 entries" to the ~7.1k one, the undated remainder to the third). Check these if it matters.
- `salary_file`: file in `salaries/` for the slate (blank for NHL 2026-10-02: no DKSalaries file was collected).

### entries.csv (one row per entry)
`contest_id, rank, user, entry_no, max_entries, points, lineup, payout`
- `user`: anonymized `user1`, `user2`, ... One mapping for the whole dataset (all sports and contests), numbered in first-seen order over contests
  sorted by sport, date, slate, contest id. The same real person is always the same `userN`, so multi-entry behaviour can be followed across contests.
- `entry_no`, `max_entries`: DraftKings' "(n/m)" suffix on the entry name, split out. `m` is the number of entries that user has in that contest.
  Blank for users with a single entry (DraftKings shows no suffix).
- `points`: final fantasy points. `lineup`: the DraftKings lineup string, unchanged.
- `payout`: dollars won, from the contest's payout table by rank. Tied entries share the average of the paid positions they span.
  Blank if no payout table. Prize amounts only; entry fees are not subtracted.

### players.csv (right-hand block of the standings file)
`contest_id, player, roster_position, pct_drafted, fpts` - final ownership (`pct_drafted`) and fantasy points per player.

### salaries/<SPORT>_<date>[_<slate>].csv
The slate's `DKSalaries` export, copied byte-for-byte (DraftKings columns only). No third-party projection files are included.

## Scoring a lineup against a contest
1. Compute your lineup's points with the contest's scoring (or take points from `players.csv` `fpts` for each player; use the same roster and scoring rules as the contest).
2. Load that contest's rows from `entries.csv`. Your rank = 1 + number of entries with `points` strictly greater than yours
   (ties share a rank; DraftKings splits tied prizes).
3. Look up the payout for that rank in `payout_table` (or the `payout` of the entry at the same rank). Percentile = rank / `field_size`.
4. ROI needs an entry fee, which is not in the data: supply it yourself.

```python
import pandas as pd
e = pd.read_csv('entries.csv'); mine = 187.4; cid = 196683453
f = e[e.contest_id == cid]; rank = 1 + (f.points > mine).sum(); print(rank, len(f))
```

## Anonymization
- DraftKings usernames (the `EntryName` column) are replaced by `userN` at build time. The username -> userN mapping exists only in memory during the build and is never written.
- Usernames are also stripped from the contest source name (only `contest-standings-<id>` is kept); no other column carries a username.
- The build ends with a self-check over every file in this folder (entries, players, contests, salaries, this README): it fails if any original username appears as a field or token (names equal to a player name or roster token are counted as benign collisions).
  This is a token-level check, not a guarantee against usernames hidden inside longer strings.
- Lineups and player names are public DraftKings data about athletes, not users.

## Reproducing
`python3 build_dataset.py [DATA_ROOT] [OUT_DIR]` reads `<DATA_ROOT>/{NBA,NFL,NHL}/testing/<MMDDYYYY>[big|small]/` (standings `.csv`, standings `.zip`, nested `drive-download-*.zip`
read in memory; `payouts*.txt`; `DKSalaries*.csv`). Duplicate copies of a contest are collapsed (largest kept).
On slow disks the build can be split: `--shard I/N` (append slices), `--range LO:HI`, then `--finish` to run the report and self-check. Every run rebuilds the same user numbering.
