'use strict';
const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const format = new Intl.NumberFormat('en-US');
const compact = new Intl.NumberFormat('en-US', {notation: 'compact', maximumFractionDigits: 1});
const dateFormat = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'});
const shortDate = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', timeZone: 'UTC'});
const DAY = 86400000;
const query = new URLSearchParams(location.search);
const demo = query.get('demo') === '1';
if (demo) document.body.dataset.demo = 'true';

const PERIODS = [1, 2, 3, 5, 6, 7, 10, 14, 21, 30, 45, 60];
const RELEASED = [['any', 'Any time'], ['7', '7 days'], ['14', '14 days'], ['30', '1 month'], ['60', '2 months'],
  ['90', '3 months'], ['180', '6 months'], ['365', '1 year'], ['older', 'Over 1 year']];
const CHARTS = [['up-and-coming', 'Up-and-Coming'], ['top-trending', 'Top Trending'], ['top-revisited', 'Top Revisited'],
  ['top-earning', 'Top Earning'], ['top-rated', 'Top Rated'], ['fun-with-friends', 'Fun with Friends']];
const PLAYER_STEPS = [50, 100, 250, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000];
/* Signals the backtest supports. Everything else in the lab is shown, not scored. */
const IN_SCORE = new Set(['5+ rising days', '3-4 rising days']);
const SELECTS = {
  updated: ['#updated-within', [['any', 'Any time'], ['1', 'Past day'], ['3', 'Past 3 days'], ['7', 'Past week'], ['30', 'Past month'], ['stale', 'Not in 3 months']]],
  score: ['#min-score', [[0, 'Any'], [15, '15+ watch'], [40, '40+ building'], [70, '70+ hot']]],
  streak: ['#min-streak', [[0, 'Any'], [2, '2+'], [3, '3+'], [4, '4+'], [5, '5+'], [6, '6+'], [7, '7+']]],
  growth: ['#min-growth', [[-Infinity, 'Any'], [0, 'Rising'], [25, '+25%'], [50, '+50%'], [100, '2× (+100%)'], [200, '3× (+200%)'], [400, '5× (+400%)']]],
  limit: ['#limit', [[50, 'Top 50'], [100, 'Top 100'], [250, 'Top 250'], [100000, 'All']]],
};
const DEFAULTS = {lens: 'radar', period: 7, released: 'any', updated: 'any', min: 0, max: 0, genre: '', family: '', chart: '',
  score: 0, streak: 0, growth: -Infinity, limit: 100, q: '', spikes: true, starred: false};
const state = {...DEFAULTS};
let data, peakHistory, backtest, modelReport, sortKey = null, direction = -1, expanded = null;
let dateIndex = new Map();

const lenses = {
  radar: {sort: 'score', test: g => g.score > 0,
    text: () => 'Games with the signals that came before past blow-ups: several straight days of rising peaks, plus 3-day growth ahead of the market. One-day spikes are marked down.'},
  slow: {sort: 'streak', test: g => g.streak >= 3,
    text: () => 'Peaks rising at least 3 days in a row. This was the strongest signal in the backtest: 5 or more straight days beat any percentage sort.'},
  newest: {sort: 'age_days', dir: 1, test: g => g.age_days != null,
    text: () => `Newest first, among games that have reached the ${format.format(data.collect_floor)}-player floor. Use the Released filter to limit the range.`},
  climbers: {sort: 'period.pct', test: g => g.period != null,
    text: () => `Largest percentage gain over ${state.period} ${plural(state.period, 'day')}, comparing daily peaks.`},
  comeback: {sort: 'period.pct', test: g => g.age_days != null && g.age_days >= 180 && (g.period?.pct ?? 0) >= 25,
    text: () => `Games over 6 months old, up at least 25% in ${state.period} ${plural(state.period, 'day')}. Old games that wake up are often worth a look.`},
  update: {sort: 'period.pct', test: g => g.updated_days != null && g.updated_days <= 3 && (g.period?.pct ?? 0) > 0,
    text: () => `Updated in the past 3 days and rising over ${state.period} ${plural(state.period, 'day')}. Shows whether an update is landing.`},
  clones: {sort: 'score', test: g => g.family != null,
    text: () => 'Games in a clone wave: an idea spreading across several creators right now. A wave shows what is popular, not which game will win: being in one has shown no edge so far, so read the score.'},
  charts: {sort: 'charts.up-and-coming', dir: 1, test: g => g.charts?.['up-and-coming'] != null,
    text: () => 'Games Roblox is placing on Up-and-Coming, ranked by chart position. Whether this predicts blow-ups is still being tested in the signal lab.'},
  unlisted: {sort: 'players', test: g => g.unlisted,
    text: () => 'Games found on Roblox\'s own lists that Rolimons has not listed yet. Their counts come straight from Roblox, and these are often the newest games.'},
  stepups: {sort: 'step_up', test: g => g.step_up != null,
    text: () => 'Games holding at least 3 times last week\'s level for two days. In a young game this can mark clearing Roblox\'s under-16 evaluation, but Roblox does not publish that, so it is a guess. Updates and events cause jumps too.'},
  model: {sort: 'chance_double', test: g => g.chance_double != null,
    text: () => 'Experimental. A trained model\'s chance of a game doubling to a new record within 14 days. It has not clearly beaten the score yet, so it runs alongside it and is graded below.'},
  sustained: {sort: '6.pct', window: '6', test: g => g.sustained && g.windows['6'] != null,
    text: () => 'Positive growth in every available window, ranked by 6-hour percentage gain.'},
  absolute: {sort: '6.delta', window: '6', test: g => g.windows['6'] != null,
    text: () => 'The largest net player gains over the past 6 hours.'},
  momentum: {sort: '1.pct', window: '1', test: g => g.windows['1'] != null,
    text: () => 'The fastest percentage growth over the past hour.'},
  entrants: {sort: 'players', test: g => g.new_entrant,
    text: () => 'First observed or seen below 1,000 players within the past 24 hours. Ranked by current players.'},
  latest: {sort: 'last.pct', test: () => true,
    text: () => 'Player change since the previous completed collection, ranked by percentage gain.'},
};

