'use strict';
const $ = selector => document.querySelector(selector);
const format = new Intl.NumberFormat('en-US');
let data, lens = 'sustained', sortKey = '6.pct', direction = -1;
const descriptions = {
  sustained: 'Positive growth in every available window, ranked by 6-hour percentage gain.',
  absolute: 'The largest net player gains over the past 6 hours.',
  momentum: 'The fastest percentage growth over the past hour.',
  entrants: 'First observed or seen below 1,000 players within the past 24 hours. Ranked by current players.'
};
const defaults = {sustained:'6.pct', absolute:'6.delta', momentum:'1.pct', entrants:'players'};
const demo = new URLSearchParams(location.search).get('demo') === '1';
if (demo) document.body.dataset.demo = 'true';
try { const theme = localStorage.getItem('theme'); if (['light','dark'].includes(theme)) document.documentElement.dataset.theme = theme; } catch {}
$('#theme').addEventListener('click', () => {
  const isDark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = isDark ? 'light' : 'dark';
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem('theme', theme); } catch {}
});
function value(game, key) {
  if (!key.includes('.')) return game[key];
  const [window, field] = key.split('.');
  return game.windows[window]?.[field] ?? null;
}
function compare(a,b) {
  const av=value(a,sortKey), bv=value(b,sortKey);
  if(av == null && bv == null) return b.players-a.players;
  if(av == null) return 1;
  if(bv == null) return -1;
  const result=typeof av === 'string' ? av.localeCompare(bv) : av-bv;
  return result*direction || b.players-a.players || a.game_id.localeCompare(b.game_id);
}
function signed(n, percentage=false) { return `${n>0?'+':''}${percentage ? n.toFixed(1)+'%' : format.format(n)}`; }
function element(tag, className, text) {
  const el=document.createElement(tag);
  if(className) el.className=className;
  if(text !== undefined) el.textContent=text;
  return el;
}
function render() {
  if(!data) return;
  const games=data.games;
  $('#eligible').textContent=format.format(data.eligible_count);
  $('#floor').textContent=`${format.format(data.min_players)}+ concurrent players`;
  $('#sustained').textContent=format.format(games.filter(g=>g.sustained).length);
  $('#entrants').textContent=format.format(games.filter(g=>g.new_entrant).length);
  $('#history').textContent=`${format.format(data.history_hours)}h`;
  $('#snapshots').textContent=`${format.format(data.snapshot_count)} completed snapshots`;
  $('#lens-description').textContent=descriptions[lens];
  const age=data.latest_snapshot ? (Date.now()-Date.parse(data.latest_snapshot))/60000 : Infinity;
  const stale=age>data.stale_minutes;
  $('#status-dot').classList.toggle('stale',stale);
  $('#freshness').textContent=demo ? 'Design preview · sample games' : !data.latest_snapshot ? 'Awaiting first collection' : stale ? 'Collection is overdue' : 'Tracking player counts';
  $('#updated').textContent=data.latest_snapshot ? `Last snapshot ${new Date(data.latest_snapshot).toLocaleString()}` : 'Snapshots every ~15 minutes';
  let selected=games.filter(g=> lens==='sustained' ? g.sustained && g.windows['6']!==null : lens==='entrants' ? g.new_entrant : g.windows[lens==='momentum'?'1':'6']!==null);
  const pending=!selected.length && games.length && lens!=='entrants' && !games.some(g=>g.windows[lens==='momentum'?'1':'6']!==null);
  // Show actual traction during cold start, explicitly unranked by growth.
  if(pending) selected=[...games];
  selected.sort(compare);
  const total=selected.length;
  selected=selected.slice(0,data.top_n);
  $('#result-count').textContent=`${selected.length} of ${total} games${pending?' · awaiting history':''}`;
  const notices=[];
  if(demo) notices.push('Sample data for design review. These are fictional games and simulated growth.');
  if(stale && !demo) notices.push('No recent snapshot. Counts may be outdated; check the GitHub Actions run.');
  if(data.history_hours<24) notices.push('History is warming up. Each comparison appears when a snapshot is available within ±20 minutes of its target. NEW means first observed by this tracker, not newly released.');
  if(pending) notices.push('This growth lens is waiting for history. Showing games by available values for now; no growth ranking is implied.');
  $('#notice').hidden=!notices.length;
  $('#notice').classList.remove('error');
  $('#notice').textContent=notices.join(' ');
  const tbody=$('#rows'); tbody.replaceChildren();
  if(!selected.length) {
    const tr=element('tr'); const td=element('td','empty',games.length?'No games match this lens right now. Try another ranking lens.':'No games above the player floor in the latest collection.');
    td.colSpan=6;tr.append(td);tbody.append(tr);
  }
  selected.forEach((game,index)=>{
    const tr=element('tr');tr.append(element('td','rank',pending?'—':String(index+1)));
    const nameCell=element('td'), wrapper=element('div','game');
    const icon=element('img','icon'); icon.alt='';icon.loading='lazy';icon.referrerPolicy='no-referrer';
    try { const url=new URL(game.icon_url);if(url.protocol==='https:') icon.src=url.href; } catch {}
    icon.addEventListener('error',()=>{icon.removeAttribute('src');icon.style.visibility='hidden';},{once:true});
    const info=element('div','game-info');const link=element('a','',game.name);
    link.href=`https://www.roblox.com/games/${encodeURIComponent(game.game_id)}`;link.target='_blank';link.rel='noopener noreferrer';link.title=game.name;
    info.append(link);const badges=element('div','badges');
    if(game.sustained) {const badge=element('span','badge rising','↗ SUSTAINED');badge.title=`Positive across ${game.available_windows} available comparison windows`;badges.append(badge);}
    if(game.new_entrant) {const badge=element('span','badge','NEW');badge.title=game.crossed_floor?'Observed below the player floor in the last 24 hours':'First observed by this tracker within 24 hours';badges.append(badge);}
    info.append(badges);wrapper.append(icon,info);nameCell.append(wrapper);tr.append(nameCell,element('td','number',format.format(game.players)));
    ['1','6','24'].forEach(window=>{const td=element('td');const metric=game.windows[window];
      if(metric) {td.className=metric.delta>0?'positive':metric.delta<0?'negative':'muted';td.append(element('span','delta',signed(metric.delta)),element('span','pct',signed(metric.pct,true)));td.title=`Compared with ${new Date(metric.baseline_at).toLocaleString()} · ${format.format(metric.baseline_players)} players`;}
      else {td.append(element('span','muted','—'));td.title='No historical snapshot within the matching tolerance';}tr.append(td);
    });tbody.append(tr);
  });
  document.querySelectorAll('[data-sort]').forEach(button=>{const active=button.dataset.sort===sortKey;button.classList.toggle('selected',active);button.setAttribute('aria-label',`${button.textContent.replace('↕','').trim()}: ${active?(direction===1?'ascending':'descending'):'sort'}`);});
  document.querySelectorAll('th').forEach(th=>th.removeAttribute('aria-sort'));
  document.querySelector(`[data-sort="${sortKey}"]`)?.closest('th').setAttribute('aria-sort',direction===1?'ascending':'descending');
}
document.querySelectorAll('[data-lens]').forEach(button=>button.addEventListener('click',()=>{
  lens=button.dataset.lens;sortKey=defaults[lens];direction=-1;
  document.querySelectorAll('[data-lens]').forEach(b=>{b.classList.toggle('active',b===button);b.setAttribute('aria-pressed',String(b===button));});render();
}));
document.querySelectorAll('[data-sort]').forEach(button=>button.addEventListener('click',()=>{
  direction=sortKey===button.dataset.sort ? -direction : button.dataset.sort==='name'?1:-1;sortKey=button.dataset.sort;render();
}));
async function load() {
  try {
    const response=await fetch(demo?'demo.json':'data.json',{cache:'no-store',signal:AbortSignal.timeout(15000)});
    if(!response.ok) throw new Error(`HTTP ${response.status}`);
    const next=await response.json();
    if(!Array.isArray(next.games)) throw new Error('Invalid leaderboard response');
    data=next;render();
  } catch(error) {
    if(data) render();
    $('#notice').hidden=false;$('#notice').classList.add('error');
    $('#notice').textContent=`Could not refresh the leaderboard (${error.message}). ${data?'Showing the last loaded snapshot.':'The first successful workflow run will publish the data.'} Retrying in 3 minutes.`;
    $('#freshness').textContent='Refresh unavailable';$('#status-dot').classList.add('stale');
    if(!data) {$('#result-count').textContent='Data unavailable';$('#rows').replaceChildren();const tr=element('tr'),td=element('td','empty','Waiting for collection data.');td.colSpan=6;tr.append(td);$('#rows').append(tr);}
  }
}
load();setInterval(load,180000);
