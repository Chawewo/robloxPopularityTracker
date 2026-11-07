import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import backtest
import charts
import clones
import config
import enrich
from analyze import build
from collect import prune, read_csv, save_snapshot
from rollup import read_floors, rollup
from signals import level, score


class Workspace(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.now = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)

    def snapshot(self, days_ago, counts, names=None, floor=None, icon="https://example.com/i.webp"):
        stamp = self.now - timedelta(days=days_ago)
        games = {str(key): [(names or {}).get(key, f"Game {key}"), count, icon] for key, count in counts.items()}
        with patch.object(config, "COLLECT_FLOOR", floor or config.COLLECT_FLOOR):
            save_snapshot({"success": True, "games": games or {"99": ["Small", 1, ""]}}, stamp, self.root)


class FloorTests(Workspace):
    def test_floor_setting_accepts_numbers_and_survives_typos(self):
        self.assertEqual(config.collect_floor("250"), 250)
        self.assertEqual(config.collect_floor(" 75 "), 75)
        self.assertEqual(config.collect_floor("abc"), config.DEFAULT_COLLECT_FLOOR)
        self.assertEqual(config.collect_floor(""), config.DEFAULT_COLLECT_FLOOR)
        self.assertEqual(config.collect_floor("-5"), config.DEFAULT_COLLECT_FLOOR)
        self.assertEqual(config.collect_floor("1"), config.MIN_COLLECT_FLOOR)

    def test_floor_decides_what_is_stored_and_is_recorded(self):
        self.snapshot(0, {1: 300, 2: 150}, floor=200)
        rows = read_csv(self.root / "collections.csv")
        self.assertEqual((rows[0]["games"], rows[0]["floor"], rows[0]["total_players"]), ("1", "200", "450"))

    def test_each_day_keeps_the_floor_it_was_collected_under(self):
        self.snapshot(2, {1: 1000}, floor=500)
        self.snapshot(1, {1: 1000}, floor=100)
        self.snapshot(0, {1: 1000}, floor=100)
        rollup(self.root)
        self.assertEqual(read_floors(self.root), [500, 100])

    def test_old_rows_without_a_floor_read_as_the_legacy_floor(self):
        (self.root / "daily_index.csv").write_text("date,snapshots\n2026-09-01,10\n")
        self.assertEqual(read_floors(self.root), [500])

    def test_an_old_absent_day_stays_under_the_old_floor(self):
        self.assertEqual(level([None], 0, floors=[500]), 400)
        self.assertEqual(level([None], 0, floors=[100]), 80)

    def test_lowering_the_floor_does_not_invent_new_games_or_gains(self):
        self.snapshot(9, {1: 2000}, floor=500)
        self.snapshot(8, {1: 2000}, floor=500)
        self.snapshot(1, {1: 2000, 2: 300}, floor=100)
        self.snapshot(0, {1: 2000, 2: 310}, floor=100)
        rollup(self.root)
        data, history = build(self.root, self.now)
        row = next(game for game in data["games"] if game["game_id"] == "2")
        self.assertFalse(row["new_entrant"])
        self.assertEqual(history["floors"][0], 500)
        self.assertEqual(history["floors"][-1], 100)
        self.assertEqual(data["collect_floor"], 100)

    def test_a_game_appearing_after_the_change_is_still_new(self):
        self.snapshot(3, {1: 2000}, floor=500)
        self.snapshot(2, {1: 2000, 2: 300}, floor=100)
        self.snapshot(0.1, {1: 2000, 2: 300, 3: 300}, floor=100)
        rows = {row["game_id"]: row for row in build(self.root, self.now)[0]["games"]}
        self.assertTrue(rows["3"]["new_entrant"])
        self.assertFalse(rows["2"]["new_entrant"])


class PruneTests(Workspace):
    def test_live_partition_is_never_rewritten_and_expired_ones_are_deleted(self):
        self.snapshot(70, {1: 1000})
        self.snapshot(31, {1: 1000})
        self.snapshot(5, {1: 1000})
        partitions = sorted((self.root / "snapshots").glob("*.csv"))
        before = {path.name: path.read_bytes() for path in partitions}
        prune(self.now, self.root)
        remaining = {path.name: path.read_bytes() for path in (self.root / "snapshots").glob("*.csv")}
        self.assertNotIn("2026-07.csv", remaining)
        self.assertEqual(remaining["2026-09.csv"], before["2026-09.csv"])

    def test_expired_rows_in_a_live_partition_do_not_reach_analysis(self):
        self.snapshot(31, {1: 90000})
        self.snapshot(5, {1: 1000})
        self.snapshot(0, {1: 1100})
        prune(self.now, self.root)
        self.assertEqual(build(self.root, self.now)[0]["snapshot_count"], 2)


