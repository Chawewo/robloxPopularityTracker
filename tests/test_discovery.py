import json
import math
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError

import backtest
import charts
import config
import discover
import model
from analyze import build, public_window
from collect import find_unlisted, read_csv, save_snapshot, utc_text, young_games
from signals import fill_unseen, market_index, score, step_up


def sort(sort_id, *games):
    return dict(sortId=sort_id, games=[dict(rootPlaceId=game_id, universeId=game_id * 10, name=f"Game {game_id}",
                                            playerCount=players, contentMaturity=maturity)
                                       for game_id, players, maturity in games])


def api(page, live=None, created="2026-08-01T00:00:00.000Z"):
    """A fake Roblox: `page` is one Explore response, `live` maps universe ids to player counts."""
    def fetch(url):
        if "get-sorts" in url:
            return dict(sorts=page)
        if "/v1/games?" in url:
            return dict(data=[dict(id=int(uid), name=f"Live {uid}", playing=count, visits=500, created=created)
                              for uid, count in (live or {}).items()])
        return dict(data=[])
    return fetch


class Workspace(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.now = datetime(2026, 11, 7, 12, tzinfo=timezone.utc)

    def run_discovery(self, listed, page, live=None, created="2026-08-01T00:00:00.000Z", now=None):
        return discover.discover(listed, now or self.now, self.root, api(page, live, created), lambda _: None)

    def rows(self):
        return {row["game_id"]: row for row in read_csv(self.root / "discovered.csv")}


class DiscoveryTests(Workspace):
    def test_a_game_missing_from_rolimons_gets_its_live_count_from_roblox(self):
        found = self.run_discovery({"1": []}, [sort("top-trending", (1, 900, "mild"), (2, 800, "mild"))], {20: 5000})
        self.assertEqual(found, {"2": ["Live 20", 5000, ""]})

    def test_a_listed_game_is_never_read_twice(self):
        self.assertEqual(self.run_discovery({"1": []}, [sort("top-trending", (1, 900, "mild"))], {10: 900}), {})
        self.assertEqual(self.rows()["1"]["listed"], "2026-11-07")

    def test_a_game_stays_watched_after_leaving_every_list(self):
        self.run_discovery({}, [sort("top-trending", (2, 800, "mild"))], {20: 800})
        later = self.now + timedelta(days=3)
        found = self.run_discovery({}, [sort("top-trending", (3, 700, "mild"))], {20: 650, 30: 700}, now=later)
        self.assertEqual(found["2"][1], 650)

    def test_a_quiet_game_is_dropped_after_the_watch_period(self):
        self.run_discovery({}, [sort("top-trending", (2, 800, "mild"))], {20: 800})
        later = self.now + timedelta(days=config.WATCH_DAYS + 2)
        self.assertNotIn("2", self.run_discovery({}, [sort("top-trending", (3, 700, "mild"))], {20: 800, 30: 700}, now=later))

    def test_young_games_keep_a_lower_floor_than_old_ones(self):
        page = [sort("top-trending", (2, 40, "mild"))]
        self.assertEqual(self.run_discovery({}, page, {20: 40}, created="2020-01-01T00:00:00.000Z"), {})
        self.assertIn("2", self.run_discovery({}, page, {20: 40}, created="2026-10-01T00:00:00.000Z"))

    def test_first_dates_and_maturity_changes_are_recorded_once(self):
        self.run_discovery({}, [sort("top-trending", (2, 800, "mild"))], {20: 800})
        later = self.now + timedelta(days=1)
        self.run_discovery({}, [sort("up-and-coming", (2, 800, "minimal"))], {20: 800}, now=later)
        row = self.rows()["2"]
        self.assertEqual((row["first_chart"], row["first_up_and_coming"]), ("2026-11-07", "2026-11-08"))
        self.assertEqual(read_csv(self.root / "maturity_changes.csv")[0]["new"], "minimal")

    def test_coverage_counts_what_rolimons_is_missing(self):
        self.run_discovery({"1": []}, [sort("top-trending", (1, 6000, "mild"), (2, 7000, "mild"), (3, 600, "mild"))], {20: 7000, 30: 600})
        row = read_csv(self.root / "coverage.csv")[0]
        self.assertEqual((row["charted_5k"], row["missing_5k"], row["charted_500"], row["missing_500"]), ("2", "1", "1", "1"))

    def test_a_rate_limit_falls_back_to_the_counts_on_the_list(self):
        def fetch(url):
            if "get-sorts" in url:
                return dict(sorts=[sort("top-trending", (2, 800, "mild"))])
            raise HTTPError(url, 429, "busy", {}, None)
        self.assertEqual(discover.discover({}, self.now, self.root, fetch, lambda _: None)["2"][1], 800)

    def test_a_scan_failure_keeps_what_already_arrived(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if len(calls) > 1:
                raise HTTPError(url, 429, "busy", {}, None)
            return dict(sorts=[sort("top-trending", (2, 800, "mild"))])
        self.assertIn("2", charts.scan(fetch, lambda _: None)[1])

    def test_losing_roblox_does_not_stop_the_collection(self):
        def broken(*_):
            raise RuntimeError("Roblox changed")
        original, discover.discover = discover.discover, broken
        self.addCleanup(setattr, discover, "discover", original)
        self.assertEqual(find_unlisted({"games": {"1": ["A", 5, ""]}}, self.now), {})


class SnapshotTests(Workspace):
    def payload(self, **games):
        return {"success": True, "games": {key.strip("g"): [key, count, "https://icon/x"] for key, count in games.items()}}

    def details(self, game_id, created):
        path = self.root / "details.csv"
        path.write_text(f"game_id,created\n{game_id},{created}\n", encoding="utf-8")

    def test_a_young_game_is_stored_under_the_floor_and_an_old_one_is_not(self):
        self.details(70000000000002, "2026-10-01T00:00:00Z")
        save_snapshot({"games": {"70000000000002": ["Young", 30, ""], "3": ["Old", 30, ""]}}, self.now, self.root)
        stored = [row["game_id"] for row in read_csv(self.root / "snapshots" / "2026-11.csv")]
        self.assertEqual(stored, ["70000000000002"])

    def test_unknown_small_games_with_recent_ids_join_the_lookup_queue(self):
        games = {str(config.RECENT_PLACE_ID + 1): ["New", 60, ""], str(config.RECENT_PLACE_ID + 2): ["Tiny", 3, ""], "5": ["Classic", 60, ""]}
        save_snapshot({"games": games}, self.now, self.root)
        self.assertEqual([row["game_id"] for row in read_csv(self.root / "below_floor.csv")], [str(config.RECENT_PLACE_ID + 1)])

    def test_found_games_join_the_snapshot_and_the_manifest_counts_them(self):
        save_snapshot({"games": {"1": ["Listed", 900, "https://icon/1"]}}, self.now, self.root, extra={"2": ["Found", 800, ""]})
        self.assertEqual(sorted(row["game_id"] for row in read_csv(self.root / "snapshots" / "2026-11.csv")), ["1", "2"])
        manifest = read_csv(self.root / "collections.csv")[0]
        self.assertEqual((manifest["games"], manifest["discovered"]), ("2", "1"))

    def test_gaining_an_icon_is_not_logged_as_a_change(self):
        save_snapshot({"games": {"1": ["Listed", 900, "https://icon/1"]}}, self.now, self.root, extra={"2": ["Found", 800, ""]})
        save_snapshot({"games": {"1": ["Listed", 900, "https://icon/1"], "2": ["Found", 800, "https://icon/2"]}},
                      self.now + timedelta(minutes=15), self.root)
        self.assertFalse((self.root / "changes.csv").exists())

    def test_young_games_ignore_rows_without_a_date(self):
        self.details(7, "")
        self.assertEqual(young_games(self.root, self.now)[0], set())

    def test_the_first_discovery_batch_is_not_marked_new(self):
        save_snapshot({"games": {"1": ["Listed", 900, ""]}}, self.now - timedelta(minutes=15), self.root)
        save_snapshot({"games": {"1": ["Listed", 900, ""]}}, self.now, self.root, extra={"2": ["Found", 900, ""]})
        (self.root / "discovered.csv").write_text("game_id,universe_id,name,first_chart,listed,first_up_and_coming,maturity,visits_at_first\n"
                                                  "2,20,Found,2026-11-07,,,mild,900\n", encoding="utf-8")
        rows = {row["game_id"]: row for row in build(self.root, self.now)[0]["games"]}
        self.assertFalse(rows["2"]["new_entrant"])
        self.assertTrue(rows["2"]["unlisted"])
        self.assertEqual((rows["2"]["visits_at_first"], rows["2"]["public_by"]), (900, "2026-11-07"))

    def test_a_game_found_later_is_marked_new(self):
        save_snapshot({"games": {"1": ["Listed", 900, ""]}}, self.now - timedelta(minutes=30), self.root, extra={"2": ["Found", 900, ""]})
        save_snapshot({"games": {"1": ["Listed", 900, ""]}}, self.now, self.root, extra={"3": ["Later", 900, ""]})
        rows = {row["game_id"]: row for row in build(self.root, self.now)[0]["games"]}
        self.assertTrue(rows["3"]["new_entrant"])


class LateFindTests(Workspace):
    def test_time_before_the_first_reading_is_held_flat(self):
        self.assertEqual(fill_unseen([None, None, 40, None, 50]), [40, 40, 40, None, 50])

    def test_a_late_find_is_not_scored_as_a_spike(self):
        peaks = [None] * 7 + [40_000]
        self.assertTrue(score(peaks, 7, [0.0] * 8, [500] * 8)["spike"])
        late = score(fill_unseen(peaks), 7, [0.0] * 8, [500] * 8)
        self.assertEqual((late["spike"], late["score"]), (False, 0))

    def test_a_late_find_has_no_baseline_and_a_listed_game_keeps_its_floor(self):
        save_snapshot({"games": {"1": ["Listed", 900, ""]}}, self.now, self.root, extra={"2": ["Found", 900, ""]})
        (self.root / "discovered.csv").write_text("game_id,universe_id,name,first_chart,listed\n"
                                                  "1,10,Listed,2026-11-07,2026-11-07\n2,20,Found,2026-11-07,\n", encoding="utf-8")
        rows = {row["game_id"]: row for row in build(self.root, self.now)[0]["games"]}
        self.assertEqual(rows["2"]["known_from"], "2026-11-07")
        self.assertIsNone(rows["1"]["known_from"])


class PublicWindowTests(unittest.TestCase):
    def test_the_first_sighting_anywhere_is_the_latest_it_can_have_gone_public(self):
        self.assertEqual(public_window(dict(first_chart="2026-11-07"), "2026-11-09T01:00:00Z"), (None, "2026-11-07"))

    def test_up_and_coming_gives_an_earliest_date(self):
        window = public_window(dict(first_chart="2026-11-07", first_up_and_coming="2026-11-29"), "2026-11-20T00:00:00Z")
        self.assertEqual(window, ("2026-11-01", "2026-11-07"))


class StepUpTests(unittest.TestCase):
    def test_a_lasting_jump_counts_and_a_one_day_spike_does_not(self):
        base = [100, 110, 90, 100, 105, 95, 100]
        self.assertEqual(step_up(base + [400, 450], 8), 4.0)
        self.assertIsNone(step_up(base + [400, 120], 8))

    def test_a_game_with_no_earlier_readings_is_not_a_jump(self):
        self.assertIsNone(step_up([None] * 7 + [5000, 6000], 8))

    def test_later_days_do_not_change_the_answer(self):
        base = [100] * 7 + [400, 450]
        self.assertEqual(step_up(base, 8), step_up(base + [1, 1], 8))


class ModelFeatureTests(unittest.TestCase):
    peaks = [100, 110, 120, None, 150, 160, 170, 200, 240, 300, 2000, 4000]
    market = [0.0] * 12
    detail = dict(created="2026-10-01T00:00:00Z", genre="RPG", verified="1", creator_type="Group", max_players="30")

    def row(self, peaks, day=9):
        from datetime import date
        return model.features(peaks, day, self.market, [100] * 12, self.detail, {"RPG": 3}, True, date(2026, 11, 1))

    @staticmethod
    def same(first, second):
        return all(first[key] == second[key] or (math.isnan(first[key]) and math.isnan(second[key])) for key in first)

    def test_features_never_use_days_after_the_one_they_describe(self):
        self.assertTrue(self.same(self.row(self.peaks), self.row(self.peaks[:10] + [9, 9])))

    def test_every_model_feature_is_built(self):
        self.assertEqual(set(self.row(self.peaks)), set(model.FEATURES))

    def test_values_describe_the_game(self):
        row = self.row(self.peaks)
        self.assertEqual((row["age"], row["young"], row["verified"], row["genre"], row["in_wave"]), (31, 1, 1, 3, 1))
        self.assertAlmostEqual(row["log_level"], math.log(240))

    def test_a_game_without_a_date_still_gets_features(self):
        from datetime import date
        row = model.features(self.peaks, 9, self.market, [100] * 12, {}, {}, False, date(2026, 11, 1))
        self.assertTrue(math.isnan(row["age"]))


class ModelGradingTests(Workspace):
    def write(self, rows):
        path = self.root / "model" / "picks" / "2026-10.csv"
        path.parent.mkdir(parents=True)
        path.write_text("date,game_id,score,double,triple,raw\n" + "".join(f"{row}\n" for row in rows), encoding="utf-8")

    def test_only_finished_days_are_graded_and_winners_count(self):
        dates = [(datetime(2026, 10, 1) + timedelta(days=day)).date().isoformat() for day in range(20)]
        series = {"1": [1000] * 5 + [5000] * 15, "2": [1000] * 20}
        self.write(["2026-10-03,1,0,50,10,0.5", "2026-10-03,2,90,5,1,0.05", "2026-10-18,1,0,50,10,0.5"])
        rows = model.graded_rows(self.root, dates, series)
        self.assertEqual([row["day"] for row in rows], [2, 2])
        report = model.grade(rows)
        self.assertEqual(report["days"], 1)
        self.assertEqual(report["model"]["double"], 50.0)

    def test_no_picks_yet_grades_nothing(self):
        report = model.grade(model.graded_rows(self.root, [], {}))
        self.assertEqual(report["days"], 0)
        self.assertIsNone(report["model"]["double"])

    def test_a_new_day_is_due_and_the_same_day_is_not(self):
        (self.root / "daily_index.csv").write_text("date,snapshots,floor\n2026-11-05,10,100\n", encoding="utf-8")
        output = self.root / "model.json"
        self.assertTrue(model.due(self.root, output))
        output.write_text(json.dumps(dict(through="2026-11-05")), encoding="utf-8")
        self.assertFalse(model.due(self.root, output))


class BlindSpotTests(unittest.TestCase):
    def test_a_game_first_seen_huge_counts_as_arriving_too_late(self):
        series = {"late": [None] * 8 + [9000] * 4, "early": [None] * 6 + [600, 900, 1500, 6000, 7000, 8000],
                  "small": [None] * 8 + [700] * 4, "old": [9000] * 12}
        self.assertEqual(backtest.late_arrivals(series, [500] * 12), dict(games=2, seen_early=1))

    def test_catch_counts_only_picks_made_before_the_win(self):
        rows = [dict(game_id="a", day=1, score=9, outcome=dict(five_x=True)), dict(game_id="b", day=1, score=1, outcome=dict(five_x=True))]
        original, backtest.TOP_K = backtest.TOP_K, 1
        self.addCleanup(setattr, backtest, "TOP_K", original)
        self.assertEqual(backtest.caught(rows, "five_x", "score")["games"], 2)


class FloorStaysConsistent(unittest.TestCase):
    def test_market_ignores_small_games(self):
        series = [[30, 300], [2000, 2000]]
        self.assertEqual(market_index(series, 2), [0.0, 0.0])

    def test_utc_text_is_used_for_discovery_dates(self):
        self.assertEqual(utc_text(datetime(2026, 11, 7, tzinfo=timezone.utc))[:10], "2026-11-07")


if __name__ == "__main__":
    unittest.main()
