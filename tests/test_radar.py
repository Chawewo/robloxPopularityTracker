import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import backtest
import collect
import config
import enrich
from analyze import build
from collect import prune, read_csv, save_snapshot
from rollup import read_daily, rollup
from signals import level, market_index, rising_streak, score


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)

    def snapshot(self, days_ago, counts, hour=12):
        stamp = (self.now - timedelta(days=days_ago)).replace(hour=hour)
        games = {str(key): [f"Game {key}", count, "https://example.com/i.webp"] for key, count in counts.items()}
        save_snapshot({"success": True, "games": games or {"99": ["Small", 1, ""]}}, stamp, self.root)


class RollupTests(Workspace):
    def test_daily_peak_skips_the_open_day_and_is_idempotent(self):
        self.snapshot(2, {1: 1000}, hour=3)
        self.snapshot(2, {1: 3000}, hour=20)
        self.snapshot(1, {1: 500}, hour=8)
        self.snapshot(0, {1: 9000})
        self.assertEqual(len(rollup(self.root)), 2)
        self.assertEqual(rollup(self.root), [])
        dates, peaks = read_daily(self.root)
        self.assertEqual(dates, ["2026-09-08", "2026-09-09"])
        self.assertEqual(peaks["1"], {"2026-09-08": 3000, "2026-09-09": 500})
        day = read_csv(self.root / "daily" / "2026-09.csv")[0]
        self.assertEqual((day["avg"], day["samples"]), ("2000", "2"))

    def test_orphan_rows_from_an_interrupted_write_are_ignored(self):
        self.snapshot(1, {1: 1000})
        self.snapshot(0, {1: 1100})
        with (self.root / "snapshots" / "2026-09.csv").open("a") as stream:
            stream.write("2026-09-09T13:00:00Z,1,999999\n")
        rollup(self.root)
        self.assertEqual(read_daily(self.root)[1]["1"]["2026-09-09"], 1000)

    def test_daily_peaks_outlive_raw_snapshot_pruning(self):
        self.snapshot(70, {1: 1500})
        self.snapshot(0, {1: 2000})
        rollup(self.root)
        prune(self.now, self.root)
        self.assertEqual(list(read_daily(self.root)[1]["1"].values()), [1500])

    def test_an_absent_game_is_distinct_from_a_day_without_collection(self):
        self.snapshot(2, {1: 1000, 2: 800})
        self.snapshot(1, {1: 1000})
        self.snapshot(0, {1: 1000})
        rollup(self.root)
        dates, peaks = read_daily(self.root)
        self.assertEqual(len(dates), 2)
        self.assertNotIn(dates[1], peaks["2"])


