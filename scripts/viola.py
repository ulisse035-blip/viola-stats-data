#!/usr/bin/env python3
"""Viola Stats – aggiornamento dati (gira su GitHub Actions o in locale).

Uso:  python3 scripts/viola.py <modo> [--root CARTELLA] [--match ID] [--forza]
Modi:
  auto            per il cron ogni 15': se c'e' una partita della Fiorentina in corso
                  esegue 'intervallo' a fine primo tempo e 'fine' a fine partita (altrimenti esce subito)
  intervallo      salva statistiche del primo tempo (squadra + giocatori) della partita in corso
  fine            salva le statistiche complete delle partite concluse + classifica/rosa/marcatori/calendario
  heatmap         aggiunge heatmap/posizioni medie quando FotMob le pubblica + statistiche stagionali giocatori
  season-rollover passa alla nuova stagione e cancella la piu' vecchia (tiene sempre 2 stagioni)
  backfill        ricostruisce tutto (tutte le partite delle 2 stagioni, giocatori, stagioni)
Scrive manifest.json con i file modificati/eliminati (utile per pubblicare a mano).
Fonti: FotMob (pagine pubbliche + endpoint heatmap/statistiche) e ESPN (API pubbliche) per verifica e riserva.
Solo libreria standard Python 3.9+.
"""
import argparse, datetime as dt, gzip, hashlib, json, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

ROME = ZoneInfo('Europe/Rome')
FM_TEAM, ESPN_TEAM = 8535, '109'
UA = 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36'
# (id FotMob, nome IT) – competizioni ufficiali possibili per la Fiorentina
FM_LEAGUES = [(55, 'Serie A'), (141, 'Coppa Italia'), (222, 'Supercoppa Italiana'), (10216, 'Conference League'), (10615, 'Conference League'),
              (73, 'Europa League'), (10613, 'Europa League'), (42, 'Champions League'), (10611, 'Champions League')]
FM_QUAL = {10615, 10613, 10611}
ESPN_LEAGUES = {'ita.1': 'Serie A', 'ita.coppa_italia': 'Coppa Italia', 'ita.super_cup': 'Supercoppa Italiana', 'uefa.europa.conf': 'Conference League',
                'uefa.europa.conf_qual': 'Conference League', 'uefa.europa': 'Europa League', 'uefa.europa_qual': 'Europa League',
                'uefa.champions': 'Champions League', 'uefa.champions_qual': 'Champions League'}
ROOT = '.'
CHANGED, DELETED, LOG = [], [], []
STATS = {'req': 0, 'err': 0, 'fm_blocked': False}

def log(*a):
    s = ' '.join(str(x) for x in a); LOG.append(s); print(s, flush=True)

def get(url, js=False, tries=3, raw=False):
    err = None
    for i in range(tries):
        try:
            STATS['req'] += 1
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Language': 'it,en;q=0.8', 'Accept-Encoding': 'gzip'})
            with urllib.request.urlopen(req, timeout=30) as r:
                b = r.read()
                if r.headers.get('Content-Encoding') == 'gzip': b = gzip.decompress(b)
            time.sleep(0.4)
            if raw: return b
            t = b.decode('utf-8')
            return json.loads(t) if js else t
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (403, 429) and 'fotmob' in url: STATS['fm_blocked'] = True
            if e.code in (400, 404): break
            time.sleep(2 * (i + 1))
        except Exception as e:
            err = e; time.sleep(2 * (i + 1))
    STATS['err'] += 1
    raise RuntimeError(f'{url}: {err}')

def next_data(html):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
    if not m: raise RuntimeError('pagina senza __NEXT_DATA__ (blocco o cambio struttura)')
    return json.loads(m.group(1))['props']['pageProps']