const plural = (count, word) => `${word}${count === 1 ? '' : 's'}`;
const signed = (n, percentage = false) => `${n > 0 ? '+' : ''}${percentage ? (Math.abs(n) >= 1000 ? format.format(Math.round(n)) + '%' : n.toFixed(1) + '%') : format.format(n)}`;
const utcDate = stamp => dateFormat.format(new Date(stamp));
function ageText(days) {
  if (days == null) return '—';
  if (days < 1) return 'Today';
  if (days < 60) return `${days}d ago`;
  if (days < 730) return `${Math.round(days / 30.4)}mo ago`;
  return `${(days / 365).toFixed(1).replace('.0', '')}y ago`;
}
function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}
function storageGet(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch { return fallback; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
}

const stars = new Set(storageGet('stars', []));
const metricCell = key => ({
  render(td, game) {
    const metric = key === 'last' ? game.since_last : game.windows[key];
    if (!metric) {
      td.append(element('span', 'muted', key === 'last' ? '—' : data.window_coverage?.[key]?.reason === 'not_enough_history' ? 'Warming up' : 'No sample'));
      td.title = key === 'last' ? 'Game not in the previous collection' : 'No snapshot near that time. Collection has overnight gaps.';
      return;
    }
    td.className = tone(metric.delta);
    td.append(element('span', 'delta', signed(metric.delta)), element('span', 'pct', signed(metric.pct, true)));
    if (key !== 'last' && metric.approximate) td.append(element('small', 'interval', `≈ ${metric.elapsed_hours}h actual`));
    td.title = `Compared with ${new Date(metric.baseline_at).toLocaleString()} · ${format.format(metric.baseline_players)} players`;
  },
});
const tone = n => n > 0 ? 'positive' : n < 0 ? 'negative' : 'muted';
const chartRank = (game, chart) => game.charts?.[chart] != null ? `#${game.charts[chart]}` : '—';
const percentCell = key => ({render(td, game) { const n = game[key]; if (n == null) { td.append(element('span', 'muted', '—')); return; } td.className = tone(n); td.append(element('span', 'delta', signed(n, true))); }});
const textCell = fn => ({render(td, game) { const text = fn(game); td.append(element('span', text === '—' ? 'muted' : '', text)); }});
const columns = [
  {id: 'score', label: 'SCORE', sort: 'score', on: true, render: scoreCell},
  {id: 'released', label: 'RELEASED', sort: 'age_days', first: 1, on: true, title: 'When the Roblox experience was created. A game can be created long before it launches publicly.',
    render(td, g) { td.append(element('span', g.age_days == null ? 'muted' : '', ageText(g.age_days))); if (g.created) td.title = `Created ${utcDate(g.created)}`; else td.title = 'Release date not fetched yet'; }},
  {id: 'players', label: 'PLAYING NOW', sort: 'players', on: true, render(td, g) { td.classList.add('number'); td.append(format.format(g.players)); }},
  {id: 'spark', label: '30-DAY PEAKS', on: true, render: sparkCell},
  {id: 'period', on: true, dynamic: true, sort: 'period.pct', sub: 'period.delta', render: periodCell},
  {id: 'market', label: 'VS MARKET', sort: 'period.vsMarket', on: true, title: 'Change over the selected period after removing the median game\'s move, which cancels weekends and holidays.',
    render(td, g) { if (!g.period) return td.append(element('span', 'muted', '—')); td.className = tone(g.period.vsMarket); td.append(element('span', 'delta', `${g.period.bound ? '≥ ' : ''}${signed(g.period.vsMarket, true)}`)); }},
  {id: 'streak', label: 'RISING DAYS', sort: 'streak', on: true, title: 'Consecutive days the 3-day peak level rose. Capped at 10.',
    render(td, g) { td.append(element('span', g.streak >= 5 ? 'delta positive' : g.streak ? 'delta' : 'muted', g.streak ?? '—')); }},
  {id: 'chance', label: 'MODEL CHANCE', sort: 'chance_double', title: 'Experimental. Chance of doubling to a new record within 14 days, from the trained model. Updated daily.', ...textCell(g => g.chance_double == null ? '—' : `${g.chance_double.toFixed(0)}%`)},
  {id: 'chance3', label: 'MODEL 3×', sort: 'chance_triple', title: 'Experimental. Chance of tripling to a new record within 14 days.', ...textCell(g => g.chance_triple == null ? '—' : `${g.chance_triple.toFixed(0)}%`)},
  {id: 'stepup', label: 'STEP-UP', sort: 'step_up', title: 'How many times higher the last two days sit than the week before. Blank under 3×.', ...textCell(g => g.step_up == null ? '—' : `${g.step_up}×`)},
  {id: 'source', label: 'COUNT FROM', sort: 'unlisted', title: 'Roblox means Rolimons has not listed this game yet, so its count is read directly from Roblox.', ...textCell(g => g.unlisted ? 'Roblox' : 'Rolimons')},
  {id: 'publicby', label: 'PUBLIC BY', sort: 'public_by', title: 'The latest date the game can have gone public, from its first sighting. Roblox does not publish a launch date. Tracking began Sep 10, 2026 and list scanning Nov 7, 2026, so earlier launches show those dates.', ...textCell(g => g.public_by ? shortDate.format(new Date(g.public_by)) : '—')},
  {id: 'maturity', label: 'MATURITY', sort: 'maturity', first: 1, title: 'Roblox\'s content label, logged when it changes. It does not show whether a game has cleared the under-16 evaluation.', ...textCell(g => g.maturity ?? '—')},
  {id: 'last', label: 'LAST RUN', sort: 'last.delta', sub: 'last.pct', ...metricCell('last')},
  {id: 'h1', label: '1H', sort: '1.delta', sub: '1.pct', ...metricCell('1')},
  {id: 'h6', label: '6H', sort: '6.delta', sub: '6.pct', ...metricCell('6')},
  {id: 'h24', label: '24H', sort: '24.delta', sub: '24.pct', on: true, ...metricCell('24')},
  {id: 'peak', label: '24H PEAK', sort: 'peak_24h', ...textCell(g => g.peak_24h == null ? '—' : format.format(g.peak_24h))},
  {id: 'wave', label: 'CLONE WAVE', sort: 'family', first: 1, ...textCell(g => g.family ? `${g.family} (${g.family_role})` : '—')},
  {id: 'upcoming', label: 'UP-AND-COMING', sort: 'charts.up-and-coming', first: 1, ...textCell(g => chartRank(g, 'up-and-coming'))},
  {id: 'upcomingdays', label: 'DAYS ON CHART', sort: 'up_and_coming_days', title: 'Days on Up-and-Coming in the past 8 days.', ...textCell(g => g.up_and_coming_days ? String(g.up_and_coming_days) : '—')},
  {id: 'trending', label: 'TOP TRENDING', sort: 'charts.top-trending', first: 1, ...textCell(g => chartRank(g, 'top-trending'))},
  {id: 'revisited', label: 'TOP REVISITED', sort: 'charts.top-revisited', first: 1, title: 'Roblox\'s own chart of games players return to, which is a retention signal.', ...textCell(g => chartRank(g, 'top-revisited'))},
  {id: 'earning', label: 'TOP EARNING', sort: 'charts.top-earning', first: 1, ...textCell(g => chartRank(g, 'top-earning'))},
  {id: 'newplayers', label: 'NEW PLAYERS/DAY ≈', sort: 'new_players', title: 'Daily awards of the game\'s most-earned badge. Many games award one on first join, so this roughly tracks new players.', ...textCell(g => g.new_players == null ? '—' : compact.format(g.new_players))},
  {id: 'newchange', label: 'NEW PLAYERS Δ', sort: 'new_players_change_pct', title: 'Change in that badge count over the past week.', ...percentCell('new_players_change_pct')},
  {id: 'visitsaccel', label: 'VISITS ACCEL', sort: 'visits_accel_pct', title: 'Visits per day in the newer half of the past week against the older half.', ...percentCell('visits_accel_pct')},
  {id: 'favrate', label: 'FAVS/1K VISITS', sort: 'favorites_per_1k', title: 'Favorites per 1,000 visits. High values mean visitors want to come back.', ...textCell(g => g.favorites_per_1k == null ? '—' : g.favorites_per_1k.toFixed(1))},
  {id: 'likechange', label: 'LIKES Δ', sort: 'like_change', title: 'Change in like percentage over the past week, in points.', ...textCell(g => g.like_change == null ? '—' : `${g.like_change > 0 ? '+' : ''}${g.like_change.toFixed(1)}`)},
  {id: 'updates', label: 'UPDATES/30D', sort: 'updates_30d', title: 'Times the game was published in the past 30 days.', ...textCell(g => g.updates_30d ? String(g.updates_30d) : '—')},
  {id: 'sinceupdate', label: 'SINCE UPDATE', sort: 'since_update_pct', title: 'Change in level since the day before the latest update, within 30 days.', ...percentCell('since_update_pct')},
  {id: 'group', label: 'GROUP MEMBERS', sort: 'group_members', ...textCell(g => g.group_members == null ? '—' : compact.format(g.group_members))},
  {id: 'groupchange', label: 'GROUP Δ', sort: 'group_change_pct', title: 'Change in group members over the past week.', ...percentCell('group_change_pct')},
  {id: 'below', label: 'BELOW RECORD', sort: 'below_tracked_peak_pct', first: 1, title: 'How far the current count sits below the highest daily peak on record.', ...textCell(g => g.below_tracked_peak_pct == null ? '—' : `${g.below_tracked_peak_pct.toFixed(0)}%`)},
  {id: 'tracked', label: 'FIRST SEEN', sort: 'tracked_days', first: 1, title: 'When this tracker first stored the game. Tracking began Sep 10, 2026, and the floor was 500 until Nov 2026. Useful when an old experience launches as a new game.', ...textCell(g => ageText(g.tracked_days))},
  {id: 'updated', label: 'UPDATED', sort: 'updated_days', first: 1, ...textCell(g => ageText(g.updated_days))},
  {id: 'genre', label: 'GENRE', sort: 'genre', first: 1, ...textCell(g => g.subgenre || g.genre || '—')},
  {id: 'visits', label: 'VISITS/DAY', sort: 'visits_per_day', ...textCell(g => g.visits_per_day == null ? '—' : compact.format(g.visits_per_day))},
  {id: 'minutes', label: 'MIN/VISIT ≈', sort: 'minutes_per_visit', title: 'A rough estimate of time per visit from average players and visits per day. Longer usually means better retention.', ...textCell(g => g.minutes_per_visit == null ? '—' : g.minutes_per_visit.toFixed(1))},
  {id: 'likes', label: 'LIKES', sort: 'like_pct', ...textCell(g => g.like_pct == null ? '—' : `${g.like_pct.toFixed(0)}%`)},
  {id: 'favorites', label: 'FAVORITES', sort: 'favorites', ...textCell(g => g.favorites == null ? '—' : compact.format(g.favorites))},
  {id: 'total', label: 'TOTAL VISITS', sort: 'visits', ...textCell(g => g.visits == null ? '—' : compact.format(g.visits))},
];
const visible = new Set(storageGet('columns', null) ?? columns.filter(c => c.on).map(c => c.id));
const tierOf = score => score >= 70 ? 'Hot' : score >= 40 ? 'Building' : score >= 15 ? 'Watch' : '';

function theme() {
  try { const saved = localStorage.getItem('theme'); if (['light', 'dark'].includes(saved)) document.documentElement.dataset.theme = saved; } catch {}
  $('#theme').addEventListener('click', () => {
    const isDark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
    const next = isDark ? 'light' : 'dark';
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem('theme', next); } catch {}
  });
}

