"""Bounded public market research. Each unavailable dataset remains explicit."""
import concurrent.futures as cf
import datetime as dt
import json
import math
import time
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo
TZ=ZoneInfo('Asia/Shanghai')

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://quote.eastmoney.com/' if 'eastmoney.com' in url else 'https://finance.sina.com.cn/'})
    with urllib.request.urlopen(req,timeout=12) as r:
        raw=r.read(8_000_001)
    if len(raw)>8_000_000:raise ValueError('response too large')
    return json.loads(raw)

def safe(name,url,parser):
    errors=[]
    for attempt in range(3):
        try:
            request_url=url+('&_='+str(time.time_ns()) if attempt else '')
            data=parser(fetch(request_url))
            return {'name':name,'status':'ok','source':url,'data':data,'attempts':attempt+1,'observed_at':dt.datetime.now(TZ).isoformat()}
        except Exception as e:
            errors.append(type(e).__name__)
            if attempt<2:time.sleep(1)
    return {'name':name,'status':'failed','source':url,'error':errors[-1],'attempts':3,'attempt_errors':errors,'observed_at':dt.datetime.now(TZ).isoformat()}


def exchange(symbol):return ('sh' if symbol.startswith('6') else 'sz')+symbol

def daily(symbol,day):
    code=exchange(symbol)
    url='https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param='+code+',day,,,'+'60,qfq'
    def parse(p):
        d=p['data'][code];bars=d.get('qfqday',d.get('day',[]))
        rows=[{'date':r[0],'open':float(r[1]),'close':float(r[2]),'high':float(r[3]),'low':float(r[4]),'volume':float(r[5])} for r in bars if r[0]<=day]
        if not rows:raise ValueError('No dated daily bars')
        return rows
    return safe(symbol,url,parse)

def minute(symbol,day,observed):
    code=exchange(symbol);url='https://web.ifzq.gtimg.cn/appstock/app/minute/query?code='+code
    def parse(p):
        d=p['data'][code]['data'];date=d['date'];date=str(date).replace('-','')
        if date!=day.replace('-',''):raise ValueError('Minute date mismatch')
        rows=[];last=-1
        for text in d['data']:
            r=text.split();hhmm=r[0];stamp=dt.datetime.fromisoformat(day+'T'+hhmm[:2]+':'+hhmm[2:]+':00+08:00')
            if stamp>dt.datetime.fromisoformat(observed):continue
            price=float(r[1]);volume=float(r[2])
            if not math.isfinite(price) or price<=0 or volume<last:raise ValueError('Invalid minute values')
            last=volume;rows.append({'time':stamp.isoformat(),'price':price,'cumulative_volume_lots':volume})
        if not rows:raise ValueError('No usable minutes')
        return {'date':day,'bars':rows,'kind':'public_price_and_cumulative_volume_not_L2'}
    return safe(symbol,url,parse)

def indices(day):
    url='https://push2.eastmoney.com/api/qt/ulist.np/get?secids=1.000001,0.399001,0.399006&fields=f12,f14,f2,f3,f6,f124'
    def parse(p):
        rows=[]
        for r in p['data']['diff']:
            stamp=dt.datetime.fromtimestamp(r['f124'],TZ)
            if stamp.date().isoformat()!=day:raise ValueError('Index date mismatch')
            rows.append({'symbol':r['f12'],'name':r['f14'],'price':r['f2']/100,'change_pct':r['f3']/100,'amount_yuan':r['f6'],'quote_at':stamp.isoformat()})
        if len(rows)!=3:raise ValueError('Incomplete indices')
        return rows
    return safe('indices',url,parse)

def pool(kind,day):
    url='https://push2ex.eastmoney.com/getTopic'+kind+'?date='+day.replace('-','')+'&pagesize=300&Pageindex=0&sort=fbt:asc&ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt'
    if kind=='DTPool':url=url.replace('sort=fbt:asc','sort=fund:asc')
    def parse(p):
        d=p['data']
        if not isinstance(d,dict) or str(d.get('qdate'))!=day.replace('-','') or not isinstance(d.get('pool'),list) or d.get('tc')!=len(d.get('pool',[])):raise ValueError('Pool unavailable')
        return d
    return safe(kind,url,parse)

