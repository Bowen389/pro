"""Evidence summaries and research review checks; never promotes or trades automatically."""
import datetime as dt
import hashlib
import json
from pathlib import Path

VERSION='research-review-v1'

def summarize(study,enrichment,analysis):
    ds=enrichment.get('datasets',{});reasons=[];evidence=[]
    missing=[k for k in ('indices','breadth','broken','down_limit') if ds.get(k,{}).get('status')!='ok']
    if missing:reasons.append('环境数据不完整：'+','.join(missing))
    breadth=ds.get('breadth',{})
    ratio=None
    if breadth.get('status')=='ok':
        d=breadth['data'];n=d.get('count',0)
        if n:ratio=d.get('up',0)/n;evidence.append({'kind':'breadth','up_fraction':ratio,'source':breadth.get('source'),'note':'源无交易日期，需与当日指数及快照共同核验'})
    indices=ds.get('indices',{})
    weak=[]
    if indices.get('status')=='ok':
        weak=[r['name'] for r in indices['data'] if r['change_pct']<=-1]
        evidence.append({'kind':'indices','weak_names':weak,'source':indices.get('source')})
    stressed=ratio is not None and ratio<=0.25 and len(weak)>=2
    posture='data_gap' if missing else 'defensive_research' if stressed else 'conditional_research'
    if stressed:reasons.append('上涨占比≤25%且至少两个主要指数跌幅≥1%；优先研究风险与修复，不能仅凭个股强势提高预期')
    if analysis.get('status')!='analyzed':reasons.append('作者观点尚未完成有正文依据的分析；不能宣称游资共识')
    if not enrichment.get('l2_imports'):reasons.append('未取得当日券商L2快照；封单稳定性与成交可达性尚不能确认')
    candidates=[]
    for r in study.get('roles',[])[:6]:
        blockers=[]
        if r.get('break_count',0)>=3:blockers.append('已知反复开板'+str(r['break_count'])+'次，承接需核验')
        if r.get('first_limit_time')=='09:25:00' and r.get('turnover_pct',0)<3:blockers.append('早封低换手，不能假定能成交')
        if r.get('return20_pct') is not None and r['return20_pct']>=50:blockers.append('20日涨幅≥50%，延伸风险需单独核验；阈值尚未校准')
        blockers.append('尚未验证实际入场规则与成交条件')
        candidates.append({'symbol':r['symbol'],'name':r['name'],'role':r['role'],'unmet_conditions':blockers,'confirmation':r['expected'],'invalid':r['invalid'],'buy_signal':False})
    # Identical paraphrases across sources are a lead to inspect, never independent votes.
    grouped={}
    for c in analysis.get('claims',[]):
        key=''.join(c.get('subject','').split())+'|'+''.join(c.get('view','').split())
        grouped.setdefault(key,[]).append(c.get('url'))
    duplicates=[{'claim_key':k,'sources':sorted(set(v)),'independent_consensus':False} for k,v in grouped.items() if len(set(v))>1]
    return {'version':VERSION,'posture':posture,'evidence':evidence,'not_yet_satisfied':reasons,'candidates':candidates,'duplicate_claim_leads':duplicates,'auto_orders':False,'note':'描述性研究规则未校准；缺证据不等于无机会，也不自动给出买卖指令'}

REQUIRED=('hypothesis','parent_version','train_start','train_end','holdout_start','holdout_end','fees_bps','slippage_bps','fill_model','failed_trials','multiple_testing_control','excluded_samples','evidence_files')
def check_experiment(root,trial):
    missing=[k for k in REQUIRED if k not in trial];issues=[]
    if not missing:
        try:
            ts,te,hs,he=[dt.date.fromisoformat(trial[k]) for k in ('train_start','train_end','holdout_start','holdout_end')]
            if not ts<=te<hs<=he:issues.append('训练和样本外区间未严格分开')
            if any(not isinstance(trial[k],(int,float)) or not 0<=trial[k]<1000 for k in ('fees_bps','slippage_bps')):issues.append('成本口径无效')
            if not isinstance(trial['failed_trials'],list) or not isinstance(trial['excluded_samples'],list):issues.append('失败版本及排除样本必须列为数组')
            if not trial['fill_model'] or not trial['multiple_testing_control']:issues.append('成交或多重搜索口径缺失')
            files=trial['evidence_files']
            if not isinstance(files,list) or not files:issues.append('没有证据文件')
            else:
                for record in files:
                    path=(root/record['path']).resolve()
                    if not path.is_relative_to(root.resolve()) or not path.is_file():issues.append('证据路径不存在或越界');continue
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:issues.append('证据内容与登记hash不一致')
        except (ValueError,TypeError,KeyError):issues.append('实验登记格式错误')
    return {'version':trial.get('version','unknown'),'status':'BLOCKED' if missing or issues else 'READY_FOR_REVIEW','missing':missing,'issues':issues,'automatic_adoption':False,'interpretation':'仅检查研究材料齐备性，不证明统计显著、收益或可成交；不自动部署参数'}

