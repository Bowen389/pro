"""Twelve-module reports assembled from archived evidence, without filling data gaps."""
import datetime as dt
import html
import re
from collections import Counter
from scoring import DIMENSIONS

VERSION='decision-report-v2'

def esc(value):return str(value).replace('|','／').replace('\n',' ')
def table(headers,rows):
    return '\n'.join(['|'+'|'.join(headers)+'|','|'+'|'.join(['---']*len(headers))+'|']+['|'+'|'.join(esc(x) for x in r)+'|' for r in rows])
def value(x,suffix=''):return '缺失' if x is None else str(round(x,2) if isinstance(x,float) else x)+suffix

def risk_filter(snapshot,enrichment):
    """Known risks excluded. Completeness requires a dated security-status record.
    Notice titles alone cannot certify the absence of delisting risk.
    """
    statuses=enrichment.get('security_status_by_date',{}).get(snapshot['date'],enrichment.get('security_status',{}))
    notices={r['name']:r['data'] for r in enrichment.get('datasets',{}).get('notices',[]) if r.get('status')=='ok'}
    kept=[];excluded=[];unknown=[]
    for row in snapshot['rows']:
        name=row['name'];code=row['symbol'];reason=None
        if re.search(r'ST|退',name,re.I):reason='名称含ST或退市标记'
        entry=statuses.get(code,{})
        verified=entry.get('date')==snapshot['date'] and entry.get('source','').startswith(('https://','http://')) and isinstance(entry.get('is_st'),bool) and isinstance(entry.get('delisting_risk'),bool)
        if verified and (entry['is_st'] or entry['delisting_risk']):reason='已核验证券状态为ST或退市风险'
        for n in notices.get(code,[]):
            if n['date']>snapshot['date']:continue
            title=n['title']
            if re.search(r'终止上市|退市整理|可能.*退市|退市风险警示',title) and not re.search(r'撤销|解除|不触及|不存在',title):reason='公告风险线索：'+title
        if reason:excluded.append({'symbol':code,'name':name,'reason':reason});continue
        kept.append(row)
        if not verified:unknown.append(code)
    return {'rows':kept,'excluded':excluded,'unverified_symbols':unknown,'complete':not unknown,'scope':'仅本源涨停样本；全市场覆盖仍需单独验证','note':'未检出风险不等于证明无风险；状态缺失时不发布严格剔除口径统计'}

def metrics(snapshot,history,enrichment):
    """Same-scope disjoint pools and whole prior cohorts; no survivor-only premiums."""
    audit=risk_filter(snapshot,enrichment);ds=enrichment.get('datasets',{})
    out=dict.fromkeys(('up_limit','down_limit','broken_rate','seal_rate','first_promotion','second_promotion','chain_promotion','limit_premium','chain_premium','big_loss'))
    if audit['complete']:out['up_limit']=len(audit['rows'])
    def checked_pool(key):
        data=ds.get(key,{})
        if data.get('status')!='ok':return None
        raw=data.get('data',{}).get('pool')
        if not isinstance(raw,list) or data['data'].get('tc')!=len(raw):return None
        rows=[dict(symbol=str(r.get('c',r.get('symbol',''))),name=r.get('n',r.get('name',''))) for r in raw]
        if any(not r['symbol'] or not r['name'] for r in rows):return None
        result=risk_filter(dict(snapshot,rows=rows),enrichment)
        return result['rows'] if result['complete'] else None
    broken=checked_pool('broken');down=checked_pool('down_limit')
    if down is not None:out['down_limit']=len(down)
    if audit['complete'] and broken is not None:
        sealed={r['symbol'] for r in audit['rows']};opened={r['symbol'] for r in broken}
        if len(opened)==len(broken) and not sealed & opened and sealed|opened:
            out['broken_rate']=100*len(opened)/len(sealed|opened);out['seal_rate']=100*len(sealed)/len(sealed|opened)
    prior=[s for s in history if s.get('phase')=='close' and s['date']<snapshot['date']]
    if not prior:return out
    previous=max(prior,key=lambda s:s['source_updated_at']);old=risk_filter(previous,enrichment)
    if not old['complete'] or not audit['complete']:return out
    today={r['symbol']:r for r in audit['rows']}
    for key,predicate in [('first_promotion',lambda r:r['streak']==1),('second_promotion',lambda r:r['streak']==2),('chain_promotion',lambda r:r['streak']>=2)]:
        cohort=[r for r in old['rows'] if predicate(r)]
        if cohort:out[key]=100*sum(r['symbol'] in today and today[r['symbol']]['streak']==r['streak']+1 for r in cohort)/len(cohort)
    bars={r['name']:{b['date']:b for b in r['data']} for r in ds.get('daily',[]) if r.get('status')=='ok'}
    changes={}
    for row in old['rows']:
        data=bars.get(row['symbol'],{});a=data.get(previous['date']);b=data.get(snapshot['date'])
        if a and b and a['close']>0:changes[row['symbol']]=100*(b['close']/a['close']-1)
    if old['rows'] and len(changes)==len(old['rows']):
        out['limit_premium']=sum(changes.values())/len(changes);out['big_loss']=sum(v<=-7 for v in changes.values())
    chained=[r for r in old['rows'] if r['streak']>=2]
    if chained and all(r['symbol'] in changes for r in chained):out['chain_premium']=sum(changes[r['symbol']] for r in chained)/len(chained)
    return out

