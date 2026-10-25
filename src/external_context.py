"""Timestamped external markets and public headline events. No generated news facts."""
import concurrent.futures
import datetime as dt
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

TZ=ZoneInfo('Asia/Shanghai')
UTC=dt.timezone.utc
MARKETS=[
 {'symbol':'^GSPC','name':'标普500','region':'US','tz':'America/New_York','open':'09:30','close':'16:00','kind':'index'},
 {'symbol':'^DJI','name':'道琼斯','region':'US','tz':'America/New_York','open':'09:30','close':'16:00','kind':'index'},
 {'symbol':'^IXIC','name':'纳斯达克','region':'US','tz':'America/New_York','open':'09:30','close':'16:00','kind':'index'},
 {'symbol':'^SOX','name':'费城半导体','region':'US','tz':'America/New_York','open':'09:30','close':'16:00','kind':'index'},
 {'symbol':'^N225','name':'日经225','region':'JP','tz':'Asia/Tokyo','open':'09:00','close':'15:30','kind':'index'},
 {'symbol':'^KS11','name':'韩国综指','region':'KR','tz':'Asia/Seoul','open':'09:00','close':'15:30','kind':'index'},
 {'symbol':'^HSI','name':'恒生指数','region':'HK','tz':'Asia/Hong_Kong','open':'09:30','close':'16:10','kind':'index'},
 {'symbol':'^HSCE','name':'恒生国企','region':'HK','tz':'Asia/Hong_Kong','open':'09:30','close':'16:10','kind':'index'},
 {'symbol':'ES=F','name':'标普期货','region':'US','tz':'America/New_York','kind':'future'},
 {'symbol':'NQ=F','name':'纳指期货','region':'US','tz':'America/New_York','kind':'future'},
 {'symbol':'CNH=X','name':'美元兑离岸人民币','region':'FX','tz':'UTC','kind':'fx'}]
FEEDS=[('BBC Business','https://feeds.bbci.co.uk/news/business/rss.xml'),('WSJ Markets','https://feeds.a.dj.com/rss/RSSMarketsMain.xml')]
SINA='https://feed.mix.sina.com.cn/api/roll/get?pageid=153&lid=2509&num=50&page=1'
TOPICS={
 '半导体/算力':['semiconductor','chip','nvidia','tsmc','intel','amd','芯片','半导体','英伟达','算力','存储'],
 '机器人/AI':['artificial intelligence','robot','openai','ai ','机器人','人工智能','大模型','ai应用'],
 '利率/汇率':['federal reserve','interest rate','treasury','inflation','dollar','美联储','降息','加息','通胀','人民币','汇率'],
 '能源/地缘':['oil','crude','iran','war','tariff','sanction','石油','原油','战争','关税','制裁'],
 '政策/监管':['regulation','policy','监管','政策','国务院','证监会','财政部','央行']}


def get(url):
    request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 LeaderResearch/0.3','Referer':'https://www.cls.cn/telegraph' if 'cls.cn' in url else 'https://finance.yahoo.com/'})
    with urllib.request.urlopen(request,timeout=16) as r:
        b=r.read(3_000_001)
        if len(b)>3_000_000:raise ValueError('response too large')
        return b


def finite(value):
    x=float(value)
    if not math.isfinite(x):raise ValueError('nonfinite quote')
    return x


