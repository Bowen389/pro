"""Versioned leader roles, prospective outcome accounting and bounded research reports."""
from collections import defaultdict
import datetime as dt
import hashlib
import json
import re

VERSION='leader-evidence-v1.1'
ALIASES={'AI应用':'AI应用','AI短剧':'传媒/短剧','短剧':'传媒/短剧','AI':'AI综合','算力租赁':'算力','端侧AI':'端侧AI'}
def themes(text):return sorted(set(ALIASES.get(x.strip(),x.strip()) for x in re.split(r'[+、,，/]',text) if x.strip()))
def build(snapshot,history,enrichment):
    grouped=defaultdict(list)
    for r in snapshot['rows']:
        for t in themes(r['theme_label']):grouped[t].append(r)
    prior=[s for s in history if s['date']<snapshot['date'] and s.get('phase')=='close']
    prior=max(prior,key=lambda s:s['source_updated_at']) if prior else None
    old=defaultdict(set)
    if prior:
        for r in prior['rows']:
            for t in themes(r['theme_label']):old[t].add(r['symbol'])
    groups=[]
    for name,rows in grouped.items():
        groups.append({'theme':name,'count':len(rows),'height':max(r['streak'] for r in rows),'prior_count':len(old[name]) if prior else None,'new_members':sorted({r['symbol'] for r in rows}-old[name]) if prior else None,'leaders':sorted(rows,key=lambda r:(-r['streak'],r['first_limit_time']))[:3]})
    groups.sort(key=lambda g:(-g['count'],-g['height'],g['theme']))
    maxheight=max((r['streak'] for r in snapshot['rows']),default=0);roles=[]
    dailies={r['name']:r['data'] for r in enrichment.get('datasets',{}).get('daily',[]) if r['status']=='ok'}
    for r in snapshot['rows']:
        own=[g for g in groups if r['symbol'] in {x['symbol'] for x in grouped[g['theme']]}]
        anchor=r['streak']==maxheight
        theme_core=any(g['count']>=3 and r['streak']>=2 and r['streak']==g['height'] for g in own)
        role='市场高度锚' if anchor else '题材高度核心' if theme_core else '同题材跟随观察' if any(g['count']>=3 for g in own) else '独立强势观察'
        bars=dailies.get(r['symbol'],[]);past=[b for b in bars if b['date']<snapshot['date']]
        ret20=(r['close']/past[-20]['close']-1)*100 if len(past)>=20 else None
        follow=max((g['count'] for g in own),default=1)
        expected=('高度锚保持相对强势，观察同题材'+str(follow)+'只样本是否继续跟随' if anchor else '题材高度优势保持，比较同方向其他高度股的承接' if theme_core else '等待题材核心保持强势后，再观察自身能否维持强度')
        invalid=('高度锚走弱且后排无接替；缺分钟数据时不判触发' if anchor else '题材高度优势丧失且跟随减少' if theme_core else '核心走弱或自身反复开板，原跟随假设失效')
        roles.append({**r,'role':role,'themes':themes(r['theme_label']),'breadth':max((g['count'] for g in own),default=1),'return20_pct':ret20,'liquidity_warning':'早封低换手，成交能力待核验' if r['first_limit_time']=='09:25:00' and r['turnover_pct']<3 else '成交能力仍待核验','support':['连续'+str(r['streak'])+'板','同题材池内最多'+str(max((g['count'] for g in own),default=1))+'只','首封'+r['first_limit_time']],'invalid':invalid,'expected':expected,'classification':role,'counterevidence':['缺少全题材非涨停样本与因果验证']})
    roles.sort(key=lambda r:(-int(r['role']=='市场高度锚'),-int(r['role']=='题材高度核心'),-r['streak'],-r['breadth'],r['first_limit_time'],r['symbol']))
    return {'version':VERSION,'date':snapshot['date'],'prior_date':prior['date'] if prior else None,'groups':groups,'roles':roles,'rule_status':'UNVALIDATED_ROLE_RULES','note':'角色优先级不是收益预测，题材统计仅限本涨停池'}

def apply_plan(plan,study):
    plan['version']=VERSION
    plan['candidates']=[{k:r[k] for k in ('symbol','name','support','counterevidence','classification','expected','invalid')}|{'buy_signal':False} for r in study['roles'][:6]]
    return plan

