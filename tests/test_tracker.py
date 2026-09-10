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
        self.assertEqual(data["games"][0]["windows"], {"1": None, "6": None, "24": None})

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


if __name__ == "__main__":
    unittest.main()
