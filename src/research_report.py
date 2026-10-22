"""Evidence-first deterministic research report; heuristics are explicitly unvalidated."""
from collections import Counter,defaultdict
import datetime as dt
import hashlib
import json
from pathlib import Path


def esc(text):return str(text).replace('|','／').replace('\n',' ')


def rank(snapshot):
    from leader_study import build
    study=build(snapshot,[],{})
    result=[]
    for row in study['baseline_ranking']:
        r=dict(row);r['early_low_turnover']=r['first_limit_time']=='09:25:00' and r['turnover_pct']<3
        result.append(r)
    return result


def condition_plan(snapshot,target,created_at):
    pool=rank(snapshot)
    return {'version':'leader-backend-v0.2','market_date':snapshot['date'],'target_date':target,'created_at':created_at,'data_scope':snapshot['scope'],'source_updated_at':snapshot['source_updated_at'],'status':'RESEARCH_ONLY','rule_status':'UNVALIDATED_HEURISTICS','source_snapshot_hash':hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest(),'candidates':[{'symbol':r['symbol'],'name':r['name'],'support':r['support'],'counterevidence':r['counterevidence'],'classification':r['classification'],'expected':'自身保持相对强势，相关题材出现跟随而非独立冲高','invalid':'核心走弱且同题材承接失败；数据不可用','buy_signal':False} for r in pool[:6]],'hypotheses':[{'id':'H01','claim':'早封且封板稳定的标的能否获得次日持续性','status':'pending','evaluation':'次日收盘与开盘表现，分组记录失败样本，不假设涨停可成交'},{'id':'H02','claim':'强度锚与板块扩散是否共同出现','status':'pending','evaluation':'缺分钟数据，当前只能对照题材标签与存续数量，不能宣称因果带动'}],'calendar_status':'weekday_estimate_not_exchange_calendar','auto_orders':False}