class SignalTests(unittest.TestCase):
    market = [0.0] * 20

    def test_absent_days_count_as_below_the_floor(self):
        self.assertEqual(level([None, None, 2000], 2), config.COLLECT_FLOOR * 0.8)

    def test_streak_counts_consecutive_rises_only(self):
        self.assertEqual(rising_streak([900, 1000, 900, 950, 1000, 1100, 1200, 1300], 7), 3)
        self.assertEqual(rising_streak([1000] * 8, 7), 0)

    def test_steady_climb_beats_a_larger_one_day_spike(self):
        climb = [1000, 1100, 1200, 1300, 1450, 1600, 1800, 2000, 2250, 2500]
        spike = [1000] * 9 + [20000]
        self.assertGreater(score(climb, 9, self.market)["score"], score(spike, 9, self.market)["score"])
        self.assertTrue(score(spike, 9, self.market)["spike"])
        self.assertFalse(score(climb, 9, self.market)["spike"])

    def test_score_stays_within_range_and_ignores_later_days(self):
        peaks = [600 * 1.4 ** day for day in range(16)]
        self.assertEqual(score(peaks, 15, self.market)["score"], 100)
        early = score(peaks + [1, 1, 1], 15, self.market + [0, 0, 0])
        self.assertEqual(early, score(peaks, 15, self.market))
        self.assertGreaterEqual(score([None] * 5 + [500], 5, self.market)["score"], 0)

    def test_a_market_wide_rise_earns_no_growth_credit(self):
        flat = [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        rising = [0.0, 0.3, 0.6, 0.9, 1.2, 1.5]
        peaks = [1000, 1350, 1800, 2450, 3300, 4500]
        self.assertGreater(score(peaks, 5, flat)["vs_market_3"], score(peaks, 5, rising)["vs_market_3"])
        self.assertAlmostEqual(score(peaks, 5, rising)["vs_market_3"], 0, delta=0.05)

    def test_one_huge_game_does_not_set_the_market(self):
        games = [[1000, 2000]] * 2 + [[1000, 1000]] * 5 + [[100000, 900000]]
        self.assertEqual(market_index(games, 2), [0.0, 0.0])


class AnalyzeTests(Workspace):
    def test_small_games_are_listed_but_do_not_count_as_traction(self):
        self.snapshot(1, {1: 700})
        self.snapshot(0, {1: 800, 2: 1500})
        data = build(self.root, self.now)[0]
        rows = {row["game_id"]: row for row in data["games"]}
        self.assertEqual(data["eligible_count"], 1)
        self.assertFalse(rows["1"]["traction"])
        self.assertTrue(rows["2"]["traction"])

    def test_small_game_is_not_called_a_new_entrant_for_staying_small(self):
        self.snapshot(5, {1: 700})
        self.snapshot(0.5, {1: 750}, hour=2)
        self.snapshot(0, {1: 800})
        row = build(self.root, self.now)[0]["games"][0]
        self.assertFalse(row["crossed_floor"])
        self.assertFalse(row["new_entrant"])

    def test_history_holds_daily_peaks_plus_a_rolling_day(self):
        self.snapshot(2, {1: 1000})
        self.snapshot(1, {1: 1200, 2: 900})
        self.snapshot(0, {1: 1500})
        rollup(self.root)
        data, history = build(self.root, self.now)
        self.assertEqual(history["dates"], ["2026-09-08", "2026-09-09"])
        self.assertEqual(history["games"]["1"], [1000, 1200, 1500])
        self.assertNotIn("2", history["games"])
        self.assertEqual(len(history["market"]), 3)
        self.assertEqual(data["games"][0]["peak_24h"], 1500)

    def test_rolling_peak_covers_only_the_past_day(self):
        self.snapshot(3, {1: 90000})
        self.snapshot(0, {1: 1000})
        row = build(self.root, self.now)[0]["games"][0]
        self.assertEqual(row["peak_24h"], 1000)

    def test_game_without_roblox_details_still_lists(self):
        self.snapshot(0, {1: 1500})
        row = build(self.root, self.now)[0]["games"][0]
        self.assertIsNone(row["age_days"])
        self.assertIsNone(row["like_pct"])
        self.assertFalse(row["verified"])

    def test_details_and_engagement_reach_the_row(self):
        self.snapshot(0, {1: 1440})
        (self.root / "details.csv").write_text(
            ",".join(enrich.DETAIL_FIELDS) + "\n"
            "1,10,2026-08-11T00:00:00Z,2026-09-07T00:00:00Z,Simulation,Tycoon,Studio,Group,1,20,5000,70,90,10,2026-09-10T00:00:00Z\n",
            encoding="utf-8")
        path = self.root / "engagement" / "2026-09.csv"
        path.parent.mkdir()
        path.write_text(",".join(enrich.ENGAGEMENT_FIELDS) + "\n2026-09-08T12:00:00Z,1,1000,10,5,1\n2026-09-10T12:00:00Z,1,5000,70,90,10\n")
        row = build(self.root, self.now)[0]["games"][0]
        self.assertEqual(row["age_days"], 30)
        self.assertEqual(row["updated_days"], 3)
        self.assertEqual(row["genre"], "Simulation")
        self.assertEqual(row["like_pct"], 90)
        self.assertEqual(row["visits_per_day"], 2000)
        self.assertEqual(row["minutes_per_visit"], 1036.8)


def rate_limited(url="http://x"):
    return HTTPError(url, 429, "Too Many Requests", None, None)


class EnrichTests(Workspace):
    item = dict(id=10, created="2026-08-11T03:29:19.433Z", updated="2026-09-07T12:18:46.772Z", genre_l1="RPG",
                genre_l2="Action RPG", maxPlayers=12, visits=5000, favoritedCount=70,
                creator=dict(name="Studio", type="Group", hasVerifiedBadge=True))

    def fetch(self, url):
        self.urls.append(url)
        if "/universe" in url:
            return dict(universeId=int(url.split("/")[-2]) * 10)
        if "votes" in url:
            return dict(data=[dict(id=10, upVotes=90, downVotes=10)])
        return dict(data=[self.item])

    def run_enrich(self, fetch=None):
        return enrich.enrich(self.now, self.root, fetch or self.fetch, lambda _: None)

    def setUp(self):
        super().setUp()
        self.urls = []
        self.snapshot(0, {1: 1500})

    def test_saves_release_details_and_one_engagement_sample(self):
        self.assertEqual(self.run_enrich(), dict(resolved=1, refreshed=1))
        row = read_csv(self.root / "details.csv")[0]
        self.assertEqual((row["created"], row["updated"], row["genre"], row["up_votes"]),
                         ("2026-08-11T03:29:19Z", "2026-09-07T12:18:46Z", "RPG", "90"))
        self.assertEqual(len(read_csv(self.root / "engagement" / "2026-09.csv")), 1)

    def test_universe_lookups_are_cached_and_refreshes_are_spaced(self):
        self.run_enrich()
        self.urls.clear()
        self.assertEqual(self.run_enrich(), dict(resolved=0, refreshed=0))
        self.assertEqual(self.urls, [])

    def test_rate_limit_keeps_progress(self):
        self.snapshot(0, {1: 1500, 2: 1500, 3: 1500}, hour=13)

        def limited(url):
            if "/places/2/" in url:
                raise rate_limited(url)
            return self.fetch(url)
        self.run_enrich(limited)
        self.assertEqual([row["game_id"] for row in read_csv(self.root / "universes.csv")], ["3"])

    def test_batches_stay_within_the_roblox_limit(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return dict(data=[])
        enrich.fetch_details([str(n) for n in range(120)], fetch, lambda _: None)
        sizes = [len(url.split("=")[1].split(",")) for url in calls]
        self.assertEqual(max(sizes), config.ENRICH_BATCH)
        self.assertEqual(sum(sizes), 240)

    def test_missing_dates_do_not_produce_a_broken_timestamp(self):
        row = enrich.detail_row("1", "10", dict(id=10), None, "2026-09-10T12:00:00Z")
        self.assertEqual((row["created"], row["updated"]), ("", ""))

    def test_a_roblox_failure_never_fails_collection(self):
        with patch.object(enrich, "enrich", side_effect=OSError("offline")):
            enrich.main()

    def test_newest_listed_games_resolve_first(self):
        self.snapshot(0, {5: 1500, 900: 1500, 40: 1500}, hour=13)
        with patch.object(config, "ENRICH_RESOLVE_BUDGET", 1):
            self.run_enrich()
        self.assertEqual([row["game_id"] for row in read_csv(self.root / "universes.csv")], ["900"])


class CollectOrderTests(unittest.TestCase):
    def test_days_are_rolled_up_before_raw_snapshots_are_pruned(self):
        calls = []
        response = MagicMock()
        response.__enter__.return_value = io.BytesIO(b'{"games": {}}')
        with patch.object(collect, "urlopen", return_value=response),              patch.object(collect, "save_snapshot", side_effect=lambda *_: calls.append("save") or 0),              patch("rollup.rollup", side_effect=lambda: calls.append("rollup")),              patch.object(collect, "prune", side_effect=lambda *_: calls.append("prune")):
            collect.main()
        self.assertEqual(calls, ["save", "rollup", "prune"])


class BacktestTests(Workspace):
    def test_sustained_level_ignores_one_day_events(self):
        self.assertEqual(backtest.sustained_level([1000, 50000, 1000, 1000], 0, 4), 1000)
        self.assertEqual(backtest.sustained_level([1000, 4000, 4000, 4000], 0, 4), 4000)

    def test_a_climb_counts_and_a_spike_already_visible_does_not(self):
        days = backtest.LOOKBACK + backtest.HORIZON + 1
        before = [1000] * (backtest.LOOKBACK + 1)
        series = {
            "climb": before + [3000] * (days - len(before)),
            "faded": [1000] * backtest.LOOKBACK + [9000] + [4000] * (days - len(before)),
        }
        rows = backtest.observations(series, days, [0.0] * days)
        outcome = {row["game_id"]: row["outcome"]["double"] for row in rows if row["day"] == backtest.LOOKBACK}
        self.assertTrue(outcome["climb"])
        self.assertFalse(outcome["faded"])

    def test_report_runs_on_real_shaped_data(self):
        for day in range(35, -1, -1):
            self.snapshot(day, {1: 1000 + (35 - day) * 300, 2: 1200})
        rollup(self.root)
        result = backtest.run(self.root)
        self.assertEqual(result["days_tested"], 7)
        self.assertEqual(result["observations"], 14)
        self.assertEqual(len(result["bands"]), len(backtest.BANDS))
        json.dumps(result)


if __name__ == "__main__":
    unittest.main()