def breadth(day):
    base='https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.'
    try:
        total=int(fetch(base+'getHQNodeStockCount?node=hs_a'))
        if not 3000<total<10000:raise ValueError('Unexpected universe')
        def page(n):
            expected=min(100,total-(n-1)*100)
            for attempt in range(3):
                try:
                    url=base+'getHQNodeData?node=hs_a&num=100&page='+str(n)+'&sort=symbol&asc=1'
                    if attempt:url+='&_='+str(time.time_ns())
                    p=fetch(url)
                    if not isinstance(p,list) or len(p)!=expected:raise ValueError('Incomplete page '+str(n))
                    return p
                except Exception:
                    if attempt==2:raise
                    time.sleep(1)
        with cf.ThreadPoolExecutor(max_workers=4) as ex:pages=list(ex.map(page,range(1,(total+99)//100+1)))
        raw=[r for p in pages for r in p];unique={r['symbol']:r for r in raw}
        if len(unique)!=total or len(raw)!=total:raise ValueError('Incomplete/duplicate universe '+str(len(raw))+'/'+str(len(unique))+'/'+str(total))
        rows=[];excluded_invalid=0
        for r in unique.values():
            code=r['code']
            if not code.startswith(('0','3','6')):continue
            try:
                price=float(r['trade']);previous=float(r['settlement'])
                if price<=0 or previous<=0:excluded_invalid+=1;continue
                pct=float(r['changepercent']);amount=float(r['amount'])
                if not all(math.isfinite(v) for v in (price,previous,pct,amount)):raise ValueError('Invalid quote')
                rows.append({'symbol':code,'name':r['name'],'price':price,'previous':previous,'change_pct':pct,'amount_yuan':amount,'quote_time':r['ticktime']})
            except (ValueError,TypeError,KeyError):excluded_invalid+=1
        return {'name':'breadth','status':'ok','source':base,'data':{'scope':'Sina hs_a 沪深A股；按证券代码过滤；快照非同时到达','date':day,'date_verification':'request_day_only_provider_has_no_date','excluded_invalid':excluded_invalid,'count':len(rows),'up':sum(r['price']>r['previous'] for r in rows),'down':sum(r['price']<r['previous'] for r in rows),'flat':sum(r['price']==r['previous'] for r in rows),'amount_yuan':sum(r['amount_yuan'] for r in rows),'rows':rows}}
    except Exception as e:return {'name':'breadth','status':'failed','error':type(e).__name__,'detail':str(e)[:120],'source':base}

def notices(symbol,day):
    url='https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=5&page_index=1&ann_type=A&stock_list='+symbol+'&client_source=web&f_node=0&s_node=0'
    def parse(p):
        result=[]
        for r in p['data']['list']:
            date=r['notice_date'][:10]
            if date>day:continue
            result.append({'date':date,'title':r['title'][:180],'url':'https://data.eastmoney.com/notices/detail/'+symbol+'/'+r['art_code']+'.html'})
        return result
    return safe(symbol,url,parse)

def collect(snapshot,observed,extra_symbols=(),environment_only=False):
    day=snapshot['date'];symbols=[r['symbol'] for r in sorted(snapshot['rows'],key=lambda r:(-r['streak'],r['first_limit_time']))[:12]]
    symbols=list(dict.fromkeys(list(extra_symbols)+symbols))[:24]
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        jobs={'indices':ex.submit(indices,day),'breadth':ex.submit(breadth,day),'broken':ex.submit(pool,'ZBPool',day),'down_limit':ex.submit(pool,'DTPool',day)}
        result={k:f.result() for k,f in jobs.items()}
    if environment_only:
        return {'date':day,'observed_at':observed,'capture_type':'live_environment_only','datasets':result,'limitations':['午间仅采集指数、广度、炸板与跌停池；逐股日线、分钟、公告集中在晚间采集']}
    for name,fn in [('daily',lambda s:daily(s,day)),('minutes',lambda s:minute(s,day,observed)),('notices',lambda s:notices(s,day))]:
        with cf.ThreadPoolExecutor(max_workers=4) as ex:result[name]=list(ex.map(fn,symbols))
    return {'date':day,'observed_at':observed,'capture_type':'live_collection','datasets':result,'limitations':['当日候选及待核对预案合计最多24只，不代表全市场分钟覆盖','公开分钟价格与累计量不是逐笔订单流','公告仅最近5条，不代表完整风险筛查','实时广度源无日期字段，不能用于历史回填或独立确认交易日']}