def parse_quote(payload,config,observed):
    now=dt.datetime.fromisoformat(observed)
    result=payload['chart']['result'][0];meta=result['meta']
    tick=dt.datetime.fromtimestamp(meta['regularMarketTime'],UTC)
    if tick>now:raise ValueError('quote timestamp is in the future')
    local=now.astimezone(ZoneInfo(config['tz']));quote_local=tick.astimezone(ZoneInfo(config['tz']))
    bars=[]
    quotes=result.get('indicators',{}).get('quote',[{}])[0].get('close',[])
    for ts,close in zip(result.get('timestamp',[]),quotes):
        if close is not None and ts<=tick.timestamp():
            day=dt.datetime.fromtimestamp(ts,ZoneInfo(config['tz'])).date()
            bars.append((day,finite(close)))
    previous=[(day,close) for day,close in bars if day<quote_local.date()]
    previous_close=max(previous,key=lambda x:x[0])[1] if previous else meta.get('previousClose')
    # chartPreviousClose refers to the chart range start; never use it as yesterday's close.
    price=finite(meta['regularMarketPrice'])
    pct=(price/finite(previous_close)-1)*100 if previous_close is not None and finite(previous_close)>0 else None
    if config['kind']!='index':session='连续报价/可能延迟，非结算价'
    elif local.weekday()>4:session='非工作日时段，显示最近报价'
    elif local.time()<dt.time.fromisoformat(config['open']):session='当日未开盘，显示上一交易日'
    elif quote_local.date()<local.date():session='当日报价尚未更新/休市待核实'
    elif local.time()>=dt.time.fromisoformat(config['close']):session='收盘时段报价（可能延迟）'
    else:session='盘中报价（可能延迟）'
    age=(now-tick).total_seconds()/3600
    return {**config,'status':'ok','price':price,'previous_close':previous_close,'change_pct':pct,'quote_at':tick.astimezone(TZ).isoformat(),'exchange_date':quote_local.date().isoformat(),'observed_at':observed,'session':session,'age_hours':round(age,2),'stale':age>96,'source':'Yahoo Finance public chart','source_url':'https://finance.yahoo.com/quote/'+urllib.parse.quote(config['symbol'],safe='')+'/'}


def topics(title):
    lower=title.lower()
    return [key for key,words in TOPICS.items() if any(word in lower for word in words)]


def item(source,title,url,published,observed,window_start):
    now=dt.datetime.fromisoformat(observed);start=dt.datetime.fromisoformat(window_start)
    if published.tzinfo is None:published=published.replace(tzinfo=UTC)
    if not start<=published<=now:return None
    title=' '.join(re.sub('<[^>]+>',' ',title).split())[:180]
    if not title or not url.startswith(('https://','http://')):return None
    key=hashlib.sha256((title.lower()+published.date().isoformat()).encode()).hexdigest()[:16]
    return {'id':key,'source':source,'headline':title,'url':url,'published_at':published.astimezone(TZ).isoformat(),'first_seen_at':observed,'topics':topics(title),'evidence_level':'报道标题；原始事实与市场影响待核验','event_status':'预告/不确定性表述' if re.search(r'预计|可能|拟|将于|计划|expected|may |could |plans? ',title,re.I) else '已发布报道；不等于事件已证实'}


def parse_rss(raw,source,observed,window_start):
    root=ET.fromstring(raw);records=[]
    for row in root.findall('.//item'):
        title=row.findtext('title') or '';url=row.findtext('link') or '';pub=row.findtext('pubDate')
        if not pub:continue
        try:r=item(source,title,url,parsedate_to_datetime(pub),observed,window_start)
        except (ValueError,TypeError):continue
        if r:records.append(r)
    return records


def parse_sina(raw,observed,window_start):
    payload=json.loads(raw)
    rows=payload['result']['data']
    if not isinstance(rows,list):raise ValueError('Sina schema changed')
    records=[]
    for row in rows:
        try:
            r=item('新浪财经公开滚动',row.get('title',''),row.get('url',''),dt.datetime.fromtimestamp(int(row['ctime']),UTC),observed,window_start)
        except (ValueError,TypeError,KeyError):continue
        if r:records.append(r)
    return records