def trend_pool(snapshot,enrichment):
    """Exact 15/30 trading-session returns from dated adjusted closes, never weekly substitutes."""
    rows=[];pending=[];removed=[]
    history={r['name']:r['data'] for r in enrichment.get('datasets',{}).get('daily',[]) if r.get('status')=='ok'}
    names={r['symbol']:r for r in snapshot['rows']}
    for code,bars in history.items():
        if code not in names:continue
        bars=sorted([b for b in bars if b['date']<=snapshot['date']],key=lambda b:b['date'])
        if not bars or bars[-1]['date']!=snapshot['date'] or len({b['date'] for b in bars})!=len(bars):pending.append({'symbol':code,'reason':'缺失：目标日K线或日期唯一性核验失败'});continue
        last=bars[-1];ret15=(last['close']/bars[-16]['close']-1)*100 if len(bars)>=16 and bars[-16]['close']>0 else None
        ret30=(last['close']/bars[-31]['close']-1)*100 if len(bars)>=31 and bars[-31]['close']>0 else None
        # Consecutive historical limit-ups require a dated limit-state series, not percentage rounding.
        series=enrichment.get('limit_history',{}).get(code,[])
        maxrun=run=0;bydate={r['date']:r['limit_up_close'] for r in series if isinstance(r.get('limit_up_close'),bool)}
        if len(bars)>=31:
            for bar in bars[-30:]:
                run=run+1 if bydate.get(bar['date']) else 0;maxrun=max(run,maxrun)
        qualifies=(ret15 is not None and ret15>60) or (ret30 is not None and ret30>100 and maxrun>=3)
        if not qualifies:
            if ret15 is None or (ret30 is not None and ret30>100 and not all(b['date'] in bydate for b in bars[-30:])):pending.append({'symbol':code,'reason':'缺失：足够日线或30日历史连板状态'})
            continue
        if len(bars)<20:pending.append({'symbol':code,'reason':'缺失：20日均线'});continue
        ma={n:sum(b['close'] for b in bars[-n:])/n for n in (5,10,20)}
        state='主升' if last['close']>=ma[5]>=ma[10]>=ma[20] else '趋势破坏' if last['close']<ma[20] else '高位震荡'
        record={'symbol':code,'name':names[code]['name'],'return15_pct':ret15,'return30_pct':ret30,'start15':bars[-16]['date'] if len(bars)>=16 else None,'start30':bars[-31]['date'] if len(bars)>=31 else None,'end':last['date'],'reason':'15日>60%' if ret15 is not None and ret15>60 else '30日>100%且至少三连板','state':state,'ma':ma,'new_high20':last['close']>=max(b['close'] for b in bars[-20:]),'note':'仅对已采日线样本筛选；均线描述规则未校准，顶部/筹码稳定性缺失'}
        (removed if state=='趋势破坏' else rows).append(record)
    return {'rows':rows,'removed':removed,'pending':pending,'coverage':'候选日线样本，非全市场扫描；北交所/非涨停趋势股覆盖缺失'}

