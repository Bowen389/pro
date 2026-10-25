"""Evidence-bound ten-dimensional research cards; no legacy strength formula."""
import math

VERSION='leader-card-v2'
DIMENSIONS=('逻辑硬度','板块地位','人气热度','资金强度','筹码结构','主动性','带动性','空间高度','监管安全','接力性价比')

def score(row, evidence=None, asof=None):
    """Only dated, sourced research assessments are accepted; missing is never zero.
    Pool-relative height/position are transparent descriptive proxies, not causal claims.
    """
    evidence=evidence or {};items={}
    for name in DIMENSIONS:
        e=evidence.get(name,{})
        try:
            value=float(e['value'])
            valid=math.isfinite(value) and 0<=value<=10 and bool(e['basis']) and e['source'].startswith(('https://','http://')) and bool(e['observed_at'])
            from datetime import datetime
            stamp=datetime.fromisoformat(e['observed_at'])
            valid=valid and stamp.tzinfo is not None
            if asof:valid=valid and stamp<=datetime.fromisoformat(asof)
        except (ValueError,KeyError,TypeError,AttributeError):valid=False
        items[name]={'value':round(value,2),'basis':e['basis'],'source':e['source'],'observed_at':e['observed_at'],'kind':'sourced_research_assessment'} if valid else {'value':None,'basis':'缺失：需要该维度的独立证据','source':None}
    count=sum(e['value'] is not None for e in items.values());subtotal=round(sum(e['value'] or 0 for e in items.values()),2)
    return {'score_version':VERSION,'score_components':items,'score_coverage':count,'score_observed_max':count*10,'score_subtotal':subtotal,'research_score':subtotal if count==10 else None,'score_max':100,'score_status':'complete_unvalidated' if count==10 else 'partial_evidence','score_note':'各项0—10；监管安全越高风险越低；缺项不补零、不折算百分制；未经收益校准'}

def ranked(rows):
    # Incomplete cards cannot be ranked against complete cards by an artificial total.
    result=[dict(r)|score(r) for r in rows]
    return sorted(result,key=lambda r:(-int(r.get('role')=='市场高度锚'),-int(r.get('role')=='题材高度核心'),-r.get('streak',0),-r.get('breadth',0),r.get('first_limit_time','99:99:99'),r['symbol']))
