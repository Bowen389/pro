"""Scheduled research pipeline. Writes only public state/report files to the repository."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from zoneinfo import ZoneInfo
from collector import collect
from quant import database
from public_sources import collect_sources
from research_report import condition_plan
from phase_reports import render_midday,render_close
from external_context import collect_external,overlay,render_external

TZ=ZoneInfo('Asia/Shanghai')


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(raw,encoding='utf-8');tmp.replace(path)


def load_snapshots(root):
    result=[]
    for path in (root/'state/snapshots').glob('*/*.json'):
        result.append(json.loads(path.read_text(encoding='utf-8')))
    return result


def next_weekday(day):
    day+=dt.timedelta(days=1)
    while day.weekday()>4:day+=dt.timedelta(days=1)
    return day.isoformat()


def evaluate(root,snapshot,observed):
    """Account for every candidate, including missing ones. This is not a P&L backtest."""
    results=[];today={x['symbol']:x for x in snapshot['rows']}
    for path in (root/'state/plans'/snapshot['date']).glob('*.json'):
        plan=json.loads(path.read_text(encoding='utf-8'))
        created=dt.datetime.fromisoformat(plan['created_at'])
        start=dt.datetime.fromisoformat(snapshot['date']+'T09:25:00+08:00')
        outcomes=[]
        for c in plan['candidates']:
            item=today.get(c['symbol'])
            outcomes.append({'symbol':c['symbol'],'name':c['name'],'in_today_limit_pool':bool(item),'streak':item['streak'] if item else None,'condition_result':'UNRESOLVED_NO_INTRADAY_SERIES','note':'收盘在池不等于预案触发或买入成交；不在池不等于亏损'})
        result={'plan_file':str(path.relative_to(root)),'target_date':snapshot['date'],'evaluated_at':observed,'timing':'prospective' if created<start else 'late_or_retrospective','outcomes':outcomes,'pnl':None,'interpretation':'只核对涨停池出现情况，缺失行情不推断收益、胜率或因果带动'}
        key=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        write_json(root/'state/evaluations'/snapshot['date']/(key+'.json'),result);results.append(result)
    return results


def publish_latest(root,status):
    manifest=root/'state/latest_by_phase.json'
    latest=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {}
    latest[status['phase']]={k:status.get(k) for k in ('run_key','report_path','market_date','phase','result','started_at')}
    write_json(manifest,latest)
    labels={'morning':'盘前修订预案','midday':'午间盘内观察','close':'收盘复盘','evening':'晚间外盘与事件'}
    lines=['# 各时段最新报告','','|时段|最新报告|','|---|---|']
    for phase in ('morning','midday','close','evening'):
        record=latest.get(phase)
        if record and record.get('report_path'):
            lines.append(f"|{labels[phase]}|[{record['run_key']}]({record['report_path'].removeprefix('reports/')})|")
        else:lines.append(f"|{labels[phase]}|尚未生成|")
    lines+=['','[最新运行状态](../state/status.json)','系统处于开发验证阶段。各阶段只生成对应产物，不重复发布同一份复盘。']
    (root/'reports').mkdir(exist_ok=True)
    (root/'reports/LATEST.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def external_run(root,phase,now,run_key,status,history,loader):
    day=now.date().isoformat();observed=now.isoformat()
    saved=[]
    for path in (root/'state/external').glob('*/*.json'):
        c=json.loads(path.read_text(encoding='utf-8'))
        if c.get('phase')=='evening' and dt.datetime.fromisoformat(c['observed_at'])<now:saved.append((path,c))
    evening_path,evening=max(saved,key=lambda p:p[1]['observed_at']) if saved else (None,None)
    if phase=='evening':
        start=now.replace(hour=15,minute=0,second=0,microsecond=0)
        if start>now:start=now-dt.timedelta(hours=12)
    else:start=dt.datetime.fromisoformat(evening['observed_at']) if evening else now-dt.timedelta(hours=18)
    # Quote source fetches record their own observation times; revisions are frozen afterward.
    context=loader(phase,observed,start.isoformat())
    context_path=root/'state/external'/day/(run_key+'.json');write_json(context_path,context)
    ok=sum(x.get('status')=='ok' and not x.get('stale') for x in context.get('markets',[]))
    feed_ok=sum(x.get('status')=='ok' for x in context.get('news_sources',[]))
    if not ok and not feed_ok:raise ValueError('All external market and news sources failed')
    revision=None;plan=None
    if phase=='morning':
        closes=[s for s in history if s.get('phase')=='close' and s['date']<day]
        if not closes:raise ValueError('No prior saved close snapshot; morning plan cannot be created')
        snapshot=max(closes,key=lambda s:s['source_updated_at'])
        if (now.date()-dt.date.fromisoformat(snapshot['date'])).days>4:raise ValueError('Prior close snapshot stale')
        created_at=dt.datetime.now(TZ).isoformat()
        plan=condition_plan(snapshot,day,created_at);plan['version']='leader-backend-v0.3'
        revision=overlay(plan,context,evening)
        plan['external_revision']=revision;plan['external_context_path']=str(context_path.relative_to(root))
        plan['evening_context_path']=str(evening_path.relative_to(root)) if evening_path else None
        plan_path=root/'state/plans'/day/(run_key+'.json');write_json(plan_path,plan)
        status.update({'plan_path':str(plan_path.relative_to(root)),'market_date':snapshot['date'],'market_count':snapshot['count']})
        if now.time()>=dt.time(9,25):status['warnings'].append('本次盘前任务迟到，预案按实际生成时间留档')
    text=render_external(context,phase,plan=plan,revision=revision,evening=evening if phase=='morning' else None)
    path=root/'reports'/day/(run_key+'.md');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    partial=ok<len(context.get('markets',[])) or feed_ok<len(context.get('news_sources',[]))
    status.update({'external_market_count':ok,'news_source_count':feed_ok,'news_event_count':len(context.get('events',[])),'market_status':'external_quotes_only' if phase=='evening' else 'prior_close_plus_external','source_status':'partial' if partial else 'collected_external','report_status':'evening_external_brief' if phase=='evening' else 'morning_revised_plan','report_path':str(path.relative_to(root)),'external_context_path':str(context_path.relative_to(root)),'result':'partial' if partial else 'success','product':phase})
    status['warnings']+=['外盘可能延迟，按报价时间及当地交易状态解读','新闻源有限，标题事件与A股映射仍待核验']
    publish_latest(root,status)
    return status


def perform(root,phase,now=None,source_loader=collect_sources,market_loader=None,external_loader=collect_external):
    now=now or dt.datetime.now(TZ);observed=now.isoformat();day=now.date().isoformat()
    phase=phase if phase!='auto' else 'morning' if now.hour<10 else 'midday' if now.hour<13 else 'evening' if now.hour>=18 else 'close'
    run_key=now.strftime('%Y%m%dT%H%M%S')+'_'+phase+'_'+os.environ.get('GITHUB_RUN_ID','local')+'_'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    status={'run_key':run_key,'phase':phase,'started_at':observed,'run_url':f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','Bowen389/pro')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}" ,'system_status':'DEVELOPMENT_VALIDATION','market_status':'pending','source_status':'pending','report_status':'pending','warnings':[],'exit_code':0}
    try:
        if now.weekday()>4:
            status.update({'market_status':'skipped_weekend','source_status':'skipped','report_status':'skipped','result':'skipped'})
            return status
        history=load_snapshots(root)
        if phase in ('evening','morning'):
            return external_run(root,phase,now,run_key,status,history,external_loader)
        if phase=='midday' and not dt.time(11,30)<=now.time()<dt.time(12,30):raise ValueError('Missed midday capture window; will not label close data as midday')
        if phase=='close' and now.time()<dt.time(15,10):raise ValueError('Close task before final data window')
        if market_loader is None:
            db=database(root/'runtime.sqlite')
            try:
                for attempt in range(3):
                    try:
                        _,snapshot=collect(db,day,phase);break
                    except Exception:
                        if attempt==2:raise
                        time.sleep(3)
            finally:db.close()
        else:snapshot=market_loader(day,phase,observed)
        if snapshot['date']!=day:raise ValueError('Market date mismatch')
        updated=dt.datetime.fromisoformat(snapshot['source_updated_at'])
        if phase=='close' and updated.time()<dt.time(15):raise ValueError('Source is not a close snapshot')
        if phase=='midday' and updated.time()>=dt.time(12,30):raise ValueError('Source is not a midday snapshot')
        key=hashlib.sha256(json.dumps(snapshot,sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        write_json(root/'state/snapshots'/day/(phase+'_'+key+'.json'),snapshot)
        status['market_status']='collected_public_limit_pool'
        target=next_weekday(now.date())
        status['market_date']=snapshot['date'];status['market_count']=snapshot['count']
        source_config=json.loads((root/'config/sources.json').read_text(encoding='utf-8'))
        try:batch=source_loader(source_config,observed)
        except Exception as exc:
            batch={'collected_at':observed,'posts':[],'catalogues':[],'error':type(exc).__name__}
        status['source_posts']=len(batch.get('posts',[]))
        status['source_readable']=sum(x.get('status') in ('public_excerpt','partial_login_required') for x in batch.get('posts',[]))
        failures=sum(x.get('status') in ('fetch_failed','unparsed') for x in batch.get('posts',[]))+sum(x.get('status')=='failed' for x in batch.get('catalogues',[]))
        status['source_status']='partial' if failures or not status['source_readable'] else 'collected_public_excerpts'
        write_json(root/'state/sources'/day/(run_key+'.json'),batch)
        plan=condition_plan(snapshot,target,dt.datetime.now(TZ).isoformat())
        evaluations=evaluate(root,snapshot,observed) if phase=='close' else []
        if phase=='midday':
            plan['status']='INTRADAY_DRAFT_NOT_FROZEN_PREMARKET'
            write_json(root/'state/intraday_drafts'/day/(run_key+'.json'),plan)
            report=render_midday(snapshot,history)
        else:
            plan['status']='CLOSE_OBSERVATION_POOL_NOT_PREMARKET_PLAN'
            write_json(root/'state/close_observations'/day/(run_key+'.json'),plan)
            report=render_close(snapshot,history,batch,plan,evaluations)
        report_path=root/'reports'/day/(run_key+'.md');report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(report,encoding='utf-8')
        status.update({'report_status':'generated_research_draft','report_path':str(report_path.relative_to(root)),'plan_path':None,'plans_checked':len(evaluations),'result':'partial' if status['source_status']=='partial' else 'success'})
        publish_latest(root,status)
        status['warnings']+=['仅有涨停池；全市场指数、炸板池、历史分钟和L2未接通','自动研究分未验证；模型未配置时不进行语义观点提炼','工作日为近似交易日历；节假日数据可能陈旧并报错']
    except Exception as exc:
        status.update({'result':'failed','exit_code':1,'error_type':type(exc).__name__,'error':str(exc)[:400],'report_status':'not_generated'})
    finally:
        status['finished_at']=dt.datetime.now(TZ).isoformat()
        write_json(root/'state/status.json',status)
        write_json(root/'state/runs'/day/(run_key+'.json'),status)
    return status


def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['auto','morning','midday','close','evening'],default='auto');a=p.parse_args()
    root=Path(__file__).resolve().parents[1];status=perform(root,a.phase)
    print(json.dumps(status,ensure_ascii=False,indent=2))
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:
            f.write('# 龙头研究后台运行\n\n```json\n'+json.dumps(status,ensure_ascii=False,indent=2)+'\n```\n')
            if status.get('report_path'):f.write('\n'+(root/status['report_path']).read_text(encoding='utf-8'))
    sys.exit(status['exit_code'])


if __name__=='__main__':main()
