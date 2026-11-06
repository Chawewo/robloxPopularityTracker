"""Find clone waves from game titles.

Roblox trends spread by copying: one hit, then a run of new games sharing its idea and
wording. Nothing here is a hand-kept list. A phrase becomes a family when it is unusually
common among recent releases compared with older games, and several creators are using it.
"""
import re
import unicodedata
from collections import defaultdict
from statistics import median

YOUNG_DAYS = 120  # A game this new counts as part of the current wave.
MIN_YOUNG_GAMES = 4
MIN_CREATORS = 3
MIN_LIFT = 3.0
STOP_WORDS = frozenset("""a an the of in on and to for with your my you me is it at by be or vs from all
    new update updated upd updates event events free op beta alpha testing test release released season
    game games roblox rbx official classic original edition version remastered rewritten v1 v2 v3 part
    get become make be do can will are this that how who why what just big best super mega ultra
    player players team teams world worlds rank ranked
    """.split())
TAGS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|【[^】]*】|\|.*$")


def stem(word):
    if len(word) > 3 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokens(title):
    """Meaningful words from a title, without tags such as [UPDATE] and without emoji."""
    text = unicodedata.normalize("NFKD", TAGS.sub(" ", title))
    text = "".join(char if char.isascii() else " " for char in text).lower()
    words = re.findall(r"[a-z][a-z0-9']*", text)
    return [stem(word.replace("'", "")) for word in words if word not in STOP_WORDS and len(word) > 2]


def phrases(title):
    """Single words and adjacent pairs. Pairs catch ideas like 'steal egg' or 'grow plant'."""
    words = tokens(title)
    return set(words) | {f"{first} {second}" for first, second in zip(words, words[1:])}


def find_families(games):
    """Detect clone waves.

    Each game is a dict with game_id, name, age_days, creator, level and level_before, where
    level_before is its level a week earlier. Games with unknown age are skipped.
    Returns (families by phrase, the family of each game id).
    """
    known = [game for game in games if game.get("age_days") is not None]
    young = [game for game in known if game["age_days"] <= YOUNG_DAYS]
    older = len(known) - len(young)
    if not young or not older:
        return {}, {}
    members = defaultdict(list)
    for game in known:
        for phrase in phrases(game["name"]):
            members[phrase].append(game)
    families = {}
    for phrase, group in members.items():
        recent = [game for game in group if game["age_days"] <= YOUNG_DAYS]
        creators = {game.get("creator") or game["game_id"] for game in recent}
        if len(recent) < MIN_YOUNG_GAMES or len(creators) < MIN_CREATORS:
            continue
        lift = (len(recent) / len(young)) / ((len(group) - len(recent) + 1) / older)
        if lift < MIN_LIFT:
            continue
        leader = max(group, key=lambda game: game["level"])
        changes = [game["level"] / game["level_before"] for game in group if game.get("level_before")]
        total = sum(game["level"] for game in group)
        before = sum(game.get("level_before") or 0 for game in group)
        families[phrase] = dict(phrase=phrase, lift=round(lift, 1), games=len(group), recent=len(recent),
            creators=len(creators), leader=leader["game_id"], leader_name=leader["name"],
            leader_level=round(leader["level"]), total=round(total), before=round(before),
            growth_pct=round((total - before) / before * 100, 1) if before else None,
            median_growth_pct=round((median(changes) - 1) * 100, 1) if changes else None)
    return dedupe(families, members)


def dedupe(families, members):
    """Give each game one family, starting from the widest wave so its variants stay together."""
    ranked = sorted(families.values(), key=lambda family: (-family["recent"], -family["lift"], family["phrase"]))
    assigned = {}
    kept = {}
    for family in ranked:
        fresh = [game for game in members[family["phrase"]] if game["game_id"] not in assigned]
        share = len(fresh) / family["games"]
        # A phrase mostly covering games already placed is the same wave under another name.
        if share < 0.5:
            continue
        kept[family["phrase"]] = family
        for game in fresh:
            assigned[game["game_id"]] = family["phrase"]
    return kept, assigned


def role(game, family):
    """Label a game's place in its family: the leader, a clone chasing it, or an older member."""
    if game["game_id"] == family["leader"]:
        return "leader"
    return "clone" if game.get("age_days") is not None and game["age_days"] <= YOUNG_DAYS else "member"

