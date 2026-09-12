import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from analyze import analyze
from collect import prune, read_csv, save_snapshot, validate


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)

    def snapshot(self, offset, counts, names=None):
        games = {str(key): [(names or {}).get(key, f"Game {key}"), count, "https://example.com/icon.webp"]
                 for key, count in counts.items()}
        # API always has games, even when none pass the collection floor.
        save_snapshot({"success": True, "games": games or {"99": ["Small game", 1, ""]}},
                      self.now + timedelta(minutes=offset), self.root)

    def result(self):
        return analyze(self.root, self.now)

    def test_cold_start_does_not_invent_growth(self):
        self.snapshot(0, {1: 1500, 2: 999, 3: 400})
        data = self.result()
        self.assertEqual(data["eligible_count"], 1)
        self.assertEqual(data["history_hours"], 0)
        self.assertFalse(data["games"][0]["sustained"])
        self.assertEqual(data["games"][0]["windows"], {str(h): None for h in config.WINDOWS})

    def test_jitter_and_crossing_and_percent(self):
        self.snapshot(-1448, {1: 600})
        self.snapshot(-365, {1: 800})
        self.snapshot(-58, {1: 1000})
        self.snapshot(0, {1: 1600})
        row = self.result()["games"][0]
        self.assertEqual(row["windows"]["1"]["delta"], 600)
        self.assertEqual(row["windows"]["6"]["pct"], 100)
        self.assertTrue(row["sustained"])
        self.assertTrue(row["crossed_floor"])
        self.assertTrue(row["new_entrant"])

    def test_missing_and_below_floor_are_not_current(self):
        self.snapshot(-60, {1: 2000, 2: 1500, 3: 1200})
        self.snapshot(0, {2: 999, 3: 1400})
        self.assertEqual([row["game_id"] for row in self.result()["games"]], ["3"])

    def test_empty_complete_snapshot_removes_stale_games(self):
        self.snapshot(-60, {1: 3000})
        self.snapshot(0, {})
        self.assertEqual(self.result()["games"], [])
        self.assertEqual(self.result()["snapshot_count"], 2)

    def test_outside_tolerance_is_missing(self):
        self.snapshot(-81, {1: 1000})
        self.snapshot(0, {1: 2000})
        self.assertIsNone(self.result()["games"][0]["windows"]["1"])

    def test_one_negative_window_is_not_sustained(self):
        self.snapshot(-1440, {1: 3000})
        self.snapshot(-360, {1: 1000})
        self.snapshot(-60, {1: 1200})
        self.snapshot(0, {1: 2000})
        self.assertFalse(self.result()["games"][0]["sustained"])

    def test_metadata_and_duplicate_timestamp(self):
        self.snapshot(-60, {1: 1000})
        before = read_csv(self.root / "games.csv")[0]["first_seen"]
        self.snapshot(0, {1: 2000}, {1: "Renamed, game 🎮"})
        self.snapshot(0, {1: 9999})
        row = self.result()["games"][0]
        self.assertEqual(row["first_seen"], before)
        self.assertEqual(row["name"], "Renamed, game 🎮")
        self.assertEqual(row["players"], 2000)

    def test_invalid_response_never_creates_data(self):
        for payload in [None, [], {}, {"games": {}}, {"success": False, "games": {"1": ["x", 1, ""]}},
                        {"games": {"1": ["x", -5, ""]}}, {"games": {"1": ["x", True, ""]}}]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                save_snapshot(payload, self.now, self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_month_boundary_and_pruning_preserve_first_seen(self):
        self.snapshot(-70 * 1440, {1: 1100})
        first_seen = read_csv(self.root / "games.csv")[0]["first_seen"]
        self.snapshot(-1440, {1: 1500})
        self.snapshot(0, {1: 2000})
        prune(self.now, self.root)
        data = self.result()
        self.assertEqual(data["snapshot_count"], 2)
        self.assertEqual(data["games"][0]["first_seen"], first_seen)
        self.assertFalse(data["games"][0]["new_entrant"])
        self.assertEqual(data["games"][0]["windows"]["24"]["delta"], 500)

    def test_partial_uncommitted_snapshot_is_ignored(self):
        self.snapshot(-60, {1: 1100})
        with (self.root / "snapshots" / "2026-09.csv").open("a") as stream:
            stream.write("2026-09-10T12:00:00Z,1,5000\n")
        self.assertEqual(self.result()["games"][0]["players"], 1100)

    def test_missing_committed_rows_fail_loudly(self):
        self.snapshot(0, {1: 1100})
        (self.root / "snapshots" / "2026-09.csv").write_text("timestamp,game_id,players\n")
        with self.assertRaises(ValueError):
            self.result()

    def test_monthly_file_rollover_keeps_comparisons(self):
        self.snapshot(-60, {1: 1100})
        with patch.object(config, "SNAPSHOT_FILE_BYTES", 1):
            self.snapshot(0, {1: 2000})
        self.assertTrue((self.root / "snapshots" / "2026-09-002.csv").exists())
        self.assertEqual(self.result()["games"][0]["windows"]["1"]["delta"], 900)

    def test_last_run_comparison_when_hour_window_is_unavailable(self):
        self.snapshot(-33, {1: 2000, 2: 3000, 3: 1000})
        self.snapshot(0, {1: 2500, 2: 2700, 3: 1000})
        data = self.result()
        rows = {r['game_id']: r for r in data['games']}
        self.assertEqual(data['last_interval_minutes'], 33)
        self.assertEqual(rows['1']['since_last']['pct'], 25)
        self.assertEqual(rows['2']['since_last']['delta'], -300)
        self.assertEqual(rows['3']['since_last']['pct'], 0)
        self.assertIsNone(rows['1']['windows']['1'])

    def test_last_run_does_not_skip_missing_game_or_invent_zero(self):
        self.snapshot(-120, {1: 1200})
        self.snapshot(-30, {2: 1500})
        self.snapshot(0, {1: 2400, 2: 1600, 3: 3000})
        rows = {r['game_id']: r for r in self.result()['games']}
        self.assertIsNone(rows['1']['since_last'])
        self.assertIsNone(rows['3']['since_last'])
        self.assertEqual(rows['2']['since_last']['delta'], 100)

    def test_last_run_across_multiday_gap(self):
        self.snapshot(-3 * 1440, {1: 1000})
        self.snapshot(0, {1: 2000})
        row = self.result()['games'][0]
        self.assertEqual(row['since_last']['pct'], 100)
        self.assertEqual(row['since_last']['elapsed_minutes'], 4320)
        self.assertTrue(all(row['windows'][str(h)] is None for h in (1, 6, 24)))

    def test_sparse_six_hour_match_is_labeled_with_actual_duration(self):
        self.snapshot(-310, {1: 1000})
        self.snapshot(0, {1: 1500})
        data = self.result()
        metric = data['games'][0]['windows']['6']
        self.assertEqual(metric['pct'], 50)
        self.assertTrue(metric['approximate'])
        self.assertEqual(metric['elapsed_hours'], 5.17)
        self.assertEqual(data['window_coverage']['1']['reason'], 'collection_gap')
        self.assertEqual(data['window_coverage']['24']['reason'], 'not_enough_history')

    def test_six_hour_match_still_rejects_unrelated_time(self):
        self.snapshot(-200, {1: 1000})
        self.snapshot(0, {1: 1500})
        self.assertIsNone(self.result()['games'][0]['windows']['6'])

    def test_multiday_growth_and_peak_use_recorded_history(self):
        self.snapshot(-168 * 60, {1: 1000})
        self.snapshot(-72 * 60, {1: 2000})
        self.snapshot(-48 * 60, {1: 2500})
        self.snapshot(-24 * 60, {1: 5000})
        self.snapshot(0, {1: 3000})
        row = self.result()['games'][0]
        self.assertEqual(row['windows']['48']['pct'], 20)
        self.assertEqual(row['windows']['72']['pct'], 50)
        weekly = row['windows']['168']
        self.assertEqual(weekly['pct'], 200)
        self.assertEqual(weekly['observed_peak'], 5000)
        self.assertEqual(weekly['below_peak_pct'], 40)
        self.assertEqual(weekly['observation_count'], 5)

    def test_weekly_window_does_not_use_three_day_history(self):
        self.snapshot(-72 * 60, {1: 1000})
        self.snapshot(0, {1: 3000})
        data = self.result()
        self.assertIsNone(data['games'][0]['windows']['168'])
        self.assertEqual(data['window_coverage']['168']['reason'], 'not_enough_history')


if __name__ == "__main__":
    unittest.main()
