"""Import broker-exported public research snapshots. Never places orders."""
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path

def validate(payload):
    records=payload['records'];observed=dt.datetime.fromisoformat(payload['exported_at'])
    if not observed.tzinfo or not isinstance(records,list) or len(records)>200000:raise ValueError('Invalid export envelope')
    previous={};clean=[]
    for r in records:
        symbol=r['symbol'];time=dt.datetime.fromisoformat(r['quote_at'])
        if len(symbol)!=6 or not symbol.isdigit() or not symbol.startswith(('0','3','6')):raise ValueError('Invalid symbol')
        if not time.tzinfo or time>observed or time.date().isoformat()!=payload['date']:raise ValueError('Invalid quote time')
        if symbol in previous and time<=previous[symbol]:raise ValueError('Unordered/duplicate snapshots')
        values={k:float(r[k]) for k in ('price','limit_up_price','bid_amount_yuan','ask_amount_yuan','seal_amount_yuan')}
        if any(not math.isfinite(v) or v<0 for v in values.values()) or values['price']<=0 or values['limit_up_price']<=0:raise ValueError('Invalid numeric field')
        previous[symbol]=time;clean.append({'symbol':symbol,'quote_at':time.isoformat(),**values})
    return {'date':payload['date'],'exported_at':observed.isoformat(),'source':payload['source'],'records':clean,'kind':'broker_snapshot_not_order_queue','limitations':['相邻快照封单变化不等于撤单量','没有委托队列，不能证明排队成交','不含个人账户、持仓、委托或密钥']}

def summary(payload):
    groups={}
    for r in payload['records']:groups.setdefault(r['symbol'],[]).append(r)
    result=[]
    for symbol,rows in groups.items():
        sealed=[r for r in rows if abs(r['price']-r['limit_up_price'])<0.005]
        first=sealed[0]['seal_amount_yuan'] if sealed else None;last=sealed[-1]['seal_amount_yuan'] if sealed else None
        result.append({'symbol':symbol,'snapshots':len(rows),'first_at':rows[0]['quote_at'],'last_at':rows[-1]['quote_at'],'sealed_snapshots':len(sealed),'seal_snapshot_retention':last/first if first else None,'tradable_fill':'unknown','cancel_amount':'unknown'})
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('--root',default=str(Path(__file__).resolve().parents[1]));a=p.parse_args()
    data=validate(json.loads(Path(a.input).read_text(encoding='utf-8')));data['summary']=summary(data)
    digest=hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()[:16]
    out=Path(a.root)/'state/l2'/data['date']/(digest+'.json');out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');print(out)
if __name__=='__main__':main()