/* Daily peaks allow any window. Each past day is matched by the midpoint of the rolling 24 hours. */
function periodChange(game, days) {
  const peaks = peakHistory?.games?.[game.game_id];
  if (!peaks || !data.latest_snapshot) return null;
  const now = peaks[peaks.length - 1];
  const target = new Date(Date.parse(data.latest_snapshot) - DAY / 2 - days * DAY).toISOString().slice(0, 10);
  const index = dateIndex.get(target);
  if (index == null || now == null) return null;
  const bound = peaks[index] == null;
  // A late find's absence means nobody was looking, not that it was under the floor.
  if (bound && game.known_from && target < game.known_from) return null;
  const base = bound ? (peakHistory.floors?.[index] ?? peakHistory.floor) : peaks[index];
  const market = Math.exp(peakHistory.market[peakHistory.market.length - 1] - peakHistory.market[index]);
  const pct = (now - base) / base * 100;
  return {now, base, bound, date: target, delta: now - base, pct: bound ? Math.max(pct, 0) : pct, vsMarket: (now / base / market - 1) * 100};
}
function value(game, key) {
  if (!key.includes('.')) return game[key] ?? null;
  const [scope, field] = key.split('.');
  if (scope === 'charts') return game.charts?.[field] ?? null;
  if (scope === 'period') return game.period?.[field] ?? null;
  return (scope === 'last' ? game.since_last : game.windows?.[scope])?.[field] ?? null;
}
function compare(a, b) {
  const key = sortKey ?? lenses[state.lens].sort;
  const av = value(a, key), bv = value(b, key);
  if (av == null && bv == null) return b.players - a.players;
  if (av == null) return 1;
  if (bv == null) return -1;
  const result = typeof av === 'string' ? av.localeCompare(bv) : av - bv;
  return result * sortDirection() || (b.period?.pct ?? 0) - (a.period?.pct ?? 0) || b.players - a.players || a.game_id.localeCompare(b.game_id);
}
const sortDirection = () => sortKey ? direction : lenses[state.lens].dir ?? -1;

function matches(game) {
  const age = game.age_days;
  if (state.released === 'older' && !(age != null && age > 365)) return false;
  if (!['any', 'older'].includes(state.released) && !(age != null && age <= Number(state.released))) return false;
  const updated = game.updated_days;
  if (state.updated === 'stale' && !(updated != null && updated > 90)) return false;
  if (!['any', 'stale'].includes(state.updated) && !(updated != null && updated <= Number(state.updated))) return false;
  if (game.players < state.min || (state.max && game.players > state.max)) return false;
  if (state.genre && game.genre !== state.genre) return false;
  if (state.family && game.family !== state.family) return false;
  if (state.chart && game.charts?.[state.chart] == null) return false;
  if ((game.score ?? 0) < state.score || (game.streak ?? 0) < state.streak) return false;
  if (state.growth > -Infinity && !(game.period && game.period.pct >= state.growth)) return false;
  if (!state.spikes && game.spike) return false;
  if (state.starred && !stars.has(game.game_id)) return false;
  const term = state.q.trim().toLocaleLowerCase();
  return !term || game.name.toLocaleLowerCase().includes(term) || (game.creator ?? '').toLocaleLowerCase().includes(term);
}
function selection() {
  const lens = lenses[state.lens];
  const pending = Boolean(lens.window) && !data.games.some(g => g.windows[lens.window] != null);
  const rows = (pending ? data.games : data.games.filter(lens.test)).filter(matches).sort(compare);
  return {rows, pending};
}