def review_registry(root):
    results=[]
    for path in sorted((root/'state/experiments').glob('*.json')):
        try:results.append(check_experiment(root,json.loads(path.read_text(encoding='utf-8'))))
        except (ValueError,OSError):results.append({'version':path.stem,'status':'BLOCKED','missing':[],'issues':['登记文件无法读取'],'automatic_adoption':False})
    return results

def render(summary,reviews):
    labels={'data_gap':'环境数据有缺口，保留判断','defensive_research':'环境承压，优先核查风险与修复','conditional_research':'继续条件研究，尚无已验证交易指令'}
    lines=['## 综合研究结论与未满足条件','',labels[summary['posture']]+'。',summary['note'],'','### 当前不能直接升级为参与理由的事项']
    lines.extend('- '+x for x in summary['not_yet_satisfied'])
    lines+=['','|观察对象|定位|仍未满足|确认与失效|','|---|---|---|---|']
    def esc(s):return str(s).replace('|','／').replace('\n',' ')
    for c in summary['candidates']:lines.append('|'+esc(c['name']+' '+c['symbol'])+'|'+esc(c['role'])+'|'+esc('；'.join(c['unmet_conditions']))+'|'+esc(c['confirmation']+'；失效：'+c['invalid'])+'|')
    lines+=['','### 下一时段的三种分支','- 若核心保持强势且同题材跟随增加：继续核验扩散与自身承接，不把高开直接当买点。','- 若仅高度股维持、后排继续减少：按局部抱团观察，不将其推广为全市场转强。','- 若核心与后排共同走弱或关键数据缺失：原确认条件不成立，记录失败及缺口。']
    if reviews is not None:lines+=['','### 研究变更检查']
    if reviews is not None and not reviews:lines.append('尚无登记并附证据的完整策略实验；当前假设台账不等于完成回测或自主进化。')
    for r in reviews or []:lines.append('- '+r['version']+'：'+r['status']+'；缺少：'+','.join(r.get('missing',[]))+'；问题：'+'；'.join(r.get('issues',[])))
    if reviews is not None:lines+=['- 后续实验必须列明成本、成交模型、时间分割、失败版本、多重搜索处理和证据文件；材料齐备仍不能自动认定有效。','']
    return '\n'.join(lines)

def backtest_reference(root):
    """Expose latest frozen public experiment without treating it as an approved strategy."""
    latest=root/'state/backtests/LATEST.json'
    if not latest.exists():return ''
    try:
        pointer=json.loads(latest.read_text(encoding='utf-8'))
        path=(root/pointer['result']).resolve()
        if not path.is_relative_to(root.resolve()):raise ValueError('Invalid experiment path')
        result=json.loads(path.read_text(encoding='utf-8'))
        lines=['','### 最新公开日线回测（探索性）',f"核验信号日{len(result['verified_dates'])}个；历史信号{result['signal_count']}条；输入和协议已留档，不能据此认定策略有效。",'', '|固定方案|验证段已平仓|未平仓|已实现盈亏|含估值净值变化|','|---|---:|---:|---:|---:|']
        for row in result['results']:
            if row['phase']!='holdout':continue
            pnl=sum(t['net_pnl'] for t in row['trades'])
            lines.append(f"|{row['variant']}|{row['closed_trades']}|{row['unresolved_positions']}|{pnl:.2f}元|{row['total_return']:.2%}|")
        report=pointer['report'].removeprefix('reports/')
        lines+=['',f'[回测完整报告](../{report})','样本短、历史状态和成交仍有缺口；不据验证段结果选择赢家或自动改规则。','']
        return '\n'.join(lines)
    except (ValueError,KeyError,TypeError,OSError):return '\n### 公开回测记录缺口\n最新结果未能读取，不将其视为验证通过。\n'
