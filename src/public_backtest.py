"""Reproducible daily proxy, including all skips and unresolved exits. No broker API."""
import argparse
import concurrent.futures as cf
import datetime as dt
from decimal import Decimal,ROUND_HALF_UP
import hashlib
import gzip
import json
import math
from pathlib import Path
from research_data import fetch,safe,exchange


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def limit(price,multiplier):return float((Decimal(str(price))*Decimal(str(multiplier))).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP))
def mainboard(code):return len(code)==6 and code.startswith(('600','601','603','605','000','001','002','003'))
def parse_bars(payload,code,end):
    rows=payload['data'][code]['day'];result=[];seen=set()
    for row in rows:
        if row[0]>end:continue
        o,c,h,l,v=map(float,row[1:6])
        if row[0] in seen or not all(math.isfinite(x) for x in (o,c,h,l,v)) or min(o,c,h,l)<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h:raise ValueError('Invalid daily bar')
        seen.add(row[0]);result.append({'date':row[0],'open':o,'close':c,'high':h,'low':l,'volume_lots':v})
    if len(result)<60:raise ValueError('Insufficient history')
    return sorted(result,key=lambda b:b['date'])
def get_bars(symbol,end):
    code=exchange(symbol);url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param='+code+',day,,,640,'
    return safe(symbol,url,lambda p:parse_bars(p,code,end))
def get_pool(day):
    url='https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=200&sort=fbt:asc&date='+day.replace('-','')
    def parse(p):
        d=p['data']
        if not isinstance(d,dict) or not isinstance(d.get('pool'),list) or d.get('tc')!=len(d['pool']):raise ValueError('Pool unavailable/incomplete')
        return {'requested_date':day,'provider_qdate':str(d.get('qdate')),'rows':d['pool'],'date_validation':'requires_independent_price_check'}
    return safe(day,url,parse)

def build_signals(pools,histories,config):
    signals=[];audit=[]
    for day,pool in sorted(pools.items()):
        if pool['status']!='ok':audit.append({'date':day,'status':'pool_unavailable'});continue
        raw=pool['data'];eligible=[r for r in raw['rows'] if mainboard(r['c']) and not any(t in r['n'].upper() for t in ('ST','退'))]
        candidates=[];errors=[]
        for row in eligible:
            code=row['c'];bars=histories.get(code,[]);idx=next((i for i,b in enumerate(bars) if b['date']==day),None)
            if idx is None:errors.append(code+':missing_daily');continue
            b=bars[idx]
            if abs(b['close']-row['p']/1000)>0.005:errors.append(code+':pool_price_date_mismatch');continue
            if idx<60 or b['close']<3 or b['volume_lots']<=0:continue
            # Compare adjusted/unadjusted continuity separately before using limit prices.
            streak=0;j=idx
            while j>0 and abs(bars[j]['close']-limit(bars[j-1]['close'],1.1))<=0.005:
                streak+=1;j-=1
            if streak<1:errors.append(code+':not_10pct_limit_by_daily');continue
            if row.get('lbc')!=streak:errors.append(code+':streak_mismatch');continue
            ma20=sum(x['close'] for x in bars[idx-19:idx+1])/20
            candidates.append({'symbol':code,'name':row['n'],'signal_date':day,'streak':streak,'close':b['close'],'ma20':ma20,'volume_price_proxy':b['volume_lots']*100*b['close']})
        # Never cherry-pick matching stocks from a date whose source content failed date checks.
        status='verified_mainboard_subset' if eligible and not errors else 'rejected_incomplete_or_mismatched'
        audit.append({'date':day,'status':status,'provider_qdate':raw['provider_qdate'],'eligible_rows':len(eligible),'validated_signals':len(candidates),'errors':errors,'scope':'仅验证主板非ST子集；不能证明全历史池完整'})
        if status=='verified_mainboard_subset':signals.extend(candidates)
    return signals,audit

def commission(value,cfg):return max(cfg['minimum_commission_yuan'],value*cfg['commission_bps']/10000)
def validate_config(cfg):
    for key in ('commission_bps','minimum_commission_yuan','sell_stamp_bps','slippage_bps','initial_cash'):
        value=cfg[key]
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Invalid '+key)
    if cfg['initial_cash']<=0:raise ValueError('Initial cash must be positive')
    for key in ('holding_sessions','max_positions','max_new_per_day'):
        if isinstance(cfg[key],bool) or not isinstance(cfg[key],int) or cfg[key]<1:raise ValueError('Invalid '+key)
    if cfg['holding_sessions']<2:raise ValueError('T+1 requires holding at least two sessions')
    if cfg['train_end']>=cfg['holdout_start']:raise ValueError('Training and holdout overlap')