def collect_external(phase,observed,window_start,getter=get):
    def quote(config):
        url='https://query1.finance.yahoo.com/v8/finance/chart/'+urllib.parse.quote(config['symbol'],safe='')+'?interval=1d&range=5d'
        try:
            payload=json.loads(getter(url))
            fetched=dt.datetime.now(TZ).isoformat()
            return parse_quote(payload,config,fetched)
        except Exception as e:return {**config,'status':'failed','observed_at':observed,'error':type(e).__name__}
    def news(pair):
        source,url=pair
        try:
            raw=getter(url)
            rows=parse_sina(raw,observed,window_start) if source=='新浪财经公开滚动' else parse_rss(raw,source,observed,window_start)
            return {'source':source,'url':url,'status':'ok','events':rows}
        except Exception as e:return {'source':source,'url':url,'status':'failed','error':type(e).__name__,'events':[]}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:quotes=list(pool.map(quote,MARKETS))
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:feeds=list(pool.map(news,FEEDS+[('新浪财经公开滚动',SINA)]))
    events={r['id']:r for feed in feeds for r in feed['events']}
    return {'phase':phase,'observed_at':observed,'news_window_start':window_start,'markets':quotes,'news_sources':[{k:v for k,v in f.items() if k!='events'} for f in feeds],'events':sorted(events.values(),key=lambda r:r['published_at'],reverse=True)[:60],'coverage':'公开报价及有限新闻源，非完整全球事件流','news_complete':False,'reporting_rule':'记录报价时间与交易状态；缺失不替换为零；标题关联不等于利好利空已验证'}


def overlay(plan,context,evening=None):
    actions=[];available={m['symbol']:m for m in context.get('markets',[]) if m['status']=='ok' and not m.get('stale')}
    us=[available[s]['change_pct'] for s in ('^GSPC','^DJI','^IXIC') if s in available and available[s].get('change_pct') is not None]
    risk='risk_watch' if len(us)>=2 and sum(x<=-1 for x in us)>=2 else 'conditional_watch'
    if risk=='risk_watch':actions.append({'type':'market_risk','action':'降低进攻预期，等待盘内确认','reason':'至少两项美国主要指数跌幅达到1%；仅为未校准风险提示','evidence':[available[s]['source_url'] for s in ('^GSPC','^DJI','^IXIC') if s in available]})
    sox=available.get('^SOX')
    if sox and sox.get('change_pct') is not None and abs(sox['change_pct'])>=1:
        actions.append({'type':'sector_watch','action':'半导体映射需谨慎确认' if sox['change_pct']<0 else '半导体映射加入竞价观察','reason':'费城半导体指数变化；不能直接外推A股个股','evidence':[sox['source_url']]})
    evening_ids={x['id'] for x in (evening or {}).get('events',[])}
    events=[e for e in context.get('events',[]) if e['id'] not in evening_ids]
    for e in events:
        if e['topics']:actions.append({'type':'reported_event','action':'加入盘前待核验事项','reason':' / '.join(e['topics'])+'：'+e['headline'],'evidence':[e['url']],'event_id':e['id']})
    return {'base_snapshot_hash':plan['source_snapshot_hash'],'risk_posture':risk,'candidate_order_changed':False,'changes':actions,'new_event_ids':[e['id'] for e in events],'note':'保留盘内基础观察池；只追加有来源的风险/题材条件，不把标题直接变成交易指令'}


def market_table(context,baseline=None):
    old={m['symbol']:m for m in (baseline or {}).get('markets',[]) if m.get('status')=='ok'}
    lines=['|市场|最新值|较前收盘|北京时间报价时点|状态|较晚间变化|','|---|---:|---:|---|---|---:|']
    for m in context['markets']:
        if m['status']!='ok':lines.append(f"|{m['name']}|缺失|—|—|采集失败|—|");continue
        pct=f"{m['change_pct']:+.2f}%" if m.get('change_pct') is not None else '缺前收盘'
        prior=old.get(m['symbol']);diff=(m['price']/prior['price']-1)*100 if prior and prior['price'] else None
        delta=f'{diff:+.2f}%' if diff is not None else '—'
        session=m['session']+('；陈旧待核实' if m.get('stale') else '')
        lines.append(f"|{m['name']}|{m['price']:.2f}|{pct}|{m['quote_at']}|{session}|{delta}|")
    return lines