def card_table(rows):
    return table(['标的',*DIMENSIONS,'已评分小计/可评分上限','覆盖','总分/100','结论'],[[r['name']+' '+r['symbol'],*[value(r['score_components'][d]['value']) for d in DIMENSIONS],str(r['score_subtotal'])+'/'+str(r['score_observed_max']),str(r['score_coverage'])+'/10',value(r['research_score']),r.get('relay_verdict','观察')] for r in rows])

def render(snapshot,history,study,enrichment,analysis,learning,evaluations=()):
    ds=enrichment.get('datasets',{});risk=study['risk_audit'];rows=study['roles'];strict=risk['complete'];prior=[s for s in history if s.get('phase')=='close' and s['date']<snapshot['date']];previous=max(prior,key=lambda s:s['source_updated_at']) if prior else None
    heights=Counter(r['streak'] for r in rows);trend=trend_pool(dict(snapshot,rows=risk['rows']),enrichment)
    source=snapshot.get('source','https://www.jikefupan.com/')
    measured=metrics(snapshot,history,enrichment)
    def conclusion(s):return '\n\n**模块结论：'+s+'**\n'
    b=ds.get('breadth',{});indices=ds.get('indices',{});posture='防守（证据未齐，先核验）'
    lines=['# '+snapshot['date']+' 龙头研究：收盘判断与次日条件预案','','## 一、次日作战仪表盘','',f"A股行情基准：{snapshot['date']} 15:00收盘；源快照：{snapshot['source_updated_at']}；报告版本：{VERSION}。盘后信息另标发布时间，不回写盘中事实。",'', '情绪周期：缺失／待确认。当前数据不能完整验证周期阶段；不以单日涨停数强判高潮或退潮。','仓位倾向：'+posture+'；仅研究框架，无自动交易。','',table(['关键指标','严格剔除ST/退市风险口径','依据与缺口'],[
        ['涨停数',value(measured['up_limit']),source+'；'+risk['note']],['跌停数',value(measured['down_limit']),'同口径风险过滤后计数；缺状态则缺失'],['炸板率／封板率',value(measured['broken_rate'],'%')+'／'+value(measured['seal_rate'],'%'),'分母=同口径两池互斥并集；有重叠或筛查缺失则不计算'],['连板高度／最高板',str(max(heights,default=0))+'／'+'、'.join(r['name'] for r in rows if r['streak']==max(heights,default=0)) if strict else '缺失','连续板数，不使用N天M板替代'],['首板／二板／连板晋级率','／'.join(value(measured[k],'%') for k in ('first_promotion','second_promotion','chain_promotion')),'前后两日同口径已核验证券集合；连续板数+1定义晋级'],['昨日涨停／连板今日溢价',value(measured['limit_premium'],'%')+'／'+value(measured['chain_premium'],'%'),'昨日完整样本今日收盘相对前收盘；非仅存活者，非策略收益'],['大面股数量',value(measured['big_loss']),'完整昨日涨停样本今日收盘相对前收盘≤-7%']]),'', '三个优先研究方向（本源标签样本，未确认全市场主线）：'+'、'.join(g['theme'] for g in study['groups'][:3]),'三个核心观察锚（风险待核验者不可升级接力）：'+'、'.join(r['name'] for r in rows[:3]),'三个风险信号：高度锚与后排同时转弱；同口径炸板率上升；公告出现业务澄清、监管或退市风险。']
    lines.append(conclusion('下一交易日先核验题材扩散和核心承接，再决定原观察假设是否成立；缺少证据时不强判晋级。'))
    lines+=['## 二、今日数据口径与盘面总览','', '严格市场统计：'+('本源涨停池风险过滤已核验；全市场仍缺完整证券状态' if strict else '缺失：证券风险核验未覆盖全部样本。'), '名称或已发现风险剔除：'+str(len(risk['excluded']))+'只；风险状态待核验：'+str(len(risk['unverified_symbols']))+'只。',table(['数据','结果','说明'],[['全市场涨跌家数／成交额／较昨日变化','缺失','现有广度源未完成ST/退市风险剔除，不用于严格市场总数'],['机构行情还是接力行情','缺失／待确认','需机构/交易席位证据、题材成交占比和梯队持续性'],['赚钱与亏钱效应','仅本源标签变化可观察','完整板块收益和未涨停样本缺失'],['市场温度','待确认','不能将未经风险过滤的广度解释为严格市场统计']]),'数据源状态：'+ '；'.join(k+'='+ds.get(k,{}).get('status','missing') for k in ('indices','breadth','broken','down_limit'))]
    if indices.get('status')=='ok':lines.append(table(['指数','涨跌幅','报价时间'],[[r['name'],value(r['change_pct'],'%'),r['quote_at']] for r in indices['data']]))
    if risk['excluded']:lines.append(table(['剔除代码','名称','原因'],[[r['symbol'],r['name'],r['reason']] for r in risk['excluded']]))
    lines.append(conclusion('当前可用的是有来源的局部证据；严格剔除口径不完整时，市场总数不发布估算值。'))
    lines+=['## 三、新闻、外围与发酵方向','','<!-- EXTERNAL_CONTEXT -->','硬逻辑：需原始公告、财务或业务事实；消息刺激：报道/政策线索；纯情绪：缺乏业务兑现证据且主要依赖筹码。不得仅凭题材标签分类。','作者分析状态：'+analysis.get('status','missing')]
    for c in analysis.get('claims',[]):lines.append('- '+esc(c['author'])+'：'+esc(c['view'])+'；条件：'+esc(c['condition'])+'；失效：'+esc(c['invalid'])+'；[出处]('+c['url']+')')
    lines+=['### 公司公告证据（仅已采候选，非完整风险库）']
    for dataset in ds.get('notices',[]):
        if dataset.get('status')!='ok':continue
        for notice in dataset.get('data',[])[:3]:
            if notice['date']<=snapshot['date']:lines.append('- '+dataset['name']+' '+notice['date']+' ['+esc(notice['title'])+']('+notice['url']+')；标题线索，正文与业务影响待核验。')
    lines.append(conclusion('外围和新闻只形成待核验的方向调整，标题关联不等于业务兑现或买点。'))
    lines+=['## 四、资金与主线','',table(['题材样本','本次数量','前收盘数量','最高连续板','状态'],[[g['theme'],g['count'],value(g['prior_count']),g['height'],'仅源标签样本；非严格全市场统计'] for g in study['groups'][:12]]),'国家队、机构、北向、游资方向：缺失。ETF净流入不等于识别国家队身份；行情大单不等于机构成交。','主线/支线/中军/补涨/卡位：只提供候选角色，不将样本数量最多直接宣布为主线。']
    lines.append(conclusion('题材切换需要连续扩散、成交占比和相对强度确认，目前只记录观察线索。'))
    lines+=['## 五、宏观与流动性','',table(['项目','数据','超短线含义'],[['融资余额、宽基ETF份额及持仓身份','缺失','不能确认杠杆回流或国家队增减持'],['人民币、美债、日债、欧债','外围采补仅覆盖部分报价；其余缺失','需结合时间和风险偏好，不单独推导A股买点']])];lines.append(conclusion('宏观信息缺口不以叙事填充；只有新增且可验证的变化才调整风险条件。'))
    lines+=['## 六、涨停梯队与接力价值','','以下为已剔除名称/已知风险后的待核验样本，不是严格风险筛查完成的接力名单；当日未回封股票不进入本梯队。']
    for label,predicate in [('首板',lambda r:r['streak']==1),('二板',lambda r:r['streak']==2),('三板及以上',lambda r:r['streak']>=3)]:
        lines+=['### '+label,table(['代码/名称','题材','连续板','首封','开板','封单金额','封单比','换手','成交额','角色/风险'],[[r['symbol']+' '+r['name'],r['theme_label'],r['streak'],r['first_limit_time'],r['break_count'],'缺失',value(r['seal_ratio_pct'],'%'),value(r['turnover_pct'],'%'),'缺失',r['role']+'；'+r['risk_status']] for r in rows if predicate(r)])]
    lines+=['### 断板反包、近期涨停今未涨停、炸板未回封、主升未涨停','缺失：完整历史涨停状态、非涨停趋势池及同口径炸板名单未接齐。主板、创业板、科创板、北交所须分别核对；不使用涨停池替代全市场扫描。','逻辑硬度与晋级概率：未取得足够业务/验证证据者标缺失，不宣称高概率。'];lines.append(conclusion('先核对收盘封板状态和连续板数，再研究接力；封单比不能替代封单金额或留存率。'))
    lines+=['## 七、高涨幅连板龙头趋势池','',trend['coverage'],table(['标的','入选依据','15日区间/涨幅','30日区间/涨幅','趋势','MA5/10/20','创新高','接力结论'],[[r['name']+' '+r['symbol'],r['reason'],str(r['start15'])+'→'+r['end']+' / '+value(r['return15_pct'],'%'),str(r['start30'])+'→'+r['end']+' / '+value(r['return30_pct'],'%'),r['state'],' / '.join(value(r['ma'][n]) for n in (5,10,20)),r['new_high20'],'观察；风险、业务、人气待核验'] for r in trend['rows']])]
    for r in trend['removed']:lines.append('- '+r['name']+'：图形破坏，移出龙头趋势池；收盘低于MA20。')
    lines+=['日线缺失/历史连板待核验：'+str(len(trend['pending']))+'只；不以周涨幅或N天M板代替筛选条件。','趋势池十维评分与模块八共用同一评分卡，避免同标的出现不同分数。'];lines.append(conclusion('只接受精确交易日区间和当前趋势共同满足的样本；未满足或缺证据者不强行入池。'))
    lines+=['## 八、龙头、人气与情绪周期','',card_table(rows[:12]),'评分明细与逐项依据保存于同次state/research中的study.roles；研究分未校准收益。监管安全分越高表示风险越低。','同花顺、开盘啦、东方财富历史热榜、讨论变化、资金强度、主动带动证据：未采到则缺失。仅样本空间高度和标签地位可形成描述性代理分，不能据此给出完整100分。','近期>40%且曾连板的全市场筛选：缺失；模块七不满足精确条件的股票不能因人气高而混入。'];lines.append(conclusion('空间高度不等于龙头质量；证据覆盖不足时使用角色观察顺序，不按残缺总分排名。'))
    lines+=['## 九、盘中动态时间轴','',table(['时间点','资金攻击方向','核心个股','板块反应','情绪影响'],[[r['first_limit_time'],'缺失：仅首封事件',r['name']+' '+r['symbol'],'缺失：分钟级联动未验证','不能据首封先后证明主动带动'] for r in sorted(rows,key=lambda x:x['first_limit_time'])[:15]]),'首封事件来自收盘源回报，不是当时实时观察记录。公开分钟价格和累计量不能识别资金身份。'];lines.append(conclusion('时间先后与因果带动分开；未验证的资金攻击故事不写入预案依据。'))
    lines+=['## 十、市场切换与后续展望','',table(['题材','生命周期','依据','下一验证'],[[g['theme'],'待确认',str(g['count'])+'只源标签样本；此前'+value(g['prior_count']),'核验新增跟随、核心承接及成交占比'] for g in study['groups'][:8]]),'未来数周至一两个月方向：仅作为条件假设，缺少产业、资金和跨日证据时不宣布新主线成立。'];lines.append(conclusion('先验证题材能否跨日存续和扩散，再讨论从旧主线切换到新主线。'))
    lines+=['## 十一、美国AI泡沫简短分析','',table(['核验项','结果'],[[x,'缺失：需最新公司财报/原始统计，本任务不重复抓取背景材料'] for x in ('资本开支增速','自由现金流是否转负','债务与盈利增速','席勒估值历史比较','科技权重及市场广度','企业AI使用量','数据中心建设及算力利用率')])];lines.append(conclusion('现有外围行情不足以判定AI泡沫破裂；不把未经核验的财务数字当作A股风险事实。'))
    lines+=['## 十二、次日交易计划（研究框架）','',table(['优先级','标的/角色','风险核验','连续板','核心逻辑','确认条件','失效条件','结论'],[[str(i+1),r['name']+' '+r['symbol']+'／'+r['role'],r['risk_status'],r['streak'],'业务硬度缺失；当前仅源标签'+r['theme_label'],r['expected'],r['invalid'],r['relay_verdict']] for i,r in enumerate(rows[:6])]),'接力备选分类：首板/二板/三板见模块六；断板反包/未回封/全市场趋势池缺失，不强行补齐。','触发核验：竞价是否符合预期；开盘后是否保持相对强势；分时承接、封单金额及留存、换手与板块联动是否同步。未定义或未验证的阈值不填精确数字。','风险信号：高度锚断板且无人接替、后排失败扩散、监管或退市风险、公告否定业务预期。风险待核验者仅观察；已发现风险者回避。','跨日冻结预案核对：'+str(len(evaluations))+'份；可用真实盘前样本：'+str(learning.get('eligible_prospective',0))+'条；不等于成交或胜率。']
    for e in evaluations:lines.append('- '+e['plan_file']+'；'+e['timing'])
    lines.append(conclusion('下一交易日盯题材是否继续扩散、核心是否主动承接及风险公告；评分缺项、风险未核验时不升级为重点接力。'))
    lines+=['## 来源与覆盖','','- 涨停样本：'+source,'- 规则及证据：同次state/research、state/snapshots、state/sources、state/external；缺失不是零。','- 排除日志、逐项评分依据、研究版本均留档；未接入来源的模块显示缺失，不宣称完整交付。']
    return '\n\n'.join(lines)+'\n'