def simulate(signals,histories,sessions,cfg,variant,phase):
    validate_config(cfg)
    cash=cfg['initial_cash'];positions={};trades=[];events=[];equity=[];plan={};marks={}
    by_symbol={s:{b['date']:b for b in bars} for s,bars in histories.items()}
    eligible=[s for s in signals if (s['signal_date']<=cfg['train_end'] if phase=='train' else s['signal_date']>=cfg['holdout_start'])]
    eligible=[s for s in eligible if variant=='height_baseline' or s['streak']>=2]
    if variant=='two_board_ma20':eligible=[s for s in eligible if s['close']>s['ma20']]
    for s in eligible:plan.setdefault(s['signal_date'],[]).append(s)
    for day,rows in plan.items():rows.sort(key=lambda r:(-r['streak'],-r['volume_price_proxy'],r['symbol']));plan[day]=rows[:cfg['max_new_per_day']]
    index={d:i for i,d in enumerate(sessions)}
    for day in sessions:
        if phase=='train' and day>cfg['train_end']:break
        if phase=='holdout' and day<cfg['holdout_start']:continue
        i=index[day];today=by_symbol
        # Orders were frozen on the previous market session, before the current open.
        previous=sessions[i-1] if i else None
        for s in plan.get(previous,[]):
            code=s['symbol'];b=today.get(code,{}).get(day);prev=today.get(code,{}).get(previous)
            reason=None
            if len(positions)>=cfg['max_positions']:reason='position_cap'
            elif code in positions:reason='already_held'
            elif not b or not prev or b['volume_lots']<=0:reason='missing_or_suspended_entry'
            elif b['open']>=limit(prev['close'],1.1)-0.005:reason='open_at_upper_limit'
            elif b['low']<limit(prev['close'],0.9)-0.021 or b['high']>limit(prev['close'],1.1)+0.021:reason='corporate_action_or_special_limit_unknown'
            if reason:events.append({**s,'date':day,'status':'skipped','reason':reason});continue
            price=b['open']*(1+cfg['slippage_bps']/10000)
            if price>b['high'] or price>=limit(prev['close'],1.1):events.append({**s,'date':day,'status':'skipped','reason':'slippage_price_unreachable'});continue
            budget=min(cash,cfg['initial_cash']/cfg['max_positions']);qty=int(budget/(price*(1+cfg['commission_bps']/10000))/100)*100
            while qty>0 and qty*price+commission(qty*price,cfg)>cash:qty-=100
            if qty<=0:events.append({**s,'date':day,'status':'skipped','reason':'insufficient_cash_for_lot'});continue
            cost=qty*price+commission(qty*price,cfg);cash-=cost
            positions[code]={**s,'entry_date':day,'entry_index':i,'entry_price':price,'qty':qty,'entry_cost':cost,'scheduled_exit_index':i+cfg['holding_sessions']-1,'last_mark':b['close']}
        for code,p in list(positions.items()):
            b=today.get(code,{}).get(day);prev=today.get(code,{}).get(previous)
            if b:p['last_mark']=b['close']
            if i<p['scheduled_exit_index'] or day<=p['entry_date']:continue
            reason=None
            if not b or not prev or b['volume_lots']<=0:reason='missing_or_suspended_exit'
            elif b['low']<limit(prev['close'],0.9)-0.021 or b['high']>limit(prev['close'],1.1)+0.021:reason='corporate_action_or_special_limit_unknown'
            elif b['close']<=limit(prev['close'],0.9)+0.005:reason='close_at_lower_limit'
            if reason:events.append({'symbol':code,'date':day,'status':'exit_deferred','reason':reason});p['unresolved_reason']=reason;continue
            price=b['close']*(1-cfg['slippage_bps']/10000)
            if price<b['low']:events.append({'symbol':code,'date':day,'status':'exit_deferred','reason':'slippage_price_unreachable'});continue
            gross=price*p['qty'];proceeds=gross-commission(gross,cfg)-gross*cfg['sell_stamp_bps']/10000;cash+=proceeds
            trades.append({**p,'exit_date':day,'exit_price':price,'net_pnl':proceeds-p['entry_cost'],'net_return':proceeds/p['entry_cost']-1,'phase':phase,'variant':variant,'fill_type':'daily_open_close_proxy_not_broker_fill'});del positions[code]
        nav=cash+sum(p['qty']*p['last_mark'] for p in positions.values());equity.append({'date':day,'equity':nav,'cash':cash,'positions':len(positions)})
    peak=cfg['initial_cash'];dd=0
    for e in equity:peak=max(peak,e['equity']);dd=min(dd,e['equity']/peak-1)
    return {'variant':variant,'phase':phase,'closed_trades':len(trades),'open_positions':list(positions.values()),'unresolved_positions':len(positions),'total_return':equity[-1]['equity']/cfg['initial_cash']-1 if equity else 0,'max_drawdown':dd,'win_rate':sum(t['net_pnl']>0 for t in trades)/len(trades) if trades else None,'trades':trades,'events':events,'equity':equity,'pending_signal_count':sum(s['signal_date']>=sessions[-1] for s in eligible) if sessions else len(eligible),'interpretation':'含未平仓估值的模拟净值；非已实现收益，未结清持仓必须单列'}

