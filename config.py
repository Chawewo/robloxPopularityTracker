"""Tracker settings. All timestamps and comparison windows use UTC."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT = ROOT / "dashboard" / "data.json"
HISTORY_OUTPUT = ROOT / "dashboard" / "history.json"
BACKTEST_OUTPUT = ROOT / "dashboard" / "backtest.json"
MODEL_OUTPUT = ROOT / "dashboard" / "model.json"
STATUS_OUTPUT = ROOT / "dashboard" / "status.json"
ENDPOINT = "https://api.rolimons.com/games/v1/gamelist"
MIN_PLAYERS = 1_000  # "Traction" floor; the dashboard's player filter can go down to COLLECT_FLOOR.
# Games below this are not stored. Change it here, or set a COLLECT_FLOOR repository variable
# in GitHub (Settings, Secrets and variables, Actions, Variables), which overrides this value.
# History keeps each day's floor, so changing it never distorts past comparisons.
DEFAULT_COLLECT_FLOOR = 100
MIN_COLLECT_FLOOR = 10  # Below this the list is mostly dormant games and storage grows fast.
# Young games keep this lower floor: a new game's 16+ stage can happen well under 100 players.
YOUNG_FLOOR = 25
YOUNG_DAYS = 120
RECENT_PLACE_ID = 70_000_000_000_000  # Every game created in 2026 so far has an id above this.


def collect_floor(value=None):
    """Read the floor, falling back to the default rather than stopping collection on a typo."""
    text = str(os.environ.get("COLLECT_FLOOR", "") if value is None else value).strip()
    return max(int(text), MIN_COLLECT_FLOOR) if text.isdigit() else DEFAULT_COLLECT_FLOOR


COLLECT_FLOOR = collect_floor()
WINDOWS = (1, 6, 24, 48, 72, 168)
TOLERANCE_MINUTES = 20
WINDOW_TOLERANCE_MINUTES = {1: 20, 6: 90, 24: 180, 48: 180, 72: 180, 168: 180}
RETENTION_DAYS = 30  # Raw snapshots only. Daily peaks in data/daily are kept permanently.
TOP_N = 100  # Display limit per lens; JSON retains all eligible games for sorting.
STALE_MINUTES = 45
SNAPSHOT_FILE_BYTES = 40 * 1024 * 1024
PUBLISHED_HISTORY_DAYS = 365  # Daily peaks sent to the dashboard for any-length comparisons.

# Roblox enrichment (release dates, genres, engagement). Best effort; see enrich.py.
ENRICH_BATCH = 50  # Roblox rejects larger universeIds lists on /v1/games.
ENRICH_REQUEST_DELAY = 0.15  # Seconds between /v1/games requests.
ENRICH_RESOLVE_DELAY = 1.1  # The place-to-universe endpoint allows 60 requests a minute.
ENRICH_RESOLVE_BUDGET = 45  # New lookups per run; cached forever, so this only slows a backlog.
ENRICH_REFRESH_HOURS = 6
ENRICH_BADGE_BUDGET = 60  # Badge lookups per run, at most once a day per game.
ENRICH_BADGE_DELAY = 0.65  # The badge endpoint allows 100 requests a minute.
ENRICH_GROUP_BUDGET = 6  # The group endpoint allows only 7 requests a minute.
CHART_PAGES = 6
# Roblox's Explore lists differ by device and country, so each scan finds different games.
# Regional hits matter: one of the biggest breakouts this autumn was an Arabic-language game.
DISCOVERY_SCANS = (("computer", "all"), ("high_end_phone", "all"), ("low_end_phone", "all"), ("console", "all"),
                   ("high_end_phone", "br"), ("high_end_phone", "id"), ("high_end_phone", "sa"))
DISCOVERY_DELAY = 0.25  # Seconds between Explore requests.
WATCH_DELAY = 0.4  # Seconds between player-count requests for games Rolimons does not list.
WATCH_DAYS = 30  # Stop reading an unlisted game after this long without activity.
WATCH_MAX = 2_000  # 40 requests a run at most.
ICON_BUDGET = 200
UP_AND_COMING_DAYS = 28  # Roblox describes this chart as experiences published in the past 28 days.
