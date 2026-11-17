#!/usr/bin/env python3
"""Build an anonymized dataset of real DraftKings contest fields (read-only on sources).

Usage: python3 build_dataset.py [DATA_ROOT] [OUT_DIR] [--shard I/N]
  DATA_ROOT default: ../optimization-fantasy   (contains NHL/NFL/NBA/testing/<MMDDYYYY>[big|small]/)
  OUT_DIR   default: the directory containing this script

Source files are only ever opened for reading (zips are read in memory, never extracted).
The username -> userN mapping lives in memory only and is never written anywhere.
"""
import csv, glob, hashlib, io, os, re, shutil, sys, zipfile, collections

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', 'optimization-fantasy'))
OUT = HERE
SPORTS = ['NBA', 'NFL', 'NHL']          # alphabetical -> deterministic user numbering
DATE_RE = re.compile(r'^(\d{2})(\d{2})(\d{4})(big|small)?$')
ENTRY_RE = re.compile(r'^(.*?)\s*\((\d+)/(\d+)\)\s*$')
TIER_RE = re.compile(r'^(\d+)(?:st|nd|rd|th)(?:\s*-\s*(\d+)(?:st|nd|rd|th))?\t\$([\d,]+(?:\.\d+)?)\s*$')
DK_SALARY_COLS = {'Position', 'Name + ID', 'Name', 'ID', 'Roster Position', 'Salary',
                  'Game Info', 'TeamAbbrev', 'AvgPointsPerGame', 'Status', 'Starting'}
OWN_RE = re.compile(r'kindo|mario', re.I)

failures = []      # (where, what) -- never contains usernames
notes = []


