"""Quant Research 1.0: evidence ledger, daily research reports, walk-forward studies."""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

VERSION = 'LT_RESEARCH_1.0'
TZ = ZoneInfo('Asia/Shanghai')


def timestamp(value):
    x = dt.datetime.fromisoformat(value)
    if x.tzinfo is None:
        raise ValueError('时间必须包含时区')
    return x


def clock():
    return dt.datetime.now(TZ).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def database(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT NOT NULL, recorded_at TEXT NOT NULL, payload TEXT NOT NULL)')
    return db


def save(db, kind, payload):
    key = digest([kind, payload])
    with db:
        db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)', (key, kind, clock(), json.dumps(payload, ensure_ascii=False)))
    return key


def events(db, kind):
    return [(r['id'], json.loads(r['payload']), r['recorded_at']) for r in db.execute('SELECT * FROM events WHERE kind=? ORDER BY rowid', (kind,))]


def learn(db, item):
    for key in ('source', 'available_at', 'claim', 'tags'):
        if not item.get(key):
            raise ValueError('知识缺少 ' + key)
    if timestamp(item['available_at']) > timestamp(clock()):
        raise ValueError('材料时间在未来')
    item = dict(item)
    item['status'] = 'hypothesis'
    item['evidence_level'] = item.get('evidence_level', '未独立核验')
    if item.get('supersedes'):
        if not any(k == item['supersedes'] for k, _, _ in events(db, 'knowledge')):
            raise ValueError('被修订知识不存在')
    return save(db, 'knowledge', item)


def retrieve(db, query, cutoff, limit=8):
    point = timestamp(cutoff)
    records = [(k, p) for k, p, recorded in events(db, 'knowledge') if timestamp(p['available_at']) <= point and timestamp(recorded) <= point]
    superseded = {p.get('supersedes') for _, p in records}
    terms = query.lower().split()
    results = []
    for key, item in records:
        if key in superseded:
            continue
        body = (item['claim'] + ' ' + ' '.join(item['tags'])).lower()
        score = sum(term in body for term in terms)
        if score or not terms:
            results.append({'id': key, **item, 'relevance': score})
    return sorted(results, key=lambda r: (-r['relevance'], r['id']))[:limit]


NUMBERS = ('open', 'high', 'low', 'close', 'volume', 'amount', 'prev_close', 'limit_up', 'limit_down')