function scoreCell(td, game) {
  if (game.score == null) return td.append(element('span', 'muted', '—'));
  const tier = tierOf(game.score);
  td.append(element('span', `score ${tier.toLowerCase()}`, game.score));
  if (tier) td.append(element('small', 'interval', tier));
  td.title = reasons(game).map(r => r.text).join(' · ') || 'No breakout signal yet';
}
function sparkCell(td, game) {
  const peaks = (peakHistory?.games?.[game.game_id] ?? []).slice(-31);
  const known = peaks.filter(v => v != null);
  if (known.length < 2) return td.append(element('span', 'muted', '—'));
  const low = Math.min(...known), high = Math.max(...known);
  const width = 104, height = 30, pad = 3;
  const x = i => (peaks.length > 1 ? i / (peaks.length - 1) : 0) * (width - pad * 2) + pad;
  const y = v => height - pad - (high === low ? .5 : (v - low) / (high - low)) * (height - pad * 2);
  let path = '', open = false;
  peaks.forEach((v, i) => { if (v == null) { open = false; return; } path += `${open ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; open = true; });
  const rising = known[known.length - 1] >= known[0];
  td.title = `Daily peak, past ${peaks.length - 1} days: low ${format.format(low)}, high ${format.format(high)}`;
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
  svg.setAttribute('class', `spark ${rising ? 'up' : 'down'}`);
  svg.setAttribute('aria-hidden', 'true');
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'path');
  line.setAttribute('d', path);
  svg.append(line);
  td.append(svg);
}
function periodCell(td, game) {
  const p = game.period;
  if (!p) {
    td.append(element('span', 'muted', 'No data'));
    td.title = game.known_from ? `First found ${dateFormat.format(new Date(game.known_from))}, so there is nothing to compare with` : peakHistory ? 'History does not reach that far back' : 'History file unavailable';
    return;
  }
  const mark = p.bound ? '≥ ' : '';
  td.className = tone(p.delta);
  td.append(element('span', 'delta', `${mark}${signed(p.delta)}`), element('span', 'pct', `${mark}${signed(p.pct, true)}`));
  td.append(element('small', 'interval', p.bound ? `from under ${format.format(p.base)}` : `from ${format.format(p.base)}`));
  td.title = `24-hour peak of ${format.format(p.now)} compared with the peak on ${dateFormat.format(new Date(p.date))}`;
}
function reasons(game) {
  const list = [];
  if (game.streak >= 5) list.push({text: `Peaks up ${game.streak} days straight, the strongest signal in the backtest`, good: true});
  else if (game.streak >= 3) list.push({text: `Peaks up ${game.streak} days straight`, good: true});
  if (game.vs_market_3 != null && game.vs_market_3 > 0.1) list.push({text: `${signed((Math.exp(game.vs_market_3) - 1) * 100, true)} against the market over 3 days`, good: true});
  if (game.spike) list.push({text: 'Single-day spike: recent peak is far above its usual level, and these often fade', good: false});
  if (game.unlisted) list.push({text: 'Not on Rolimons yet, so found through Roblox\'s own lists', good: true});
  if (game.step_up != null) list.push({text: `Holding ${game.step_up}× last week's level for two days${game.age_days != null && game.age_days <= 120 ? ', which in a young game can mean it just reached a wider audience' : ''}`, good: true});
  if (game.chance_double != null && game.chance_double >= 15) list.push({text: `The experimental model gives ${game.chance_double.toFixed(0)}% to double to a new record within 14 days`, good: true});
  if (game.age_days != null && game.age_days >= 14 && game.age_days <= 30) list.push({text: `In the launch window: created ${game.age_days} days ago, the age that doubled most often`, good: true});
  else if (game.age_days != null && game.age_days > 60 && game.age_days <= 180) list.push({text: `Created ${game.age_days} days ago: games this age did worse than average`, good: false});
  else if (game.age_days != null && game.age_days < 14) list.push({text: `Brand new, created ${game.age_days} ${plural(game.age_days, 'day')} ago`, good: true});
  if (game.family) list.push({text: game.family_role === 'leader' ? `Leads the "${game.family}" clone wave` : `Part of the "${game.family}" clone wave, which has shown no edge by itself`, good: game.family_role === 'leader'});
  if (game.charts?.['up-and-coming'] != null) list.push({text: `#${game.charts['up-and-coming']} on Up-and-Coming${game.up_and_coming_days > 1 ? ` for ${game.up_and_coming_days} days` : ''}`, good: true});
  if (game.charts?.['top-revisited'] != null) list.push({text: `#${game.charts['top-revisited']} on Top Revisited, so players are returning`, good: true});
  if (game.new_players_change_pct >= 50) list.push({text: `New-player badge awards up ${game.new_players_change_pct.toFixed(0)}% this week`, good: true});
  if (game.visits_accel_pct >= 30) list.push({text: `Visits per day accelerating ${game.visits_accel_pct.toFixed(0)}%`, good: true});
  if (game.updated_days != null && game.updated_days <= 3) list.push({text: `Updated ${game.updated_days ? `${game.updated_days} ${plural(game.updated_days, 'day')} ago` : 'today'}`, good: true});
  if (game.below_tracked_peak_pct > 50) list.push({text: `${game.below_tracked_peak_pct.toFixed(0)}% below its record peak of ${format.format(game.tracked_peak)}`, good: false});
  return list;
}

function headers() {
  const row = $('#headers');
  row.replaceChildren();
  const rank = element('th', 'rank', '#'); rank.scope = 'col'; row.append(rank);
  const title = element('th', 'title-col'); title.scope = 'col'; title.append(sortButton('GAME', 'name', 1)); row.append(title);
  columns.filter(c => visible.has(c.id)).forEach(column => {
    const th = element('th'); th.scope = 'col';
    const label = column.dynamic ? `${state.period}-DAY CHANGE` : column.label;
    if (column.title) th.title = column.title;
    if (column.sort) {
      th.append(sortButton(label, column.sub && !column.dynamic ? column.sub : column.sort, column.first ?? -1));
      if (column.sub) { const sub = sortButton(column.dynamic ? 'PLAYERS Δ' : 'BY PLAYERS Δ', column.dynamic ? column.sub : column.sort, -1); sub.classList.add('subsort'); th.append(sub); }
    } else th.append(label);
    row.append(th);
  });
  const active = sortKey ?? lenses[state.lens].sort;
  $$('[data-sort]').forEach(button => {
    const on = button.dataset.sort === active;
    button.classList.toggle('selected', on);
    if (on) button.closest('th').setAttribute('aria-sort', sortDirection() === 1 ? 'ascending' : 'descending');
  });
}
function sortButton(label, key, first) {
  const button = element('button', '', `${label} ↕`);
  button.dataset.sort = key;
  button.addEventListener('click', () => {
    direction = (sortKey ?? lenses[state.lens].sort) === key ? -sortDirection() : first;
    sortKey = key;
    render();
  });
  return button;
}
function gameRow(game, index, pending, span) {
  const tr = element('tr', 'game-row');
  const rankCell = element('td', 'rank');
  const star = element('button', `star${stars.has(game.game_id) ? ' on' : ''}`, stars.has(game.game_id) ? '★' : '☆');
  star.setAttribute('aria-label', `Star ${game.name}`);
  star.setAttribute('aria-pressed', String(stars.has(game.game_id)));
  star.addEventListener('click', event => {
    event.stopPropagation();
    if (stars.has(game.game_id)) stars.delete(game.game_id); else stars.add(game.game_id);
    storageSet('stars', [...stars]);
    render();
  });
  rankCell.append(star, element('span', 'rank-number', pending ? '—' : String(index + 1)));
  const nameCell = element('td', 'title-col');
  const wrapper = element('div', 'game');
  const icon = element('img', 'icon');
  icon.alt = ''; icon.loading = 'lazy'; icon.referrerPolicy = 'no-referrer';
  try { const url = new URL(game.icon_url); if (url.protocol === 'https:') icon.src = url.href; } catch {}
  icon.addEventListener('error', () => { icon.removeAttribute('src'); icon.style.visibility = 'hidden'; }, {once: true});
  const info = element('div', 'game-info');
  const link = element('a', '', game.name);
  link.href = `https://www.roblox.com/games/${encodeURIComponent(game.game_id)}`;
  link.target = '_blank'; link.rel = 'noopener noreferrer'; link.title = game.name;
  link.addEventListener('click', event => event.stopPropagation());
  info.append(link);
  const badges = element('div', 'badges');
  if (game.genre) badges.append(element('span', 'tag', game.genre));
  if (game.unlisted) { const badge = element('span', 'badge chart', 'NOT ON ROLIMONS'); badge.title = 'Found on Roblox\'s own lists. Its count comes directly from Roblox.'; badges.append(badge); }
  if (game.step_up != null) { const badge = element('span', 'badge rising', `STEP-UP ${game.step_up}×`); badge.title = 'Holding well above last week\'s level for two days'; badges.append(badge); }
  if (game.charts?.['up-and-coming'] != null) { const badge = element('span', 'badge chart', `UP-AND-COMING #${game.charts['up-and-coming']}`); badges.append(badge); }
  if (game.family) { const badge = element('span', 'badge', game.family_role === 'leader' ? 'WAVE LEADER' : 'CLONE WAVE'); badge.title = `"${game.family}" wave`; badges.append(badge); }
  if (game.sustained) { const badge = element('span', 'badge rising', '↗ SUSTAINED'); badge.title = `Positive across ${game.available_windows} available comparison windows`; badges.append(badge); }
  if (game.new_entrant) { const badge = element('span', 'badge', 'NEW'); badge.title = game.crossed_floor ? 'Observed below 1,000 players in the last 24 hours' : 'First observed by this tracker within 24 hours'; badges.append(badge); }
  if (game.spike) { const badge = element('span', 'badge warn', 'SPIKE'); badge.title = 'A recent peak is far above its usual level'; badges.append(badge); }
  info.append(badges);
  const open = element('button', 'open', expanded === game.game_id ? '▾ Details' : '▸ Details');
  open.setAttribute('aria-expanded', String(expanded === game.game_id));
  info.append(open);
  wrapper.append(icon, info);
  nameCell.append(wrapper);
  tr.append(rankCell, nameCell);
  columns.filter(c => visible.has(c.id)).forEach(column => { const td = element('td'); column.render(td, game); tr.append(td); });
  tr.addEventListener('click', event => {
    if (event.target.closest('a')) return;
    expanded = expanded === game.game_id ? null : game.game_id;
    render();
  });
  const list = [tr];
  if (expanded === game.game_id) {
    const detail = element('tr', 'detail-row');
    const cell = element('td'); cell.colSpan = span;
    cell.append(detailPanel(game));
    detail.append(cell);
    list.push(detail);
  }
  return list;
}
function detailPanel(game) {
  const panel = element('div', 'detail');
  const chartBox = element('div', 'detail-chart');
  chartBox.append(element('h4', '', 'Daily peak players'));
  chartBox.append(peakChart(game));
  const side = element('div', 'detail-side');
  side.append(element('h4', '', 'Why it scores this way'));
  const list = element('ul', 'reasons');
  const found = reasons(game);
  if (!found.length) list.append(element('li', 'muted', 'No strong signal right now.'));
  found.forEach(r => list.append(element('li', r.good ? 'good' : 'caution', `${r.good ? '↗' : '!'} ${r.text}`)));
  side.append(list);
  side.append(element('h4', '', 'Change by period'));
  const grid = element('div', 'change-grid');
  [1, 3, 6, 7, 14, 30].forEach(days => {
    const change = periodChange(game, days);
    const box = element('div');
    box.append(element('span', '', `${days}d`), element('strong', change ? tone(change.delta) : 'muted', change ? `${change.bound ? '≥ ' : ''}${signed(change.pct, true)}` : '—'));
    grid.append(box);
  });
  side.append(grid);
  const facts = element('dl', 'facts');
  const fact = (label, text) => { if (text != null) { facts.append(element('dt', '', label), element('dd', '', text)); } };
  fact('Created', game.created && utcDate(game.created));
  fact('Updated', game.updated && utcDate(game.updated));
  fact('Public by', game.public_by && utcDate(game.public_by));
  fact('Public since', game.public_from && `Not before ${utcDate(game.public_from)}, from Up-and-Coming`);
  fact('First tracked', game.first_seen && utcDate(game.first_seen));
  fact('First on Roblox lists', game.first_chart && utcDate(game.first_chart));
  fact('Visits when found', game.visits_at_first != null ? format.format(game.visits_at_first) : null);
  fact('Maturity', game.maturity);
  fact('Model chance', game.chance_double != null ? `${game.chance_double.toFixed(0)}% to double, ${(game.chance_triple ?? 0).toFixed(0)}% to triple` : null);
  fact('Genre', [game.genre, game.subgenre].filter(Boolean).join(' / ') || null);
  fact('Creator', game.creator && `${game.creator}${game.verified ? ' ✓' : ''}`);
  fact('Total visits', game.visits != null ? format.format(game.visits) : null);
  fact('Visits per day', game.visits_per_day != null ? format.format(game.visits_per_day) : null);
  fact('Favorites', game.favorites != null ? format.format(game.favorites) : null);
  fact('Likes', game.like_pct != null ? `${game.like_pct}%` : null);
  fact('Clone wave', game.family && `${game.family} (${game.family_role})`);
  fact('Charts', Object.entries(game.charts ?? {}).map(([id, rank]) => `${CHARTS.find(c => c[0] === id)?.[1] ?? id} #${rank}`).join(', ') || null);
  fact('New players/day ≈', game.new_players != null ? format.format(game.new_players) : null);
  fact('Updates in 30 days', game.updates_30d || null);
  fact('Group members', game.group_members != null ? format.format(game.group_members) : null);
  fact('Record peak', game.tracked_peak != null ? format.format(game.tracked_peak) : null);
  side.append(facts);
  panel.append(chartBox, side);
  return panel;
}
function peakChart(game) {
  const peaks = peakHistory?.games?.[game.game_id];
  const known = (peaks ?? []).filter(v => v != null);
  if (known.length < 2) return element('p', 'muted', 'Not enough peakHistory to chart yet.');
  const labels = [...peakHistory.dates.slice(-(peaks.length - 1)), 'Last 24 hours'];
  const width = 620, height = 220, left = 48, right = 12, top = 12, bottom = 26;
  const high = Math.max(...known) * 1.08;
  const x = i => left + i / (peaks.length - 1) * (width - left - right);
  const y = v => height - bottom - v / high * (height - top - bottom);
  const ns = 'http://www.w3.org/2000/svg';
  const make = (tag, attrs, text) => { const el = document.createElementNS(ns, tag); Object.entries(attrs).forEach(([k, v]) => el.setAttribute(k, v)); if (text != null) el.textContent = text; return el; };
  const svg = make('svg', {viewBox: `0 0 ${width} ${height}`, class: 'peak-chart', role: 'img', 'aria-label': `Daily peak players for ${game.name}, from ${format.format(Math.min(...known))} to ${format.format(Math.max(...known))}`});
  const rough = high / 4, magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].find(n => n * magnitude >= rough) * magnitude;
  for (let v = 0; v <= high; v += step) {
    svg.append(make('line', {x1: left, x2: width - right, y1: y(v), y2: y(v), class: 'grid'}));
    svg.append(make('text', {x: left - 7, y: y(v) + 3, 'text-anchor': 'end', class: 'axis'}, compact.format(v)));
  }
  const ticks = Math.min(5, peaks.length - 1);
  for (let t = 0; t < ticks; t++) {
    const i = Math.round(t * (peaks.length - 2) / Math.max(ticks - 1, 1));
    svg.append(make('text', {x: x(i), y: height - 8, 'text-anchor': t === 0 ? 'start' : t === ticks - 1 ? 'end' : 'middle', class: 'axis'}, shortDate.format(new Date(labels[i]))));
  }
  let path = '', open = false;
  peaks.forEach((v, i) => { if (v == null) { open = false; return; } path += `${open ? 'L' : 'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`; open = true; });
  svg.append(make('path', {d: path, class: 'peak-line'}));
  const cross = make('line', {y1: top, y2: height - bottom, class: 'cross', visibility: 'hidden'});
  const dot = make('circle', {r: 4, class: 'cross-dot', visibility: 'hidden'});
  svg.append(cross, dot);
  const box = element('div', 'chart-box');
  const tip = element('div', 'chart-tip'); tip.hidden = true;
  box.append(svg, tip);
  const pick = event => {
    const rect = svg.getBoundingClientRect();
    const ratio = ((event.clientX - rect.left) / rect.width * width - left) / (width - left - right);
    const i = Math.max(0, Math.min(peaks.length - 1, Math.round(ratio * (peaks.length - 1))));
    const v = peaks[i];
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    if (v != null) { dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(v)); }
    dot.setAttribute('visibility', v != null ? 'visible' : 'hidden');
    tip.hidden = false;
    tip.textContent = `${i === peaks.length - 1 ? labels[i] : dateFormat.format(new Date(labels[i]))}: ${v != null ? `${format.format(v)} peak` : `under ${format.format(peakHistory.floor)}`}`;
    tip.style.left = `${Math.min(x(i) / width * 100, 66)}%`;
  };
  svg.addEventListener('pointermove', pick);
  svg.addEventListener('pointerleave', () => { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); tip.hidden = true; });
  return box;
}

