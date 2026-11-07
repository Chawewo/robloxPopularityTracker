# Next Up — Roblox “Next Big Game” Tracker

A Python collector and GitHub Pages dashboard for catching Roblox games before they blow up. It tracks every game above a floor you can change (100 concurrent players by default, 25 for games under 4 months old), finds games the source list has missed by scanning Roblox's own lists, adds release dates, engagement and chart positions, detects clone waves, and ranks games by the signals that came before past blow-ups.

**Dashboard:** https://chawewo.github.io/robloxPopularityTracker/ (available after the first successful Pages deployment).

## Run locally

Requires Python 3.11 or newer. No third-party dependencies, except the optional trained model (`pip install -r requirements-model.txt`, then `python model.py`).

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

- **Source:** one GET to https://api.rolimons.com/games/v1/gamelist with browser-style User-Agent and Rolimons Referer. Request timeout: 45 seconds, no retries. Counts reflect Rolimons’ cached observation, not exact real-time Roblox counts. Games Rolimons has not listed are read directly from Roblox; see [Finding games Rolimons misses](#finding-games-rolimons-misses).
- **Storage:** `data/snapshots/YYYY-MM.csv` contains `timestamp,game_id,players`; only games at or above the collection floor are retained. At 40 MiB, a month rolls over into `YYYY-MM-002.csv`, etc., to stay below GitHub's file limit. Analysis streams only relevant months and retains observations for the last 24h plus tolerance in memory. `data/games.csv` tracks names, icons, and first observation. Metadata is rewritten only when changed. `data/collections.csv` marks completed snapshots, including zero-qualifier runs; orphan rows from interrupted writes are ignored and incomplete recent committed snapshots fail analysis.
- **Current:** every game in the newest complete snapshot is listed. Those with at least 1,000 players count as having traction. A missing game is unknown, never a stale current count or a zero baseline.
- **Growth:** nearest observed count to 1h, 6h, and 24h before the latest collection, within ±20 minutes. Ties use the earlier observation. `delta = current − baseline`; `pct = delta / max(baseline, 1) × 100`. JSON includes the actual matched baseline timestamp. No interpolation or missing-value imputation.
- **Sustained:** positive growth in every available window, with at least one comparison. The default lens additionally requires a 6h baseline. This is an early signal, not proof of uninterrupted growth; daily cycles, updates, and events can affect counts.
- **New entrant:** first observed by this tracker within 24h, or a current count at or above 1,000 after a week below it, so a game dipping under 1,000 overnight does not qualify. First observed does not mean newly released. The initial batch will all show NEW. Crossing from below the collection floor cannot always be proven for previously seen games, so missing observations are not inferred as crossings.
- **Ranking:** sustained 6h percentage gain; absolute 6h player change; 1h percentage change; new entrants by current players. Each lens displays up to 100 games. JSON retains all eligible games so another lens or column can find its own top 100. Click count, name, delta, or percentage headers to sort ascending/descending; missing values remain last.
- **Cold start:** comparisons stay blank until a matching baseline exists. Before a lens has history, it shows current traction with an explicit “awaiting history” notice and no rank numbers. Around 1h/6h/24h of collection unlocks the respective comparisons (subject to tolerance and schedule delays).
- **Retention:** at least 30 days of raw snapshots in the working tree. Partitions are deleted whole once every row has expired, because trimming rows would add a full copy of a 40 MB file to Git history on every run. Daily rollups are kept permanently. Metadata first-seen dates persist. Old Git commits still retain prior data, so pruning does not shrink repository history; archive/rotate the repository if long-term Git size becomes an issue.

Edit `config.py` to tune floors, comparison windows, tolerance, retention, display limit, and stale threshold. The dashboard fetches every three minutes; this does not poll Rolimons.

The public Pages artifact contains leaderboard, history and demo data, not snapshot CSVs. A public repository exposes committed history. Use this for personal discovery and respect the upstream service’s terms; avoid bulk redistribution.

## Changes since the previous run

The LAST RUN column compares the newest two completed collections, showing player delta, percentage, and the actual interval in minutes. It works for manual runs and scheduling gaps without pretending the interval is one hour. A game missing from the previous collection has no comparison; an unchanged count shows zero. This lens is selected automatically until a 6h comparison becomes available. The 1h/6h/24h windows still require their own baselines within the configured tolerance.