def read_bars(path):
    rows, seen = [], set()
    with open(path, encoding='utf-8-sig', newline='') as f:
        for line, raw in enumerate(csv.DictReader(f), 2):
            try:
                for key in ('symbol', 'date', 'asset', 'available_at'):
                    if not raw.get(key):
                        raise ValueError('缺少 ' + key)
                dt.date.fromisoformat(raw['date'])
                timestamp(raw['available_at'])
                if raw['asset'] not in ('stock', 'etf'):
                    raise ValueError('asset应为stock或etf')
                key = (raw['symbol'], raw['date'])
                if key in seen:
                    raise ValueError('重复股票日期')
                seen.add(key)
                row = dict(raw)
                for field in NUMBERS:
                    value = raw.get(field, '')
                    row[field] = float(value) if value else None
                    if row[field] is not None and not math.isfinite(row[field]):
                        raise ValueError('非有限数字')
                if any(row[k] is None for k in ('open', 'high', 'low', 'close', 'volume', 'amount')):
                    raise ValueError('OHLCV或成交额缺失')
                if not (0 < row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']):
                    raise ValueError('OHLC关系错误')
                if row['volume'] < 0 or row['amount'] < 0:
                    raise ValueError('负成交量或成交额')
                if any(row[k] is not None and row[k] <= 0 for k in ('prev_close', 'limit_up', 'limit_down')):
                    raise ValueError('价格字段非正')
                rows.append(row)
            except (ValueError, KeyError) as exc:
                raise ValueError(f'CSV第{line}行: {exc}') from exc
    if not rows:
        raise ValueError('空行情文件')
    return rows


def features(rows, cutoff):
    point = timestamp(cutoff)
    groups = defaultdict(list)
    for row in rows:
        if row['date'] <= point.date().isoformat() and timestamp(row['available_at']) <= point:
            groups[row['symbol']].append(row)
    result = []
    for symbol, bars in groups.items():
        bars.sort(key=lambda r: r['date'])
        r = dict(bars[-1])
        r['history_count'] = len(bars)
        r['ma20'] = statistics.mean(x['close'] for x in bars[-20:]) if len(bars) >= 20 else None
        r['return20'] = r['close'] / bars[-21]['close'] - 1 if len(bars) >= 21 else None
        r['position60'] = None
        if len(bars) >= 60:
            lo = min(x['low'] for x in bars[-60:]); hi = max(x['high'] for x in bars[-60:])
            r['position60'] = (r['close'] - lo) / (hi - lo) if hi > lo else None
        r['change'] = r['close'] / r['prev_close'] - 1 if r['prev_close'] else None
        r['at_limit'] = r['limit_up'] is not None and abs(r['close'] - r['limit_up']) < 0.0051
        streak = 0
        for x in reversed(bars):
            if x['limit_up'] is None or abs(x['close'] - x['limit_up']) >= 0.0051:
                break
            streak += 1
        r['streak'] = streak
        r['atr14'] = None
        if len(bars) >= 15:
            r['atr14'] = statistics.mean(max(bars[i]['high'] - bars[i]['low'], abs(bars[i]['high'] - bars[i-1]['close']), abs(bars[i]['low'] - bars[i-1]['close'])) for i in range(len(bars)-14, len(bars)))
        result.append(r)
    return result


def build_plan(db, rows, cutoff, target_date, universe_size=None, demo=False):
    timestamp(cutoff)
    if timestamp(cutoff) > timestamp(clock()):
        raise ValueError('不能使用未来截止时间')
    if dt.date.fromisoformat(target_date) <= timestamp(cutoff).date():
        raise ValueError('计划交易日必须晚于行情截止日；交易日需调用方确认')
    request_key = digest([VERSION, rows, cutoff, target_date, universe_size, demo])
    for key, previous, _ in events(db, 'plan'):
        if previous.get('request_key') == request_key:
            return key, previous
    calculated = features(rows, cutoff)
    if not calculated:
        raise ValueError('截止时间前无可用行情')
    latest = max(r['date'] for r in calculated)
    current = [r for r in calculated if r['date'] == latest]
    stale = [r['symbol'] for r in calculated if r['date'] != latest]
    directions = defaultdict(lambda: {'limit_count': 0, 'first': 0, 'second': 0, 'third_plus': 0, 'count': 0})
    candidates, excluded, etfs = [], [], []
    for r in current:
        if r['asset'] == 'etf':
            etfs.append({'symbol': r['symbol'], 'ma20': r['ma20'], 'atr14': r['atr14'], 'status': 'ETF战法待录入，仅统计'})
            continue
        theme = r.get('theme') or '未分类'
        t = directions[theme]; t['count'] += 1
        if r['at_limit']:
            t['limit_count'] += 1
            t['first' if r['streak'] == 1 else 'second' if r['streak'] == 2 else 'third_plus'] += 1
        reasons = []
        if r.get('is_st', '').lower() not in ('false', '0'):
            reasons.append('ST状态未知或为真')
        if r.get('tradable', '').lower() not in ('true', '1'):
            reasons.append('可交易状态未确认')
        if r.get('board') != 'main':
            reasons.append('非已确认主板')
        if r['history_count'] < 60:
            reasons.append('历史不足60根')
        if r['limit_up'] is None:
            reasons.append('缺涨停价')
        if not r['at_limit']:
            reasons.append('未收盘涨停')
        branch = None
        if r['streak'] in (1, 2):
            branch = 'LT_NEXT_BOARD'  # prior one/two-board -> next two/three-board observation
        if r['streak'] == 1 and r['position60'] is not None and r['position60'] <= .4 and r['ma20'] is not None and r['close'] >= r['ma20']:
            branch = 'LOW_FIRST_BOARD'
        if branch is None:
            reasons.append('不属于当前两个研究分支')
        if reasons:
            excluded.append({'symbol': r['symbol'], 'reasons': reasons})
        else:
            candidates.append({'symbol': r['symbol'], 'name': r.get('name', ''), 'theme': theme, 'branch': branch, 'metrics': r, 'reason': '通过日线观察池条件；尚未验证盘中主动性和带动性', 'trigger': '等待竞价主动性、题材扩散和封单稳定性验证；当前不产生买入信号', 'invalidate': '数据缺失、状态变化、盘中确认失败', 'risks': ['排板成交不可由日线推断', '题材分类可能不完整', '日线筛选规则尚未完成样本外验证']})
    day = dt.date.fromisoformat(latest)
    gap = (timestamp(cutoff).date() - day).days
    warnings = ['日线代理研究，盘中确认未接入', '题材统计仅覆盖已导入股票，不代表全市场']
    if universe_size is None:
        warnings.append('全市场覆盖率未知')
    elif len(current) < universe_size:
        warnings.append(f'覆盖不足：{len(current)}/{universe_size}')
    if stale:
        warnings.append('陈旧标的已剔除：' + ','.join(stale))
    if gap > 0:
        warnings.append(f'最新行情距截止日{gap}个自然日，需核对交易日历')
    if demo:
        warnings.insert(0, '合成演示数据，不能用于交易')
    missing_limits = sum(r['asset'] == 'stock' and r['limit_up'] is None for r in current)
    bad = demo or gap > 0 or stale or missing_limits or universe_size is None or len(current) < universe_size
    plan = {'version': VERSION, 'target_date': target_date, 'cutoff': cutoff, 'created_at': clock(), 'mode': 'DEMO' if demo else 'RESEARCH_ONLY', 'status': 'INCOMPLETE' if bad else 'IMPORTED_UNIVERSE_COMPLETE', 'latest_date': latest, 'input_digest': digest(rows), 'warnings': warnings, 'market': {'universe_count': len(current), 'stocks': sum(r['asset']=='stock' for r in current), 'up': sum(r['change'] is not None and r['change']>0 for r in current if r['asset']=='stock'), 'down': sum(r['change'] is not None and r['change']<0 for r in current if r['asset']=='stock'), 'limit_up': sum(r['at_limit'] for r in current if r['asset']=='stock')}, 'directions': dict(directions), 'candidates': candidates, 'excluded': excluded, 'etfs': etfs, 'knowledge': retrieve(db, '主动性 带动性 首板', cutoff), 'decision': '观察；当前系统不生成可执行买单'}
    plan['request_key'] = request_key
    plan['snapshot_id'] = save(db, 'market_snapshot', {'rows': rows, 'input_digest': digest(rows)})
    return save(db, 'plan', plan), plan


def report(plan_id, p, output):
    out = Path(output); out.mkdir(parents=True, exist_ok=True)
    lines = ['# 盘前研究计划', '', f"计划日：{p['target_date']}｜行情：{p['latest_date']}｜状态：{p['status']}", f"计划ID：{plan_id}", '', '**' + p['decision'] + '**', '', '## 数据检查']
    lines += ['- ' + w for w in p['warnings']]
    lines += ['', '## 市场', '', json.dumps(p['market'], ensure_ascii=False), '', '## 方向与梯队', '', '|方向|导入数|涨停|首板|二板|三板以上|', '|---|---:|---:|---:|---:|---:|']
    for theme, x in sorted(p['directions'].items(), key=lambda a: -a[1]['limit_count']):
        lines.append(f"|{theme}|{x['count']}|{x['limit_count']}|{x['first']}|{x['second']}|{x['third_plus']}|")
    lines += ['', '## 观察候选']
    if not p['candidates']:
        lines.append('无符合条件的候选。')
    for c in p['candidates']:
        m = c['metrics']
        pos = f"{m['position60']:.1%}" if m['position60'] is not None else '缺失'
        lines += ['', f"### {c['symbol']} {c['name']} / {c['branch']}", f"题材：{c['theme']}；连板：{m['streak']}；60日位置：{pos}；MA20：{m['ma20']:.3f}", c['reason'], '触发：' + c['trigger'], '失效：' + c['invalidate'], '风险：' + '；'.join(c['risks'])]
    lines += ['', '## 排除理由']
    lines += [f"- {x['symbol']}：{'；'.join(x['reasons'])}" for x in p['excluded']]
    lines += ['', '## ETF', '', '战法待提供，当前仅输出指标，不输出ETF交易建议。', '', '## 相关学习记录']
    lines += [f"- [{x['id'][:12]}] {x['claim']}（来源：{x['source']}；{x['evidence_level']}）" for x in p['knowledge']]
    (out/'pre_market.md').write_text('\n'.join(lines), encoding='utf-8')
    page = '<!doctype html><html lang=zh-CN><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>盘前研究计划</title><style>body{max-width:1000px;margin:40px auto;padding:24px;background:#f4f6f9;color:#182534;font:16px/1.8 system-ui}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:32px;border-radius:16px}h1{color:#173e67}</style><h1>量化研究 · 盘前计划</h1><pre>' + html.escape('\n'.join(lines)) + '</pre></html>'
    (out/'pre_market.html').write_text(page, encoding='utf-8')
    (out/'plan.json').write_text(json.dumps({'plan_id': plan_id, **p}, ensure_ascii=False, indent=2), encoding='utf-8')


def review(db, plan_id, fills_path, output):
    row = db.execute("SELECT payload FROM events WHERE id=? AND kind='plan'", (plan_id,)).fetchone()
    if row is None:
        raise ValueError('冻结计划不存在')
    p = json.loads(row['payload'])
    fills = []
    with open(fills_path, encoding='utf-8-sig', newline='') as f:
        for r in csv.DictReader(f):
            if timestamp(r['time']).astimezone(TZ).date().isoformat() != p['target_date']:
                raise ValueError('成交日期不属于计划日')
            if r['side'] not in ('BUY', 'SELL'):
                raise ValueError('side必须为BUY/SELL')
            r['quantity'] = int(r['quantity']); r['price'] = float(r['price'])
            if r['quantity'] <= 0 or not math.isfinite(r['price']) or r['price'] <= 0:
                raise ValueError('成交数量/价格无效')
            r['planned'] = r['symbol'] in {c['symbol'] for c in p['candidates']}
            fills.append(r)
    result = {'plan_id': plan_id, 'fills': fills, 'recorded_at': clock(), 'note': '导入的成交记录；未成交不能直接解释为信号未触发。无完整持仓成本账，不计算已实现收益。'}
    key = save(db, 'review', result)
    Path(output).write_text('# 盘后复盘\n\n' + result['note'] + '\n\n' + '\n'.join(f"- {r['symbol']} {r['side']} {r['quantity']}@{r['price']}；计划内={r['planned']}" for r in fills), encoding='utf-8')
    return key


def experiment(db, rows, train_end, test_start, horizon=3, fee=.001):
    """Exploratory daily proxy: close>MA20, next open entry, h sessions close exit."""
    if horizon < 1 or fee < 0 or not math.isfinite(fee):
        raise ValueError('实验参数无效')
    if dt.date.fromisoformat(test_start) <= dt.date.fromisoformat(train_end):
        raise ValueError('测试段必须在训练段后')
    groups = defaultdict(list)
    for r in rows:
        if timestamp(r['available_at']) > timestamp(clock()):
            raise ValueError('实验存在未来可见数据')
        groups[r['symbol']].append(r)
    trades = []
    for symbol, bars in groups.items():
        bars.sort(key=lambda r:r['date'])
        next_allowed = 20
        for i in range(20, len(bars)-horizon):
            signal = bars[i]
            if i < next_allowed or signal['asset'] != 'stock':
                continue
            if signal.get('board') != 'main' or signal.get('is_st') not in ('false','0') or signal.get('tradable') not in ('true','1'):
                continue
            if signal['close'] <= statistics.mean(r['close'] for r in bars[i-19:i+1]):
                continue
            entry = bars[i+1]; exitbar = bars[i+horizon]
            entry_time = timestamp(entry['date']+'T09:30:00+08:00')
            if timestamp(signal['available_at']) > entry_time:
                continue
            if entry.get('tradable') not in ('true','1') or exitbar.get('tradable') not in ('true','1'):
                continue
            # Unknown or locked limit prices cannot be assumed tradable.
            if entry['limit_up'] is None or exitbar['limit_down'] is None:
                continue
            if entry['open'] >= entry['limit_up']-.005 or exitbar['close'] <= exitbar['limit_down']+.005:
                continue
            if exitbar['date'] <= entry['date']:
                continue  # A-share T+1: no same-day exit
            phase = 'train' if exitbar['date'] <= train_end else 'test' if signal['date'] >= test_start else 'purged'
            if phase == 'purged':
                continue
            trades.append({'symbol':symbol,'signal_date':signal['date'],'entry_date':entry['date'],'exit_date':exitbar['date'],'phase':phase,'return':exitbar['close']/entry['open']-1-fee})
            next_allowed = i+horizon+1
    summary = {}
    for phase in ('train','test'):
        values = [t['return'] for t in trades if t['phase']==phase]
        summary[phase] = {'count':len(values), 'mean_net_return':statistics.mean(values) if values else None,'win_rate':sum(x>0 for x in values)/len(values) if values else None}
    payload = {'version':VERSION,'input_digest':digest(rows),'train_end':train_end,'test_start':test_start,'horizon':horizon,'round_trip_cost':fee,'summary':summary,'trades':trades,'limitations':['探索性均线基线，不是龙头策略回测','无组合资金约束，不是账户收益','幸存者偏差、历史状态与题材需数据提供方保证','日线成交代理，未模拟排板、分钟冲击及真实跌停顺延','未据此批准任何策略上线']}
    return save(db,'experiment',payload), payload


def main():
    parser=argparse.ArgumentParser(description='量化同步学习与研究系统')
    parser.add_argument('--db',default='data/research.sqlite')
    sub=parser.add_subparsers(dest='cmd',required=True)
    p=sub.add_parser('learn');p.add_argument('file')
    p=sub.add_parser('search');p.add_argument('query');p.add_argument('--cutoff',default=clock())
    p=sub.add_parser('daily');p.add_argument('csv');p.add_argument('--cutoff',required=True);p.add_argument('--target-date',required=True);p.add_argument('--universe-size',type=int);p.add_argument('--demo',action='store_true');p.add_argument('--output',default='reports')
    p=sub.add_parser('review');p.add_argument('plan_id');p.add_argument('fills');p.add_argument('--output',default='post_market.md')
    p=sub.add_parser('experiment');p.add_argument('csv');p.add_argument('--train-end',required=True);p.add_argument('--test-start',required=True);p.add_argument('--horizon',type=int,default=3);p.add_argument('--fee',type=float,default=.001);p.add_argument('--output',default='experiment.json')
    args=parser.parse_args();db=database(args.db)
    if args.cmd=='learn': print(learn(db,json.loads(Path(args.file).read_text(encoding='utf-8'))))
    elif args.cmd=='search': print(json.dumps(retrieve(db,args.query,args.cutoff),ensure_ascii=False,indent=2))
    elif args.cmd=='daily':
        key,p=build_plan(db,read_bars(args.csv),args.cutoff,args.target_date,args.universe_size,args.demo);report(key,p,args.output);print(key)
    elif args.cmd=='review':print(review(db,args.plan_id,args.fills,args.output))
    elif args.cmd=='experiment':
        key,p=experiment(db,read_bars(args.csv),args.train_end,args.test_start,args.horizon,args.fee);Path(args.output).write_text(json.dumps({'experiment_id':key,**p},ensure_ascii=False,indent=2),encoding='utf-8');print(key)


if __name__=='__main__':main()