function render() {
  if (!data) return;
  data.games.forEach(game => { game.period = periodChange(game, state.period); });
  const games = data.games;
  $$('[data-lens]').forEach(button => { const on = button.dataset.lens === state.lens; button.classList.toggle('active', on); button.setAttribute('aria-pressed', String(on)); });
  chips();
  $('#eligible').textContent = format.format(games.length);
  $('#floor').textContent = `Floor ${format.format(data.collect_floor)} · ${format.format(data.unlisted_count ?? 0)} not on Rolimons`;
  floorControls();
  $('#watch').textContent = format.format(games.filter(g => (g.score ?? 0) >= 40).length);
  $('#fresh').textContent = format.format(games.filter(g => g.age_days != null && g.age_days <= 90).length);
  // Daily peaks outlive raw snapshots, so they are the true length of the history.
  const days = peakHistory?.dates?.length || data.history_hours / 24;
  $('#history').textContent = days >= 2 ? `${format.format(Math.round(days))} days` : `${format.format(data.history_hours)}h`;
  $('#snapshots').textContent = `Daily peaks · ${format.format(data.snapshot_count)} recent snapshots`;
  $('#lens-description').textContent = lenses[state.lens].text();
  const age = data.latest_snapshot ? (Date.now() - Date.parse(data.latest_snapshot)) / 60000 : Infinity;
  const stale = age > data.stale_minutes;
  $('#status-dot').classList.toggle('stale', stale);
  $('#freshness').textContent = demo ? 'Design preview · sample games' : !data.latest_snapshot ? 'Awaiting first collection' : stale ? 'Collection is overdue' : 'Tracking player counts';
  $('#updated').textContent = data.latest_snapshot ? `Last snapshot ${new Date(data.latest_snapshot).toLocaleString()}` : 'Snapshots every ~15 minutes';
  const {rows, pending} = selection();
  const shown = rows.slice(0, state.limit);
  $('#result-count').textContent = `${format.format(shown.length)} of ${format.format(rows.length)} games${pending ? ' · awaiting peakHistory' : ''}`;
  notices(pending);
  headers();
  const span = 2 + columns.filter(c => visible.has(c.id)).length;
  const tbody = $('#rows');
  tbody.replaceChildren();
  if (!shown.length) {
    const tr = element('tr');
    const td = element('td', 'empty', state.starred && !stars.size ? 'Nothing starred yet. Star a game to keep it here.' : 'No games match these filters. Try a wider period or fewer filters.');
    td.colSpan = span; tr.append(td); tbody.append(tr);
  }
  shown.forEach((game, index) => tbody.append(...gameRow(game, index, pending, span)));
  genreHeat();
  waves();
  saveUrl();
}
function notices(pending) {
  const list = [];
  const lens = lenses[state.lens];
  if (demo) list.push('Sample data for design review. These are fictional games and simulated growth.');
  const stale = data.latest_snapshot && (Date.now() - Date.parse(data.latest_snapshot)) / 60000 > data.stale_minutes;
  if (stale && !demo) list.push('No recent snapshot. Counts may be outdated; check the GitHub Actions run.');
  if (!peakHistory && !demo) list.push('Daily peakHistory is unavailable, so period changes and trend charts are blank.');
  const missing = data.games.filter(g => g.age_days == null).length;
  if (missing && (state.released !== 'any' || state.lens === 'newest')) list.push(`${format.format(missing)} tracked ${plural(missing, 'game')} have no release date yet and are left out here.`);
  if (state.released !== 'any') list.push(`Released means when the Roblox experience was created. Some games sit private for months first, and a game only appears here once it reaches ${format.format(data.collect_floor)} players.`);
  if (state.min && state.min < data.collect_floor) list.push(`Below ${format.format(data.collect_floor)} players, only games from the past 4 months are stored, down to ${format.format(data.young_floor ?? data.collect_floor)} players. See the README to change this.`);
  if (state.lens === 'charts' && !data.chart_days) list.push('Chart tracking starts with the next collection.');
  if (peakHistory && state.period > peakHistory.dates.length) list.push(`History reaches ${peakHistory.dates.length} days, so a ${state.period}-day comparison has no starting point yet.`);
  if (pending) list.push('No snapshot exists near this time because collection pauses overnight, so these games are unranked. The daily lenses above are not affected.');
  if (state.lens === 'latest') list.push('Last-run changes compare consecutive collections, even across scheduling gaps.');
  $('#notice').hidden = !list.length;
  $('#notice').classList.remove('error');
  $('#notice').textContent = list.join(' ');
}
function chips() {
  $$('#period-chips button').forEach(b => { const on = Number(b.dataset.value) === state.period; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  $$('#released-chips button').forEach(b => { const on = b.dataset.value === state.released; b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on)); });
  const custom = $('#period-custom');
  if (document.activeElement !== custom) custom.value = PERIODS.includes(state.period) ? '' : state.period;
  const reach = peakHistory?.dates?.length;
  if (reach) { custom.max = reach; $$('#period-chips button').forEach(b => { b.disabled = Number(b.dataset.value) > reach; }); }
}
function floorControls() {
  const list = $('#player-steps');
  if (list.dataset.floor === String(data.collect_floor)) return;
  list.dataset.floor = data.collect_floor;
  list.replaceChildren(...PLAYER_STEPS.filter(step => step >= (data.young_floor ?? data.collect_floor)).map(step => { const option = element('option'); option.value = step; return option; }));
  $('#min-players').placeholder = `${format.format(data.collect_floor)} (floor)`;
}
function waves() {
  const box = $('#wave-rows');
  box.replaceChildren();
  const list = (data.families ?? []).slice(0, 10);
  $('#wave-summary').textContent = list.length ? 'Ideas spreading across creators, found by comparing recent titles with older ones. Select one to see its games.' : 'No clone waves detected yet.';
  list.forEach(family => {
    const button = element('button', `wave-row${state.family === family.phrase ? ' active' : ''}`);
    button.setAttribute('aria-pressed', String(state.family === family.phrase));
    const change = family.median_growth_pct;
    button.append(element('span', '', `“${family.phrase}”`), element('strong', tone(change ?? 0), change == null ? '—' : signed(change, true)),
      element('small', '', `${family.recent} new from ${family.creators} creators · ${compact.format(family.total)} peak players · led by ${family.leader_name}`));
    button.title = 'Change is the median game in this wave over 7 days.';
    button.addEventListener('click', () => { state.family = state.family === family.phrase ? '' : family.phrase; $('#family').value = state.family; render(); });
    box.append(button);
  });
}
function renderLab() {
  const body = $('#lab-rows');
  body.replaceChildren();
  const signals = backtest?.signals ?? [];
  $('#lab-summary').textContent = signals.length ? 'Each signal is replayed against what happened next. Vs average compares its doubling rate with the average game. Only signals that hold up affect the score; the rest are shown for you to use, and new ones keep collecting evidence.' : 'No results yet.';
  signals.forEach(signal => {
    const tr = element('tr');
    tr.append(element('td', '', signal.name));
    tr.append(element('td', '', signal.double == null ? '—' : `${signal.double.toFixed(1)}%`));
    tr.append(element('td', '', signal.lift == null ? '—' : `${signal.lift.toFixed(1)}×`));
    tr.append(element('td', 'muted', format.format(signal.samples)));
    const status = element('td');
    const scored = IN_SCORE.has(signal.name);
    status.append(element('span', `status-pill${scored ? ' in' : ''}`, scored ? 'In score' : !signal.enough ? 'Collecting' : signal.lift >= 1.5 ? 'Promising' : signal.lift < 0.75 ? 'Warning sign' : 'No edge'));
    tr.append(status);
    body.append(tr);
  });
}
function genreHeat() {
  const groups = new Map();
  data.games.forEach(game => {
    if (!game.genre || !game.period || game.period.bound || game.players < 1000) return;
    if (!groups.has(game.genre)) groups.set(game.genre, []);
    groups.get(game.genre).push(game.period.pct);
  });
  const median = values => { const s = [...values].sort((a, b) => a - b); return s[Math.floor(s.length / 2)]; };
  const list = [...groups].filter(([, v]) => v.length >= 5).map(([name, v]) => ({name, count: v.length, median: median(v)})).sort((a, b) => b.median - a.median).slice(0, 8);
  $('#genre-summary').textContent = list.length ? `Median ${state.period}-day change among games above 1,000 players. Select a genre to filter the list.` : 'Needs more games with genre data.';
  const box = $('#genre-rows');
  box.replaceChildren();
  list.forEach(row => {
    const button = element('button', `genre-row${state.genre === row.name ? ' active' : ''}`);
    button.setAttribute('aria-pressed', String(state.genre === row.name));
    button.append(element('span', '', row.name), element('span', 'muted', `${row.count} games`), element('strong', tone(row.median), signed(row.median, true)));
    button.addEventListener('click', () => { state.genre = state.genre === row.name ? '' : row.name; $('#genre').value = state.genre; render(); });
    box.append(button);
  });
}
function renderTrust() {
  if (!backtest) { $('#trust-summary').textContent = 'The backtest has not run yet.'; return; }
  $('#trust-summary').textContent = `Replayed ${backtest.days_tested} past days across ${format.format(backtest.games_tested)} games, using only data available on each day, then checked the next ${backtest.horizon_days} days. A hit means a sustained 3-day level above both the highest level already visible and the game's record, so routine bounces do not count. Even at the top, most picks do not blow up.`;
  const rows = [
    ['All games', backtest.base_rates, backtest.observations],
    ...backtest.bands.slice().reverse().map(b => [`Score ${b.low}–${b.high}`, b, b.observations]),
    [`Top ${backtest.top_k} daily by score`, backtest.top_by_score, null],
    [`Top ${backtest.top_k} daily by 3-day %`, backtest.top_by_growth_3d, null],
    [`Top ${backtest.top_k} daily by 7-day %`, backtest.top_by_growth_7d, null],
  ];
  const body = $('#trust-rows');
  body.replaceChildren();
  rows.forEach(([label, rates, samples]) => {
    const tr = element('tr');
    tr.append(element('td', '', label));
    ['double', 'triple', 'five_x'].forEach(key => tr.append(element('td', '', rates[key] == null ? '—' : `${rates[key].toFixed(1)}%`)));
    tr.append(element('td', 'muted', samples == null ? '—' : format.format(samples)));
    body.append(tr);
  });
}