def render(snapshot,history,source_batch,plan,phase):
    rows=snapshot['rows'];pool=rank(snapshot); heights=Counter(r['streak'] for r in rows)
    same=[x for x in history if x['date']==snapshot['date'] and x.get('phase')=='midday' and x.get('source_updated_at','')<snapshot['source_updated_at']]
    midday=max(same,key=lambda x:x['source_updated_at']) if same else None
    earlier=[x for x in history if x['date']<snapshot['date'] and x.get('phase')=='close']
    previous=max(earlier,key=lambda x:x['source_updated_at']) if earlier else None
    lines=[f"# {snapshot['date']} 龙头研究 · {phase}",'',f"采集：{snapshot.get('observed_at')}；数据源更新：{snapshot['source_updated_at']}",'','**状态：真实公开涨停池已采集；报告为待验证研究稿，尚未通过完整交付验收。**','','## 一、数据覆盖与缺口','',f"本次涨停池 {len(rows)} 只。未取得全市场涨跌分布、指数、炸板池及完整分钟序列，不能单独以此判定市场情绪。",'封单比与封单留存率不同；09:25早封不能证明全天一字、也不能证明买不到。以下基线强度排序只用于观察，不是经验证的收益预测。','','## 二、快照比较','','|时点|涨停池数量|数据状态|','|---|---:|---|',f"|前一交易日收盘|{previous['count'] if previous else '缺失'}|{'已保存' if previous else '尚未积累同源快照'}|",f"|本日午间|{midday['count'] if midday else '缺失'}|{'已保存' if midday else '没有当时采集，不能拿收盘倒推'}|",f"|本次|{len(rows)}|本次公开数据|"]
    lines+=['','## 三、梯队及晋级','','|连续板数|当前数量|','|---|---:|']
    for n,c in sorted(heights.items(),reverse=True):lines.append(f'|{n}|{c}|')
    if previous:
        today={r['symbol']:r for r in rows};groups=defaultdict(list)
        for r in previous['rows']:groups[r['streak']].append(r)
        lines+=['','|昨日板数|昨日数量|今天连续晋级|晋级率|','|---|---:|---:|---:|']
        for n,items in sorted(groups.items()):
            promoted=sum(r['symbol'] in today and today[r['symbol']]['streak']==n+1 for r in items)
            lines.append(f'|{n}|{len(items)}|{promoted}|{promoted/len(items):.1%}|')
        lines.append('仅比较已保存同源池；无牌照/停牌等全市场状态过滤，需人工复核。')
    else:lines+=['','前日同源池缺失，暂不计算晋级率，也不将当前高板数等同于晋级成功率。']
    themes=defaultdict(list)
    for r in rows:
        for theme in r['theme_label'].split('+'):themes[theme].append(r)
    lines+=['','## 四、题材结构','','|来源标签|涨停样本数|最高板|样本|','|---|---:|---:|---|']
    for theme,items in sorted(themes.items(),key=lambda kv:(-len(kv[1]),kv[0]))[:12]:
        lines.append(f"|{esc(theme)}|{len(items)}|{max(r['streak'] for r in items)}|{esc('、'.join(r['name'] for r in items[:5]))}|")
    lines+=['','标签存在交叉及归类误差，不能把行数相加作为市场总数。没有题材全量成交占比与连续多日扩散证据，暂不宣布唯一主线。','','## 五、观察池与反证','','|标的|板数|首封|开板|封单比|换手|基线强度分/55|定位|','|---|---:|---|---:|---:|---:|---:|---|']
    for r in pool[:10]:lines.append(f"|{esc(r['name'])} {r['symbol']}|{r['streak']}|{r['first_limit_time']}|{r['break_count']}|{r['seal_ratio_pct']:.2f}%|{r['turnover_pct']:.2f}%|{r['research_score']}|{r['classification']}|")
    for c in plan['candidates']:
        lines+=['',f"### {c['name']} {c['symbol']}",'依据：'+'；'.join(c['support']),'反证/缺口：'+'；'.join(c['counterevidence']),'待验证：'+c['expected'],'失效：'+c['invalid']]
    excluded=[r for r in pool if r['break_count']>=3 or r['first_limit_time']>='14:00:00']
    lines+=['','## 六、暂缓优先的样本及原因']
    for r in excluded[:10]:lines.append(f"- {r['name']}："+'；'.join((['反复开板'+str(r['break_count'])+'次，需验证承接'] if r['break_count']>=3 else [])+(['首次封板较晚'] if r['first_limit_time']>='14:00:00' else [])))
    if not excluded:lines.append('没有触发本版晚封/反复开板标记的样本，不能解释为全池可买。')
    lines+=['','## 七、公开作者材料','','|作者|文章|发表时间|可读性|','|---|---|---|']
    for post in source_batch.get('posts',[]):lines.append(f"|{esc(post['author'])}|[{esc(post.get('title') or '待核实标题')}]({post['url']})|{post.get('published_at') or '未解析'}|{post['status']}|")
    lines+=['','只保存短公开片段。尚未完成模型语义分析，不根据标题或摘录自动给作者贴看多/看空标签。旧文不冒充当日观点。','','## 八、下一交易日条件预案','',f"目标日：{plan['target_date']}（当前仅按工作日估计，交易日历待核实）。",'- 共振：候选自身强势且题材持续扩散，再研究盘中确认；高开本身不等于买点。','- 抱团：只强少数高度股、后排持续失败，放弃将其解释为主线启动。','- 风险：多个高度锚失败、昨日强势组普遍无承接，保持观察。','- 除权除息、监管提示、停复牌及公司公告仍需盘前独立核验。','','## 九、学习与迭代待办']
    for h in plan['hypotheses']:lines.append(f"- {h['id']}：{h['claim']}。验证：{h['evaluation']}。状态：{h['status']}。")
    lines+=['','模型接口可选；当前未配置时仅有确定性研究稿。单日样本、研究分和自动测试都不是策略有效的证据。','','## 来源','',f"- 行情：{snapshot.get('source','https://www.jikefupan.com/')}",'- 框架起点：https://olenara.com/longtou-qimo50-20261010.html','- 运行记录：state/status.json；冻结计划：state/plans/；历史快照：state/snapshots/。']
    return '\n'.join(lines)+'\n'