def ledger(root,snapshot,observed,enrichment=None):
    """Every frozen candidate retained; no inferred fills or optimistic returns."""
    bars_by_symbol={r['name']:r['data'] for r in (enrichment or {}).get('datasets',{}).get('daily',[]) if r['status']=='ok'}
    records=[];today={r['symbol']:r for r in snapshot['rows']}
    for path in (root/'state/plans'/snapshot['date']).glob('*.json'):
        plan=json.loads(path.read_text());timely=dt.datetime.fromisoformat(plan['created_at'])<dt.datetime.fromisoformat(snapshot['date']+'T09:25:00+08:00')
        for c in plan['candidates']:
            bars=bars_by_symbol.get(c['symbol'],[])
            previous=[b for b in bars if b['date']<snapshot['date']]
            target=next((b for b in bars if b['date']==snapshot['date']),None)
            benchmark={'open_change_pct':(target['open']/previous[-1]['close']-1)*100,'close_change_pct':(target['close']/previous[-1]['close']-1)*100,'kind':'quote_benchmark_not_strategy_return'} if previous and target and snapshot.get('phase')=='close' else None
            records.append({'benchmark':benchmark,'plan':str(path.relative_to(root)),'symbol':c['symbol'],'name':c['name'],'rule_version':plan['version'],'role':c.get('classification','unknown'),'early_stable':any(x.startswith('首封') and x[2:]<'10:00:00' for x in c.get('support',[])) and '开板0次' in c.get('support',[]),'prospective':timely,'in_close_limit_pool':c['symbol'] in today,'trigger_status':'unknown_without_validated_intraday_rule','fill_status':'unknown','net_return':None,'outcome_status':'pool_membership_only'})
    measured=[r for r in records if r['prospective'] and r['benchmark']]
    summary={'measured_count':len(measured),'mean_open_change_pct':sum(r['benchmark']['open_change_pct'] for r in measured)/len(measured) if measured else None,'mean_close_change_pct':sum(r['benchmark']['close_change_pct'] for r in measured)/len(measured) if measured else None,'interpretation':'行情基准，不是交易收益或策略胜率'}
    return {'benchmark_summary':summary,'date':snapshot['date'],'observed_at':observed,'records':records,'sample_count':len(records),'eligible_prospective':sum(r['prospective'] for r in records),'automatic_rule_change':False,'note':'全部候选保留；不在涨停池不等于亏损，不将池内出现率称作交易胜率'}

def learning_summary(root,current):
    records={}
    for path in sorted((root/'state/learning').glob('*/*.json')):
        data=json.loads(path.read_text(encoding='utf-8'))
        for r in data.get('records',[]):records[(r['plan'],r['symbol'])]=r
    for r in current['records']:records[(r['plan'],r['symbol'])]=r
    groups=defaultdict(list)
    for r in records.values():
        if r['prospective']:
            groups[(r['rule_version'],'早封稳定' if r.get('early_stable') else '其他候选')].append(r)
    results=[]
    for (version,cohort),rows in sorted(groups.items()):
        measured=[r for r in rows if r.get('benchmark')]
        dates={r['plan'].split('/')[-2] for r in measured}
        results.append({'version':version,'cohort':cohort,'total_candidates':len(rows),'missing_benchmark':len(rows)-len(measured),'measured':len(measured),'distinct_dates':len(dates),'mean_close_change_pct':sum(r['benchmark']['close_change_pct'] for r in measured)/len(measured) if measured else None,'research_action':'人工复核并另开样本外实验' if len(measured)>=30 and len(dates)>=10 else '继续收集；样本不足不改规则'})
    return {'hypothesis':'H01 早封且稳定候选的次日行情持续性；非交易收益检验','groups':results,'automatic_rule_change':False,'minimum_review_samples':30,'minimum_review_dates':10,'note':'阈值只决定何时人工复核，不代表统计显著；没有成交收益核算'}


