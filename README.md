# Next Up — Roblox “Next Big Game” Tracker

A Python collector and GitHub Pages dashboard for catching Roblox games before they blow up. It tracks every game above 500 concurrent players, adds release dates and engagement from Roblox, and ranks games by the signals that came before past blow-ups.

**Dashboard:** https://chawewo.github.io/robloxPopularityTracker/ (available after the first successful Pages deployment).

## Run locally

Requires Python 3.11 or newer. No third-party dependencies.

```sh
python collect.py
python enrich.py
python analyze.py
python backtest.py
python -m unittest discover -s tests -v
python -m http.server 8000 --directory dashboard
```

Open http://localhost:8000. Use `?demo=1` to review a clearly labeled, fictional populated leaderboard. Demo data is separate from real history and is never consumed by the analyzer. Serve via HTTP; opening the HTML directly does not allow reliable JSON fetching.

Avoid repeatedly running the live collector: one run per ~15 minutes is sufficient. Tests use temporary synthetic snapshots and do not call the API.

## GitHub Actions and Pages

The repository is `Chawewo/robloxPopularityTracker`. The workflow runs on pushes to `main` that change code, manual dispatch, and every 15 minutes at minutes 4, 19, 34, and 49 UTC. The offset avoids the busiest top-of-hour period. Collection and deployment are serialized; an active collection is never cancelled by another trigger.

1. Settings → Actions → General → Workflow permissions → Read and write permissions.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Push the files to the default branch (`main`), or use Actions → **Track Roblox risers** → **Run workflow**.
4. Check both `collect` and `deploy` jobs succeed. The workflow commits `data/` and the backtest with `[skip ci]`, then deploys `dashboard/`. `dashboard/data.json` and `dashboard/history.json` are rebuilt every run and are not committed; run `python analyze.py` to create them locally.

If branch protection prevents the bot from pushing, allow this workflow to write to the collection branch before enabling the schedule. Manual workflow runs must target the default branch. If your default branch is renamed, update the push trigger too.

The workflow requests `contents: write`, `pages: write`, and `id-token: write`; the last is required for Pages deployment. No personal token is stored in this project. API failure or malformed data fails collection and leaves the deployed dashboard intact; the page marks old snapshots overdue after 45 minutes. Data is committed before deployment, so a Pages failure does not discard the snapshot.

