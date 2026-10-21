"""Public limit-up snapshots. A snapshot is not a complete-market feed or L2."""
import argparse
import datetime as dt
import hashlib
import math
import json
import re
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from quant import database, save, clock, timestamp

URL='https://www.jikefupan.com/'


class Tables(HTMLParser):
    def __init__(self):
        super().__init__();self.rows=[];self.row=None;self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':self.row=[]
        if tag in ('td','th') and self.row is not None:self.cell=[]
    def handle_data(self,data):
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(' '.join(''.join(self.cell).split()));self.cell=None
        if tag=='tr' and self.row is not None:
            self.rows.append(self.row);self.row=None


def number(value):
    result=float(value.rstrip('%'))
    if result!=result or abs(result)==float('inf'):raise ValueError('非有限数字')
    return result


def parse(body,expected_date):
    parser=Tables();parser.feed(body)
    headers=['代码','名称','最新价','涨幅','封单比','换手率','流通市值','总市值','首次封板','最后封板','炸板次数','连板数','行业']
    matches=[i for i,row in enumerate(parser.rows) if row==headers]
    if len(matches)!=1:raise ValueError('数据表结构改变或不存在')
    page_dates=set(re.findall(r'(20\d{2}-\d{2}-\d{2})',body))
    ladder=next((r for r in parser.rows if len(r)==3 and r[0]=='进度'),None)
    if ladder is None or expected_date not in ladder[2]:raise ValueError('梯队日期与目标不一致')
    # Require an explicit timestamp, never silently substitute fetch time as market time.
    updated=re.search(r'最后更新时间[^0-9]{0,30}(20\d{2}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})',re.sub('<[^>]+>',' ',body))
    if updated is None or not updated[1].startswith(expected_date):raise ValueError('缺少目标日期更新时间')
    items=[];seen=set()
    for row in parser.rows[matches[0]+1:]:
        if len(row)!=13:continue
        code,name,price,pct,seal,turn,floatcap,totalcap,first,last,breaks,streak,theme=row
        if not re.fullmatch(r'\d{6}',code):continue
        if code in seen:raise ValueError('重复证券代码')
        seen.add(code)
        for t in (first,last):dt.time.fromisoformat(t)
        if first>last:raise ValueError('首次封板晚于最后封板')
        n=1 if streak=='首板' else int(streak.replace('连板',''))
        item={'symbol':code,'name':name,'close':number(price),'change_pct':number(pct),'seal_ratio_pct':number(seal),'turnover_pct':number(turn),'first_limit_time':first,'last_limit_time':last,'break_count':int(breaks),'streak':n,'theme_label':theme}
        if item['close']<=0 or item['break_count']<0 or n<1:raise ValueError('数值范围异常')
        items.append(item)
    if not items:raise ValueError('涨停表为空')
    if any(x['seal_ratio_pct'] < 0 or x['turnover_pct'] < 0 for x in items):raise ValueError('负比率')
    return {'date':expected_date,'source_updated_at':updated[1]+'+08:00','rows':items,'count':len(items),'scope':'source_limit_up_pool_only','limitations':['第三方字段未独立核验','非全市场OHLC或L2','封单比不是封单留存率','开板次数口径可能因供应源不同而异']}


def collect(db,expected_date,phase='close',body=None,observed_at=None):
    requested_observation=observed_at
    observed_at=observed_at or clock()
    try:
        if body is None:
            req=urllib.request.Request(URL,headers={'User-Agent':'LongtouResearch/1.0'})
            with urllib.request.urlopen(req,timeout=40) as response:
                raw=response.read(3_000_001)
                if len(raw)>3_000_000:raise ValueError('响应过大')
                body=raw.decode('utf-8')
        if requested_observation is None:observed_at=clock()
        snapshot=parse(body,expected_date)
        if timestamp(snapshot['source_updated_at'])>timestamp(observed_at):raise ValueError('源更新时间晚于观测时间')
        snapshot.update({'source':URL,'observed_at':observed_at,'phase':phase,'capture_type':'live' if timestamp(observed_at).date().isoformat()==expected_date else 'retrospective','body_sha256':hashlib.sha256(body.encode()).hexdigest()})
        key=save(db,'public_snapshot',snapshot)
        return key,snapshot
    except Exception as exc:
        save(db,'collection_failure',{'source':URL,'expected_date':expected_date,'observed_at':observed_at,'error_type':type(exc).__name__,'message':str(exc)[:300]})
        raise


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',default='data/research.sqlite');p.add_argument('--date',required=True);p.add_argument('--phase',choices=['midday','close'],default='close');p.add_argument('--output',default='public_snapshot.json')
    a=p.parse_args();key,snapshot=collect(database(a.db),a.date,a.phase);Path(a.output).write_text(json.dumps({'snapshot_id':key,**snapshot},ensure_ascii=False,indent=2),encoding='utf-8');print(f'{key} rows={snapshot["count"]}')


if __name__=='__main__':main()
