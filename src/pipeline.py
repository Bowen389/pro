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
from research_data import collect as collect_research,minute
from research_review import summarize,render as render_review
from author_analysis import analyze as analyze_authors
from leader_study import build as build_study,apply_plan,ledger,learning_summary,render as render_study
from phase_reports import render_midday
from external_context import collect_external,overlay,render_external
from report_v2 import render as render_decision,to_html

TZ=ZoneInfo('Asia/Shanghai')
MORNING_DEADLINE=dt.time(9,15)

class MorningDeadlineMissed(ValueError):
    pass

def check_morning_deadline(now):
    if now.astimezone(TZ).time()>=MORNING_DEADLINE:
        raise MorningDeadlineMissed('盘前交付已超过北京时间09:15截止；不生成有效盘前预案')


def write_json(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(data,ensure_ascii=False,indent=2)+'\n'
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(raw,encoding='utf-8');tmp.replace(path)


def load_snapshots(root):
    result=[]
    for path in (root/'state/snapshots').glob('*/*.json'):
        result.append(json.loads(path.read_text(encoding='utf-8')))
    return result


def attach_evidence(root,enrichment,day,observed):
    evidence_dir=root/'state/verified_evidence'
    for path in evidence_dir.glob('*.json'):
        record=json.loads(path.read_text(encoding='utf-8'))
        if record.get('date','')>day or dt.datetime.fromisoformat(record['observed_at'])>dt.datetime.fromisoformat(observed):continue
        enrichment.setdefault('security_status_by_date',{})[record['date']]=record.get('security_status',{})
        if record['date']==day:enrichment['score_evidence']=record.get('score_evidence',{})
    return enrichment


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
        start=dt.datetime.fromisoformat(snapshot['date']+'T09:15:00+08:00')
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
    latest[status['phase']]={k:status.get(k) for k in ('run_key','report_path','market_date','phase','result','started_at','delivery_status','deadline_at','skip_reason','warnings')}
    write_json(manifest,latest)
    labels={'morning':'盘前修订预案','midday':'午间盘内观察','close':'手动收盘记录','evening':'晚间综合复盘'}
    lines=['# 各时段最新报告','','更新时间：'+status['started_at']+'。旧报告不代表今日已交付。','','|时段|最新报告与状态|','|---|---|']
    for phase in ('morning','midday','evening'):
        record=latest.get(phase)
        if record and record.get('report_path'):
            label=labels[phase]+'（'+str(record.get('result'))+'；编制 '+str(record.get('started_at'))+'；行情 '+str(record.get('market_date') or '缺失')+'）'
            if phase=='morning':
                label+='（'+record['run_key'][:8]+'；'+('已迟到，非有效盘前预案' if record.get('delivery_status')=='late' else '核对报告日期与编制时点')+'）'
            lines.append(f"|{label}|[{record['run_key']}]({record['report_path'].removeprefix('reports/')})|")
        else:
            detail=(record.get('skip_reason') or record.get('result') or '尚未生成') if record else '尚未生成'
            lines.append(f"|{labels[phase]}|未交付：{detail}|")
    lines+=['','[最新运行状态](../state/status.json)','系统处于开发验证阶段。各阶段只生成对应产物，不重复发布同一份复盘。']
    (root/'reports').mkdir(exist_ok=True)
    (root/'reports/LATEST.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def external_run(root,phase,now,run_key,status,history,loader):
    day=now.date().isoformat();observed=now.isoformat()
    collection_started=time.monotonic()
    if phase=='morning':
        status['deadline_at']=day+'T09:15:00+08:00'
        check_morning_deadline(now)
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
        completed=now+dt.timedelta(seconds=time.monotonic()-collection_started)
        check_morning_deadline(completed)
        created_at=completed.isoformat()
        status['delivery_status']='on_time'
        plan=condition_plan(snapshot,day,created_at)
        studies=[]
        for p in (root/'state/research'/snapshot['date']).glob('*.json'):
            saved_study=json.loads(p.read_text(encoding='utf-8'))
            if saved_study.get('study') and dt.datetime.fromisoformat(saved_study['observed_at'])<=now:studies.append(saved_study)
        if studies:
            saved=max(studies,key=lambda x:x['observed_at'])
            apply_plan(plan,build_study(snapshot,history,attach_evidence(root,saved.get('enrichment',{}),snapshot['date'],observed)))
        else:apply_plan(plan,build_study(snapshot,history,{}))
        revision=overlay(plan,context,evening)
        plan['delivery_status']='on_time';plan['deadline_at']=status['deadline_at']
        plan['external_revision']=revision;plan['external_context_path']=str(context_path.relative_to(root))
        plan['evening_context_path']=str(evening_path.relative_to(root)) if evening_path else None
        plan_path=root/'state/plans'/day/(run_key+'.json');write_json(plan_path,plan)
        status.update({'plan_path':str(plan_path.relative_to(root)),'market_date':snapshot['date'],'market_count':snapshot['count']})
    text=render_external(context,phase,plan=plan,revision=revision,evening=evening if phase=='morning' else None)
    path=root/'reports'/day/(run_key+'.md');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding='utf-8')
    partial=ok<len(context.get('markets',[])) or feed_ok<len(context.get('news_sources',[]))
    status.update({'external_market_count':ok,'news_source_count':feed_ok,'news_event_count':len(context.get('events',[])),'market_status':'external_quotes_only' if phase=='evening' else 'prior_close_plus_external','source_status':'partial' if partial else 'collected_external','report_status':'evening_external_brief' if phase=='evening' else 'morning_revised_plan','report_path':str(path.relative_to(root)),'external_context_path':str(context_path.relative_to(root)),'result':'partial' if partial else 'success','product':phase})
    status['warnings']+=['外盘可能延迟，按报价时间及当地交易状态解读','新闻源有限，标题事件与A股映射仍待核验']
    publish_latest(root,status)
    return status


def evening_run(root,now,run_key,status,source_loader,market_loader,external_loader):
    # Reuse close collection/validation, then publish one combined evening product.
    target=now.date()
    if now.time()<dt.time(15,10):
        target-=dt.timedelta(days=1)
    while target.weekday()>4:target-=dt.timedelta(days=1)
    status['target_market_date']=target.isoformat()
    domestic=perform(root,'close',now,source_loader,market_loader,external_loader,target_day=target.isoformat())
    domestic_text=None
    if domestic.get('report_path'):
        path=root/domestic['report_path']
        domestic_text=path.read_text(encoding='utf-8')
        path.unlink()
    manifest=root/'state/latest_by_phase.json'
    if manifest.exists():
        latest=json.loads(manifest.read_text(encoding='utf-8'))
        latest.pop('close',None);write_json(manifest,latest)
    try:
        external_run(root,'evening',now,run_key,status,load_snapshots(root),external_loader)
        external_text=(root/status['report_path']).read_text(encoding='utf-8')
        external_ok=True
    except Exception as exc:
        external_ok=False
        external_text='## 外盘与事件补充失败\n\n'+type(exc).__name__+': '+str(exc)[:250]+'\n'
        status['warnings'].append('外盘采补失败；不能据此认定没有隔夜事件')
    if not domestic_text and not external_ok:raise ValueError('Both close review and external collection failed')
    if not domestic_text:
        domestic_text='## 收盘复盘缺失\n\n'+domestic.get('error','收盘数据未取得')+'\n'
    report_path=root/'reports'/now.date().isoformat()/(run_key+'.md')
    report_path.parent.mkdir(parents=True,exist_ok=True)
    combined=domestic_text.replace('<!-- EXTERNAL_CONTEXT -->',external_text) if '<!-- EXTERNAL_CONTEXT -->' in domestic_text else domestic_text+'\n---\n\n'+external_text
    report_path.write_text('# 晚间综合复盘：A股收盘、外盘与事件\n\n'+combined,encoding='utf-8')
    report_path.with_suffix('.html').write_text(to_html(report_path.read_text(encoding='utf-8')),encoding='utf-8')
    for key in ('market_date','market_count','source_posts','source_readable','plans_checked','research_datasets','author_analysis_status'):
        if key in domestic:status[key]=domestic[key]
    partial=domestic.get('result')!='success' or not external_ok or status.get('result')=='partial'
    status.update({'result':'partial' if partial else 'success','report_path':str(report_path.relative_to(root)),'report_status':'combined_evening_review','market_status':domestic['market_status'],'domestic_result':domestic['result'],'product':'evening'})
    status['warnings']+=domestic.get('warnings',[])
    if domestic.get('result')=='failed':
        status['warnings'].append('A股收盘复盘缺失：'+domestic.get('error','未知原因'))
    if partial:
        status['delivery_status']='incomplete'
        report_path.write_text('> ⚠ 部分完成：请检查收盘复盘与外盘数据缺失项。目标A股日期：'+target.isoformat()+'；实际采集：'+now.isoformat()+'。\n\n'+report_path.read_text(encoding='utf-8'),encoding='utf-8')
        report_path.with_suffix('.html').write_text(to_html(report_path.read_text(encoding='utf-8')),encoding='utf-8')
    publish_latest(root,status)
    return status


def track_run(root,now,run_key,status):
    day=now.date().isoformat();plans=[]
    for path in (root/'state/plans'/day).glob('*.json'):
        p=json.loads(path.read_text(encoding='utf-8'))
        created=dt.datetime.fromisoformat(p['created_at']).astimezone(TZ)
        if created<=now and created.date()==now.date() and created.time()<MORNING_DEADLINE and p.get('delivery_status')!='late':plans.append(p)
    if not plans:
        status.update({'result':'skipped','market_status':'no_frozen_plan','report_status':'no_report_data_capture_only'})
        return status
    plan=max(plans,key=lambda p:p['created_at'])
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        records=list(pool.map(lambda c:minute(c['symbol'],day,now.isoformat()),plan['candidates']))
    write_json(root/'state/minutes'/day/(run_key+'.json'),{'observed_at':now.isoformat(),'plan_created_at':plan['created_at'],'records':records})
    ok=sum(r['status']=='ok' for r in records)
    status.update({'result':'success' if ok==len(records) else 'partial','market_status':'minute_capture','market_count':ok,'report_status':'no_report_data_capture_only'})
    return status


def perform(root,phase,now=None,source_loader=collect_sources,market_loader=None,external_loader=collect_external,target_day=None):
    now=now or dt.datetime.now(TZ);observed=now.isoformat();day=now.date().isoformat()
    day=target_day or day
    phase=phase if phase!='auto' else 'morning' if now.hour<10 else 'midday' if now.hour<13 else 'evening' if now.hour>=18 else 'close'
    run_key=now.strftime('%Y%m%dT%H%M%S')+'_'+phase+'_'+os.environ.get('GITHUB_RUN_ID','local')+'_'+os.environ.get('GITHUB_RUN_ATTEMPT','1')
    status={'run_key':run_key,'phase':phase,'started_at':observed,'run_url':f"https://github.com/{os.environ.get('GITHUB_REPOSITORY','Bowen389/pro')}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}" ,'system_status':'DEVELOPMENT_VALIDATION','market_status':'pending','source_status':'pending','report_status':'pending','warnings':[],'exit_code':0}
    try:
        if dt.date.fromisoformat(day).weekday()>4 and phase!='evening':
            status.update({'market_status':'skipped_weekend','source_status':'skipped','report_status':'skipped','result':'skipped'})
            return status
        if phase=='track':
            return track_run(root,now,run_key,status)
        history=load_snapshots(root)
        if phase=='evening':
            return evening_run(root,now,run_key,status,source_loader,market_loader,external_loader)
        if phase=='morning':
            return external_run(root,phase,now,run_key,status,history,external_loader)
        if phase=='midday' and not dt.time(11,30)<=now.time()<dt.time(12,30):
            status.update({'result':'skipped','delivery_status':'missed_window','report_status':'not_generated','skip_reason':'午间任务已过期，不将其他时段数据标为午间'})
            publish_latest(root,status)
            return status
        if phase=='close' and day==now.date().isoformat() and now.time()<dt.time(15,10):raise ValueError('Close task before final data window')
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
        target=next_weekday(dt.date.fromisoformat(day))
        status['market_date']=snapshot['date'];status['market_count']=snapshot['count']
        if phase=='midday':
            enrichment=collect_research(snapshot,dt.datetime.now(TZ).isoformat(),environment_only=True)
            write_json(root/'state/research'/day/(run_key+'.json'),{'observed_at':observed,'enrichment':enrichment,'profile':'midday_core'})
            report=render_midday(snapshot,history,enrichment)
            report_path=root/'reports'/day/(run_key+'.md');report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(report,encoding='utf-8')
            datasets=enrichment.get('datasets',{})
            partial=any(datasets.get(k,{}).get('status')!='ok' for k in ('indices','breadth','broken','down_limit'))
            status.update({'result':'partial' if partial else 'success','report_status':'midday_core_observation','report_path':str(report_path.relative_to(root)),'source_status':'not_requested_midday','research_datasets':{k:v.get('status') for k,v in datasets.items()}})
            publish_latest(root,status)
            return status
        source_config=json.loads((root/'config/sources.json').read_text(encoding='utf-8'))
        try:batch=source_loader(source_config,observed)
        except Exception as exc:
            batch={'collected_at':observed,'posts':[],'catalogues':[],'error':type(exc).__name__}
        status['source_posts']=len(batch.get('posts',[]))
        status['source_readable']=sum(x.get('status') in ('public_excerpt','partial_login_required') for x in batch.get('posts',[]))
        failures=sum(x.get('status') in ('fetch_failed','unparsed') for x in batch.get('posts',[]))+sum(x.get('status')=='failed' for x in batch.get('catalogues',[]))
        status['source_status']='partial' if failures or not status['source_readable'] else 'collected_public_excerpts'
        analysis=analyze_authors(batch.get('posts',[]),observed)
        for post in batch.get('posts',[]):post.pop('_analysis_text',None)
        batch['analysis']=analysis
        write_json(root/'state/sources'/day/(run_key+'.json'),batch)
        frozen_symbols=[]
        for frozen in (root/'state/plans'/day).glob('*.json'):
            frozen_symbols.extend(c['symbol'] for c in json.loads(frozen.read_text(encoding='utf-8'))['candidates'])
        enrichment=collect_research(snapshot,dt.datetime.now(TZ).isoformat(),frozen_symbols)
        attach_evidence(root,enrichment,day,observed)
        l2_records=[]
        for l2 in (root/'state/l2'/day).glob('*.json'):
            data=json.loads(l2.read_text(encoding='utf-8'))
            if dt.datetime.fromisoformat(data['exported_at'])<=dt.datetime.now(TZ):l2_records.append({'path':str(l2.relative_to(root)),'summary':data.get('summary',[])})
        enrichment['l2_imports']=l2_records
        breadth_data=enrichment.get('datasets',{}).get('breadth',{}).get('data',{})
        universe=breadth_data.pop('rows',[])
        if universe:
            chunks=[]
            for offset in range(0,len(universe),500):
                chunk=root/'state/universe'/day/(run_key+'_'+str(offset//500)+'.json')
                write_json(chunk,{'observed_at':enrichment['observed_at'],'rows':universe[offset:offset+500]})
                chunks.append(str(chunk.relative_to(root)))
            breadth_data['row_files']=chunks
        study=build_study(snapshot,history,enrichment)
        learning=ledger(root,snapshot,observed,enrichment)
        learning['iteration']=learning_summary(root,learning)
        synthesis=summarize(study,enrichment,analysis)
        write_json(root/'state/research'/day/(run_key+'.json'),{'observed_at':observed,'enrichment':enrichment,'study':study,'author_analysis':analysis,'synthesis':synthesis})
        write_json(root/'state/learning'/day/(run_key+'.json'),learning)
        plan=apply_plan(condition_plan(snapshot,target,dt.datetime.now(TZ).isoformat(),study=study),study)
        evaluations=evaluate(root,snapshot,observed) if phase=='close' else []
        if phase=='validation':
            report='# 当前研究采样验证（非收盘复盘、非盘前预案）\n\n实际采集时间：'+observed+'；源更新时间：'+snapshot['source_updated_at']+'。\n\n此任务用于验证新增数据链路，不将盘中数据标为收盘终值。\n'
        else:
            plan['status']='CLOSE_OBSERVATION_POOL_NOT_PREMARKET_PLAN'
            write_json(root/'state/close_observations'/day/(run_key+'.json'),plan)
            report=render_decision(snapshot,history,study,enrichment,analysis,learning,evaluations)
        if phase=='validation':
            report+='\n'+render_review(synthesis,None)+'\n'+render_study(study,enrichment,analysis,learning)
        status['research_datasets']={k:(v.get('status') if isinstance(v,dict) else str(sum(x.get('status')=='ok' for x in v))+'/'+str(len(v))) for k,v in enrichment.get('datasets',{}).items()}
        research_partial=any(v.get('status')!='ok' if isinstance(v,dict) else any(x.get('status')!='ok' for x in v) for v in enrichment.get('datasets',{}).values())
        research_partial=research_partial or not study['risk_audit']['complete']
        status['report_version']='decision-report-v2'
        status['risk_status']='verified_sample' if study['risk_audit']['complete'] else 'incomplete'
        status['author_analysis_status']=analysis['status']
        if analysis['status']!='analyzed':status['warnings'].append('作者语义分析：'+analysis['status'])
        report_path=root/'reports'/day/(run_key+'.md');report_path.parent.mkdir(parents=True,exist_ok=True);report_path.write_text(report,encoding='utf-8')
        report_path.with_suffix('.html').write_text(to_html(report.replace('<!-- EXTERNAL_CONTEXT -->','外盘与新闻由晚间合并阶段补充；手动收盘任务尚未采集。')),encoding='utf-8')
        status.update({'report_status':'generated_research_draft','report_path':str(report_path.relative_to(root)),'plan_path':None,'plans_checked':len(evaluations),'result':'partial' if status['source_status']=='partial' or analysis['status']!='analyzed' or research_partial else 'success'})
        publish_latest(root,status)
        status['warnings']+=['研究数据覆盖以research_datasets及归档记录为准；L2尚未实接','角色排序未验证收益；模型未配置时不进行语义观点提炼','工作日为近似交易日历；节假日数据可能陈旧并报错']
    except Exception as exc:
        if isinstance(exc,MorningDeadlineMissed):
            status.update({'delivery_status':'missed_deadline','deadline_at':day+'T09:15:00+08:00','result':'skipped','exit_code':0,'skip_reason':str(exc),'report_status':'not_generated'})
            publish_latest(root,status)
            return status
        status.update({'result':'failed','exit_code':1,'error_type':type(exc).__name__,'error':str(exc)[:400],'report_status':'not_generated'})
    finally:
        status['finished_at']=dt.datetime.now(TZ).isoformat()
        if status.get('result')=='failed':
            publish_latest(root,status)
        write_json(root/'state/status.json',status)
        write_json(root/'state/runs'/now.date().isoformat()/(run_key+'.json'),status)
    return status


def main():
    p=argparse.ArgumentParser();p.add_argument('--phase',choices=['auto','morning','midday','close','evening','track','validation'],default='auto');a=p.parse_args()
    root=Path(__file__).resolve().parents[1];status=perform(root,a.phase)
    print(json.dumps(status,ensure_ascii=False,indent=2))
    if status.get('result') in ('skipped','partial'):
        message=status.get('skip_reason') or '报告部分完成，请检查缺失项：'+'；'.join(status.get('warnings',[]))
        print('::warning::'+message.replace('\n',' ').replace('\r',' '))
    output=os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output,'a',encoding='utf-8') as f:
            f.write('report_path='+str(status.get('report_path') or '')+'\n')
            f.write('run_record_path=state/runs/'+status['started_at'][:10]+'/'+status['run_key']+'.json\n')
    summary=os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary,'a',encoding='utf-8') as f:
            f.write('# 龙头研究后台运行\n\n```json\n'+json.dumps(status,ensure_ascii=False,indent=2)+'\n```\n')
            if status.get('report_path'):f.write('\n'+(root/status['report_path']).read_text(encoding='utf-8'))
    sys.exit(status['exit_code'])


if __name__=='__main__':main()