On September 10, scheduled triggers had not appeared despite successful push and manual runs. The cron was changed to `4-59/15 * * * *` as a recovery attempt. This does not guarantee scheduler recovery: only a run with event `schedule` verifies it. Each run now includes a summary showing its trigger, snapshot time, elapsed interval, and comparison coverage. No local scheduler has been installed.

## Windows collection timer

`Roblox Tracker - GitHub Collection` runs `dispatch_workflow.py` with hidden Python every 15 minutes and at sign-in. It runs as the current Windows user without elevation, while signed in (locking is fine). It cannot run during sleep, shutdown, or sign-out; missed starts are handled when available. Your GitHub credentials stay in the existing Git credential helper; no token is saved in the repository. The script skips active workflows and snapshots under 12 minutes old. GitHub cron remains a fallback when the PC is unavailable. Dispatch only requests a run; network or GitHub service failures can still delay collection. Inspect `.scheduler.log` and Task Scheduler for status. Reinstall with `powershell -File install_scheduler.ps1` under your normal account. Disable or remove the named task in Task Scheduler to stop it.

## Sparse historical comparisons

Window tolerances are now 1h ±20 minutes, 6h ±90 minutes, and 24h ±3 hours. Every available time-window metric includes its actual elapsed hours. Matches farther than 20 minutes from the target are labeled approximate. No interpolation is used. Missing comparisons explicitly distinguish insufficient history from collection gaps. This supersedes the original uniform ±20-minute matching rule described above.

## Changing the collection floor

The floor decides which games are stored. Rolimons lists about 7,800 games, so lower floors really do reveal more:

| Floor | Games (Nov 2026) |
|---|---|
| 500 | 1,486 |
| 200 | 2,348 |
| 100 | 3,056 |
| 50 | 3,831 |
| 10 | 5,526 |

Change it either way:

- **No code:** in GitHub, go to Settings → Secrets and variables → Actions → Variables and create `COLLECT_FLOOR` with the number you want. It overrides the default on the next run.
- **In code:** edit `DEFAULT_COLLECT_FLOOR` in `config.py`.

Games created in the past 120 days have their own lower floor of 25 players (`YOUNG_FLOOR` in `config.py`), because a new game's 16+ stage can happen well below 100 players. Small games with recent ids are queued for a release-date lookup, and only those that turn out to be young are stored.

The minimum is 10, and an invalid value falls back to the default rather than stopping collection. Each day records the floor it was collected under, so changing it never distorts history: an absent day still means "below the floor that day", and lowering the floor does not mark long-running games as new. Storage scales with game count, so 100 costs about double 500.

The floor only controls what is stored. Min players on the dashboard controls what you see.

## Finding games Rolimons misses

Rolimons adds games late and in bursts, about every three days in late October. On Nov 7, 2026, 96 of the 349 games with 5,000 or more players on Roblox's own lists were not on it, and neither were 450 of the 984 with 500 to 4,999. Those missing games were mostly young, with a median age of 39 days. One had over 45,000 players. Of 290 games that reached 5,000 players after tracking began, only 24 were ever seen under 2,000, and no score can catch a game it cannot see.

`discover.py` runs during collection and closes most of that gap:

- **Scanning:** it reads Roblox's Explore lists on four device types plus Brazil, Indonesia and Saudi Arabia, because lists differ by device and country. That found 1,809 games on the first full scan.
- **Watching:** every game found is remembered in `data/discovered.csv`. Games Rolimons has not listed are read directly from Roblox, up to 2,000 at a time, and keep being read after they leave every list. They are dropped after 30 days below the floor.
- **Coverage:** `data/coverage.csv` records how much of Roblox's lists Rolimons is missing each day, and the dashboard shows it.

Only games on these lists can be found, so a game no list shows stays invisible until Rolimons adds it. This step is best effort: if Roblox's endpoints change or rate-limit, collection continues from Rolimons alone.

A game found late has no past data, so its earlier days count as unknown rather than below the floor. It shows no change figures until readings exist, and its score starts at 0 and builds from what is actually observed.

## Roblox's release rules

Since May 19, 2026, a new game reaches only age-checked players aged 16 and over at first. Reaching younger players needs a refundable publishing fee or a Plus subscription, verification, and passing an evaluation, which includes 250 highly engaged 16+ players within 60 days. Roblox said on Oct 12 that this would drop to 100 in November, and it changed the rules several times over the summer, so older patterns may stop applying. Expedited Review costs a refundable 50,000 Robux and takes up to 48 hours, but the same standards apply.