def to_html(markdown):
    """Safe standalone HTML, simple headings, paragraphs and tables; no raw scripts."""
    out=['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>龙头研究报告</title><style>body{font:16px/1.65 system-ui;max-width:1300px;margin:30px auto;padding:0 20px;color:#17243a}table{border-collapse:collapse;width:100%;font-size:14px;display:block;overflow:auto}td,th{border:1px solid #ccd3df;padding:8px;text-align:left}th{background:#edf2f9}h2{border-top:2px solid #bdcde2;padding-top:22px}p{overflow-wrap:anywhere}</style><body>'];in_table=False
    for line in markdown.splitlines():
        if line.startswith('|'):
            if not in_table:out.append('<table>');in_table=True
            cells=line.strip('|').split('|')
            if all(re.fullmatch(r'\s*:?-+:?\s*',c) for c in cells):continue
            out.append('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in cells)+'</tr>');continue
        if in_table:out.append('</table>');in_table=False
        if not line.strip():continue
        match=re.match(r'^(#{1,3}) (.*)',line)
        out.append('<h'+str(len(match[1]))+'>'+html.escape(match[2])+'</h'+str(len(match[1]))+'>' if match else '<p>'+html.escape(line)+'</p>')
    if in_table:out.append('</table>')
    return ''.join(out)+'</body></html>'