class ChangeLogTests(Workspace):
    def test_title_and_icon_changes_are_logged(self):
        self.snapshot(1, {1: 1000}, {1: "Old name"})
        self.snapshot(0, {1: 1000}, {1: "New name"}, icon="https://example.com/other.webp")
        rows = read_csv(self.root / "changes.csv")
        self.assertEqual([(row["field"], row["old"], row["new"]) for row in rows],
                         [("name", "Old name", "New name"), ("icon", "", "")])


def game(game_id, name, age, creator, players=1000, before=None):
    return dict(game_id=str(game_id), name=name, age_days=age, creator=creator, level=players, level_before=before or players)


class CloneTests(unittest.TestCase):
    def wave(self):
        recent = [game(i, title, 30, f"Studio {i}", 2000, 1000) for i, title in enumerate(
            ("Steal An Egg", "Pull An Egg", "Lift Rock For Eggs", "Fake an Egg", "[UPDATE] Egg Tycoon"))]
        older = [game(100 + i, f"Old Title {i}", 900, f"Veteran {i}") for i in range(40)]
        return recent + older

    def test_tags_emoji_and_plurals_are_ignored(self):
        self.assertEqual(clones.tokens("[🥚 UPDATE] Steal Eggs! (Beta)"), ["steal", "egg"])

    def test_a_spreading_idea_becomes_a_family_with_a_leader(self):
        games = self.wave()
        games[0]["level"] = 90000
        families, assigned = clones.find_families(games)
        family = families["egg"]
        self.assertEqual((family["recent"], family["creators"], family["leader"]), (5, 5, "0"))
        self.assertEqual(assigned["1"], "egg")
        self.assertEqual(clones.role(games[0], family), "leader")
        self.assertEqual(clones.role(games[1], family), "clone")
        self.assertEqual(family["median_growth_pct"], 100.0)

    def test_one_creator_repeating_a_word_is_not_a_wave(self):
        games = [game(i, f"Egg Game {i}", 30, "Same Studio") for i in range(6)]
        games += [game(100 + i, f"Old Title {i}", 900, f"V{i}") for i in range(40)]
        self.assertEqual(clones.find_families(games), ({}, {}))

    def test_a_word_common_in_old_games_is_not_a_wave(self):
        games = [game(i, f"Tower {i}", 30, f"S{i}") for i in range(6)]
        games += [game(100 + i, f"Tower Old {i}", 900, f"V{i}") for i in range(40)]
        self.assertEqual(clones.find_families(games)[0], {})

    def test_variants_of_one_wave_merge(self):
        games = self.wave() + [game(50, "Steal A Clown Egg", 30, "Studio 50")]
        families, assigned = clones.find_families(games)
        self.assertIn("egg", families)
        self.assertNotIn("steal egg", families)

    def test_games_without_a_release_date_are_skipped(self):
        games = self.wave() + [dict(game_id="77", name="Egg Hunt", age_days=None, creator="X", level=1, level_before=1)]
        self.assertNotIn("77", clones.find_families(games)[1])


class ScoreBonusTests(unittest.TestCase):
    climbing = [1000, 1000, 1000, 1000, 1100, 1250, 1400, 1600]
    market = [0.0] * 8

    def points(self, **options):
        return score(self.climbing, 7, self.market, **options)["score"]


class ChartTests(Workspace):
    def fake(self, url):
        phone = "high_end_phone" in url
        games = [dict(rootPlaceId=9, universeId=90, isSponsored=True), dict(rootPlaceId=1, universeId=10), dict(rootPlaceId=2, universeId=20)]
        if phone:
            games.reverse()
        return dict(sorts=[dict(sortId="up-and-coming", games=games), dict(sortId="ignored", games=games)])

    def test_sponsored_slots_are_skipped_and_best_device_rank_wins(self):
        positions = charts.fetch_positions(self.fake, lambda _: None)
        self.assertNotIn(("up-and-coming", "9"), positions)
        self.assertEqual(positions[("up-and-coming", "1")][0], 1)
        self.assertEqual(positions[("up-and-coming", "2")][0], 1)
        self.assertFalse(any(key[0] == "ignored" for key in positions))

    def test_pages_are_followed_until_the_token_runs_out(self):
        calls = []

        def fetch(url):
            calls.append(url)
            return dict(sorts=[], nextSortsPageToken="more" if "sortsPageToken" not in url else None)
        charts.fetch_positions(fetch, lambda _: None)
        self.assertEqual(len(calls), 2 * len(config.DISCOVERY_SCANS))
        self.assertIn("sortsPageToken=more", calls[1])

    def test_repeat_runs_keep_the_best_rank_and_count_samples(self):
        charts.record(self.now, self.root, lambda url: dict(sorts=[dict(sortId="up-and-coming", games=[dict(rootPlaceId=1, universeId=10)])]))
        crowded = [dict(rootPlaceId=5, universeId=50), dict(rootPlaceId=1, universeId=10)]
        charts.record(self.now, self.root, lambda url: dict(sorts=[dict(sortId="up-and-coming", games=crowded)]))
        row = next(row for row in read_csv(self.root / "charts" / "2026-09-10.csv") if row["game_id"] == "1")
        self.assertEqual((row["best_rank"], row["last_rank"], row["samples"]), ("1", "2", "2"))

    def test_an_empty_response_writes_nothing(self):
        self.assertEqual(charts.record(self.now, self.root, lambda url: None), {})
        self.assertFalse((self.root / "charts").exists())

    def test_rows_show_chart_ranks_and_days(self):
        self.snapshot(0, {1: 1500})
        for day in (2, 1, 0):
            charts.record(self.now - timedelta(days=day), self.root, self.fake)
        row = build(self.root, self.now)[0]["games"][0]
        self.assertEqual(row["charts"], {"up-and-coming": 1})
        self.assertEqual(row["up_and_coming_days"], 3)


