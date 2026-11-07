"""Read saved audit fixtures offline. Never opens a controller or edits a policy."""
import json,argparse
from pathlib import Path
from . import nightmare as N

def replay(fixture,budget_ms=1500):
    data=json.loads(Path(fixture).read_text(encoding='utf-8'));state=data['state'];finding=data.get('finding',{})
    current=N.choose_action(state,{'time_budget_ms':budget_ms,'max_nodes':8000,'beam_width':64})
    alternative=finding.get('alternative_line')
    result={'fixture':str(fixture),'source_status':finding.get('status','synthetic'),'current_plan':current,
        'alternative_first_action_selected':bool(alternative and current['action']==alternative[0]),
        'automatic_policy_change':False,'live_match':False}
    if finding.get('type')=='suspected_missed_lethal':result['counterexample_resolved']=current['certain_lethal']
    elif finding.get('type')=='modeled_lethal_not_prioritized':result['counterexample_resolved']=current.get('modeled_lethal',False)
    else:result['counterexample_resolved']=None
    result['review_required']=finding.get('status')!='confirmed' or result['counterexample_resolved'] is None
    return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('fixture');parser.add_argument('--budget-ms',type=int,default=1500);args=parser.parse_args()
    print(json.dumps(replay(args.fixture,args.budget_ms),ensure_ascii=False,indent=2))
