#!/usr/bin/env python3
"""直前オッズの自動取得(Macのターミナルで動かす。2026-11-22)
  発走5分前と3分前に JRA公式サイトの単勝オッズを1回ずつ取り、
  (2026-11-22 東京4Rで測定: JRA公式は締切前に約30〜80秒ごとに更新。netkeiba は5分前のあと更新されず使えない)
  ・5分前→3分前に急に売れた馬(odds_check.py と同じ判定)
  ・☆が当日10倍以上か(10倍未満なら見送り)
  を画面に出して、Macの通知で知らせる。投票はしない(自分で買う)。

使い方(ターミナルで):
  python3 ~/Documents/競馬用/ツール/odds_watch.py                  今日の全レース
  python3 ~/Documents/競馬用/ツール/odds_watch.py --races 京都6,京都11   レースを絞る
  python3 ~/Documents/競馬用/ツール/odds_watch.py --now 京都6          今すぐ1回だけ取る(動作確認)
  止めるときは control + C。Macをスリープさせないこと(蓋を閉じない)。

負荷: 1日の開始時にレース一覧(2〜4ページ)、各レースで2回(5分前・3分前)だけ取る。個人の検討用。
保存: ~/Documents/競馬用/直前オッズ/<yyyymmdd>/<場所><R>R.csv (列: 馬番, 馬名, 人気, 5分前, 直前, 支持率の変化(倍), 判定)
レポートの ☆ と馬名は ~/Documents/競馬用/レポート/<yymmdd>/ の 一覧.csv と <場所><R>R_評価.csv から読む(無くても動く)。

判定の根拠(検証結果.md、2026年1〜10月 約3.7万走):
  5分前→3分前に単勝の支持率が 1.42〜1.65倍 → 複勝回収122〜146%、1.22〜1.42倍 → 95〜106%、1.65倍超は上乗せなし、売れなくなった馬は45〜60%。
  9番人気以下の急な売れは特に高い(複勝134〜247%)。10か月だけの結果で確定ではない。"""
import argparse, csv, datetime as dt, json, os, re, subprocess, sys, time
try:
    from zoneinfo import ZoneInfo; JST = ZoneInfo('Asia/Tokyo')
except Exception: JST = dt.timezone(dt.timedelta(hours=9))


def now_jst():
    """日本時間(GitHub のサーバーは UTC なので、時刻はすべて日本時間で扱う)"""
    return dt.datetime.now(JST).replace(tzinfo=None)


HOME = os.environ.get('KEIBA_HOME') or os.path.expanduser('~/Documents/競馬用')
PL = {'01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京', '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉'}
LO, HI, STRONG = 1.22, 1.65, 1.42
UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15'


def get(url):
    """macOS の curl を使う(Python の証明書設定に左右されない)"""
    r = subprocess.run(['curl', '-s', '--max-time', '15', '-A', UA, url], capture_output=True)
    if r.returncode != 0: raise RuntimeError(f'取得できず(curl {r.returncode})')
    b = r.stdout
    for enc in ('utf-8', 'euc-jp'):
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode('utf-8', 'replace')


JRA = 'https://www.jra.go.jp/JRADB/accessO.html'


def post(cname):
    """JRA公式サイトのオッズのページ(cname を POST で送る。ブラウザでボタンを押したときと同じ)"""
    r = subprocess.run(['curl', '-s', '--max-time', '15', '-A', UA, '-d', 'cname=' + cname, JRA], capture_output=True)
    if r.returncode != 0: raise RuntimeError(f'取得できず(curl {r.returncode})')
    return r.stdout.decode('cp932', 'replace')


def _top_cname():
    try:
        m = re.search(r"doAction\('/JRADB/accessO\.html',\s*'(pw15oli00/[0-9A-F]{2})'\)", get('https://www.jra.go.jp/keiba/'))
        if m: return m.group(1)
    except Exception: pass
    return 'pw15oli00/6D'