class CommunityTests(Workspace):
    def setUp(self):
        super().setUp()
        self.snapshot(0, {1: 1500, 2: 1500, 3: 1500})
        (self.root / "universes.csv").write_text("game_id,universe_id\n1,10\n2,20\n3,30\n")
        self.calls = []

    def fetch(self, url):
        self.calls.append(url)
        return dict(data=[dict(statistics=dict(pastDayAwardedCount=40)), dict(statistics=dict(pastDayAwardedCount=900))])

    def test_badges_take_the_most_awarded_and_sample_once_a_day(self):
        self.assertEqual(enrich.sample_badges(self.now, self.root, self.fetch, lambda _: None), 3)
        self.assertEqual(enrich.sample_badges(self.now, self.root, self.fetch, lambda _: None), 0)
        rows = read_csv(self.root / "badges" / "2026-09.csv")
        self.assertEqual({row["daily_awards"] for row in rows}, {"900"})
        self.assertEqual(len(self.calls), 3)

    def test_badge_rate_limit_keeps_what_it_has(self):
        def limited(url):
            if "/20/" in url:
                raise HTTPError(url, 429, "Too Many", None, None)
            return self.fetch(url)
        self.assertEqual(enrich.sample_badges(self.now, self.root, limited, lambda _: None), 1)

    def test_budget_is_respected(self):
        with patch.object(config, "ENRICH_BADGE_BUDGET", 2):
            self.assertEqual(enrich.sample_badges(self.now, self.root, self.fetch, lambda _: None), 2)

    def test_groups_cover_only_group_owned_listed_games(self):
        header = ",".join(enrich.DETAIL_FIELDS)
        (self.root / "details.csv").write_text(
            header + "\n1,10,2026-08-01T00:00:00Z,,,,A,Group,0,,,,,,2026-09-10T00:00:00Z,555\n"
            "2,20,2026-08-01T00:00:00Z,,,,B,User,0,,,,,,2026-09-10T00:00:00Z,777\n")
        calls = []

        def fetch(url):
            calls.append(url)
            return dict(memberCount=1234)
        self.assertEqual(enrich.sample_groups(self.now, self.root, fetch), 1)
        self.assertTrue(calls[0].endswith("/555"))

    def test_published_updates_are_logged_only_when_the_date_changes(self):
        (self.root / "details.csv").write_text(",".join(enrich.DETAIL_FIELDS) + "\n1,10,2026-08-01T00:00:00Z,2026-09-01T00:00:00Z,,,A,Group,0,,1,1,1,1,2026-09-09T00:00:00Z,555\n")

        def fetch(url):
            if "votes" in url:
                return dict(data=[])
            if "/v1/games?" in url:
                return dict(data=[dict(id=10, created="2026-08-01T00:00:00Z", updated="2026-09-09T00:00:00Z")])
            return dict(sorts=[])
        enrich.enrich(self.now, self.root, fetch, lambda _: None)
        self.assertEqual(read_csv(self.root / "updates.csv"), [dict(game_id="1", updated="2026-09-09T00:00:00Z")])


class SignalLabTests(unittest.TestCase):
    def test_lab_ranks_signals_with_enough_samples_first(self):
        hit = dict(double=True, triple=False, five_x=False)
        miss = dict(double=False, triple=False, five_x=False)
        rows = [dict(game_id=str(i), day=0, outcome=hit if i < 200 else miss, signals=["Strong"] if i < 200 else []) for i in range(1000)]
        rows += [dict(game_id="x", day=0, outcome=hit, signals=["Tiny"])]
        lab = backtest.signal_lab(rows, ["Tiny", "Strong"])
        self.assertEqual([item["name"] for item in lab], ["Strong", "Tiny"])
        self.assertTrue(lab[0]["enough"])
        self.assertFalse(lab[1]["enough"])
        self.assertAlmostEqual(lab[0]["lift"], 100 / 20.08, places=1)


if __name__ == "__main__":
    unittest.main()