function renderBlindSpot() {
  const late = backtest?.late_arrivals, caught = backtest?.five_x_caught, today = data?.coverage;
  const parts = [];
  if (late?.games) parts.push(`${format.format(late.games)} games reached 5,000 players after tracking began, and only ${format.format(late.seen_early)} were seen under 2,000 first. A score cannot catch a game the source lists late, which is why the tracker now scans Roblox directly.`);
  if (caught?.games) parts.push(`Of ${format.format(caught.games)} games that could be replayed as a 5× win, the daily top ${backtest.top_k} caught ${format.format(caught.caught)}.`);
  $('#blind-summary').textContent = parts.join(' ') || 'Waiting for the first scan of Roblox\'s lists.';
  const body = $('#blind-rows');
  body.replaceChildren();
  if (!today) return;
  [['5,000+ players', today.charted_5k, today.missing_5k], ['500–4,999 players', today.charted_500, today.missing_500]].forEach(([label, total, missing]) => {
    const tr = element('tr');
    const share = Number(total) ? ` (${Math.round(missing / total * 100)}%)` : '';
    tr.append(element('td', '', label), element('td', '', format.format(total)), element('td', '', `${format.format(missing)}${share}`));
    body.append(tr);
  });
  const tr = element('tr');
  tr.append(element('td', '', 'Now read directly from Roblox'), element('td', '', format.format(today.watching)), element('td', 'muted', '—'));
  body.append(tr);
}
function renderModel() {
  const summary = $('#model-summary');
  if (!modelReport) { summary.textContent = 'The model has not run yet.'; return; }
  const forward = modelReport.forward, held = modelReport.held_out;
  const graded = forward?.days ? `It has been graded on ${forward.days} ${plural(forward.days, 'day')} neither had seen.` : `Forward grading starts ${backtest?.horizon_days ?? 14} days after the first logged picks.`;
  summary.textContent = `Experimental, and the score is unchanged. The model retrains daily and logs its top picks, which are graded against the score's on days neither had seen. ${graded} The score stays in charge unless the model clearly wins that record over several weeks. Held-out rows come from games the model never trained on, a week away from its training days.`;
  const rows = [['Held out: all games', held?.base, held?.days], ['Held out: score', held?.score, held?.days], ['Held out: model', held?.model, held?.days],
    ['Forward test: score', forward?.score, forward?.days], ['Forward test: model', forward?.model, forward?.days]];
  const body = $('#model-rows');
  body.replaceChildren();
  rows.forEach(([label, rates, days]) => {
    const tr = element('tr');
    tr.append(element('td', '', label));
    ['double', 'triple', 'five_x'].forEach(key => tr.append(element('td', '', rates?.[key] == null ? '—' : `${rates[key].toFixed(1)}%`)));
    tr.append(element('td', 'muted', days == null ? '—' : format.format(days)));
    body.append(tr);
  });
}