**No public field shows whether a game has passed.** Maturity labels, `minimumAge` and `isContentRestricted` did not separate games in a sample of about 100. So the tracker collects indirect evidence, all of it logged for the signal lab:

- **Step-ups:** a game holding at least 3 times last week's level for two days. In a young game this can mean it just reached a wider audience, but updates and events do the same, so it is a guess.
- **First appearance on a Roblox list**, in `data/discovered.csv`.
- **Maturity label changes**, in `data/maturity_changes.csv`. A creator filling in the full questionnaire may be heading for under-16 audiences, which is untested.

## When a game really launched

Roblox publishes only Created and Updated. Created is when the experience was made, not when it went public, so the dashboard adds what can be inferred:

- **Public by:** the first sighting anywhere, which is the latest it can have gone public. Tracking began Sep 10, 2026 and list scanning Nov 7, 2026, so earlier launches show those dates.
- **Public since:** Roblox describes Up-and-Coming as experiences published in the past 28 days, so appearing there suggests a launch no more than 28 days earlier. On Nov 7, 47 of 234 Up-and-Coming games had a Created date over 30 days old, and one was 177 days old, which shows how far Created can drift from launch. Roblox does not define "published", so treat this as a strong hint.

Rolimons' late listing also means the age at which a game was first seen does not prove how long it took to blow up. Discovery will give a cleaner answer as history builds.

## Long-range change

`rollup.py` compresses each completed UTC day into `data/daily/YYYY-MM.csv` (peak, average, samples per game) and records the day in `data/daily_index.csv`. It runs before pruning, so long-range history survives raw pruning. `analyze.py` publishes these peaks to `dashboard/history.json`, which is rebuilt each run, deployed to Pages, and not committed.

The dashboard compares the peak of the last 24 hours with the peak on the matching past day, so **any** period works, including a custom number of days. Peaks are used because collection has overnight gaps: a peak barely depends on which hours were sampled, while point-in-time counts and daily averages do. The peak also removes the time-of-day cycle.

- **≥ values:** a game absent on the baseline day was under that day's collection floor, so the gain is shown as a lower bound against that floor. Sorting uses that lower bound. These games would otherwise disappear from the ranking, and they are often the ones you want.
- **Vs market:** the change after removing the median game's move over the same days. The median stops one giant game from defining the market, and the adjustment cancels weekends and holidays, which matters for periods that are not whole weeks.

## Release dates and engagement

`enrich.py` reads Roblox's public APIs for release date, last update, genre, creator, visits, favorites and votes. Place-to-universe lookups are limited to 60 a minute, so they run in small batches, newest place ids first, and are cached permanently in `data/universes.csv`. `data/details.csv` is refreshed every 6 hours and one engagement sample per day goes to `data/engagement/YYYY-MM.csv`. The step is best effort: an outage or rate limit prints a message and collection continues.

