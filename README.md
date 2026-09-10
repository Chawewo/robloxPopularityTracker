# Next Up — Roblox “Next Big Game” Tracker

A Python collector and GitHub Pages dashboard for spotting Roblox games gaining players while they have real traction (at least 1,000 concurrent players).

**Dashboard:** https://chawewo.github.io/robloxPopularityTracker/ (available after the first successful Pages deployment).

## Run locally

Requires Python 3.11 or newer. No third-party dependencies.

```sh
python collect.py
python analyze.py
python -m unittest discover -s tests -v
python -m http.server 8000 --directory dashboard
```

Open http://localhost:8000. Use `?demo=1` to review a clearly labeled, fictional populated leaderboard. Demo data is separate from real history and is never consumed by the analyzer. Serve via HTTP; opening the HTML directly does not allow reliable JSON fetching.

Avoid repeatedly running the live collector: one run per ~15 minutes is sufficient. Tests use temporary synthetic snapshots and do not call the API.

## GitHub Actions and Pages

The repository is `Chawewo/robloxPopularityTracker`. The workflow runs on pushes to `main` that change code, manual dispatch, and every 15 minutes at minutes 7, 22, 37, and 52 UTC. The offset avoids the busiest top-of-hour period. Collection and deployment are serialized; an active collection is never cancelled by another trigger.

1. Settings → Actions → General → Workflow permissions → Read and write permissions.
2. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
3. Push the files to the default branch (`main`), or use Actions → **Track Roblox risers** → **Run workflow**.
4. Check both `collect` and `deploy` jobs succeed. The workflow commits history with `[skip ci]`, then deploys only `dashboard/`.

If branch protection prevents the bot from pushing, allow this workflow to write to the collection branch before enabling the schedule. Manual workflow runs must target the default branch. If your default branch is renamed, update the push trigger too.

The workflow requests `contents: write`, `pages: write`, and `id-token: write`; the last is required for Pages deployment. No personal token is stored in this project. API failure or malformed data fails collection and leaves the deployed dashboard intact; the page marks old snapshots overdue after 45 minutes. Data is committed before deployment, so a Pages failure does not discard the snapshot.

GitHub scheduling is **best effort**, not gap-free: jobs can be delayed or dropped, and public-repository schedules may be disabled after 60 days without repository activity. Public standard runners are generally free; private repositories use your plan’s Actions allowance. See [schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions), and [custom Pages workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).

## Data and signals

- **Source:** one GET to https://api.rolimons.com/games/v1/gamelist with browser-style User-Agent and Rolimons Referer. Request timeout: 45 seconds, no retries. Counts reflect Rolimons’ cached observation, not exact real-time Roblox counts.
- **Storage:** `data/snapshots/YYYY-MM.csv` contains `timestamp,game_id,players`; only games with at least 500 players are retained. `data/games.csv` tracks names, icons, and first observation. Metadata is rewritten only when changed. `data/collections.csv` marks completed snapshots, including zero-qualifier runs; orphan rows from interrupted writes are ignored and incomplete committed snapshots fail analysis.
- **Current:** only games in the newest complete snapshot with at least 1,000 players qualify. A missing game is unknown, never a stale current count or a zero baseline.
- **Growth:** nearest observed count to 1h, 6h, and 24h before the latest collection, within ±20 minutes. Ties use the earlier observation. `delta = current − baseline`; `pct = delta / max(baseline, 1) × 100`. JSON includes the actual matched baseline timestamp. No interpolation or missing-value imputation.
- **Sustained:** positive growth in every available window, with at least one comparison. The default lens additionally requires a 6h baseline. This is an early signal, not proof of uninterrupted growth; daily cycles, updates, and events can affect counts.
- **New entrant:** first observed by this tracker within 24h, or an observed count below 1,000 within 24h followed by a current count at or above 1,000. First observed does not mean newly released. The initial batch will all show NEW. Crossing from below the collection floor cannot always be proven for previously seen games, so missing observations are not inferred as crossings.
- **Ranking:** sustained 6h percentage gain; absolute 6h player change; 1h percentage change; new entrants by current players. Each lens displays up to 100 games. JSON retains all eligible games so another lens or column can find its own top 100. Click count, name, delta, or percentage headers to sort ascending/descending; missing values remain last.
- **Cold start:** comparisons stay blank until a matching baseline exists. Before a lens has history, it shows current traction with an explicit “awaiting history” notice and no rank numbers. Around 1h/6h/24h of collection unlocks the respective comparisons (subject to tolerance and schedule delays).
- **Retention:** exact rolling 60 days in the working tree, pruning old rows and empty files. Metadata first-seen dates persist. Old Git commits still retain prior data, so pruning does not shrink repository history; archive/rotate the repository if long-term Git size becomes an issue.

Edit `config.py` to tune floors, comparison windows, tolerance, retention, display limit, and stale threshold. The dashboard currently presents fixed 1h/6h/24h columns; update its UI if changing window choices. The dashboard fetches every three minutes; this does not poll Rolimons.

The public Pages artifact contains leaderboard data and demo data, not snapshot CSVs. A public repository exposes committed history. Use this for personal discovery and respect the upstream service’s terms; avoid bulk redistribution.