def esc(s):return str(s).replace('|','／').replace('\n',' ')
def render(study,enrichment,analysis,learning):
    ds=enrichment.get('datasets',{});lines=['## 市场环境、龙头角色与验证进度','','规则版本：'+study['version']+'；尚未校准收益。角色排序取代单一研究分，不能据榜首确认龙头。','','### 市场环境']
    index=ds.get('indices',{})
    for r in index.get('data',[]) if index.get('status')=='ok' else []:lines.append(f"- {r['name']}：{r['change_pct']:+.2f}%；报价：{r['quote_at']}")
    b=ds.get('breadth',{})
    if b.get('status')=='ok':
        d=b['data'];lines.append(f"- 广度：上涨{d['up']}、下跌{d['down']}、平盘{d['flat']}；有效样本{d['count']}；成交额{d['amount_yuan']/1e8:.1f}亿元。日期仅为请求日，源不含交易日期；休市日不可用于判断当日变化。")
    for name,label in [('indices','主要指数'),('breadth','全市场广度'),('broken','炸板池'),('down_limit','跌停池')]:
        r=ds.get(name,{});lines.append('- '+label+'：'+r.get('status','missing')+'；来源：'+r.get('source','无'))
    for key,label in [('broken','炸板'),('down_limit','跌停')]:
        r=ds.get(key,{})
        if r.get('status')=='ok':lines.append('- '+label+'池：'+str(r['data']['tc'])+'只；仅按该源池口径。')
    if b.get('status')=='ok':
        data=b['data'];ratio=data['up']/data['count'] if data['count'] else None
        if ratio is not None:lines.append('- 广度观察：'+('多数股票下跌，先观察修复与亏钱效应' if ratio<=0.25 else '多数股票上涨，检查题材持续扩散' if ratio>=0.65 else '涨跌分化，需比较核心与后排')+'。这是未校准描述规则，不是开仓信号。')
    lines+=['','### 题材扩散（仅本涨停池）','','|题材|池内数量|前收盘数量|高度|','|---|---:|---:|---:|']
    for g in study['groups'][:12]:lines.append(f"|{esc(g['theme'])}|{g['count']}|{g['prior_count'] if g['prior_count'] is not None else '缺失'}|{g['height']}|")
    lines+=['','### 角色观察池','','|标的|定位|板数|同题材数量|20日涨幅|成交约束|','|---|---|---:|---:|---:|---|']
    for r in study['roles'][:12]:lines.append(f"|{esc(r['name'])} {r['symbol']}|{r['role']}|{r['streak']}|{r['breadth']}|{round(r['return20_pct'],2) if r['return20_pct'] is not None else '缺失'}|{r['liquidity_warning']}|")
    lines+=['','### 候选分支与失效条件']
    for r in study['roles'][:6]:lines.append('- '+r['name']+' '+r['symbol']+'（'+r['role']+'）：'+r['expected']+'；失效：'+r['invalid']+'；已知开板'+str(r['break_count'])+'次。')
    lines+=['','### 作者观点与分歧','', '分析状态：'+analysis['status']+'。']
    for c in analysis.get('claims',[]):lines.append('- '+esc(c['author'])+'：'+esc(c['view'])+'；对象：'+esc(c['subject'])+'；条件：'+esc(c['condition'])+'；失效：'+esc(c['invalid'])+'；[出处]('+c['url']+')')
    if not analysis.get('claims'):lines.append('未生成有正文依据的观点，不以标题猜测立场。')
    lines+=['','### 分钟、公告与学习核对']
    for name,label in [('daily','历史日线'),('minutes','当日分钟'),('notices','候选公告')]:
        rows=ds.get(name,[]);lines.append(f"- {label}：成功{sum(x['status']=='ok' for x in rows)}/{len(rows)}；原始结果及失败原因见同次state/research记录。")
    for r in ds.get('notices',[]):
        if r['status']=='ok':
            for n in r['data'][:2]:lines.append(f"- {r['name']} {n['date']} [{esc(n['title'])}]({n['url']})")
    lines+=['- 有日线价格基准的真实盘前样本：'+str(learning.get('benchmark_summary',{}).get('measured_count',0))+'；只比较开收盘相对前收盘变化，不代表策略成交收益。']
    lines+=['- 已导入当日券商L2快照批次：'+str(len(enrichment.get('l2_imports',[])))+'；仅快照封单留存，非撤单率或成交保证。']
    lines+=['','### 跨日假设台账','']
    for g in learning.get('iteration',{}).get('groups',[]):lines.append('- '+g['version']+' / '+g['cohort']+'：候选'+str(g['total_candidates'])+'，价格基准可用'+str(g['measured'])+'，缺失'+str(g['missing_benchmark'])+'，覆盖'+str(g['distinct_dates'])+'日；'+g['research_action'])
    if not learning.get('iteration',{}).get('groups'):lines.append('尚无可用的真实盘前样本；不由历史回填制造预测记录。')
    lines+=['- 当日冻结候选核对：'+str(learning['sample_count'])+'条，真正盘前记录'+str(learning['eligible_prospective'])+'条。','- 当前核对范围为收盘池出现情况；分钟条件与真实成交尚未验证，不公布胜率或虚构盈亏。','- 券商快照由独立导入提供；无导入时L2缺失。真实撤单和排队成交仍待队列数据。','- 每次保存规则版本与全部候选结果；规则改动作为新实验，禁止回写旧预案。']
    return '\n'.join(lines)+'\n'