def race_list(day):
    """JRA公式サイトの「オッズ レース選択」から、その日の全レース(場所・R・発走時刻・単勝複勝ページの cname)"""
    top = post(_top_cname())
    out = []
    for m in re.finditer(r"'(pw15orl\d0(\d{2})\d{4}\d{2}\d{2}(\d{8})/[0-9A-F]{2})'", top):
        cn, pc, d8 = m.group(1), m.group(2), m.group(3)
        if d8 != day or any(o.get('_k') == cn for o in out): continue
        pg = post(cn)
        for tr in pg.split('<tr')[1:]:
            c = re.search(r"'(pw151ouS3\d+Z/[0-9A-F]{2})'", tr); n = re.search(r'alt="(\d+)レース"', tr); tm = re.search(r'(\d{1,2})時(\d{2})分', tr)
            if not (c and n and tm): continue   # 発走済みのレースは時刻が無いので飛ばす
            nm = re.search(r'class="stakes">\s*([^<]+?)\s*</div>', tr); sub = re.search(r'</div>\s*<div>([^<]+)</div>', tr)
            out.append(dict(_k=cn, rid=c.group(1), place=PL.get(pc, pc), R=int(n.group(1)),
                            name=(nm.group(1) if nm else '') + (' ' + sub.group(1) if sub else ''),
                            post=dt.datetime(int(day[:4]), int(day[4:6]), int(day[6:]), int(tm.group(1)), int(tm.group(2)))))
    return sorted(out, key=lambda o: o['post'])


def win_odds(cname):
    """単勝オッズ {馬番: オッズ}・馬名・発表時刻(「11時17分現在」)。取消・除外は飛ばす"""
    t = post(cname)
    od, NM = {}, {}
    for tr in t.split('<tr')[1:]:
        n = re.search(r'<td class="num">(\d+)</td>', tr); o = re.search(r'<td class="odds_tan">(?:\s|<[^>]+>)*([\d.]+)', tr)
        h = re.search(r'<td class="horse">.*?>([^<>]+)</a>', tr, re.S)
        if n and o:
            od[int(n.group(1))] = float(o.group(1))
            if h: NM[int(n.group(1))] = h.group(1).strip()
    tm = re.search(r'(\d+時\d+分)現在', t)
    win_odds.names = NM
    return od, (tm.group(1) if tm else '')


def judge(o5, o3):
    ks = [k for k in o5 if k in o3]
    s5 = sum(1 / o5[k] for k in ks); s3 = sum(1 / o3[k] for k in ks)
    pop5 = {k: i + 1 for i, k in enumerate(sorted(ks, key=lambda k: o5[k]))}
    rows = []
    for k in ks:
        r = round(((1 / o3[k]) / s3) / ((1 / o5[k]) / s5), 2)
        if STRONG <= r <= HI: j = '◎ 急に売れた(過去 複勝回収122〜146%)'
        elif LO <= r < STRONG: j = '○ 売れた(過去 複勝回収95〜106%)'
        elif r > HI: j = '△ 売れすぎ(上乗せなし)'
        elif r < 1 / 1.22: j = '× 売れなくなった(回収45〜60%)'
        else: j = ''
        if j[:1] in ('◎', '○') and pop5[k] >= 9: j += ' ★人気薄(複勝134〜247%)'
        rows.append(dict(馬番=k, 人気=pop5[k], 五分前=o5[k], 直前=o3[k], 変化=r, 判定=j))
    return sorted(rows, key=lambda d: -d['変化'])


