"""Original strength heuristic, retained as an unvalidated comparison baseline."""
import datetime as dt
import math

VERSION = 'legacy-strength-v1'

def score(row):
    result = {'score_version': VERSION, 'research_score': None, 'score_components': None,
              'score_status': 'missing_or_invalid', 'score_max': 55}
    try:
        time = row['first_limit_time']
        if dt.datetime.strptime(time, '%H:%M:%S').strftime('%H:%M:%S') != time:
            return result
        breaks, seal, streak = (float(row[k]) for k in ('break_count', 'seal_ratio_pct', 'streak'))
        if not all(math.isfinite(x) for x in (breaks, seal, streak)) or breaks < 0 or seal < 0 or streak < 1 or not breaks.is_integer() or not streak.is_integer():
            return result
    except (KeyError, ValueError, TypeError):
        return result
    components = {'early_seal': 20 if time < '10:00:00' else 10 if time < '11:30:00' else 0,
                  'seal_stability': max(0, 15-min(breaks, 15)),
                  'seal_ratio': min(seal, 5)*2,
                  'low_board_bonus': 10 if streak in (1, 2, 3) else 0}
    return result | {'research_score': round(sum(components.values()), 2),
                     'score_components': components, 'score_status': 'unvalidated_baseline'}

def ranked(rows):
    result = [dict(r) | score(r) for r in rows]
    result.sort(key=lambda r: (r['research_score'] is None, -(r['research_score'] or 0), r['symbol']))
    for i, row in enumerate(result):
        row['baseline_rank'] = i+1 if row['research_score'] is not None else None
    return result