def decode(b):
    for enc in ('utf-8-sig', 'cp1252'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    failures.append(('decode', 'fell back to latin-1'))
    return b.decode('latin-1')


def iter_zip(data, label):
    """Yield (name, bytes) for every file in a zip, recursing into nested zips. In memory only."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            b = zf.read(info)
            if info.filename.lower().endswith('.zip'):
                yield from iter_zip(b, label + '!' + info.filename)
            else:
                yield info.filename, b, label + '!' + info.filename


def scan_folder(d):
    """Return (standings{cid: [(bytes, label)]}, payouts{key: [(text, label, name)]})."""
    standings = collections.defaultdict(list)
    payouts = []
    def handle(name, b, label):
        base = os.path.basename(name)
        m = re.match(r'contest-standings-(\d{6,})', base)
        if m and base.lower().endswith('.csv'):
            standings[m.group(1)].append((b, label))
        elif base.lower().startswith('payouts') and base.lower().endswith('.txt'):
            payouts.append((decode(b), label, base))
    for p in sorted(os.listdir(d)):
        fp = os.path.join(d, p)
        if not os.path.isfile(fp):
            continue
        low = p.lower()
        if low.endswith('.zip') and (low.startswith('contest-standings') or low.startswith('drive-download')):
            with open(fp, 'rb') as f:
                for name, b, lab in iter_zip(f.read(), p):
                    handle(name, b, lab)
        elif low.endswith('.csv') and low.startswith('contest-standings'):
            with open(fp, 'rb') as f:
                handle(p, f.read(), p)
        elif low.endswith('.txt') and low.startswith('payouts'):
            with open(fp, 'rb') as f:
                handle(p, f.read(), p)
    return standings, payouts


def parse_payout(text):
    tiers = []
    for ln in text.splitlines():
        m = TIER_RE.match(ln)
        if m:
            lo = int(m.group(1)); hi = int(m.group(2) or m.group(1))
            tiers.append((lo, hi, float(m.group(3).replace(',', ''))))
    return tiers


def payout_for(tiers_arr, pos):
    return tiers_arr.get(pos, 0.0)


def expand(tiers):
    d = {}
    for lo, hi, amt in tiers:
        for p in range(lo, hi + 1):
            d[p] = amt
    return d


def fmt_tiers(tiers):
    return '|'.join(f'{lo}={a:.2f}' if lo == hi else f'{lo}-{hi}={a:.2f}' for lo, hi, a in tiers)


def money(x):
    return f'{x:.2f}'


def main():
    # ---------------------------------------------------------------- discovery
    folders = []   # (sport, iso_date, slate, path)
    for sport in SPORTS:
        base = os.path.join(ROOT, sport, 'testing')
        for name in os.listdir(base):
            m = DATE_RE.match(name)
            if m and os.path.isdir(os.path.join(base, name)):
                mm, dd, yy, slate = m.groups()
                folders.append((sport, f'{yy}-{mm}-{dd}', slate or '', os.path.join(base, name)))
    folders.sort()

    contests = {}      # cid -> dict
    for sport, date, slate, path in folders:
        standings, payouts = scan_folder(path)
        # salaries file (DK columns only; copied verbatim after header validation)
        sal = sorted(glob.glob(os.path.join(path, 'DKSalaries*.csv')))
        sal_src = None
        if len(sal) >= 1:
            sal_src = max(sal, key=os.path.getsize)
            if len(sal) > 1:
                notes.append(f'{sport} {date} {slate}: {len(sal)} DKSalaries files, used largest')
        # payouts by explicit id
        by_id = {}
        anon = []
        for text, lab, base in payouts:
            ids = re.findall(r'(?<!\d)(\d{9,})(?!\d)', base)
            if ids:
                by_id.setdefault(ids[-1], (text, lab, base))
            else:
                anon.append((text, lab, base))
        for cid, cands in standings.items():
            best = max(cands, key=lambda c: len(c[0]))
            if len(set(hashlib.sha1(c[0]).hexdigest() for c in cands)) > 1:
                notes.append(f'contest {cid}: {len(cands)} differing copies in {sport} {date}, used largest')
            rec = dict(cid=cid, sport=sport, date=date, slate=slate, folder=path, data=best[0],
                       label=best[1], sal_src=sal_src,
                       payout=by_id.get(cid), payout_match='id' if cid in by_id else 'none')
            if cid in contests:
                old = contests[cid]
                notes.append(f'contest {cid} found in two folders ({old["sport"]} {old["date"]}{old["slate"]} / {sport} {date}{slate}); kept larger')
                if len(rec['data']) <= len(old['data']):
                    continue
            contests[cid] = rec
        # heuristic matching of id-less payout files within the folder
        these = [c for c in contests.values() if c['folder'] == path and c['payout'] is None]
        anon.sort(key=lambda x: (not re.search(r'\d+\s*entries', x[2]), x[2]))   # 'N entries' files first
        for text, lab, base in anon:
            if not these:
                failures.append(('payout', f'unmatched id-less payout file {base!r} in {sport} {date}'))
                continue
            m = re.search(r'(\d+)\s*entries', base)
            if m:
                n = int(m.group(1))
                best_c = min(these, key=lambda c: abs(c['data'].count(b'\n') - n))   # nearest row count
            else:
                best_c = these[0] if len(these) == 1 else None
            if best_c is None:
                failures.append(('payout', f'ambiguous id-less payout file {base!r} in {sport} {date}'))
                continue
            best_c['payout'] = (text, lab, base); best_c['payout_match'] = 'heuristic'
            these.remove(best_c)
            notes.append(f'heuristic payout match: {base!r} -> contest {best_c["cid"]}')

    order = sorted(contests.values(), key=lambda c: (c['sport'], c['date'], c['slate'], int(c['cid'])))
    return order


def parse_contest(c, record):
    """Parse one standings file -> (entries, players, max_entries_counter)."""
    cid = c['cid']
    def fail(kind, msg):
        if record:
            failures.append((kind, msg))
    text = decode(c['data'])
    rdr = csv.reader(io.StringIO(text, newline=''))
    header = next(rdr, None)
    if not header or header[:5] != ['Rank', 'EntryId', 'EntryName', 'TimeRemaining', 'Points']:
        fail('header', f'contest {cid}: unexpected header')
        return [], [], collections.Counter()
    entries, plist = [], []
    maxes = collections.Counter()
    for ln, row in enumerate(rdr, start=2):
        if not row or not any(x.strip() for x in row):
            continue
        row += [''] * (11 - len(row))
        try:
            if row[0].strip():
                rank = int(row[0]); name = row[2]; pts = row[4]; lineup = row[5]
                m = ENTRY_RE.match(name)
                uname, eno, mx = (m.group(1), m.group(2), m.group(3)) if m else (name.strip(), '', '')
                if not uname:
                    raise ValueError('empty username')
                if mx:
                    maxes[mx] += 1
                entries.append((rank, uname, eno, mx, pts, lineup))
            if row[7].strip():
                plist.append((row[7], row[8], row[9], row[10]))
        except Exception as e:
            fail('row', f'contest {cid} line {ln}: {type(e).__name__}')
    if not entries:
        fail('empty', f'contest {cid}: no entries parsed')
    return entries, plist, maxes


def assign_users(order):
    """Pass 1 (cheap, no writes): number users in first-seen order over the sorted contests.
    The mapping is rebuilt in memory on every run (deterministic) and never stored."""
    user_ids, own_hits = {}, set()
    for c in order:
        entries, _, _ = parse_contest(c, record=True)
        c['n_entries'] = len(entries)
        for e in entries:
            u = e[1]
            if u not in user_ids:
                user_ids[u] = f'user{len(user_ids) + 1}'
                if OWN_RE.search(u):
                    own_hits.add(u)
    return user_ids, own_hits


def write_slice(order, user_ids, lo, hi, first):
    """Pass 2: write contests order[lo:hi]. `first` -> truncate + header, else append."""
    os.makedirs(os.path.join(OUT, 'salaries'), exist_ok=True)
    mode = 'w' if first else 'a'
    ef = open(os.path.join(OUT, 'entries.csv'), mode, newline='', encoding='utf-8')
    pf = open(os.path.join(OUT, 'players.csv'), mode, newline='', encoding='utf-8')
    cf = open(os.path.join(OUT, 'contests.csv'), mode, newline='', encoding='utf-8')
    ew, pw, cw = csv.writer(ef), csv.writer(pf), csv.writer(cf)
    if first:
        ew.writerow(['contest_id', 'rank', 'user', 'entry_no', 'max_entries', 'points', 'lineup', 'payout'])
        pw.writerow(['contest_id', 'player', 'roster_position', 'pct_drafted', 'fpts'])
        cw.writerow(['contest_id', 'sport', 'date', 'slate', 'source_stem', 'field_size', 'max_entries',
                     'entry_fee', 'paid_places', 'prize_pool', 'payout_table', 'payout_match', 'salary_file'])
    for c in order[lo:hi]:
        cid = c['cid']
        entries, plist, maxes = parse_contest(c, record=False)
        if not entries:
            continue
        tiers, ptable, pay = [], '', None
        if c['payout']:
            tiers = parse_payout(c['payout'][0])
            if tiers:
                pay = expand(tiers); ptable = fmt_tiers(tiers)
        pay_cache = {}
        if pay is not None:
            by_rank = collections.Counter(e[0] for e in entries)
            # tied entries share the average of the paid positions they span (DraftKings rule)
            for r, n in by_rank.items():
                pay_cache[r] = sum(pay.get(p, 0.0) for p in range(r, r + n)) / n
        for rank, uname, eno, mx, pts, lineup in entries:
            ew.writerow([cid, rank, user_ids[uname], eno, mx, pts, lineup,
                         money(pay_cache[rank]) if pay is not None else ''])
        for p in plist:
            pw.writerow([cid, p[0], p[1], p[2], p[3]])
        sal_name = ''
        if c['sal_src']:
            sal_name = f"{c['sport']}_{c['date']}" + (f"_{c['slate']}" if c['slate'] else '') + '.csv'
            with open(c['sal_src'], 'rb') as f:
                raw = f.read()
            hdr = decode(raw).splitlines()[0].split(',')
            extra = set(h.strip() for h in hdr) - DK_SALARY_COLS
            if extra:
                print(f'WARNING {sal_name}: non-DK columns {sorted(extra)} (copied anyway)')
            dst = os.path.join(OUT, 'salaries', sal_name)
            if not os.path.exists(dst):
                with open(dst, 'wb') as f:
                    f.write(raw)
        mx_val = max((int(k) for k in maxes), default='')
        cw.writerow([cid, c['sport'], c['date'], c['slate'], f'contest-standings-{cid}', len(entries), mx_val, '',
                     len(pay) if pay else '', money(sum(pay.values())) if pay else '', ptable,
                     c['payout_match'] if c['payout'] else 'none', sal_name])
    for f in (ef, pf, cf):
        f.close()


def report():
    """Counts per sport, computed from the written outputs."""
    sport_of, st = {}, collections.OrderedDict((sp, dict(contests=0, entries=0, users=set())) for sp in SPORTS)
    with open(os.path.join(OUT, 'contests.csv'), newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            sport_of[r['contest_id']] = r['sport']; st[r['sport']]['contests'] += 1
    allusers = set()
    with open(os.path.join(OUT, 'entries.csv'), encoding='utf-8', newline='') as f:
        next(f)
        for line in f:
            p = line.split(',', 7)
            sp = sport_of[p[0]]
            st[sp]['entries'] += 1; st[sp]['users'].add(p[2]); allusers.add(p[2])
    for sp, s in st.items():
        print(f"  {sp}: contests={s['contests']} entries={s['entries']} unique_users={len(s['users'])}")
    print(f"  ALL: contests={sum(s['contests'] for s in st.values())} "
          f"entries={sum(s['entries'] for s in st.values())} unique_users={len(allusers)}")


def self_check(user_ids):
    """Fail loudly if any original username survives anywhere in the output folder."""
    names = set(user_ids)
    anon_re = re.compile(r'^user\d+$')
    vocab = set()
    # vocabulary of legitimate tokens: player names/positions in players.csv, salaries, headers
    with open(os.path.join(OUT, 'players.csv'), newline='', encoding='utf-8') as f:
        for r in csv.reader(f):
            vocab.update(r[1].split()); vocab.add(r[1]); vocab.add(r[2])
    for p in glob.glob(os.path.join(OUT, 'salaries', '*.csv')):
        with open(p, encoding='utf-8-sig', errors='replace') as f:
            for ln in f:
                for fld in ln.rstrip('\n').split(','):
                    vocab.add(fld); vocab.update(fld.split())
    vocab.update(['C', 'W', 'D', 'G', 'UTIL', 'QB', 'RB', 'WR', 'TE', 'FLEX', 'DST', 'PG', 'SG', 'SF', 'PF',
                  'CPT', 'F'])
    leaks, benign = [], collections.Counter()
    files = [p for p in glob.glob(os.path.join(OUT, '**', '*'), recursive=True) if os.path.isfile(p)]
    def judge(hit, rel, ln_no):
        for t in hit:
            if anon_re.match(t) or t in vocab or t == '':
                benign[t] += 1
            else:
                leaks.append((rel, ln_no))
    for p in files:
        rel = os.path.relpath(p, OUT)
        if rel == 'entries.csv':
            # fast path: user column must be userN; lineup tokens must not be a username
            # (lineup has no commas; usernames cannot appear in it as a standalone token)
            ltoks = set()
            with open(p, encoding='utf-8', newline='') as f:
                next(f)
                for ln_no, line in enumerate(f, 2):
                    parts = line.rstrip('\r\n').split(',', 7)
                    if len(parts) != 8 or not anon_re.match(parts[2]):
                        leaks.append((rel, ln_no))
                    ltoks.update(parts[6].split())
            judge(ltoks & names, rel, 0)
            continue
        with open(p, encoding='utf-8-sig', errors='replace', newline='') as f:
            for ln_no, line in enumerate(f, 1):
                toks = set(re.split(r'[\s|;,=\"]+', line))
                judge(toks & names, rel, ln_no)
    if leaks:
        print('SELF-CHECK FAILED: username leak at', leaks[:20], file=sys.stderr)
        sys.exit(2)
    return len(files), sum(benign.values()), len(benign)


if __name__ == '__main__':
    # --shard I/N : run slice I (1-based) of N (size-balanced). Slices append to the same files, so a long
    # build can be split across several short runs; each run rebuilds the identical in-memory user mapping.
    # The self-check + report run automatically after the last slice. Default: one run (1/1).
    shard, nsh = 1, 1
    if '--shard' in sys.argv:
        shard, nsh = map(int, sys.argv[sys.argv.index('--shard') + 1].split('/'))
    args = [a for a in sys.argv[1:] if not a.startswith('--') and not re.match(r'^\d+[/:]\d+$', a)]
    if len(args) > 0: ROOT = os.path.abspath(args[0])
    if len(args) > 1: OUT = os.path.abspath(args[1])
    order = main()
    users, own = assign_users(order)
    tot = sum(len(c['data']) for c in order); acc = 0; cuts = [0]
    for i, c in enumerate(order):
        acc += len(c['data'])
        if len(cuts) < nsh and acc >= tot * len(cuts) / nsh:
            cuts.append(i)
    cuts = cuts[:nsh] + [len(order)]
    lo, hi = cuts[shard - 1], cuts[shard]
    finish_only = '--finish' in sys.argv
    if '--range' in sys.argv:      # explicit contest index range LO:HI (appends); resume helper
        lo, hi = map(int, sys.argv[sys.argv.index('--range') + 1].split(':'))
        shard = 2
    if not finish_only:
        write_slice(order, users, lo, hi, first=(shard == 1))
        print(f'slice {shard}/{nsh}: contests {lo}..{hi - 1} of {len(order)} written; users in mapping: {len(users)}')
    if finish_only or (shard == nsh and '--range' not in sys.argv):
        print('own-account-like usernames (anonymized like all others):', len(own), sorted(own)[:20])
        print('notes:'); [print('  ', n) for n in notes]
        print(f'parse failures: {len(failures)}'); [print('  ', f) for f in failures[:60]]
        print('counts per sport:'); report()
        nfiles, nben, nbt = self_check(users)
        print(f'SELF-CHECK PASSED: {nfiles} output files scanned, 0 username leaks '
              f'({nben} benign collisions across {nbt} distinct tokens: userN / player-name tokens)')