def load_report(day, place, R):
    """レポートから ☆ と馬名・見張り候補を読む(無ければ空)"""
    d = os.path.join(HOME, 'レポート', day[2:])
    star, names, watch = None, {}, set()
    try:
        for row in csv.DictReader(open(os.path.join(d, '一覧.csv'), encoding='utf-8-sig')):
            if row.get('レース') == f'{place}{R}R' and row.get('☆'):
                m = re.match(r'\s*(\d+)\s*(\S+)', row['☆'])
                if m: star = (int(m.group(1)), m.group(2))
    except Exception: pass
    # 環境変数 STARS(例: 京都6:14,京都7:17)があれば ☆ に使う(GitHub の Variables に書く)
    for x in os.environ.get('STARS', '').replace('、', ',').split(','):
        m = re.match(r'\s*(\D+?)(\d+)R?:(\d+)', x)
        if m and m.group(1) == place and int(m.group(2)) == R: star = (int(m.group(3)), '')
    try:
        for row in csv.DictReader(open(os.path.join(d, f'{place}{R:02d}R_評価.csv'), encoding='utf-8-sig')):
            k = int(float(row['馬番'])); names[k] = row['馬名']
            if row.get('直前の見張り候補') == '候補': watch.add(k)
    except Exception: pass
    return star, names, watch


def notify(title, msg):
    """Mac なら通知センター。環境変数 NTFY_TOPIC があれば ntfy でスマホに送る(GitHub Actions 用)"""
    tp = os.environ.get('NTFY_TOPIC')
    if tp:
        body = json.dumps(dict(topic=tp, title=title, message=msg, priority=4), ensure_ascii=False)
        subprocess.run(['curl', '-s', '--max-time', '15', '-H', 'Content-Type: application/json', '-d', body, 'https://ntfy.sh/'], capture_output=True)
    if sys.platform == 'darwin':
        m = msg.replace('"', "'")[:230]; t = title.replace('"', "'")
        subprocess.run(['osascript', '-e', f'display notification "{m}" with title "{t}" sound name "Glass"'], capture_output=True)


