"""Offline re-render from saved snapshots; never creates or edits frozen plans."""
import argparse
import json
from pathlib import Path
from leader_study import build
from report_v2 import render,to_html,VERSION
from external_context import render_external

def replay(root,day):
    candidates=[]
    for path in (root/'state/snapshots'/day).glob('close_*.json'):
        snapshot=json.loads(path.read_text())
        if snapshot.get('phase')=='close':candidates.append(snapshot)
    if not candidates:raise ValueError('缺失：没有已保存的目标日收盘快照')
    snapshot=max(candidates,key=lambda x:x['source_updated_at'])
    records=[]
    for path in (root/'state/research'/day).glob('*_close_*.json'):
        data=json.loads(path.read_text());records.append((path,data))
    if not records:raise ValueError('缺失：没有已保存的目标日收盘研究证据')
    path,data=max(records,key=lambda x:x[1]['observed_at'])
    history=[]
    for p in (root/'state/snapshots').glob('*/*.json'):
        s=json.loads(p.read_text())
        if s['date']<=day and s.get('source_updated_at','')<=snapshot['source_updated_at']:history.append(s)
    learning_path=root/'state/learning'/day/path.name
    learning=json.loads(learning_path.read_text()) if learning_path.exists() else {}
    study=build(snapshot,history,data['enrichment'])
    text=render(snapshot,history,study,data['enrichment'],data.get('author_analysis',{'status':'missing'}),learning)
    contexts=[]
    for p in (root/'state/external'/day).glob('*_evening_*.json'):
        c=json.loads(p.read_text())
        if c.get('phase')=='evening':contexts.append(c)
    external=render_external(max(contexts,key=lambda c:c['observed_at']),'evening') if contexts else '缺失：本地没有对应晚间外盘归档。'
    text='# 归档证据重编版（非当时发布的预案）\n\n证据截止：'+data['observed_at']+'；规则：'+VERSION+'；不补写历史预测，不修改冻结预案。\n\n'+text.replace('<!-- EXTERNAL_CONTEXT -->',external)
    target=root/'reports'/day/(day+'_research_v2.md');target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text,encoding='utf-8')
    target.with_suffix('.html').write_text(to_html(text),encoding='utf-8')
    audit=root/'state/report_replays'/day/(VERSION+'.json');audit.parent.mkdir(parents=True,exist_ok=True)
    audit.write_text(json.dumps({'kind':'OFFLINE_RE_RENDER_NOT_PROSPECTIVE','report_version':VERSION,'evidence_cutoff':data['observed_at'],'source_research':str(path.relative_to(root)),'risk_audit':{k:v for k,v in study['risk_audit'].items() if k!='rows'},'card_count':len(study['roles']),'card_scope':'top12; all cards reproducible from source evidence','cards':[{k:r[k] for k in ('symbol','name','score_version','score_components','score_coverage','score_subtotal','research_score')} for r in study['roles'][:12]],'report':str(target.relative_to(root))},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return target

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--date',required=True);args=parser.parse_args()
    print(replay(Path(__file__).resolve().parents[1],args.date))