def render_external(context,phase,base=None,plan=None,revision=None,evening=None):
    morning=phase=='morning'
    lines=['# '+('盘前预案修订' if morning else '晚间外盘与事件补充'),'',f"编制时点：{context['observed_at']}；新闻窗口起点：{context['news_window_start']}",'', '## 外盘状态与时点','']+market_table(context,evening)
    if morning:
        names='、'.join(c['name']+' '+c['symbol'] for c in plan['candidates'][:3]) or '缺失：无可用候选'
        dashboard=['## 次日作战仪表盘','','基础行情：'+plan['market_date']+'；目标交易日：'+plan['target_date']+'；盘前截止09:15。','优先观察锚：'+names,'研究倾向：'+revision['risk_posture']+'；情绪周期与严格市场统计沿用晚间已核验证据，不依据外盘单独判定。','核心核验：题材是否扩散、核心承接是否保持、公告是否出现负面反证。风险未完整核验者仅观察，不升级重点接力。','模块结论：先对照晚间假设核验隔夜变化，再等待盘内确认。','']
        lines=lines[:4]+dashboard+lines[4:]
    if not morning:lines+=['','本节补充盘外信息，与前面的A股收盘复盘合并为同一份晚间报告。美股尚未开盘时显示上一交易日报价；当晚最终收盘留待次日盘前更新。']
    lines+=['','## 新闻源覆盖','']
    for s in context['news_sources']:lines.append(f"- {s['source']}：{s['status']}（{s['url']}）")
    lines+=['','## '+('较晚间新增/更新的事件线索' if morning else '盘后至晚间的事件线索'),'']
    prior_ids={x['id'] for x in (evening or {}).get('events',[])}
    news=[e for e in context['events'] if not morning or e['id'] not in prior_ids]
    if not news:lines.append('本次新闻源未取得窗口内新增条目；不能据此判断夜间没有事件。')
    for e in news[:12]:
        lines.append(f"- {e['published_at']} [{e['headline']}]({e['url']})｜{e['source']}｜关联：{'、'.join(e['topics']) or '暂未归类'}｜{e['event_status']}")
    if morning:
        lines+=['','## 盘内基础与修订依据','',f"基础行情日：{plan['market_date']}；目标日：{plan['target_date']}。",'盘内候选沿用已保存的收盘数据，以下只解释新增信息如何改变观察条件。','','### 本次修订']
        for x in revision['changes']:lines.append('- '+x['action']+'：'+x['reason']+'；依据：'+' '.join(x['evidence']))
        if not revision['changes']:lines.append('未取得足够、可用的新证据来修改观察条件；保留基础预案。')
        lines+=['','### 今日候选及验证条件','','|候选|盘内依据|盘前修订|失效条件|','|---|---|---|---|']
        for c in plan['candidates']:lines.append(f"|{c['name']} {c['symbol']}|{'；'.join(c['support'])}|{revision['risk_posture']}；等待自身及同题材盘中确认|{c['invalid']}|")
        from report_v2 import card_table
        lines+=['','### 十维龙头研究卡','',card_table(plan['candidates']) if plan['candidates'] and 'score_coverage' in plan['candidates'][0] else '缺失：需要按新版证据规则重建评分卡。']
        lines+=['','### 开盘前仍待核验','- 港股未开盘时不能据昨收判断今早强弱；日韩盘中数据会变化。','- 确认公司公告、除权除息、停复牌、监管与新闻原始出处；相关信息未覆盖时明确缺口。','- 外盘映射、新闻关联和风险阈值均未完成收益验证，不据此认定买点成立。']
    else:lines+=['','## 交给次日盘前的待办','- 美股完成当晚交易后，更新主要指数与半导体表现。','- 补充晚报之后的新新闻，核对报道时间和原始出处。','- 更新日韩早盘，标明港股开盘前状态；逐条说明是否调整预案。']
    lines+=['','信息来自有限公开源，新闻标题只作为研究线索；缺失数据和未核实事件不会自动变为利好或利空。']
    return '\n'.join(lines)+'\n'