def report(rc, o5, o3, t5, t3, day, late=False):
    star, names, watch = load_report(day, rc['place'], rc['R'])
    for k, v in getattr(win_odds, 'names', {}).items(): names.setdefault(k, v)
    rows = judge(o5, o3)
    tag = f"{rc['place']}{rc['R']}R"
    print(f"\n===== {tag} {rc['name']} 発走 {rc['post']:%H:%M} =====")
    print(f"5分前: {t5} / 直前: {t3}" + ('(5分前に間に合わず、開始時点のオッズと比べています)' if late else ''))
    hits = []
    for d in rows:
        nm = names.get(d['馬番'], ''); mark = (' [見張り候補]' if d['馬番'] in watch else '') + (' [☆]' if star and star[0] == d['馬番'] else '')
        if d['判定'] or mark:
            print(f"  {d['馬番']:>2} {nm:<10} {d['人気']:>2}番人気 {d['五分前']:>6.1f} → {d['直前']:>6.1f}倍  支持率×{d['変化']:.2f} {d['判定']}{mark}")
        if d['判定'][:1] == '◎' or (d['判定'][:1] == '○' and '★' in d['判定']): hits.append(f"{d['馬番']}{nm}({d['直前']:.1f}倍 ×{d['変化']:.2f})")
    msg = []
    if star:
        so = o3.get(star[0])
        if not star[1]: star = (star[0], names.get(star[0], ''))
        if so is None: s = f'☆{star[0]}{star[1]}: オッズ無し(取消?)'
        elif so < 10: s = f'☆{star[0]}{star[1]} {so:.1f}倍 → 10倍未満なので見送り'
        else: s = f'☆{star[0]}{star[1]} {so:.1f}倍 → 買う対象(10倍以上)'
        print('  ' + s); msg.append(s)
    if hits: msg.append('急に売れた: ' + '、'.join(hits))
    if not msg: msg.append('急に売れた馬なし')
    print('  → ' + ' / '.join(msg) + '(締切は発走1分前)')
    notify(f'{tag} 直前オッズ(発走{rc["post"]:%H:%M})', ' / '.join(msg))
    od = os.path.join(HOME, '直前オッズ', day); os.makedirs(od, exist_ok=True)
    with open(os.path.join(od, f'{tag}.csv'), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f); w.writerow(['馬番', '馬名', '人気', '5分前', '直前', '支持率の変化(倍)', '判定', '取得_5分前', '取得_直前'])
        for d in sorted(rows, key=lambda d: d['馬番']): w.writerow([d['馬番'], names.get(d['馬番'], ''), d['人気'], d['五分前'], d['直前'], d['変化'], d['判定'], t5, t3])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--date', default=now_jst().strftime('%Y%m%d'))
    ap.add_argument('--after', default='', help='この時刻(HH:MM)より後に発走するレースだけ')
    ap.add_argument('--until', default='', help='この時刻(HH:MM)までに発走するレースだけ')
    ap.add_argument('--check', action='store_true', help='接続の確認だけ(レース一覧とオッズを1回取り、通知を1回送る)')
    ap.add_argument('--races', default='', help='例: 京都6,京都11(無ければ全レース)')
    ap.add_argument('--first', type=float, default=5.0, help='1回目は発走何分前か(既定5)')
    ap.add_argument('--second', type=float, default=3.0, help='2回目は発走何分前か(既定3)')
    ap.add_argument('--now', default='', help='例: 京都6。今すぐ2回(30秒あけて)取って表示する(動作確認用)')
    a = ap.parse_args()
    RL = race_list(a.date)
    if a.check:
        msg = f'{a.date}: レース一覧 {len(RL)}レース'
        if RL:
            od, ts = win_odds(RL[-1]['rid']); msg += f' / {RL[-1]["place"]}{RL[-1]["R"]}R の単勝 {len(od)}頭分({ts or "時刻なし"})'
        print('接続の確認:', msg); notify('直前オッズ: 接続の確認', msg); return
    if not RL: print(f'{a.date} はレースが無い(またはレース一覧が取れない)'); return
    if a.now:
        rc = next((r for r in RL if f"{r['place']}{r['R']}" == a.now.replace('R', '')), None)
        if not rc: sys.exit(f'{a.now} が見つからない')
        o5, t5 = win_odds(rc['rid']); print('1回目を取得。30秒待つ…'); time.sleep(30); o3, t3 = win_odds(rc['rid'])
        report(rc, o5, o3, t5, t3, a.date); return
    want = {x.strip().replace('R', '') for x in a.races.split(',') if x.strip()}
    RL = [r for r in RL if not want or f"{r['place']}{r['R']}" in want]
    hm = lambda x: dt.datetime.strptime(f'{a.date} {x}', '%Y%m%d %H:%M')
    if a.after: RL = [r for r in RL if r['post'] > hm(a.after)]
    if a.until: RL = [r for r in RL if r['post'] <= hm(a.until)]
    now = now_jst()
    RL = [r for r in RL if r['post'] - dt.timedelta(minutes=a.second) > now]
    if not RL: print('これから取れるレースが無い'); return
    print(f"{len(RL)}レースを見張ります(発走{a.first:g}分前と{a.second:g}分前)。止めるときは control + C。")
    for r in RL: print(f"  {r['post']:%H:%M} {r['place']}{r['R']}R {r['name']}")
    ev = sorted([(r['post'] - dt.timedelta(minutes=a.first), 1, r) for r in RL] + [(r['post'] - dt.timedelta(minutes=a.second), 2, r) for r in RL], key=lambda e: (e[0], e[1]))
    first = {}
    for t, k, r in ev:
        wait = (t - now_jst()).total_seconds()
        late = wait < -30
        if wait > 0: time.sleep(wait)
        try:
            od, ts = win_odds(r['rid'])
        except Exception as ex:
            print(f"{r['place']}{r['R']}R: オッズを取れず({ex})"); continue
        if k == 1:
            first[r['rid']] = (od, ts, late); print(f"{now_jst():%H:%M:%S} {r['place']}{r['R']}R 1回目を取得({ts})")
        else:
            if r['rid'] not in first: print(f"{r['place']}{r['R']}R: 1回目が無いので判定できない"); continue
            o5, t5, l5 = first.pop(r['rid'])
            report(r, o5, od, t5, ts, a.date, late=l5)
    print('\n今日の見張りは終わり。')


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print('\n止めました。')