def run(root,config_path,cache=None):
    cfg=json.loads(config_path.read_text());validate_config(cfg);run_id=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+digest(cfg)[:8];out=root/'state/backtests'/run_id;out.mkdir(parents=True,exist_ok=True)
    write(out/'protocol.json',cfg)
    dates=[];day=dt.date.fromisoformat(cfg['signal_start']);end=dt.date.fromisoformat(cfg['signal_end'])
    while day<=end:
        if day.weekday()<5:dates.append(day.isoformat())
        day+=dt.timedelta(days=1)
    if cache and (cache/'pool_requests.json').exists():
        cached=json.loads((cache/'pool_requests.json').read_text());pools={day:cached[day] for day in dates}
    else:
        with cf.ThreadPoolExecutor(max_workers=5) as ex:pools=dict(zip(dates,ex.map(get_pool,dates)))
    write(out/'pool_requests.json',pools)
    symbols=sorted({r['c'] for p in pools.values() if p['status']=='ok' for r in p['data']['rows'] if mainboard(r['c'])})
    print('Pool requests:',len(dates),'responded:',sum(p['status']=='ok' for p in pools.values()),'symbols:',len(symbols),flush=True)
    histories={};fetches=[]
    def history(symbol):
        path=cache/'daily'/(symbol+'.json') if cache else None
        if path and path.exists():return json.loads(path.read_text())
        return get_bars(symbol,cfg['data_end'])
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for symbol,result in zip(symbols,ex.map(history,symbols)):
            write(out/'daily'/ (symbol+'.json'),result);fetches.append({k:v for k,v in result.items() if k!='data'})
            if result['status']=='ok':
                bars=result['data'];start=next((i for i,b in enumerate(bars) if b['date']>=cfg['signal_start']),len(bars))
                result['data']=bars[max(0,start-60):]
                histories[symbol]=result['data']
                write(out/'daily'/(symbol+'.json'),result)
    # Use index bars as the session calendar; never infer trading days from weekdays.
    calendar_url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,640,'
    calendar=json.loads((cache/'calendar.json').read_text()) if cache and (cache/'calendar.json').exists() else safe('calendar',calendar_url,lambda p:parse_bars(p,'sh000001',cfg['data_end']));write(out/'calendar.json',calendar)
    if calendar['status']!='ok':raise ValueError('No verified market session calendar')
    sessions=[b['date'] for b in calendar['data'] if cfg['signal_start']<=b['date']<=cfg['data_end']]
    signals,audit=build_signals(pools,histories,cfg);write(out/'date_audit.json',audit);write(out/'signals.json',signals)
    results=[simulate(signals,histories,sessions,cfg,v,p) for v in cfg['variants'] for p in ('train','holdout')]
    data_digest=digest({'histories':histories,'pools':pools,'sessions':sessions})
    payload={'engine_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'run_id':run_id,'protocol_hash':digest(cfg),'input_hash':data_digest,'signal_count':len(signals),'verified_dates':[a['date'] for a in audit if a['status']=='verified_mainboard_subset'],'requested_dates':len(dates),'data_fetches':fetches,'results':results,'status':'EXPLORATORY_ONLY','automatic_adoption':False,'limitations':['历史池成员来源完整性和历史ST/退市状态未独立保证','日线无法验证封单、排队与实际开盘成交','除权及特殊涨跌幅仅保守剔除异常，未重建全部公司行为','训练/样本外仅时间隔离，样本不足不作有效性结论','全部固定方案并列报告，不按样本外结果挑赢家','不是原龙头框架完整复现，未纳入历史题材或作者观点']}
    write(out/'result.json',payload)
    lines=['# 公开数据日线研究回测','','状态：探索性机制验证，不批准策略上线。',f"协议hash：{payload['protocol_hash']}；输入hash：{data_digest}",f"请求{len(dates)}个工作日，核验通过{len(payload['verified_dates'])}个信号日，共{len(signals)}条历史信号。",'', '|方案|区间|已平仓|未平仓|含估值净值变化|最大回撤|已平仓胜率|','|---|---|---:|---:|---:|---:|---:|']
    for r in results:lines.append(f"|{r['variant']}|{r['phase']}|{r['closed_trades']}|{r['unresolved_positions']}|{r['total_return']:.2%}|{r['max_drawdown']:.2%}|{r['win_rate']:.2%}|" if r['win_rate'] is not None else f"|{r['variant']}|{r['phase']}|0|{r['unresolved_positions']}|{r['total_return']:.2%}|{r['max_drawdown']:.2%}|无样本|")
    lines+=['','## 固定规则',f"历史信号冻结后次一交易日开盘代理买入；持有{cfg['holding_sessions']}个市场交易日于收盘代理卖出；遵守T+1，涨停开盘跳过，跌停收盘顺延。",f"初始资金{cfg['initial_cash']}元，最多{cfg['max_positions']}仓，100股整数手，佣金{cfg['commission_bps']}bp/边且最低{cfg['minimum_commission_yuan']}元，卖出印花税{cfg['sell_stamp_bps']}bp，滑点{cfg['slippage_bps']}bp/边。",'先按前日冻结名单买入，再处理当日收盘退出；不提前使用下午卖出资金。','','## 日期审核']
    lines.extend('- '+a['date']+'：'+a['status']+'；'+'，'.join(a.get('errors',[])[:3]) for a in audit)
    lines+=['','## 局限']+['- '+x for x in payload['limitations']]+['','[原始结果与逐笔记录](../../state/backtests/'+run_id+'/result.json)']
    report=root/'reports/backtests'/(run_id+'.md');report.parent.mkdir(parents=True,exist_ok=True);report.write_text('\n'.join(lines)+'\n',encoding='utf-8');write(root/'state/backtests/LATEST.json',{'run_id':run_id,'report':str(report.relative_to(root)),'result':str((out/'result.json').relative_to(root))});print('Completed:',report,flush=True);return payload

def freeze_inputs(root,run_id):
    out=root/'state/backtests'/run_id
    cfg=json.loads((out/'protocol.json').read_text());pools=json.loads((out/'pool_requests.json').read_text());result=json.loads((out/'result.json').read_text())
    histories={}
    for path in (out/'daily').glob('*.json'):
        r=json.loads(path.read_text())
        if r['status']=='ok':histories[path.stem]=r['data']
    calendar=json.loads((out/'calendar.json').read_text())
    sessions=[b['date'] for b in calendar['data'] if cfg['signal_start']<=b['date']<=cfg['data_end']]
    bundle={'config':cfg,'pools':pools,'histories':histories,'sessions':sessions,'expected_result_hash':digest(result['results']),'expected_signal_hash':digest(json.loads((out/'signals.json').read_text()))}
    raw=json.dumps(bundle,ensure_ascii=False,separators=(',',':')).encode();packed=gzip.compress(raw,mtime=0)
    (out/'inputs.json.gz').write_bytes(packed)
    manifest={'input_archive':'inputs.json.gz','sha256':hashlib.sha256(packed).hexdigest(),'decompressed_sha256':hashlib.sha256(raw).hexdigest(),'expected_result_hash':bundle['expected_result_hash'],'protocol_hash':digest(cfg)}
    write(out/'replay_manifest.json',manifest);return manifest

def replay(archive,manifest_path):
    manifest=json.loads(manifest_path.read_text());packed=archive.read_bytes()
    if hashlib.sha256(packed).hexdigest()!=manifest['sha256']:raise ValueError('Input archive hash mismatch')
    raw=gzip.decompress(packed)
    if hashlib.sha256(raw).hexdigest()!=manifest['decompressed_sha256']:raise ValueError('Decompressed hash mismatch')
    b=json.loads(raw);cfg=b['config']
    if digest(cfg)!=manifest['protocol_hash']:raise ValueError('Protocol hash mismatch')
    signals,audit=build_signals(b['pools'],b['histories'],cfg)
    results=[simulate(signals,b['histories'],b['sessions'],cfg,v,phase) for v in cfg['variants'] for phase in ('train','holdout')]
    matched=digest(results)==b['expected_result_hash'] and digest(signals)==b['expected_signal_hash']
    if not matched:raise ValueError('Replay differs from archived result')
    return {'status':'REPLAY_MATCHED','result_hash':digest(results),'signal_count':len(signals),'verified_dates':len([a for a in audit if a['status']=='verified_mainboard_subset'])}


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));p.add_argument('--config',default='config/public_backtest.json');p.add_argument('--from-cache');p.add_argument('--replay');p.add_argument('--manifest');a=p.parse_args();root=Path(a.root)
    if a.replay:
        if not a.manifest:p.error('--manifest required with --replay')
        print(json.dumps(replay(Path(a.replay),Path(a.manifest)),ensure_ascii=False));return
    result=run(root,root/a.config,Path(a.from_cache) if a.from_cache else None);freeze_inputs(root,result['run_id'])
if __name__=='__main__':main()