_TR = str.maketrans({'ð': 'd', 'Ð': 'D', 'þ': 'th', 'Þ': 'Th', 'æ': 'ae', 'Æ': 'Ae', 'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'ß': 'ss', 'đ': 'd', 'Đ': 'D'})
def norm(s): return unicodedata.normalize('NFKD', (s or '').translate(_TR)).encode('ascii', 'ignore').decode().lower().strip()
def r2(x, n=2): return None if x is None else round(float(x), n)
def utc(s): return dt.datetime.fromisoformat(s.replace('Z', '+00:00').replace('.000', ''))
def now_utc(): return dt.datetime.now(dt.timezone.utc)
def iso(t): return t.strftime('%Y-%m-%dT%H:%M:%SZ')
def mid_from(d): return utc(d).astimezone(ROME).strftime('%Y%m%d')
def season_of(t):  # '2026-27'
    t = t.astimezone(ROME); a = t.year if t.month >= 7 else t.year - 1
    return f'{a}-{str(a + 1)[2:]}'
def s_start(s): return int(s[:4])
def fm_season(s): a = s_start(s); return f'{a}/{a + 1}'

def path(*p): return os.path.join(ROOT, *p)
def rjson(p, default=None):
    try: return json.load(open(path(p)))
    except Exception: return default
def dump(o): return json.dumps(o, ensure_ascii=False, separators=(',', ':'))
def wjson(p, o, ignore=('agg',)):
    """scrive solo se il contenuto (escluso 'agg') e' cambiato. Ritorna True se scritto."""
    old = rjson(p)
    if old is not None:
        a = {k: v for k, v in old.items() if k not in ignore} if isinstance(old, dict) else old
        b = {k: v for k, v in o.items() if k not in ignore} if isinstance(o, dict) else o
        if dump(a) == dump(b): return False
    os.makedirs(os.path.dirname(path(p)) or '.', exist_ok=True)
    open(path(p), 'w').write(dump(o))
    if p not in CHANGED: CHANGED.append(p)
    return True

# ------------------------------------------------------------------ calendario
def fm_league(lid, s):
    pp = next_data(get(f'https://www.fotmob.com/leagues/{lid}/fixtures/x?season={urllib.parse.quote(fm_season(s))}'))
    if pp.get('details', {}).get('selectedSeason') not in (fm_season(s), None): return None
    return pp

def fixtures(s):
    """tutte le partite ufficiali della Fiorentina nella stagione s (giocate e da giocare)"""
    out = {}
    tables = {}
    for lid, nome in FM_LEAGUES:
        try: pp = fm_league(lid, s)
        except Exception as e:
            if 'fotmob' in str(e): log(f'  FotMob lega {lid}: {e}')
            continue
        if not pp: continue
        if lid == 55: tables['seriea'] = pp.get('table')
        for m in pp['fixtures']['allMatches']:
            if FM_TEAM not in (int(m['home']['id']), int(m['away']['id'])): continue
            st = m['status']
            if st.get('cancelled'): continue
            k = mid_from(st['utcTime'])
            rnd = str(m.get('round') or '')
            if lid in FM_QUAL: rnd = 'Q-' + rnd
            out[k] = {'id': k, 'fm': int(m['id']), 'data': st['utcTime'].replace('.000', ''), 'comp': nome, 'compId': lid, 'turno': rnd,
                      'casa': {'id': int(m['home']['id']), 'nome': m['home']['name']}, 'osp': {'id': int(m['away']['id']), 'nome': m['away']['name']},
                      'giocata': bool(st.get('finished')), 'iniziata': bool(st.get('started')), 'ris': st.get('scoreStr'),
                      'tbd': bool(st.get('matchDateTbd'))}
    # ESPN: id per la verifica + riserva se FotMob non risponde
    for lg, nome in ESPN_LEAGUES.items():
        try: d = get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/teams/{ESPN_TEAM}/schedule?season={s_start(s)}', js=True)
        except Exception: continue
        for e in d.get('events', []):
            k = mid_from(e['date']); c = e['competitions'][0]
            x = out.get(k)
            done = c['status']['type'].get('completed', False) or c['status']['type']['name'].startswith('STATUS_F')
            if x is None:
                if season_of(utc(e['date'])) != s: continue
                h = [t for t in c['competitors'] if t['homeAway'] == 'home'][0]; a = [t for t in c['competitors'] if t['homeAway'] == 'away'][0]
                x = out[k] = {'id': k, 'fm': None, 'data': e['date'], 'comp': nome, 'compId': None, 'turno': '',
                              'casa': {'id': None, 'nome': h['team']['displayName']}, 'osp': {'id': None, 'nome': a['team']['displayName']},
                              'giocata': done, 'iniziata': done, 'ris': None, 'tbd': False}
            x['espn'] = e['id']; x['espn_lg'] = lg
    return out, tables

# ------------------------------------------------------------------ FotMob partita
SHOT_T = {'Goal': 'G', 'AttemptSaved': 'S', 'Miss': 'M', 'Post': 'P'}

def fm_match_page(fm_id):
    return next_data(get(f'https://www.fotmob.com/match/{fm_id}'))

def match_state(pp):
    hd = pp.get('header', {}).get('status', {}) or {}; g = pp['general']
    lt = (hd.get('liveTime') or {}).get('short', '') or ''
    if g.get('finished'): return 'FT'
    if lt in ('HT', 'Pausa', 'Halftime') or (hd.get('reason') or {}).get('short') == 'HT': return 'HT'
    if g.get('started'): return 'LIVE'
    return 'NS'

TIRI_K = ['sq', 'pid', 'nome', 'min', 'rec', 'esito', 'x', 'y', 'xg', 'xgot', 'situaz', 'tipo', 'porta', 'murato', 'autogol',
          'gy', 'gz', 'bx', 'by', 'linea', 'area']

def shots_of(c, home):
    """tiri FotMob -> liste compatte (chiavi in TIRI_K)"""
    tiri = []
    for s in (c.get('shotmap') or {}).get('shots') or []:
        tiri.append(['c' if s['teamId'] == home['id'] else 'o', s.get('playerId'), s.get('playerName'), s.get('min'), s.get('minAdded') or 0,
                     SHOT_T.get(s.get('eventType'), s.get('eventType')), r2(s['x'], 1), r2(s['y'], 1), r2(s.get('expectedGoals')),
                     r2(s.get('expectedGoalsOnTarget')), s.get('situation'), s.get('shotType'), 1 if s.get('isOnTarget') else 0,
                     1 if s.get('isBlocked') else 0, 1 if s.get('isOwnGoal') else 0,
                     # v1.4 (in coda, le app 1.2/1.3 leggono solo i primi 15 campi): punto in cui il tiro attraversa la linea di porta
                     # (gy = coordinata y del campo 0-68, porta tra 30.34 e 37.66; gz = altezza in metri), punto del muro, salvataggio sulla linea, tiro da dentro l'area
                     r2(s.get('goalCrossedY')), r2(s.get('goalCrossedZ')), r2(s.get('blockedX'), 1), r2(s.get('blockedY'), 1),
                     1 if s.get('isSavedOffLine') else 0, 1 if s.get('isFromInsideBox') else 0])
    return tiri

def build_fotmob(pp, meta, stato):
    g = pp['general']; c = pp['content']; hd = pp['header']
    teams = hd['teams']; home, away = teams[0], teams[1]
    if FM_TEAM not in (home['id'], away['id']): raise RuntimeError('Fiorentina non presente')
    lu = c.get('lineup') or {}
    def side(t, key):
        L = lu.get(key) or {}
        return {'id': t['id'], 'nome': t['name'], 'mod': L.get('formation'), 'all': (L.get('coach') or {}).get('name'), 'voto': L.get('rating')}
    ib = c['matchFacts'].get('infoBox') or {}
    out = {'v': 2, 'id': mid_from(g['matchTimeUTCDate']), 'fm': g['matchId'] and int(g['matchId']), 'stato': stato,
           'data': g['matchTimeUTCDate'].replace('.000', ''), 'comp': meta.get('comp') or g.get('leagueName'),
           'turno': meta.get('turno') or str((ib.get('Tournament') or {}).get('round') or ''), 'stagione': meta['stagione'],
           'casa': side(home, 'homeTeam'), 'osp': side(away, 'awayTeam'), 'fio': 'c' if home['id'] == FM_TEAM else 'o',
           'ris': [home.get('score') or 0, away.get('score') or 0],
           'stadio': (ib.get('Stadium') or {}).get('name'), 'citta': (ib.get('Stadium') or {}).get('city'),
           'arbitro': (ib.get('Referee') or {}).get('text'), 'spett': ib.get('Attendance')}
    reason = (hd.get('status') or {}).get('reason') or {}
    if reason.get('longKey') in ('afterpenalties', 'afterextratime', 'aet'): out['nota'] = reason.get('long')
    ps = (hd.get('status') or {}).get('penaltyShootoutScore') or hd.get('status', {}).get('penaltyShootout')
    if ps: out['rigori'] = ps
    ev = []
    for e in c['matchFacts'].get('events', {}).get('events', []):
        t = e.get('type'); h = 1 if e.get('isHome') else 0; m = e.get('time'); plus = e.get('overloadTime') or 0
        nm = e.get('nameStr') or (e.get('player') or {}).get('name')
        if e.get('isPenaltyShootoutEvent'): continue
        if t == 'Goal':
            kind = 'AG' if e.get('ownGoal') else ('RG' if (e.get('suffixKey') or '').startswith('pen') or e.get('goalDescriptionKey') == 'penalty' else 'G')
            ev.append([m, plus, kind, h, nm, e.get('assistInput'), '%d-%d' % tuple(e.get('newScore') or [0, 0]), (e.get('player') or {}).get('id')])
        elif t == 'Card':
            ev.append([m, plus, {'Yellow': 'Y', 'Red': 'R', 'YellowRed': 'YR'}.get(e.get('card'), 'Y'), h, nm, None, None, (e.get('player') or {}).get('id')])
        elif t == 'Substitution':
            sw = e.get('swap') or [{}, {}]
            ev.append([m, plus, 'S', h, sw[0].get('name'), sw[1].get('name') if len(sw) > 1 else None, None, sw[0].get('id')])
        elif t == 'MissedPenalty':
            ev.append([m, plus, 'RS', h, nm, None, None, (e.get('player') or {}).get('id')])
        elif t == 'VAR':
            ev.append([m, plus, 'VAR', h, nm, (e.get('VAR') or {}).get('decision', {}).get('value') if isinstance(e.get('VAR'), dict) else None, None, None])
    out['ev'] = ev
    st = {}
    for per, lab in (('All', 'T'), ('FirstHalf', '1T'), ('SecondHalf', '2T')):
        P = ((c.get('stats') or {}).get('Periods') or {}).get(per)
        if not P: continue
        rows, seen = [], set()
        for grp in P['stats']:
            for x in grp.get('stats') or []:
                v = x.get('stats')
                if not v or v[0] is None or x.get('type') == 'title': continue
                k = x.get('key') or x.get('title')
                if k in seen: continue
                seen.add(k); rows.append([k, x.get('title'), v[0], v[1]])
        if rows: st[lab] = rows
    if stato == 'HT' and '1T' in st: st = {'T': st['1T'], '1T': st['1T']}
    out['st'] = st
    out['mom'] = [[r2(p['minute'], 1), int(p['value'])] for p in ((c.get('momentum') or {}).get('main', {}) or {}).get('data') or []]
    if c.get('attackingZones'): out['zone'] = c['attackingZones']
    out['tiriK'] = TIRI_K
    out['tiri'] = shots_of(c, home)
    psd = c.get('playerStats') or {}
    def mins_of(pid):
        for grp in (psd.get(str(pid)) or {}).get('stats') or []:
            if 'Minutes played' in grp.get('stats', {}): return grp['stats']['Minutes played']['stat'].get('value')
        return None
    def plist(key):
        L = lu.get(key) or {}; res = []
        for tit, arr in ((1, L.get('starters') or []), (0, L.get('subs') or [])):
            for p in arr:
                perf = p.get('performance') or {}
                sub = [x.get('time') for x in perf.get('substitutionEvents') or []]
                mins = mins_of(p['id'])
                if not tit and not sub and not mins: continue
                vl = p.get('verticalLayout') or {}
                res.append({'id': p['id'], 'n': p.get('name'), 'num': p.get('shirtNumber'), 'r': perf.get('rating'), 'tit': tit,
                            'cap': 1 if p.get('isCaptain') else 0, 'gk': 1 if p.get('positionId') == 11 or (psd.get(str(p['id'])) or {}).get('isGoalkeeper') else 0,
                            'pos': [r2(vl['x'], 3), r2(vl['y'], 3)] if tit and vl else None, 'min': mins, 'sub': sub or None})
        return res
    out['form'] = {'c': plist('homeTeam'), 'o': plist('awayTeam')}
    gioc, opta = {}, {}
    for pid, p in psd.items():
        d = {}
        for grp in p.get('stats') or []:
            for name, v in grp.get('stats', {}).items():
                s = v.get('stat') or {}; k = v.get('key') or name
                if s.get('value') is None: continue
                val = s['value']; val = r2(val) if isinstance(val, float) else val
                d[k] = [val, s['total']] if s.get('total') is not None else val
        if not d.get('minutes_played'): continue
        gioc[str(p['id'])] = {'s': 'c' if p.get('teamId') == home['id'] else 'o', 'st': d}
        if p.get('optaId'): opta[str(p['id'])] = str(p['optaId'])
    out['gioc'] = gioc
    out['heatN'] = 0
    out['_heatUrl'] = c.get('heatmapUrl'); out['_opta'] = opta
    out['fonti'] = {'fotmob': f"https://www.fotmob.com/match/{out['fm']}"}
    return out

def add_heatmaps(out):
    hu = out.pop('_heatUrl', None); opta = out.pop('_opta', {})
    if not hu:
        hu = f"/api/data/heatmap/match/{out['fm']}/heatmaps?heatmapUrl=" + urllib.parse.quote(f"https://pub.fotmob.com/prod/db/api/heatmap/match/{out['fm']}", safe='')
    try: H = get('https://www.fotmob.com' + hu if hu.startswith('/') else hu, js=True)
    except Exception as e:
        log(f'    heatmap non disponibili ({e})'); return
    n = 0
    for pid, oid in opta.items():
        svg = (H.get('players') or {}).get('p' + oid)
        if not svg or pid not in out['gioc']: continue
        pts = re.findall(r'cx="([\d.]+)" cy="([\d.]+)"', svg)
        if not pts: continue
        flat = []
        for a, b in pts: flat += [int(round(float(a))), int(round(float(b)))]
        xs = [float(a) for a, _ in pts]; ys = [float(b) for _, b in pts]
        out['gioc'][pid]['h'] = flat
        out['gioc'][pid]['pm'] = [r2(sum(xs) / len(xs), 1), r2(sum(ys) / len(ys), 1)]
        n += 1
    out['heatN'] = n
    if n: out['heatAgg'] = H.get('lastModified')

# ------------------------------------------------------------------ ESPN
def espn_summary(eid, lg): return get(f'https://site.api.espn.com/apis/site/v2/sports/soccer/{lg}/summary?event={eid}', js=True)

def espn_facts(E):
    comp = E['header']['competitions'][0]
    sc = {x['homeAway']: int(x.get('score') or 0) for x in comp['competitors']}
    ids = {x['homeAway']: x['team']['id'] for x in comp['competitors']}
    goals = []
    for d in comp.get('details') or []:
        if not d.get('scoringPlay') or 'shootout' in json.dumps(d.get('type', {})).lower(): continue
        m = re.match(r"(\d+)'", d['clock']['displayValue'] or '')
        who = d['participants'][0]['athlete']['displayName'] if d.get('participants') else ''
        goals.append({'min': int(m.group(1)) if m else None, 'side': 'c' if d['team']['id'] == ids['home'] else 'o', 'who': who})
    stats = {}
    for t in E['boxscore'].get('teams', []):
        stats[t.get('homeAway') or ('home' if t['team']['id'] == ids['home'] else 'away')] = {s['name']: s['displayValue'] for s in t.get('statistics', [])}
    return {'ris': [sc['home'], sc['away']], 'ids': [ids['home'], ids['away']], 'goals': goals, 'stats': stats,
            'state': comp['status']['type']['name']}

ESPN2FM = [('possessionPct', 'BallPossesion', 2.0), ('totalShots', 'total_shots', 1), ('shotsOnTarget', 'ShotsOnTarget', 1),
           ('wonCorners', 'corners', 1), ('foulsCommitted', 'fouls', 2), ('yellowCards', 'yellow_cards', 0), ('saves', 'keeper_saves', 1),
           ('totalPasses', 'passes', 8), ('interceptions', 'interceptions', 2)]
def num(v):
    m = re.match(r'-?[\d.]+', str(v)); return float(m.group(0)) if m else None

def validate(out, F):
    avv = []; n = 2
    if out['ris'] != F['ris']: return False, [f"risultato FotMob {out['ris']} ≠ ESPN {F['ris']}"], 1
    if ('c' if F['ids'][0] == ESPN_TEAM else 'o') != out['fio']: return False, ['casa/trasferta diversa tra le fonti'], 2
    fm_goals = sorted([(e[0], 'c' if e[3] else 'o') for e in out['ev'] if e[2] in ('G', 'RG', 'AG')])
    es_goals = sorted([(g['min'], g['side']) for g in F['goals']])
    n += 1
    if len(fm_goals) != len(es_goals): avv.append(f'numero gol negli eventi FotMob {len(fm_goals)} vs ESPN {len(es_goals)}')
    else:
        for (a, _), (b, _) in zip(fm_goals, es_goals):
            if a is None or b is None or abs(a - b) > 1: avv.append(f'minuto gol {a} vs {b}')
    fm_names = [norm(e[4]) for e in out['ev'] if e[2] in ('G', 'RG', 'AG') and e[4]]
    for g in F['goals']:
        n += 1
        w = norm(g['who'])
        if w and not any(w.split()[-1] in x or x.split()[-1] in w for x in fm_names):
            avv.append(f"marcatore ESPN '{g['who']}' al {g['min']}' con nome diverso su FotMob")
    if out.get('stato') == 'FT':
        T = {r[0]: (r[2], r[3]) for r in out['st'].get('T', [])}
        for ek, fk, tol in ESPN2FM:
            if fk not in T: continue
            if all(num(F['stats'].get(s, {}).get(ek)) == 0 for s in ('home', 'away')) and any((num(v) or 0) > 0 for v in T[fk]): continue   # ESPN 0-0: dato mancante
            for i, s in enumerate(('home', 'away')):
                ev = num(F['stats'].get(s, {}).get(ek)); fv = num(T[fk][i])
                if ev is None or fv is None or (ev == 0 and fv > 20): continue   # ESPN a volte ha 0 (dato mancante)
                n += 1
                if abs(ev - fv) > tol: avv.append(f"{fk} {'casa' if i == 0 else 'ospite'}: FotMob {T[fk][i]} vs ESPN {ev:g}")
    return True, avv, n

def build_espn_only(meta, E):
    F = espn_facts(E); comp = E['header']['competitions'][0]
    side = {}
    for x in comp['competitors']:
        side[x['homeAway']] = {'id': x['team']['id'], 'nome': x['team']['displayName'].replace('Internazionale', 'Inter').replace('AS Roma', 'Roma'), 'mod': None, 'all': None, 'voto': None}
    ros = {r['homeAway']: r for r in E.get('rosters', [])}
    for k in ('home', 'away'):
        if k in ros: side[k]['mod'] = ros[k].get('formation')
    stname = F['state']
    stato = 'FT' if stname.startswith('STATUS_F') else 'HT' if stname == 'STATUS_HALFTIME' else 'LIVE'
    out = {'v': 2, 'id': mid_from(comp['date']), 'espn': meta.get('espn'), 'stato': stato, 'data': comp['date'], 'comp': meta.get('comp'),
           'turno': meta.get('turno', ''), 'stagione': meta['stagione'], 'casa': side['home'], 'osp': side['away'],
           'fio': 'c' if side['home']['id'] == ESPN_TEAM else 'o', 'ris': F['ris'],
           'stadio': (E.get('gameInfo', {}).get('venue') or {}).get('fullName'), 'citta': ((E.get('gameInfo', {}).get('venue') or {}).get('address') or {}).get('city'),
           'arbitro': None, 'spett': E.get('gameInfo', {}).get('attendance') or None}
    hid = side['home']['id']; ev = []
    for k in E.get('keyEvents', []):
        t = k.get('type', {}).get('text', ''); m = re.match(r"(\d+)'(?:\+(\d+))?", k.get('clock', {}).get('displayValue') or '')
        if not m: continue
        h = 1 if (k.get('team') or {}).get('id') == hid else 0
        pp = [p['athlete']['displayName'] for p in k.get('participants', [])]
        mi, plus = int(m.group(1)), int(m.group(2) or 0)
        if t.startswith('Goal') or t.startswith('Penalty - Scored') or t == 'Own Goal':
            ev.append([mi, plus, 'AG' if 'Own' in t else ('RG' if 'Penalty' in t else 'G'), h, pp[0] if pp else None, pp[1] if len(pp) > 1 else None, None, None])
        elif t == 'Yellow Card': ev.append([mi, plus, 'Y', h, pp[0] if pp else None, None, None, None])
        elif t == 'Red Card': ev.append([mi, plus, 'R', h, pp[0] if pp else None, None, None, None])
        elif t == 'Substitution': ev.append([mi, plus, 'S', h, pp[0] if pp else None, pp[1] if len(pp) > 1 else None, None, None])
    sc = [0, 0]
    for e in ev:
        if e[2] in ('G', 'RG', 'AG'): sc[0 if e[3] else 1] += 1; e[6] = '%d-%d' % tuple(sc)
    out['ev'] = ev
    lab = {'possessionPct': 'BallPossesion', 'totalShots': 'total_shots', 'shotsOnTarget': 'ShotsOnTarget', 'blockedShots': 'blocked_shots',
           'totalPasses': 'passes', 'accuratePasses': 'accurate_passes', 'accurateCrosses': 'accurate_crosses', 'accurateLongBalls': 'long_balls_accurate',
           'totalTackles': 'matchstats.headers.tackles', 'interceptions': 'interceptions', 'totalClearance': 'clearances', 'saves': 'keeper_saves',
           'wonCorners': 'corners', 'offsides': 'Offsides', 'foulsCommitted': 'fouls', 'yellowCards': 'yellow_cards', 'redCards': 'red_cards'}
    rows = []
    for ek, fk in lab.items():
        a = F['stats'].get('home', {}).get(ek); b = F['stats'].get('away', {}).get(ek)
        if a is None or b is None: continue
        if ek == 'possessionPct': a, b = round(float(a)), round(float(b))
        else: a, b = num(a), num(b); a, b = (int(a) if a is not None else a), (int(b) if b is not None else b)
        rows.append([fk, fk, a, b])
    out['st'] = {'T': rows}; out['mom'] = []; out['tiri'] = []; out['tiriK'] = []
    form, gioc = {}, {}
    m = {'totalGoals': 'goals', 'goalAssists': 'assists', 'totalShots': 'total_shots', 'shotsOnTarget': 'ShotsOnTarget', 'foulsCommitted': 'fouls',
         'foulsSuffered': 'was_fouled', 'saves': 'saves', 'goalsConceded': 'goals_conceded', 'yellowCards': 'yellow_cards'}
    for k, kk in (('home', 'c'), ('away', 'o')):
        res = []
        for p in (ros.get(k) or {}).get('roster', []):
            if not p.get('starter') and not p.get('subbedIn'): continue
            pid = 'e' + p['athlete']['id']
            res.append({'id': pid, 'n': p['athlete']['displayName'], 'num': p.get('jersey'), 'r': None, 'tit': 1 if p.get('starter') else 0,
                        'cap': 0, 'gk': 1 if (p.get('position') or {}).get('abbreviation') == 'G' else 0, 'pos': None, 'min': None, 'sub': None})
            gioc[pid] = {'s': kk, 'st': {m[s['name']]: int(s['value']) for s in p.get('stats', []) if s['name'] in m and s.get('value') is not None}}
        form[kk] = res
    out['form'] = form; out['gioc'] = gioc; out['heatN'] = 0
    out['fonti'] = {'espn': f"https://www.espn.com/soccer/match/_/gameId/{meta.get('espn')}"}
    return out

def level(m):
    if not m: return 0
    q = {'HT': 0, 'LIVE': 0, 'FT': 10}.get(m.get('stato', 'FT'), 10)
    return q + (3 if m.get('heatN') else 2 if m.get('fm') else 1)

def build_match(meta, stato_req=None):
    """costruisce il JSON di una partita (FotMob + verifica ESPN, riserva ESPN). Ritorna (dati, motivi)"""
    why = []; E = None; new = None
    if meta.get('espn'):
        try: E = espn_summary(meta['espn'], meta['espn_lg'])
        except Exception as e: why.append(f'ESPN: {e}')
    if meta.get('fm'):
        try:
            pp = fm_match_page(meta['fm'])
            stato = match_state(pp)
            if stato_req == 'HT' and stato == 'LIVE': stato = 'HT'   # intervallo perso dal cron: uso i dati del 1° tempo
            if stato in ('FT', 'HT'):
                new = build_fotmob(pp, meta, stato)
                if stato == 'HT':
                    new['ev'] = [e for e in new['ev'] if e[0] <= 45]
                    sc = [0, 0]
                    for e in new['ev']:
                        if e[2] in ('G', 'RG', 'AG'): sc[0 if e[3] else 1] += 1
                    new['ris'] = sc
                    if match_state(pp) == 'LIVE': new['gioc'] = {}; new['nota'] = 'Dati di squadra del 1° tempo (aggiornamento all\'intervallo non riuscito)'
                    new.pop('_heatUrl', None); new.pop('_opta', None)
                else:
                    add_heatmaps(new)
                if meta.get('espn'):
                    new['espn'] = meta['espn']; new['fonti']['espn'] = f"https://www.espn.com/soccer/match/_/gameId/{meta['espn']}"
                if E and stato == 'FT':
                    ok, avv, n = validate(new, espn_facts(E))
                    new['verifica'] = {'ok': ok, 'controlli': n, 'avvisi': avv, 'fonte': 'ESPN'}
                    if not ok: why.append('verifica fallita: ' + '; '.join(avv)); new = None
                elif stato == 'FT':
                    new['verifica'] = {'ok': True, 'controlli': 0, 'avvisi': ['ESPN non disponibile: non verificata'], 'fonte': None}
            else:
                why.append(f'FotMob stato {stato}')
        except Exception as e:
            why.append(f'FotMob: {e}'); new = None
    if new is None and E is not None:
        try:
            st = espn_facts(E)['state']
            if st.startswith('STATUS_F') or st == 'STATUS_HALFTIME':
                new = build_espn_only(meta, E)
                new['verifica'] = {'ok': True, 'controlli': 0, 'avvisi': ['solo ESPN (FotMob non disponibile)'] + why, 'fonte': 'ESPN'}
        except Exception as e: why.append(f'ESPN-only: {e}')
    if new: new.pop('_heatUrl', None); new.pop('_opta', None)
    return new, why

# ------------------------------------------------------------------ indice partite stagione
def save_match(s, new, prev):
    nl, ol = level(new), level(prev)
    if prev is not None and (nl < ol or (nl == ol and new.get('heatN', 0) < prev.get('heatN', 0))):
        return False, f'dati nuovi peggiori (livello {nl} < {ol}), tengo i precedenti'
    new['agg'] = iso(now_utc())
    return wjson(f's/{s}/p/{new["id"]}.json', new), 'ok'

def write_index(s, fx):
    lst = []
    for k, f in sorted(fx.items(), key=lambda kv: kv[1]['data'], reverse=True):
        m = rjson(f's/{s}/p/{k}.json')
        row = {'id': k, 'data': f['data'], 'comp': f['comp'], 'turno': f['turno'], 'casa': f['casa'], 'osp': f['osp'], 'tbd': f.get('tbd') or None}
        if m:
            fio = m['fio']
            row.update(ris=m['ris'], stato=m.get('stato', 'FT'), heat=bool(m.get('heatN')), liv=level(m) % 10, agg=m.get('agg'), voto=(m['casa'] if fio == 'c' else m['osp']).get('voto'))
            row['marc'] = [e[4] + (' (rig.)' if e[2] == 'RG' else '') for e in m['ev'] if e[2] in ('G', 'RG') and (e[3] == 1) == (fio == 'c')]
            ag = sum(1 for e in m['ev'] if e[2] == 'AG' and (e[3] == 1) != (fio == 'c'))
            if ag: row['marc'].append('autogol' if ag == 1 else f'{ag} autogol')
            row['casa'] = {'id': m['casa']['id'], 'nome': m['casa']['nome']}; row['osp'] = {'id': m['osp']['id'], 'nome': m['osp']['nome']}
            if m.get('rigori'): row['rigori'] = m['rigori']
        row = {k2: v for k2, v in row.items() if v is not None}
        lst.append(row)
    idx = {'v': 2, 'stagione': s, 'agg': iso(now_utc()), 'partite': lst}
    wjson(f's/{s}/partite.json', idx)
    return idx

# ------------------------------------------------------------------ stagione (classifica, rosa, marcatori, formazione)
def zona_from_legend(table):
    z = {}
    try:
        for t in table[0]['data']['legend']:
            title = t['title']
            it = 'Champions League' if 'Champions' in title else 'Europa League' if 'Europa League' in title else 'Conference League' if 'Conference' in title else 'Retrocessione' if 'Releg' in title else title
            for i in t['indices']: z[i] = it
    except Exception: pass
    return z

def standings(s, table):
    rows = []
    if table:
        try:
            T = table[0]['data']['table']['all']; z = zona_from_legend(table)
            for i, r in enumerate(T):
                gf, gs = [int(x) for x in r['scoresStr'].split('-')]
                rows.append({'pos': r['idx'], 'squadra': r['name'], 'id': r['id'], 'pt': r['pts'], 'g': r['played'], 'v': r['wins'], 'n': r['draws'], 'p': r['losses'],
                             'gf': gf, 'gs': gs, 'dr': r['goalConDiff'], 'zona': z.get(i, '')})
        except Exception as e: log('  classifica FotMob non leggibile', e)
    check = []
    try:
        E = get(f'https://site.api.espn.com/apis/v2/sports/soccer/ita.1/standings?season={s_start(s)}', js=True)
        ent = E['children'][0]['standings']['entries']
        es = {}
        for e in ent:
            st = {x['name']: x.get('value') for x in e['stats']}
            es[norm(e['team']['displayName'])] = (int(st.get('points', 0)), int(st.get('gamesPlayed', 0)), int(st.get('pointsFor', 0)), int(st.get('pointsAgainst', 0)))
        if not rows:  # riserva ESPN
            for e in ent:
                st = {x['name']: x.get('value') for x in e['stats']}
                rows.append({'pos': int(st['rank']), 'squadra': e['team']['displayName'].replace('Internazionale', 'Inter').replace('AS Roma', 'Roma'), 'pt': int(st['points']), 'g': int(st['gamesPlayed']),
                             'v': int(st['wins']), 'n': int(st['ties']), 'p': int(st['losses']), 'gf': int(st['pointsFor']), 'gs': int(st['pointsAgainst']), 'dr': int(st['pointDifferential']), 'zona': ''})
            rows.sort(key=lambda r: r['pos']); check.append('Classifica da ESPN (FotMob non disponibile).')
        else:
            diff = []
            alias = {'inter': 'internazionale', 'roma': 'as roma'}
            for r in rows:
                k = norm(r['squadra']); k = k if k in es else alias.get(k, k)
                if k not in es: continue
                e = es[k]
                if (r['pt'], r['g'], r['gf'], r['gs']) != e: diff.append(f"{r['squadra']} FotMob {r['pt']}pt/{r['g']}g/{r['gf']}-{r['gs']} vs ESPN {e[0]}pt/{e[1]}g/{e[2]}-{e[3]}")
            check.append('Classifica: identica su FotMob ed ESPN (punti, partite, gol).' if not diff else 'Classifica, differenze FotMob/ESPN: ' + '; '.join(diff))
    except Exception as e: check.append(f'Classifica non verificata con ESPN ({e}).')
    return rows, check

ROLE = {'keepers': 'P', 'defenders': 'D', 'midfielders': 'C', 'attackers': 'A'}
FLAG = lambda cc: ''

def role_of(pid):
    """ruolo P/D/C/A di un giocatore non piu' in rosa, dal suo file giocatore (posizione FotMob)"""
    r = ((rjson(f'g/{pid}.json') or {}).get('ruolo') or '').lower()
    if 'keeper' in r: return 'P'
    if 'back' in r: return 'D'
    if 'wing' in r or 'striker' in r or 'forward' in r: return 'A'
    return 'C'

OFF_ROLE = {'Goalkeeper': 'P', 'Defender': 'D', 'Midfielder': 'C', 'Forward': 'A', 'Attacker': 'A', 'Striker': 'A'}
def official_squad():
    """prima squadra dal sito ufficiale acffiorentina.com: {numero: (nome, ruolo P/D/C/A)}; None se non leggibile"""
    try: h = get('https://www.acffiorentina.com/fiorentina-prima-squadra-maschile')
    except Exception as e: log('  rosa ufficiale non disponibile', e); return None
    out = {}
    for sec in re.split(r'<div class="role-wrapper"', h)[1:]:
        rid = (re.search(r'id="(\w+)"', sec) or [None, ''])[1]
        ro = next((v for k, v in OFF_ROLE.items() if rid.lower().startswith(k.lower())), None)
        if not ro: continue
        for num, nm in re.findall(r'<h3 class="player-number">\s*(\d+)\s*</h3>\s*<h3 class="player-name">(.*?)</h3>', sec, re.S):
            nm = ' '.join(re.sub(r'<[^>]+>', ' ', nm).split())
            out[num] = (nm, ro)
    if len(out) < 15: log(f'  rosa ufficiale: solo {len(out)} giocatori letti, la ignoro'); return None
    return out

def espn_squad():
    try:
        d = get('https://site.api.espn.com/apis/site/v2/sports/soccer/ita.1/teams/109/roster', js=True)
        r = {(str(a.get('jersey') or ''), norm(a.get('displayName'))) for a in d.get('athletes') or []}
        return r if len(r) >= 15 else None
    except Exception as e: log('  rosa ESPN non disponibile', e); return None

def current_members(members):
    """tiene solo chi e' davvero in prima squadra: FotMob incrociato con il sito ufficiale (o, in mancanza, con ESPN).
    FotMob a volte lascia in rosa giocatori ceduti o tornati in Primavera."""
    off = official_squad()
    def toks(x): return set(re.findall(r'[a-z]{3,}', norm(x)))
    if off:
        keep = {}
        for pid, (mbr, ro) in members.items():
            num = str(mbr.get('shirtNumber') or '')
            if num and num in off: keep[pid] = (mbr, off[num][1]); continue   # stesso numero di maglia
            t = toks(mbr['name'])                                              # senza numero: nome
            hit = next((v for v in off.values() if t and t <= toks(v[0])), None)
            if hit: keep[pid] = (mbr, hit[1])
        if len(keep) >= 15:
            missing = [v[0] for k, v in off.items() if not any(str(m.get('shirtNumber') or '') == k for m, _ in keep.values())]
            if missing: log('  in rosa sul sito ufficiale ma non trovati su FotMob:', missing)
            log(f'  rosa: {len(keep)} giocatori (FotMob incrociato con il sito ufficiale), esclusi:', [m['name'] for p, (m, _) in members.items() if p not in keep])
            return keep, 'sito ufficiale ACF Fiorentina'
    es = espn_squad()
    if es:
        keep = {pid: v for pid, v in members.items() if any((n and n == str(v[0].get('shirtNumber') or '')) or nm == norm(v[0]['name']) for n, nm in es)}
        if len(keep) >= 15:
            log(f'  rosa: {len(keep)} giocatori (FotMob incrociato con ESPN)'); return keep, 'ESPN'
    return members, None

def season_data(s, fx, table):
    """stagione.json (stesso formato delle stagioni precedenti dell'app) – calcolata dai dati partita"""
    cur = rjson(f's/{s}/stagione.json', {}) or {}
    clas, check = standings(s, table)
    if not clas and cur.get('classifica'): clas = cur['classifica']
    team = None
    try:
        team = next_data(get('https://www.fotmob.com/teams/8535/squad/fiorentina'))['fallback']['team-8535']
    except Exception as e: log('  rosa FotMob non disponibile', e)
    # aggregati Serie A dalle partite
    ag, mods, slots = {}, {}, {}
    sa = [k for k, f in fx.items() if f['comp'] == 'Serie A' and f['giocata']]
    for k in sa:
        m = rjson(f's/{s}/p/{k}.json')
        if not m or m.get('stato') != 'FT': continue
        fio = m['fio']; L = m['form'][fio]
        mod = (m['casa'] if fio == 'c' else m['osp']).get('mod')
        if mod: mods.setdefault(mod, []).append(m)
        for p in L:
            stt = (m['gioc'].get(str(p['id'])) or {}).get('st', {})
            mins = stt.get('minutes_played') or p.get('min') or 0
            a = ag.setdefault(str(p['id']), {'fm': p['id'], 'nome': p['n'], 'num': p.get('num'), 'pres': 0, 'tit': 0, 'min': 0, 'gol': 0, 'ass': 0, 'rig': 0})
            if not mins and not p['tit']: continue
            a['pres'] += 1; a['tit'] += p['tit']; a['min'] += mins or 0; a['num'] = p.get('num') or a['num']
        for e in m['ev']:
            if e[2] in ('G', 'RG') and (e[3] == 1) == (fio == 'c'):
                pid = str(e[7]) if e[7] else None
                if pid and pid in ag: ag[pid]['gol'] += 1; ag[pid]['rig'] += 1 if e[2] == 'RG' else 0
                for p in L:
                    if e[5] and (p['n'] == e[5] or norm(e[5]) in norm(p['n'])): ag[str(p['id'])]['ass'] += 1; break
    gioc = []; members = {}
    if team:
        for grp in team['squad']['squad']:
            if grp['title'] == 'coach': continue
            for mbr in grp['members']: members[str(mbr['id'])] = (mbr, ROLE.get(grp['title'], 'C'))
    fonte_rosa = None
    if members: members, fonte_rosa = current_members(members)
    tin = {}
    if team:
        try:
            for t in team['transfers']['data'].get('Players in', []):
                if utc(t['transferDate']) >= dt.datetime(s_start(s), 6, 1, tzinfo=dt.timezone.utc): tin[str(t['playerId'])] = t.get('fromClub')
        except Exception: pass
    seen = set()
    for pid, (mbr, ro) in members.items():
        a = ag.get(pid, {'pres': 0, 'tit': 0, 'min': 0, 'gol': 0, 'ass': 0, 'rig': 0})
        note = [{'t': 'in', 'x': 'da ' + tin[pid]}] if pid in tin else []
        if mbr.get('injury'): note.append({'t': 'inf', 'x': 'infortunato'})
        gioc.append({'fm': int(pid), 'num': str(mbr.get('shirtNumber') or ''), 'naz': mbr.get('ccode'), 'nome': mbr['name'], 'ruolo': ro, 'note': note,
                     'cap': False, 'pres': a['pres'], 'tit': a['tit'], 'min': a['min'], 'gol': a['gol'], 'ass': a['ass'], 'rig': a['rig'], 'flag': ''})
        seen.add(pid)
    usciti = []
    for pid, a in ag.items():   # giocatori usciti: non compaiono piu' nella rosa della stagione in corso (restano nelle partite giocate)
        if pid in seen or not a['pres']: continue
        usciti.append({'fm': int(pid), 'num': str(a['num'] or ''), 'nome': a['nome'], 'ruolo': role_of(pid),
                       'pres': a['pres'], 'tit': a['tit'], 'min': a['min'], 'gol': a['gol'], 'ass': a['ass'], 'rig': a['rig']})
    # formazione tipo = modulo piu' usato, per ogni posizione il titolare piu' frequente
    form = cur.get('formazione')
    if mods:
        mod, ms = max(mods.items(), key=lambda kv: len(kv[1]))
        pos = {}
        for m in ms:
            L = [p for p in m['form'][m['fio']] if p['tit'] and p['pos']]
            for p in L:
                key = (round(p['pos'][0], 2), round(p['pos'][1], 2))
                pos.setdefault(key, {}).setdefault(str(p['id']), [p, 0]); pos[key][str(p['id'])][1] += 1
        xi = []
        for key, cand in pos.items():
            p, nn = max(cand.values(), key=lambda v: v[1])
            if any(x['fm'] == p['id'] for x in xi): continue
            if members and str(p['id']) not in members:   # formazione tipo solo con giocatori ancora in rosa
                alt = [v for v in sorted(cand.values(), key=lambda v: -v[1]) if str(v[0]['id']) in members and not any(x['fm'] == v[0]['id'] for x in xi)]
                if not alt: continue
                p, nn = alt[0]
            xi.append({'slot': f'{key[0]}-{key[1]}', 'pos': 'G' if p['gk'] else '', 'nome': p['n'], 'fm': p['id'], 'num': p.get('num'), 'titolare_nel_modulo': nn,
                       'x': round((1 - key[0]) * 84 + 8), 'y': round(100 - key[1] * 100 + (-3 if p['gk'] else 0))})
        xi.sort(key=lambda x: -x['titolare_nel_modulo']); xi = xi[:11]
        form = {'modulo': mod, 'partite_modulo': len(ms), 'moduli': [{'modulo': k2, 'partite': len(v)} for k2, v in sorted(mods.items(), key=lambda kv: -len(kv[1]))], 'xi': xi}
    fio = next((r for r in clas if r['squadra'] == 'Fiorentina'), cur.get('fiorentina'))
    coach = None
    if team:
        for grp in team['squad']['squad']:
            if grp['title'] == 'coach' and grp['members']: coach = grp['members'][0]['name']
    out = {'stagione': s, 'in_corso': True, 'allenatore': {'nome': coach or (cur.get('allenatore') or {}).get('nome', ''), 'vice': (cur.get('allenatore') or {}).get('vice')},
           'classifica': clas, 'fiorentina': fio, 'totali': {'gol': sum(x['gol'] for x in gioc + usciti), 'assist': sum(x['ass'] for x in gioc + usciti)},
           'giocatori': gioc, 'usciti': usciti, 'rosa_aggiornata': now_utc().astimezone(ROME).strftime('%d/%m/%Y'), 'formazione': form, 'altri_senza_presenze': [],
           'foto': cur.get('foto'), 'verifiche': check + [f'Marcatori, assist e presenze calcolati da {len(sa)} partite di Serie A (FotMob/Opta), risultati e marcatori verificati con ESPN.']
                        + ([f'Rosa attuale: FotMob incrociato con {fonte_rosa}; esclusi i giocatori non più in prima squadra' + (' (' + ', '.join(u['nome'] for u in usciti) + ' hanno giocato in questa stagione prima di partire).' if usciti else '.')] if fonte_rosa else []),
           'partite_sa': len(sa)}
    if fio: out['fiorentina'] = dict(fio, zona=fio.get('zona', ''))
    wjson(f's/{s}/stagione.json', out, ignore=('agg', 'rosa_aggiornata'))
    return out

# ------------------------------------------------------------------ giocatori (statistiche stagionali)
GRID_X, GRID_Y = 35, 23   # griglia heatmap stagionale (celle 3 x ~3 m)
def grid(coords):
    g = [0] * (GRID_X * GRID_Y)
    for c in coords:
        x = min(GRID_X - 1, max(0, int(c['x'] / 105 * GRID_X))); y = min(GRID_Y - 1, max(0, int(c['y'] / 68 * GRID_Y)))
        g[y * GRID_X + x] += 1
    return g

def player_file(pid, seasons):
    pp = next_data(get(f'https://www.fotmob.com/players/{pid}/x'))['data']
    info = {i.get('translationKey'): (i.get('value') or {}).get('fallback') for i in pp.get('playerInformation') or []}
    want = {}
    for s in seasons:
        a = s_start(s); want[f'{a}/{a + 1}'] = s; want[str(a + 1)] = s   # anno solare (es. campionati sudamericani) -> stagione europea che finisce quell'anno
    career = ((pp.get('careerHistory') or {}).get('careerItems') or {}).get('senior', {}).get('teamEntries', [])
    filters = (pp.get('matchFilters') or {}).get('teams') or []
    out = {'id': pid, 'n': pp.get('name'), 'foto': f'https://images.fotmob.com/image_resources/playerimages/{pid}.png',
           'ruolo': ((pp.get('positionDescription') or {}).get('primaryPosition') or {}).get('label'),
           'sq': (pp.get('primaryTeam') or {}).get('teamName'), 'sqId': (pp.get('primaryTeam') or {}).get('teamId'),
           'info': {'eta': info.get('age_sentencecase'), 'altezza': info.get('height_sentencecase'), 'piede': info.get('preferred_foot'), 'naz': info.get('country_sentencecase'),
                    'num': info.get('shirt'), 'valore': info.get('transfer_value'), 'contratto': (info.get('contract_end') or {}).get('utcTime', '')[:10] if isinstance(info.get('contract_end'), dict) else info.get('contract_end')},
           'carriera': [{'sq': c['team'], 'id': c['teamId'], 'dal': (c.get('startDate') or '')[:10], 'al': (c.get('endDate') or '')[:10], 'pres': c.get('appearances'), 'gol': c.get('goals'), 'ass': c.get('assists'), 'prestito': (c.get('transferType') or {}).get('text')} for c in career[:6]],
           'stagioni': {}}
    for ss in pp.get('statSeasons') or []:
        s = want.get(ss['seasonName'])
        if not s: continue
        a = s_start(s); w0 = dt.datetime(a, 7, 1); w1 = dt.datetime(a + 1, 6, 30)
        for t in ss['tournaments']:
            try: d = get(f"https://www.fotmob.com/api/data/playerStats?playerId={pid}&seasonId={t['entryId']}", js=True)
            except Exception as e: log(f'    stats {pid} {t["name"]}: {e}'); continue
            if not d: continue
            # squadra: quelle del filtro partite con quella competizione e periodo sovrapposto
            teams = []
            for f in filters:
                if t['tournamentId'] not in f.get('leagueIds', []): continue
                for c in career:
                    if c['teamId'] != f['teamId']: continue
                    s0 = dt.datetime.fromisoformat(c['startDate'][:10]) if c.get('startDate') else dt.datetime(1990, 1, 1)
                    s1 = dt.datetime.fromisoformat(c['endDate'][:10]) if c.get('endDate') else dt.datetime(2100, 1, 1)
                    if s0 <= w1 and s1 >= w0 and f['name'] not in teams: teams.append(f['name'])
            groups = []
            for g in (d.get('statsSection') or {}).get('items') or []:
                items = [[i.get('localizedTitleId'), i.get('title'), i.get('statValue'), r2(i.get('per90')), r2(i.get('percentileRank'), 0), r2(i.get('percentileRankPer90'), 0), i.get('statFormat')] for i in g.get('items') or []]
                groups.append([g.get('localizedTitleId'), g.get('title'), items])
            top = [[i.get('localizedTitleId'), i.get('title'), i.get('statValue')] for i in ((d.get('topStatCard') or {}).get('items') or [])]
            co = ((d.get('heatmap') or {}).get('coordinates')) or []
            e = {'comp': t['name'], 'compId': t['tournamentId'], 'sn': ss['seasonName'], 'squadre': teams, 'top': top, 'gruppi': groups,
                 'heatN': len(co), 'heat': grid(co) if co else None,
                 'pm': [r2(sum(c['x'] for c in co) / len(co), 1), r2(sum(c['y'] for c in co) / len(co), 1)] if co else None,
                 'tiri': len(d.get('shotmap') or [])}
            out['stagioni'].setdefault(s, []).append(e)
    # partite recenti (voto, minuti) delle due stagioni
    rm = []
    for m in pp.get('recentMatches') or []:
        t = utc(m['matchDate']['utcTime'])
        if season_of(t) not in seasons: continue
        rm.append({'d': m['matchDate']['utcTime'][:10], 'sq': m.get('teamName'), 'avv': m.get('opponentTeamName'), 'casa': m.get('isHomeTeam'), 'comp': m.get('leagueName'),
                   'ris': f"{m.get('homeScore')}-{m.get('awayScore')}", 'min': m.get('minutesPlayed'), 'gol': m.get('goals'), 'ass': m.get('assists'),
                   'r': (m.get('ratingProps') or {}).get('rating'), 'potm': m.get('playerOfTheMatch'), 'fm': m.get('id')})
    out['recenti'] = rm
    out['tratti'] = [[i.get('key'), i.get('title'), r2(i.get('value'))] for i in ((pp.get('traits') or {}).get('items') or [])]
    return out

def aggregate_own_heat(pid, seasons):
    """heatmap stagionale calcolata da noi sommando le heatmap partita (solo partite della Fiorentina presenti nei dati)"""
    res = {}
    for s in seasons:
        idx = rjson(f's/{s}/partite.json', {}) or {}
        g = [0] * (GRID_X * GRID_Y); n = 0; sx = sy = 0; nm = 0
        for row in idx.get('partite', []):
            if not row.get('heat'): continue
            m = rjson(f"s/{s}/p/{row['id']}.json") or {}
            h = (m.get('gioc', {}).get(str(pid)) or {}).get('h')
            if not h: continue
            nm += 1
            for i in range(0, len(h), 2):
                x = min(GRID_X - 1, int(h[i] / 105 * GRID_X)); y = min(GRID_Y - 1, int(h[i + 1] / 68 * GRID_Y))
                g[y * GRID_X + x] += 1; n += 1; sx += h[i]; sy += h[i + 1]
        if n: res[s] = {'partite': nm, 'heatN': n, 'heat': g, 'pm': [round(sx / n, 1), round(sy / n, 1)]}
    return res

def update_players(seasons, only=None):
    ids = set()
    for s in seasons:
        st = rjson(f's/{s}/stagione.json', {}) or {}
        for g in st.get('giocatori', []):
            if g.get('fm'): ids.add(int(g['fm']))
        idx = rjson(f's/{s}/partite.json', {}) or {}
        for row in idx.get('partite', []):
            m = rjson(f"s/{s}/p/{row['id']}.json")
            if not m: continue
            for p in m['form'].get(m['fio'], []):
                if isinstance(p['id'], int): ids.add(p['id'])
    if only is not None: ids &= set(only)
    n = 0
    for pid in sorted(ids):
        try:
            f = player_file(pid, seasons)
            f['nostra'] = aggregate_own_heat(pid, seasons)
            f['agg'] = iso(now_utc())
            if wjson(f'g/{pid}.json', f): n += 1
        except Exception as e: log(f'  giocatore {pid}: {e}')
    log(f'  giocatori aggiornati: {n}/{len(ids)}')
    return ids

# ------------------------------------------------------------------ Understat (PPDA, PPDA concesso, deep completions) – solo Serie A
def understat_dates(s):
    """partite della Fiorentina su Understat per la stagione s; None se Understat non risponde con dati veri (es. manutenzione)"""
    y = s_start(s); dates = None
    for url in (f'https://understat.com/getTeamData/Fiorentina/{y}', f'https://understat.com/team/Fiorentina/{y}'):
        try: t = get(url, tries=2)
        except Exception as e: log('  Understat:', e); continue
        try:
            d = json.loads(t); dates = d.get('dates') or d.get('datesData')
        except Exception:
            m = re.search(r"datesData\s*=\s*JSON\.parse\('(.*?)'\)", t, re.S)
            if m: dates = json.loads(m.group(1).encode().decode('unicode_escape'))
        if dates: break
    if not dates: log('  Understat: nessun dato (sito in manutenzione o struttura cambiata)'); return None
    out = []
    for d in dates:
        try:
            if not d.get('isResult') or 'ppda' not in d: continue
            out.append({'data': d['datetime'][:10], 'side': d['side'], 'avv': d['a' if d['side'] == 'h' else 'h']['title'],
                        'ppda': d['ppda'], 'ppda_allowed': d['ppda_allowed'], 'deep': int(d['deep']), 'deep_allowed': int(d['deep_allowed'])})
        except Exception: continue
    return out or None

def understat_apply(s, fx):
    """aggiunge m['us'] = {ppda, oppda, deep, deep_c, ppda_att, ppda_def, ...} alle partite di Serie A; True se ha trovato dati"""
    U = understat_dates(s)
    if not U: return False
    n = 0
    for k, f in fx.items():
        if f['comp'] != 'Serie A': continue
        m = rjson(f's/{s}/p/{k}.json')
        if not m or m.get('stato') != 'FT': continue
        opp = norm((m['osp'] if m['fio'] == 'c' else m['casa'])['nome'])
        day = utc(m['data'])
        u = next((u for u in U if abs((dt.datetime.fromisoformat(u['data']).date() - day.date()).days) <= 1
                  and (u['side'] == 'h') == (m['fio'] == 'c') and (norm(u['avv'])[:5] in opp or opp[:5] in norm(u['avv']))), None)
        if not u: continue
        pa, po = u['ppda'], u['ppda_allowed']
        us = {'ppda': r2(pa['att'] / pa['def']) if pa['def'] else None, 'oppda': r2(po['att'] / po['def']) if po['def'] else None,
              'deep': u['deep'], 'deep_c': u['deep_allowed'], 'pa': [pa['att'], pa['def']], 'po': [po['att'], po['def']]}
        if m.get('us') != us:
            m['us'] = us; wjson(f's/{s}/p/{k}.json', m)
        n += 1
    log(f'  Understat: dati PPDA/deep in {n} partite di Serie A')
    return n > 0

# ------------------------------------------------------------------ statistiche di squadra stagionali (s/<stagione>/squadra.json)
TEAM_KEYS = ['BallPossesion', 'expected_goals', 'expected_goals_open_play', 'expected_goals_set_play', 'expected_goals_non_penalty', 'expected_goals_on_target',
             'total_shots', 'ShotsOnTarget', 'ShotsOffTarget', 'blocked_shots', 'shots_woodwork', 'shots_inside_box', 'shots_outside_box', 'big_chance', 'big_chance_missed_title', 'touches_opp_box',
             'passes', 'accurate_passes', 'own_half_passes', 'opposition_half_passes', 'long_balls_accurate', 'accurate_crosses', 'player_throws', 'corners', 'Offsides',
             'matchstats.headers.tackles', 'interceptions', 'shot_blocks', 'clearances', 'keeper_saves',
             'duel_won', 'ground_duels_won', 'aerials_won', 'dribbles_succeeded', 'fouls', 'yellow_cards', 'red_cards']
def _sv(v):
    """valore statistica partita -> (numero, percentuale o None)"""
    if v is None: return None, None
    if isinstance(v, (int, float)): return float(v), None
    m = re.match(r'\s*(-?[\d.]+)\s*(?:\((\d+)%\))?', str(v))
    if not m: return None, None
    return float(m.group(1)), (int(m.group(2)) if m.group(2) else None)

def team_season(s):
    idx = rjson(f's/{s}/partite.json', {}) or {}
    groups = {}
    for row in idx.get('partite', []):
        if not row.get('ris') or row.get('stato', 'FT') != 'FT': continue
        m = rjson(f"s/{s}/p/{row['id']}.json")
        if not m or m.get('stato', 'FT') != 'FT': continue
        fi = 0 if m['fio'] == 'c' else 1
        st = {r[0]: (r[2], r[3]) for r in (m.get('st') or {}).get('T', [])}
        for comp in ('Tutte le competizioni', m['comp']):
            g = groups.setdefault(comp, {'comp': comp, 'partite': 0, 'v': 0, 'n': 0, 'p': 0, 'gf': 0, 'gs': 0, 'cs': 0, 'casa': 0, 'S': {}, 'us': {'n': 0, 'pa': [0, 0], 'po': [0, 0], 'deep': 0, 'deep_c': 0}})
            gf, gs = m['ris'][fi], m['ris'][1 - fi]
            g['partite'] += 1; g['gf'] += gf; g['gs'] += gs; g['cs'] += gs == 0; g['casa'] += fi == 0
            g['v' if gf > gs else 'p' if gf < gs else 'n'] += 1
            for k in TEAM_KEYS:
                if k not in st: continue
                (fv, fp), (av, ap) = _sv(st[k][fi]), _sv(st[k][1 - fi])
                if fv is None or av is None: continue
                a = g['S'].setdefault(k, {'n': 0, 'f': 0.0, 'a': 0.0, 'ft': 0, 'at': 0, 'pct': False})
                a['n'] += 1; a['f'] += fv; a['a'] += av
                if k == 'accurate_passes' and 'passes' in st:   # tentativi = passaggi totali
                    a['pct'] = True; a['ft'] += _sv(st['passes'][fi])[0] or 0; a['at'] += _sv(st['passes'][1 - fi])[0] or 0
                elif fp is not None or ap is not None:
                    a['pct'] = True
                    a['ft'] += round(fv * 100 / fp) if fp else fv; a['at'] += round(av * 100 / ap) if ap else av
            u = m.get('us')
            if u and u.get('pa'):
                U = g['us']; U['n'] += 1; U['deep'] += u['deep']; U['deep_c'] += u['deep_c']
                U['pa'] = [U['pa'][0] + u['pa'][0], U['pa'][1] + u['pa'][1]]; U['po'] = [U['po'][0] + u['po'][0], U['po'][1] + u['po'][1]]
    out = []
    order = ['Tutte le competizioni', 'Serie A', 'Coppa Italia', 'Conference League', 'Europa League', 'Champions League', 'Supercoppa Italiana']
    for comp in sorted(groups, key=lambda c: order.index(c) if c in order else 99):
        g = groups[comp]
        if comp != 'Tutte le competizioni' and g['partite'] == groups['Tutte le competizioni']['partite']: continue   # una sola competizione: inutile ripeterla
        stats = []
        for k in TEAM_KEYS:
            a = g['S'].get(k)
            if not a: continue
            if k == 'BallPossesion': stats.append([k, round(a['f'] / a['n'], 1), round(a['a'] / a['n'], 1), a['n'], 'avg']); continue
            iv = lambda x: int(x) if float(x).is_integer() else round(x, 2)
            row = [k, iv(a['f']), iv(a['a']), a['n'], 'sum']
            if a['pct'] and a['ft'] and a['at']: row += [round(a['f'] / a['ft'] * 100), round(a['a'] / a['at'] * 100), int(a['ft']), int(a['at'])]
            stats.append(row)
        U = g['us']
        us = {'partite': U['n'], 'ppda': r2(U['pa'][0] / U['pa'][1]) if U['pa'][1] else None, 'oppda': r2(U['po'][0] / U['po'][1]) if U['po'][1] else None,
              'deep': U['deep'], 'deep_c': U['deep_c']} if U['n'] else None
        out.append({k: g[k] for k in ('comp', 'partite', 'v', 'n', 'p', 'gf', 'gs', 'cs', 'casa')} | {'stats': stats, 'understat': us})
    res = {'v': 1, 'stagione': s, 'agg': iso(now_utc()), 'gruppi': out,
           'fonte': 'Somma delle statistiche FotMob (dati Opta) di ogni partita ufficiale presente nell\'app; PPDA e deep completions da Understat (solo Serie A).'}
    wjson(f's/{s}/squadra.json', res)
    return res

# ------------------------------------------------------------------ modi
def cfg():
    c = rjson('seasons.json')
    if not c:
        cur = season_of(now_utc()); a = s_start(cur)
        c = {'v': 2, 'corrente': cur, 'stagioni': [cur, f'{a - 1}-{str(a)[2:]}']}
    return c

def calendario(s, fx):
    now = now_utc(); up = []
    for f in sorted(fx.values(), key=lambda f: f['data']):
        if f['giocata'] or utc(f['data']) < now - dt.timedelta(hours=3): continue
        up.append({k: f[k] for k in ('id', 'fm', 'data', 'comp', 'turno', 'casa', 'osp', 'tbd') if k in f} | ({'espn': f['espn'], 'espn_lg': f['espn_lg']} if f.get('espn') else {}))
    wjson('calendario.json', {'v': 2, 'stagione': s, 'agg': iso(now), 'prossime': up})
    return up

def do_matches(s, fx, which, stato_req=None, forza=False, heat_days=14):
    rep = []
    for k in sorted(fx):
        f = fx[k]; f['stagione'] = s
        prev = rjson(f's/{s}/p/{k}.json')
        age = (now_utc() - utc(f['data'])).total_seconds() / 86400
        if which == 'fine':
            if not f['giocata'] and not (f.get('iniziata') and age > 0.08): continue
            if prev and prev.get('stato') == 'FT' and not forza: continue
        elif which == 'heatmap':
            if not prev or prev.get('stato') != 'FT' or (prev.get('heatN') and not forza) or age > heat_days: continue
        elif which == 'intervallo':
            if age < 0 or age > 0.2 or f['giocata']: continue
            if prev and prev.get('stato') in ('HT', 'FT'): continue
        elif which == 'backfill':
            if not f['giocata']: continue
            if prev and level(prev) >= 13 and not forza: continue
        new, why = build_match(f, stato_req)
        if not new: rep.append(f'  ✗ {k} {f["casa"]["nome"]}-{f["osp"]["nome"]}: {"; ".join(why)}'); continue
        if which != 'intervallo' and new.get('stato') != 'FT': rep.append(f'  … {k}: non ancora finita'); continue
        ok, msg = save_match(s, new, prev)
        v = new.get('verifica') or {}
        rep.append(f"  {'+' if ok else '='} {k} {new['casa']['nome']} {new['ris'][0]}-{new['ris'][1]} {new['osp']['nome']} [{new.get('stato')}] heatmap {new.get('heatN', 0)}, verifica {v.get('controlli', 0)} controlli" + (', avvisi: ' + ' | '.join(v.get('avvisi', [])) if v.get('avvisi') else '') + ('' if ok else f' ({msg})'))
    return rep

def main():
    global ROOT
    ap = argparse.ArgumentParser(description='Viola Stats – aggiornamento dati')
    ap.add_argument('modo', choices=['auto', 'intervallo', 'fine', 'heatmap', 'season-rollover', 'backfill', 'giocatori', 'squadra'])
    ap.add_argument('--root', default=os.environ.get('VIOLA_ROOT', '.'))
    ap.add_argument('--forza', action='store_true')
    ap.add_argument('--stagione', help='solo questa stagione')
    a = ap.parse_args(); ROOT = a.root
    t0 = time.time(); c = cfg(); now = now_utc()
    log(f'Viola Stats – modo {a.modo} – {now.astimezone(ROME):%d/%m/%Y %H:%M} (ora italiana)')
    seasons = c['stagioni']; cur = c['corrente']
    rep = []
    if a.modo == 'auto':
        cal = (rjson('calendario.json', {}) or {}).get('prossime', [])
        live = [f for f in cal if -10 <= (now - utc(f['data'])).total_seconds() / 60 <= 200]
        if not live:
            log('  nessuna partita della Fiorentina in corso: niente da fare'); finish(t0); return 0
        f = dict(live[0], stagione=cur)
        prev = rjson(f"s/{cur}/p/{f['id']}.json")
        if prev and prev.get('stato') == 'FT': log('  partita gia\' salvata come finale'); finish(t0); return 0
        try: st = match_state(fm_match_page(f['fm'])) if f.get('fm') else None
        except Exception as e: st = None; log('  FotMob non raggiungibile:', e)
        if st is None and f.get('espn'):
            try:
                ss = espn_facts(espn_summary(f['espn'], f['espn_lg']))['state']
                st = 'FT' if ss.startswith('STATUS_F') else 'HT' if ss == 'STATUS_HALFTIME' else 'LIVE' if ss != 'STATUS_SCHEDULED' else 'NS'
            except Exception as e: log('  ESPN non raggiungibile:', e)
        log(f'  partita {f["casa"]["nome"]}-{f["osp"]["nome"]}: stato {st}')
        mins = (now - utc(f['data'])).total_seconds() / 60
        if st == 'HT' or (st == 'LIVE' and mins > 60 and not prev): a.modo = 'intervallo'
        elif st == 'FT': a.modo = 'fine'
        else: finish(t0); return 0
    if a.modo == 'season-rollover':
        nuova = season_of(now)
        if nuova == cur and not a.forza: log(f'  stagione corrente {cur}: nessun cambio'); finish(t0); return 0
        nb = s_start(nuova); keep = [nuova, f'{nb - 1}-{str(nb)[2:]}']
        for s in seasons:
            if s not in keep:
                for dp, _, fs in os.walk(path('s', s)):
                    for fn in fs:
                        rel = os.path.relpath(os.path.join(dp, fn), ROOT); os.remove(os.path.join(dp, fn)); DELETED.append(rel)
                log(f'  eliminata stagione {s}')
        c = {'v': 2, 'corrente': nuova, 'stagioni': keep, 'agg': iso(now)}
        wjson('seasons.json', c); seasons, cur = keep, nuova
        for fn in sorted(os.listdir(path('g'))) if os.path.isdir(path('g')) else []:   # giocatori: tolgo la stagione eliminata
            g = rjson(f'g/{fn}')
            if g and any(s not in keep for s in g.get('stagioni', {})):
                g['stagioni'] = {k: v for k, v in g['stagioni'].items() if k in keep}; wjson(f'g/{fn}', g)
        a.modo = 'backfill'
    todo = [a.stagione] if a.stagione else ([cur] if a.modo in ('intervallo', 'fine', 'heatmap') else seasons)
    for s in todo:
        log(f'– stagione {s}')
        fx, tables = fixtures(s)
        if STATS['fm_blocked']: log('  ATTENZIONE: FotMob blocca le richieste (403/429): uso ESPN dove possibile')
        if a.modo in ('intervallo', 'fine', 'heatmap', 'backfill'):
            rep += do_matches(s, fx, a.modo, 'HT' if a.modo == 'intervallo' else None, a.forza)
            write_index(s, fx)
        if s == cur and a.modo in ('fine', 'heatmap', 'backfill'):
            season_data(s, fx, tables.get('seriea'))
            calendario(s, fx)
        if a.modo in ('fine', 'heatmap', 'backfill', 'squadra'):
            if a.modo != 'fine' or s == cur:
                try: understat_apply(s, fx)
                except Exception as e: log('  Understat errore:', e)
            team_season(s)
        elif s != cur and a.modo == 'backfill':
            pass   # stagioni concluse: stagione.json resta quello verificato
    if a.modo in ('heatmap', 'backfill', 'giocatori'):
        if a.modo == 'heatmap' and not a.forza:
            # solo chi ha giocato da quando il suo file e' stato aggiornato
            only = []
            idx = rjson(f's/{cur}/partite.json', {}) or {}
            for row in idx.get('partite', []):
                if not row.get('agg'): continue
                m = rjson(f"s/{cur}/p/{row['id']}.json")
                for p in (m or {}).get('form', {}).get((m or {}).get('fio', 'c'), []):
                    g = rjson(f"g/{p['id']}.json")
                    if not g or (g.get('agg') or '') < (row.get('agg') or ''): only.append(p['id'])
            update_players(seasons, only)
        else:
            update_players(seasons)
    if c != rjson('seasons.json'): c['agg'] = iso(now); wjson('seasons.json', c)
    print('\n'.join(rep) if rep else '  nessuna partita da aggiornare')
    finish(t0)
    return 0

def finish(t0):
    if CHANGED or DELETED:
        idx = rjson('seasons.json') or {}
        idx['agg'] = iso(now_utc()); open(path('seasons.json'), 'w').write(dump(idx))
        if 'seasons.json' not in CHANGED: CHANGED.append('seasons.json')
    man = {'quando': iso(now_utc()), 'modificati': sorted(set(CHANGED)), 'eliminati': sorted(set(DELETED)), 'richieste': STATS['req'], 'errori': STATS['err'],
           'fotmob_bloccato': STATS['fm_blocked'], 'secondi': round(time.time() - t0)}
    open(path('manifest.json'), 'w').write(json.dumps(man, ensure_ascii=False, indent=1))
    print(f"\nFILE MODIFICATI ({len(man['modificati'])}) / ELIMINATI ({len(man['eliminati'])}) – dettagli in manifest.json – {man['richieste']} richieste, {man['secondi']}s")
    for f in man['modificati'][:40]: print('  M ' + f)
    if len(man['modificati']) > 40: print(f"  … altri {len(man['modificati']) - 40}")
    for f in man['eliminati']: print('  D ' + f)

if __name__ == '__main__':
    sys.exit(main())
