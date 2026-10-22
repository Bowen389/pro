"""Different report products for different points in the research day."""
from collections import Counter
from research_report import render,rank


def render_midday(snapshot,history):
    prior=[s for s in history if s['date']<snapshot['date'] and s.get('phase')=='close']
    previous=max(prior,key=lambda s:s['source_updated_at']) if prior else None
    rows=snapshot['rows'];heights=Counter(r['streak'] for r in rows)
    lines=[f"# {snapshot['date']} 午间盘内观察",'',f"源时间：{snapshot['source_updated_at']}；采集时间：{snapshot['observed_at']}",'','本报告只记录上午盘内状态与下午待验证事项。午间数据不作为收盘终值。','',f"涨停池：{len(rows)}只；前一保存收盘：{previous['count'] if previous else '缺失'}只。",'梯队：'+'；'.join(f'{n}板{c}只' for n,c in sorted(heights.items(),reverse=True)),'','## 上午强度观察','','|标的|板数|首封|开板|下午待验证|','|---|---:|---|---:|---|']
    for r in rank(snapshot)[:8]:lines.append(f"|{r['name']} {r['symbol']}|{r['streak']}|{r['first_limit_time']}|{r['break_count']}|午后能否维持封板及同题材跟随|")
    lines+=['','## 下午重点核验','- 上午强核心是否仍有承接，午后新方向是否有新增扩散。','- 新增炸板、退潮风险需独立数据源确认，不能仅从现存涨停池推断。','- 收盘任务会对照本快照；缺少上午实时记录时不补写成当时已知判断。']
    return '\n'.join(lines)+'\n'


def render_close(snapshot,history,batch,observation_plan,evaluations):
    base=render(snapshot,history,batch,observation_plan,'收盘复盘')
    base=base.split('## 八、下一交易日条件预案',1)[0]
    lines=[base.rstrip(),'','## 八、今日预案核对']
    if not evaluations:lines.append('没有可对照的已保存当日预案；不追认今天的预测命中。')
    for e in evaluations:
        lines+=['',f"预案：{e['plan_file']}；时序：{e['timing']}。"]
        for r in e['outcomes']:lines.append(f"- {r['name']} {r['symbol']}：{'收盘在涨停池' if r['in_today_limit_pool'] else '未出现在涨停池'}；盘中条件是否触发仍待分钟数据核验。")
    lines+=['','## 九、交接给晚间与盘前','- 本收盘复盘在20:17生成，与外盘和新闻事件合并发布。','- 明日盘前依据隔夜信息形成正式条件预案，保存相对基础观察池的修订理由。','- 留待验证：早封稳定性、板块扩散与次日持续性的关系；保留所有失败样本。']
    return '\n'.join(lines)+'\n'