- **Released is the date the Roblox experience was created.** Some games sit private for months or reuse an old experience, so a game titled `[NEW]` can show a date years ago. A game also appears here only after reaching the collection floor.
- **Visits per day** needs two daily samples, so it stays blank for the first day or two.
- **Minutes per visit** is an estimate from average sampled players and visits per day (Little's law). Sampling leans towards the hours the collector runs, so use it to compare games rather than as an exact figure.

## Roblox charts and community signals

`enrich.py` runs these as separate best-effort steps, and all of them keep working if one source fails.

- **Charts:** position on Top Trending, Up-and-Coming, Top Revisited, Top Earning, Top Rated and Fun with Friends, by UTC day, on computer and phone (`data/charts/`). Sponsored slots are skipped.
- **New players per day:** daily awards of a game's most-earned badge (`data/badges/`). Many games award one on first join, so this roughly tracks arrivals, but it is a proxy.
- **Group members:** community size behind group-owned games (`data/groups/`). Roblox allows 7 lookups a minute, so these fill in slowly.
- **Update cadence:** each change to a game's published date (`data/updates.csv`), giving updates per 30 days and change since the latest update.
- **Engagement:** visits acceleration, favorites per 1,000 visits and like-rate change.
- **Title and icon changes:** logged to `data/changes.csv`, including history rebuilt from Git.

These endpoints are public but undocumented, so Roblox can change them without notice. Discord, YouTube and TikTok are not included: Roblox's social links need a login, and the others need accounts or API keys.

## Clone waves

`clones.py` finds trends without a hand-kept list. It strips tags and emoji from titles, then looks for words and word pairs far more common among games from the past 120 days than among older games, used by at least 3 creators. Each game joins one wave, with the biggest game as its leader, and variants such as "steal egg" stay inside the wider "egg" wave. The wave's change is its median game over 7 days, which shows whether the idea is still growing or already fading.

## Breakout score

`signals.py` scores each game from 0 to 100:

- **Rising days:** consecutive days its 3-day median peak rose. Points are deliberately convex, so 5 or more days counts far more than 2.
- **3-day growth vs market**, capped so a huge jump earns nothing extra.
- **Spike penalty:** when a recent peak is over 2.5 times its usual level.

A signal only joins the score if it improves the replay when tested on and off. Three ideas failed that test. A penalty for games aged 61 to 180 days did not improve the ranking, even though those games do worse on average. A bonus for games aged 14 to 60 days lifted doubling but hurt tripling, so it stays a filter and a note on each game. A clone-wave bonus looked useful on partial data, but with full release dates it made no real difference, so it was removed. The dashboard's signal lab shows every signal's record, including ones still collecting history, and it updates daily.

`backtest.py` replays history using only data available on each day, then checks the next 14 days. A hit needs a sustained 3-day level above both the highest level already visible and the game's record so far. A spike that already happened or a one-day event does not count, and neither does an old game bouncing back to last month's level. Results are in `dashboard/backtest.json` and on the dashboard, and refresh daily.

Through 2026-11-06, across 30 days and 2,608 games:

| Picks | Doubled | Tripled | 5× |
|---|---|---|---|
| All games | 4.0% | 1.6% | 0.5% |
| Score 70–100 | 22.2% | 9.7% | 2.8% |
| Score 40–69 | 14.3% | 3.8% | 2.3% |
| Score 15–39 | 7.4% | 2.9% | 0.9% |
| Score 0–14 | 3.8% | 1.5% | 0.4% |
| Top 25 daily by score | 14.0% | 5.9% | 1.7% |
| Top 25 daily by 3-day % | 10.8% | 4.4% | 1.2% |
| Top 25 daily by 7-day % | 7.6% | 1.3% | 0.4% |

The weights were chosen after seeing this same history, and 57 days is one short stretch that includes Halloween, so these figures are optimistic. Most high scorers still do not blow up: the score ranks which games deserve a look. Rerun `python backtest.py` as history grows, since later days are a fairer test. Charts, visits, likes, favorites, badges and groups are not in the score because there is no past data to test them against yet; the signal lab tests them automatically as history builds.

## Trained model

`model.py` runs a gradient boosting model beside the score, without changing it. Once a day it retrains on every finished outcome, publishes each game's chance of doubling or tripling to a new record, and logs its top picks in `data/model/picks/`. Those picks are later graded against the score's on days neither had seen, and the dashboard shows the running result. Switch only if the model clearly wins that record over several weeks.

So far it has not beaten the score. On held-out games, a week away from its training days, the daily top 25 doubled 9.0%, tripled 4.3% and reached 5× 2.3% of the time, against 13.1%, 5.4% and 1.7% for the score. An earlier version looked much better only because nearly half of its hits were old games bouncing back to levels they had already reached, which is why a hit must now beat the game's record. Its chances are calibrated on held-out games, so a 10% chance has meant about 10%, but at the very top there are too few rows to trust.

The model needs scikit-learn, which is installed only when a new day is ready, and a failure leaves the previous chances in place. Genre and server size use today's values rather than past ones, a small leak that is worth removing once history allows it.

## Running on GitHub Actions: known risks

- **GitHub's terms:** Actions is meant for developing, testing and publishing software, and GitHub may restrict misuse. A data collector running every 15 minutes is a grey area even though this workflow also publishes the site. Moving collection to a small always-on server would remove this risk and fix the next one.
- **Gaps:** GitHub says scheduled runs can be delayed or dropped, and in practice the cron fires every 6 to 8 hours. Regular collection depends on the Windows timer, so it pauses while the PC sleeps. Daily peaks mostly survive this; 1-hour and 6-hour comparisons do not.
- **Upstream changes:** Rolimons and the Roblox endpoints can change or block automated requests at any time. Rolimons remains the main source of player counts. Discovery depends on undocumented Roblox endpoints, and if they fail, tracking falls back to Rolimons alone.
- **Private repositories:** this repository is public, so Actions minutes are free. About 96 runs a day would exceed a private plan's free allowance.