function saveUrl() {
  const next = new URLSearchParams();
  if (demo) next.set('demo', '1');
  for (const key of Object.keys(DEFAULTS)) {
    if (state[key] !== DEFAULTS[key] && state[key] !== -Infinity) next.set(key, state[key]);
  }
  if (sortKey) { next.set('sort', sortKey); next.set('dir', direction); }
  const text = next.toString();
  try { window.history.replaceState(null, '', text ? `?${text}` : location.pathname); } catch {}
}
function readUrl() {
  for (const key of Object.keys(DEFAULTS)) {
    if (!query.has(key)) continue;
    const raw = query.get(key);
    if (typeof DEFAULTS[key] === 'boolean') state[key] = raw === 'true';
    else if (typeof DEFAULTS[key] === 'number') { const n = Number(raw); if (Number.isFinite(n)) state[key] = n; }
    else state[key] = raw;
  }
  if (!lenses[state.lens]) state.lens = DEFAULTS.lens;
  state.period = Math.max(1, Math.round(state.period));
  if (query.has('sort')) { sortKey = query.get('sort'); direction = query.get('dir') === '1' ? 1 : -1; }
}
function controls() {
  PERIODS.forEach(days => { const b = element('button', 'chip', `${days}d`); b.dataset.value = days; b.addEventListener('click', () => { state.period = days; render(); }); $('#period-chips').append(b); });
  RELEASED.forEach(([id, label]) => { const b = element('button', 'chip', label); b.dataset.value = id; b.addEventListener('click', () => { state.released = id; render(); }); $('#released-chips').append(b); });
  for (const [key, [selector, options]] of Object.entries(SELECTS)) {
    const select = $(selector);
    options.forEach(([id, label]) => { const option = element('option', '', label); option.value = id; select.append(option); });
    select.value = state[key];
    select.addEventListener('change', () => { state[key] = typeof DEFAULTS[key] === 'number' ? Number(select.value) : select.value; render(); });
  }
  $('#genre').addEventListener('change', event => { state.genre = event.target.value; render(); });
  $('#family').addEventListener('change', event => { state.family = event.target.value; render(); });
  $('#chart').addEventListener('change', event => { state.chart = event.target.value; render(); });
  const chartSelect = $('#chart');
  [['', 'Any'], ...CHARTS].forEach(([id, label]) => { const option = element('option', '', label); option.value = id; chartSelect.append(option); });
  chartSelect.value = state.chart;
  for (const [key, selector] of [['min', '#min-players'], ['max', '#max-players']]) {
    const input = $(selector);
    if (state[key]) input.value = state[key];
    input.addEventListener('input', () => { state[key] = Math.max(0, Number(input.value) || 0); render(); });
  }
  $('#name-search').value = state.q;
  $('#name-search').addEventListener('input', event => { state.q = event.target.value; render(); });
  $('#hide-spikes').checked = !state.spikes;
  $('#hide-spikes').addEventListener('change', event => { state.spikes = !event.target.checked; render(); });
  $('#starred').checked = state.starred;
  $('#starred').addEventListener('change', event => { state.starred = event.target.checked; render(); });
  $('#period-custom').addEventListener('change', event => {
    const days = Math.round(Number(event.target.value));
    if (days >= 1) { state.period = days; render(); }
  });
  $$('[data-lens]').forEach(button => button.addEventListener('click', () => { state.lens = button.dataset.lens; sortKey = null; render(); }));
  $('#reset').addEventListener('click', () => {
    Object.assign(state, DEFAULTS, {lens: state.lens});
    sortKey = null;
    for (const [key, [selector]] of Object.entries(SELECTS)) $(selector).value = state[key];
    $('#genre').value = ''; $('#family').value = ''; $('#chart').value = ''; $('#min-players').value = ''; $('#max-players').value = ''; $('#name-search').value = ''; $('#hide-spikes').checked = false; $('#starred').checked = false;
    render();
  });
  $('#copy-link').addEventListener('click', async event => {
    const button = event.currentTarget;
    try { await navigator.clipboard.writeText(location.href); button.textContent = 'Link copied'; } catch { button.textContent = 'Copy from address bar'; }
    setTimeout(() => { button.textContent = 'Copy link to this view'; }, 2000);
  });
  $('#export').addEventListener('click', exportCsv);
  columns.forEach(column => {
    const label = element('label', 'check');
    const box = element('input'); box.type = 'checkbox'; box.checked = visible.has(column.id);
    box.addEventListener('change', () => { if (box.checked) visible.add(column.id); else visible.delete(column.id); storageSet('columns', [...visible]); render(); });
    label.append(box, ` ${column.dynamic ? 'Period change' : column.label.charAt(0) + column.label.slice(1).toLowerCase()}`);
    $('#column-list').append(label);
  });
}
function families() {
  const select = $('#family');
  select.replaceChildren(element('option', '', 'Any'));
  select.firstChild.value = '';
  (data.families ?? []).forEach(family => { const option = element('option', '', family.phrase); option.value = family.phrase; select.append(option); });
  select.value = state.family;
}
function genres() {
  const select = $('#genre');
  const names = [...new Set(data.games.map(g => g.genre).filter(Boolean))].sort();
  select.replaceChildren(element('option', '', 'All genres'));
  select.firstChild.value = '';
  names.forEach(name => { const option = element('option', '', name); option.value = name; select.append(option); });
  select.value = state.genre;
}
function exportCsv() {
  const fields = [['name', g => g.name], ['game_id', g => g.game_id], ['url', g => `https://www.roblox.com/games/${g.game_id}`], ['score', g => g.score], ['rising_days', g => g.streak],
    ['players', g => g.players], ['peak_24h', g => g.peak_24h], [`change_${state.period}d_pct`, g => g.period?.pct?.toFixed(1)], [`change_${state.period}d_players`, g => g.period?.delta],
    ['baseline_under_floor', g => g.period?.bound], ['vs_market_pct', g => g.period?.vsMarket?.toFixed(1)], ['created', g => g.created], ['updated', g => g.updated], ['genre', g => g.genre],
    ['subgenre', g => g.subgenre], ['creator', g => g.creator], ['visits_per_day', g => g.visits_per_day], ['like_pct', g => g.like_pct], ['one_day_spike', g => g.spike],
    ['model_chance_double', g => g.chance_double], ['step_up', g => g.step_up], ['not_on_rolimons', g => g.unlisted], ['public_by', g => g.public_by]];
  const cell = value => {
    let text = value == null ? '' : String(value);
    if (/^[=+\-@\t\r]/.test(text) && typeof value === 'string') text = `'${text}`; // Stops spreadsheets running a title as a formula.
    return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const rows = [fields.map(f => f[0]).join(','), ...selection().rows.map(game => fields.map(f => cell(f[1](game))).join(','))];
  const link = element('a');
  link.href = URL.createObjectURL(new Blob([rows.join('\n')], {type: 'text/csv'}));
  link.download = `next-up-${state.lens}-${state.period}d.csv`;
  link.click();
  URL.revokeObjectURL(link.href);
}

async function fetchJson(name) {
  const response = await fetch(name, {cache: 'no-store', signal: AbortSignal.timeout(20000)});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
async function load() {
  try {
    // A tiny status check, so an open tab only downloads the large files when data changed.
    if (data && !demo) {
      const status = await fetchJson('status.json').catch(() => null);
      if (status?.latest_snapshot === data.latest_snapshot) { render(); return; }
    }
    const next = await fetchJson(demo ? 'demo.json' : 'data.json');
    if (!Array.isArray(next.games)) throw new Error('Invalid leaderboard response');
    const needsHistory = !peakHistory || peakHistory.latest_snapshot !== next.latest_snapshot;
    data = next;
    if (needsHistory) {
      try {
        peakHistory = await fetchJson(demo ? 'demo-history.json' : 'history.json');
        dateIndex = new Map(peakHistory.dates.map((date, i) => [date, i]));
      } catch { peakHistory = peakHistory ?? null; }
    }
    if (!backtest && !demo) { try { backtest = await fetchJson('backtest.json'); } catch {} }
    if (!modelReport && !demo) { try { modelReport = await fetchJson('model.json'); } catch {} }
    genres();
    families();
    renderTrust();
    renderLab();
    renderBlindSpot();
    renderModel();
    render();
  } catch (error) {
    if (data) render();
    $('#notice').hidden = false;
    $('#notice').classList.add('error');
    $('#notice').textContent = `Could not refresh the leaderboard (${error.message}). ${data ? 'Showing the last loaded snapshot.' : 'The first successful workflow run will publish the data.'} Retrying in 3 minutes.`;
    $('#freshness').textContent = 'Refresh unavailable';
    $('#status-dot').classList.add('stale');
    if (!data) { $('#result-count').textContent = 'Data unavailable'; const tr = element('tr'), td = element('td', 'empty', 'Waiting for collection data.'); tr.append(td); $('#rows').replaceChildren(tr); }
  }
}
theme();
readUrl();
controls();
load();
setInterval(load, 180000);