GitHub scheduling is **best effort**, not gap-free: jobs can be delayed or dropped, and public-repository schedules may be disabled after 60 days without repository activity. Public standard runners are generally free; private repositories use your plan’s Actions allowance. See [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), and [custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Data and signals

- **Source:** one GET to https://api.rolimons.com/games/v1/gamelist with browser-style User-Agent and Rolimons Referer. Request timeout: 45 seconds, no retries. Counts reflect Rolimons’ cached observation, not exact real-time Roblox counts.
- **Storage:** `data/snapshots/YYYY-MM.csv` contains `timestamp,game_id,players`; only games with at least 500 players are retained. At 40 MiB, a month rolls over into `YYYY-MM-002.csv`, etc., to stay below GitHub's file limit. Analysis streams only relevant months and retains observations for the last 24h plus tolerance in memory. `data/games.csv` tracks names, icons, and first observation. Metadata is rewritten only when changed. `data/collections.csv` marks completed snapshots, including zero-qualifier runs; orphan rows from interrupted writes are ignored and incomplete recent committed snapshots fail analysis.
- **Current:** every game in the newest complete snapshot is listed (500+ players). Those with at least 1,000 players count as having traction. A missing game is unknown, never a stale current count or a zero baseline.
- **Growth:** nearest observed count to 1h, 6h, and 24h before the latest collection, within ±20 minutes. Ties use the earlier observation. `delta = current − baseline`; `pct = delta / max(baseline, 1) × 100`. JSON includes the actual matched baseline timestamp. No interpolation or missing-value imputation.
- **Sustained:** positive growth in every available window, with at least one comparison. The default lens additionally requires a 6h baseline. This is an early signal, not proof of uninterrupted growth; daily cycles, updates, and events can affect counts.
- **New entrant:** first observed by this tracker within 24h, or a current count at or above 1,000 after a week below it, so a game dipping under 1,000 overnight does not qualify. First observed does not mean newly released. The initial batch will all show NEW. Crossing from below the collection floor cannot always be proven for previously seen games, so missing observations are not inferred as crossings.
- **Ranking:** sustained 6h percentage gain; absolute 6h player change; 1h percentage change; new entrants by current players. Each lens displays up to 100 games. JSON retains all eligible games so another lens or column can find its own top 100. Click count, name, delta, or percentage headers to sort ascending/descending; missing values remain last.
- **Cold start:** comparisons stay blank until a matching baseline exists. Before a lens has history, it shows current traction with an explicit “awaiting history” notice and no rank numbers. Around 1h/6h/24h of collection unlocks the respective comparisons (subject to tolerance and schedule delays).
- **Retention:** exact rolling 60 days of raw snapshots in the working tree, pruning old rows and empty files. Daily rollups are kept permanently. Metadata first-seen dates persist. Old Git commits still retain prior data, so pruning does not shrink repository history; archive/rotate the repository if long-term Git size becomes an issue.

Edit `config.py` to tune floors, comparison windows, tolerance, retention, display limit, and stale threshold. The dashboard fetches every three minutes; this does not poll Rolimons.

The public Pages artifact contains leaderboard, history and demo data, not snapshot CSVs. A public repository exposes committed history. Use this for personal discovery and respect the upstream service’s terms; avoid bulk redistribution.

## Changes since the previous run

The LAST RUN column compares the newest two completed collections, showing player delta, percentage, and the actual interval in minutes. It works for manual runs and scheduling gaps without pretending the interval is one hour. A game missing from the previous collection has no comparison; an unchanged count shows zero. This lens is selected automatically until a 6h comparison becomes available. The 1h/6h/24h windows still require their own baselines within the configured tolerance.

On September 10, scheduled triggers had not appeared despite successful push and manual runs. The cron was changed to `4-59/15 * * * *` as a recovery attempt. This does not guarantee scheduler recovery: only a run with event `schedule` verifies it. Each run now includes a summary showing its trigger, snapshot time, elapsed interval, and comparison coverage. No local scheduler has been installed.

## Windows collection timer

`Roblox Tracker - GitHub Collection` runs `dispatch_workflow.py` with hidden Python every 15 minutes and at sign-in. It runs as the current Windows user without elevation, while signed in (locking is fine). It cannot run during sleep, shutdown, or sign-out; missed starts are handled when available. Your GitHub credentials stay in the existing Git credential helper; no token is saved in the repository. The script skips active workflows and snapshots under 12 minutes old. GitHub cron remains a fallback when the PC is unavailable. Dispatch only requests a run; network or GitHub service failures can still delay collection. Inspect `.scheduler.log` and Task Scheduler for status. Reinstall with `powershell -File install_scheduler.ps1` under your normal account. Disable or remove the named task in Task Scheduler to stop it.

## Sparse historical comparisons

Window tolerances are now 1h ±20 minutes, 6h ±90 minutes, and 24h ±3 hours. Every available time-window metric includes its actual elapsed hours. Matches farther than 20 minutes from the target are labeled approximate. No interpolation is used. Missing comparisons explicitly distinguish insufficient history from collection gaps. This supersedes the original uniform ±20-minute matching rule described above.

## Long-range change

`rollup.py` compresses each completed UTC day into `data/daily/YYYY-MM.csv` (peak, average, samples per game) and records the day in `data/daily_index.csv`. It runs before pruning, so long-range history survives the 60-day limit at about 2 MB a month. `analyze.py` publishes these peaks to `dashboard/history.json`, which is rebuilt each run, deployed to Pages, and not committed.

The dashboard compares the peak of the last 24 hours with the peak on the matching past day, so **any** period works, including a custom number of days. Peaks are used because collection has overnight gaps: a peak barely depends on which hours were sampled, while point-in-time counts and daily averages do. The peak also removes the time-of-day cycle.

- **≥ values:** a game absent on the baseline day was under the 500-player floor, so the gain is shown as a lower bound against 500. Sorting uses that lower bound. These games would otherwise disappear from the ranking, and they are often the ones you want.
- **Vs market:** the change after removing the median game's move over the same days. The median stops one giant game from defining the market, and the adjustment cancels weekends and holidays, which matters for periods that are not whole weeks.

## Release dates and engagement

`enrich.py` reads Roblox's public APIs for release date, last update, genre, creator, visits, favorites and votes. Place-to-universe lookups are limited to 60 a minute, so they run in small batches, newest place ids first, and are cached permanently in `data/universes.csv`. `data/details.csv` is refreshed every 6 hours and one engagement sample per day goes to `data/engagement/YYYY-MM.csv`. The step is best effort: an outage or rate limit prints a message and collection continues.

- **Released is the date the Roblox experience was created.** Some games sit private for months or reuse an old experience, so a game titled `[NEW]` can show a date years ago. A game also appears here only after reaching 500 players.
- **Visits per day** needs two daily samples, so it stays blank for the first day or two.
- **Minutes per visit** is an estimate from average sampled players and visits per day (Little's law). Sampling leans towards the hours the collector runs, so use it to compare games rather than as an exact figure.

## Breakout score

`signals.py` scores each game from 0 to 100 using only its daily peaks:

- **Rising days:** consecutive days its 3-day median peak rose. Points are deliberately convex, so 5 or more days counts far more than 2.
- **3-day growth vs market**, capped so a huge jump earns nothing extra.
- **Spike penalty:** when a recent peak is over 2.5 times its usual level.

`backtest.py` replays history using only data available on each day, then checks the next 14 days. A hit needs a sustained 3-day level against the highest level already visible, so a spike that already happened or a one-day event does not count. Results are in `dashboard/backtest.json` and on the dashboard, and refresh daily.

Through 2026-11-05, across 29 days and 2,414 games:

| Picks | Doubled | Tripled | 5× |
|---|---|---|---|
| All games | 5.0% | 1.7% | 0.5% |
| Score 70–100 | 20.6% | 10.3% | 2.9% |
| Score 40–69 | 13.3% | 3.9% | 2.3% |
| Score 15–39 | 7.5% | 2.9% | 0.9% |
| Score 0–14 | 4.9% | 1.6% | 0.4% |
| Top 25 daily by score | 13.8% | 5.8% | 1.8% |
| Top 25 daily by 3-day % | 10.8% | 4.4% | 1.2% |
| Top 25 daily by 7-day % | 7.7% | 1.4% | 0.4% |

The weights were chosen after seeing this same history, and 57 days is one short stretch that includes Halloween, so these figures are optimistic. Most high scorers still do not blow up: the score ranks which games deserve a look. Rerun `python backtest.py` as history grows, since later days are a fairer test. Likes, favorites and visits are not in the score because there is no past data to test them against yet; `data/engagement/` is building that history.
