"""Append-only independent decision/action journal. Never edits historical logs or policy."""
from pathlib import Path
from contextlib import nullcontext
from datetime import datetime,timezone
from collections import Counter,defaultdict
import json
from . import nightmare as N
from .nightmare_search import STRATEGY_VERSION,DECK_VERSION,effects

LOG_VERSION='nightmare-audit-2.2.0'
def clean(x):
    if isinstance(x,dict):return {str(k):clean(v) for k,v in x.items() if k not in ('patch','art')}
    if isinstance(x,(list,tuple,set)):return [clean(v) for v in x]
    if x is None or isinstance(x,(str,int,float,bool)):return x
    if hasattr(x,'item'):
        try:return x.item()
        except (ValueError,TypeError):pass
    return str(x)

def state_record(state):
    fields=('own_hp','enemy_hp','pp','max_pp','extra_pp','graveyard','ep_left','sep_left','evolution_used','play_order','opponent_class','turn','ep_available','super_available','extra_pp_attempted_phases')
    result={k:state.get(k) for k in fields};result.update({k:clean(state.get(k,[])) for k in ('own','enemy','hand','attacked_uids','blocked_attacks')})
    result['attackable']=[u['uid'] for u in state.get('own',[]) if u.get('ready')]
    result['public_effects']={k:state.get(k) if state.get(k) is not None else (state.get('public_effects') or {}).get(k)
        for k in ('enemy_damage_prevention','enemy_auto_heal','public_effects_complete')}
    for k in ('failed_plays','failed_evolutions'):result[k]=clean(state.get(k,[]))
    result['recognition_evidence']=clean(state.get('recognition_evidence',{}))
    result['evolution_resource_evidence']=clean(state.get('evolution_resource_evidence',{}))
    # Existing OCR does not expose calibrated numeric confidence for all HUD
    # fields. Preserve null explicitly instead of inventing confidence=1.
    result['recognition']={k:{'value':state.get(k),'confidence':state.get('confidence',{}).get(k),
        'status':'unknown' if state.get(k) is None else 'observed_uncalibrated','source':state.get('observation_source','existing_vision')} for k in fields[:9]}
    for side in ('own','enemy','hand'):
        for item in result[side]:
            item['recognition']={k:item.get(k) for k in ('identity_score','cost_confidence','attack_confidence','health_confidence')}
    return result

def comparable(s):
    # Confirmed strategy counterexamples require a closed visible state. In
    # normal play unmodelled enemy abilities leave the finding under review.
    return all(s.get(k) is not None for k in ('pp','own_hp','enemy_hp','graveyard')) and (not s.get('enemy') or s.get('public_effects_complete') is True)

def diagnose(state,plan):
    findings=[];selected=plan.get('selected');alternatives=plan.get('alternatives',[])
    if not selected:return findings
    proof=comparable(state);original=plan.get('line',[])
    def add(kind,reason,alt=None,confirmed=False,causes=None):
        findings.append({'type':kind,'status':'confirmed' if confirmed else 'review','trigger':reason,
            'original_line':original,'alternative_line':(alt or {}).get('line'),
            'evidence':{'selected':selected.get('effects'),'alternative':(alt or {}).get('effects'),'known_at_decision':True},
            'causes':causes or ['strategy'],'suggested_rule':reason})
    lethal=next((a for a in alternatives if a['category']=='确定斩杀'),None)
    if lethal and not plan.get('certain_lethal'):add('suspected_missed_lethal','当时已知状态存在可执行斩杀候选',lethal,proof)
    modeled=next((alt for alt in alternatives if state.get('enemy_hp') is not None
        and state['enemy_hp']>0 and (alt.get('effects',{}).get('face_damage',{}).get('modeled') or 0)>=state['enemy_hp']
        and (alt.get('effects',{}).get('own_hp_after') or 0)>0),None)
    if modeled and not(plan.get('certain_lethal') or plan.get('modeled_lethal')):
        add('modeled_lethal_not_prioritized','可见模型存在致死候选但未优先尝试；复核关键值后单步执行，未知效果不标记为确定斩杀',modeled,False)
    a=plan['action'];best=alternatives[0] if alternatives else None
    if not plan.get('certain_lethal') and set(plan.get('search',{}).get('incomplete_reasons',[])).intersection(('time_budget','node_budget','beam_pruning','depth_budget')) and a['kind']=='end_turn':
        add('search_incomplete_lethal_review','结束前搜索未穷尽，不能断言没有斩杀；需用同一时点状态扩大离线预算',best,False)
    if a['kind']=='end_turn' and plan.get('end_turn_check',{}).get('attack_options') and best:
        se=selected['effects'];be=best['effects']
        if (be['face_damage']['certain'] or 0)>(se['face_damage']['certain'] or 0) and be['own_hp_change']==0 and not be['board']['own_lost']:
            add('missed_attack','结束回合时存在不损失己方生命/随从的更高确定伤害路线',best,proof)
    if best and best['score']>selected['score']+.01:
        if a['kind']=='attack' and a.get('target_uid') is not None:
            add('low_value_trade','交换路线被更高收益候选压过；核对保留随从与减少反杀压力的价值',best)
        elif a['kind'] in ('evolve','super_evolve'):
            add('evolution_choice','存在更高收益且进化资源不同的候选',best)
        elif a['kind']=='play':add('overextend_or_conservative','展开/保留的风险收益存在更优候选',best)
    # A second, score-independent check catches a preference that sacrifices
    # certain damage without preserving HP, board, resources or pressure.
    se=selected['effects']
    for alt in alternatives:
        ae=alt['effects']
        comparable_metrics=all(x is not None for x in (ae['face_damage']['certain'],se['face_damage']['certain'],ae['own_hp_after'],se['own_hp_after'],ae['resources']['pp_net'],se['resources']['pp_net']))
        dominates=comparable_metrics and ae['face_damage']['certain']>se['face_damage']['certain'] and ae['own_hp_after']>=se['own_hp_after'] and ae['resources']['pp_net']<=se['resources']['pp_net'] and ae['risk']['visible_enemy_attack']<=se['risk']['visible_enemy_attack'] and ae['board']['own_after']==se['board']['own_after'] and not ae['random']['pending']
        if dominates:
            kind='low_value_trade' if a['kind']=='attack' and a.get('target_uid') is not None else 'overextend_or_conservative'
            add(kind,'替代路线增加确定脸伤，且未牺牲己方HP、场面、PP或可见反杀压力',alt,proof);break
    if a['kind'] in ('evolve','super_evolve') and a.get('hand_iid'):
        better=next((r for r in alternatives if r['line'] and r['line'][0].get('uid')==a.get('uid') and r['line'][0].get('hand_iid')!=a['hand_iid'] and (r['effects']['face_damage']['certain'] or 0)>(selected['effects']['face_damage']['certain'] or 0)),None)
        if better:add('wrong_buff_target','另一个可区分手牌目标能更快兑现确定伤害',better,proof)
    s=N.normalize(state)
    for action in original:
        if action not in N.actions(s):
            add('resource_or_sequence_invalid','路线在逐动作费用/墓场/进化/血差条件复核时失效',None,proof,['card_rules','strategy']);break
        nxt=N.apply(s,action)
        if nxt is None:
            add('self_damage_omission','路线中途自伤致死，不能预支后续回血',None,proof,['card_rules','strategy']);break
        s=nxt
        if s.get('random_pending'):break
    return findings

class Journal:
    def __init__(self,base,game_id,mode='unknown'):
        self.base=Path(base);self.path=self.base/game_id;self.path.mkdir(parents=True,exist_ok=True)
        self.game_id=game_id;self.mode=mode;self.turn=0;self.decision_id=None;self.action_id=None;self.number=0
        self.started=False;self.pending=None;self.findings=[];self.decisions=[];self.last_state=None;self.technical=[];self.closed=False;self.operations=0;self.last_receipt=None
    def write(self,event,**data):
        row={'time':datetime.now(timezone.utc).isoformat(),'event':event,'game_id':self.game_id,'turn':self.turn,
            'decision_id':self.decision_id,'action_id':self.action_id,'strategy_version':STRATEGY_VERSION,'deck_version':DECK_VERSION,'log_version':LOG_VERSION,**data}
        with self.metrics.span('audit_io') if getattr(self,'metrics',None) else nullcontext():
            with (self.path/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(clean(row),ensure_ascii=False)+'\n')
        return row
    def save_json(self,name,value):
        p=self.path/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(clean(value),ensure_ascii=False,indent=2),encoding='utf-8')
    def begin_game(self,state=None):
        if self.started:return
        self.started=True;meta=self.write('game_start',mode=self.mode,play_order=(state or {}).get('play_order'))
        self.save_json('game.json',meta)
    def screenshot(self,image,suffix):
        if image is None:return None
        name=f'{self.decision_id or "session"}_{suffix}.png'
        with self.metrics.span('audit_image_io') if getattr(self,'metrics',None) else nullcontext():image.save(self.path/name)
        return name
    def finding(self,f,state=None):
        f={**f,'decision_id':self.decision_id,'action_id':self.action_id,'turn':self.turn,'finding_id':f'{self.game_id}-f{len(self.findings)+1:04d}'}
        self.findings.append(f);self.write('improvement_event',**f)
        if state is not None and (f.get('alternative_line') or f['status']=='confirmed'):
            self.save_json('regressions/'+f['finding_id']+'.json',{'state':clean(state),'observation':state_record(state),'finding':f,
                'automatic_policy_change':False,'purpose':'离线复现；review不能当真值标签'})
    def decide(self,state,plan,image=None):
        self.reconcile(state,image);self.turn=state.get('turn',self.turn);self.number+=1
        if self.pending and self.operations==0:
            self.write('decision_superseded',reason='重新识别后替代尚未执行的计划',credited_planned_damage=0)
        self.decision_id=f'{self.game_id}-d{self.number:04d}';self.action_id=self.decision_id+'-a01';self.last_receipt=None;self.operations=0
        first=plan['action'];n=N.normalize(state);after=N.apply(n,first) if first['kind']!='end_turn' else N.end_turn(n)
        expected=effects(n,after,[first]) if after is not None else None
        key=plan.get('certain_lethal') or plan.get('modeled_lethal') or first.get('card_id')==N.GARA or first['kind'] in ('evolve','super_evolve','end_turn') or first.get('target_uid') is not None or expected and (expected['own_hp_change'] or expected['random']['pending'])
        frame=self.screenshot(image,'before') if key else None
        self.write('decision',state=state_record(state),plan=plan,expected_next_action=expected,before_image=frame,summary=plan.get('summary'))
        self.decisions.append({'decision_id':self.decision_id,'turn':self.turn,'category':plan.get('selected',{}).get('category'),
            'summary':plan.get('summary'),'certain_lethal':plan.get('certain_lethal'),'modeled_lethal':plan.get('modeled_lethal',False),'search':plan.get('search'),'score':plan.get('score')})
        for f in diagnose(state,plan):self.finding(f,state)
        self.pending={'before':clean(state),'expected_state':clean(after),'expected':expected,'action':first,'plan':plan,'key':key,
            'decision_id':self.decision_id,'action_id':self.action_id,'reads':0}
        self.last_state=clean(state)
    def event(self,kind,data):
        if kind=='action':
            self.operations+=1;self.write('operation',operation_id=f'{self.action_id or self.game_id}-op{self.operations:02d}',operation=data,success=False,basis='dispatched_only')
            if data.get('name')=='silk_buff_hand' and self.pending:
                wanted=self.pending['action'].get('hand_iid');chosen=data.get('facts',{}).get('instance_id')
                if wanted and chosen!=wanted:
                    self.finding({'type':'buff_target_changed','status':'review','trigger':'强化实际选择与原手牌实例计划不一致',
                        'original_line':self.pending['plan']['line'],'alternative_line':None,'evidence':{'planned_iid':wanted,'selected_iid':chosen},
                        'causes':['recognition','operation'],'suggested_rule':'手牌移动后重新区分当前费用/攻击，不能沿用旧坐标'})
        elif kind=='receipt':
            self.last_receipt=clean(data);self.write('confirmation',confirmation=data)
            if data.get('name')=='play' and data.get('verified'):
                self.write('public_play',side='own',card_id=data.get('card'),instance_id=data.get('instance_id'),basis='existing_action_receipt')
            if data.get('outcome') in ('rejected_target','rejected_source_or_rule','not_applied'):
                certain=data.get('outcome','').startswith('rejected')
                self.finding({'type':'click_or_target_failure','status':'confirmed' if certain else 'review','trigger':data.get('outcome'),
                    'original_line':self.pending['plan']['line'] if self.pending else [],'alternative_line':None,
                    'evidence':data,'causes':['operation','recognition'],'suggested_rule':'核对目标坐标与动作前后状态；不把发送当成功'})
        elif kind in ('stopped','cleanup_error','failure_exit'):
            self.technical.append({'event':kind,**clean(data)});self.write('technical_event',detail=data)
        elif kind in ('outcome','result','network_recovery','recoverable_stall','local_missing','effect_phase_exit','lethal_recheck','end_turn_replan','end_turn_resource_check','hand_buff_confirmed','evolution_not_applied'):
            self.write(kind,detail=data)
    def reconcile(self,state,image=None,terminal=None):
        if self.last_state is not None:
            old=self.last_state
            for side in ('own','enemy'):
                previous=Counter(N.cid(c) for c in old.get(side,[]) if N.cid(c));current=Counter(N.cid(c) for c in state.get(side,[]) if N.cid(c))
                if previous!=current:self.write('public_board_change',side=side,added=dict(current-previous),removed=dict(previous-current),basis='observed identities; does not infer hidden hand or exact trigger')
        p=self.pending
        if not p:self.last_state=clean(state);return
        if self.operations==0 and not self.last_receipt:return
        self.decision_id=p['decision_id'];self.action_id=p['action_id'];p['reads']+=1
        actual={k:delta_value(p['before'].get(k),state.get(k)) for k in ('own_hp','enemy_hp','pp','graveyard','ep_left','sep_left')}
        changed=[k for k,v in actual.items() if v not in (None,0)]
        expected_state=p['expected_state'] or {};differences=[]
        random_pending=bool((p['expected'] or {}).get('random',{}).get('pending'))
        intervening_turn=state.get('turn')!=p['before'].get('turn')
        for k in (() if intervening_turn else ('own_hp','enemy_hp','pp','graveyard')):
            if state.get(k) is not None and expected_state.get(k) is not None and state[k]!=expected_state[k]:differences.append({'field':k,'expected':expected_state[k],'observed':state[k]})
        actual_board={side:{'before_count':len(p['before'].get(side,[])),'after_count':len(state.get(side,[])),
            'before':clean(p['before'].get(side,[])),'after':clean(state.get(side,[]))} for side in ('own','enemy')}
        if not intervening_turn and not random_pending:
            for side in ('own','enemy'):
                expected=expected_state.get(side,[]);observed=state.get(side,[])
                readable=all(N.cid(u) and u.get('attack') is not None and u.get('health') is not None for u in expected+observed)
                signature=lambda units:sorted((str(N.cid(u)),u['attack'],u['health']) for u in units)
                if readable and signature(expected)!=signature(observed):differences.append({'field':side+'_board','expected':signature(expected),'observed':signature(observed)})
            expected_hand={c.get('iid'):c for c in expected_state.get('hand',[])}
            for c in state.get('hand',[]):
                old=expected_hand.get(c.get('iid'))
                if old:
                    for key in ('cost','attack'):
                        if old.get(key) is not None and c.get(key) is not None and old[key]!=c[key]:differences.append({'field':'hand_'+key,'iid':c['iid'],'expected':old[key],'observed':c[key]})
        receipt=self.last_receipt or {};confirmed=bool(receipt.get('verified'))
        if p['action']['kind']=='end_turn':confirmed=confirmed or intervening_turn or terminal is not None
        self.write('action_result',planned=p['action'],expected=p['expected'],actual_delta=actual,actual_board_change=actual_board,
            before=state_record(p['before']),after=state_record(state),confirmation=receipt,
            status='confirmed' if confirmed else 'unconfirmed',differences=differences,random_branch=random_pending,intervening_turn=intervening_turn,
            basis='实际资源/HP变化独立保存，未确认操作不计计划收益',after_image=self.screenshot(image,'after') if p['key'] or differences else None)
        if differences:
            card_id=p['action'].get('card_id');kind='sequence_condition_changed' if card_id in (N.RABBIT,N.BURST) else 'graveyard_or_cost_difference' if any(d['field'] in ('pp','graveyard','hand_cost') for d in differences) else 'self_damage_or_heal_difference' if any(d['field']=='own_hp' for d in differences) else 'prediction_observation_difference'
            self.finding({'type':kind,'status':'review','trigger':'计划与实际可见状态不一致',
                'original_line':p['plan']['line'],'alternative_line':None,'evidence':{'differences':differences,'random':random_pending,'receipt':receipt},
                'causes':['recognition','card_rules','operation'],'suggested_rule':'先复核识别/卡规/目标与结算，不由单局结果修改策略'},p['before'])
        if not confirmed and not changed and p['action']['kind']!='end_turn':
            self.finding({'type':'unconfirmed_or_stale_action','status':'review','trigger':'发送后没有可确认变化，禁止把计划收益记为实际收益',
                'original_line':p['plan']['line'],'alternative_line':None,'evidence':receipt,'causes':['operation','recognition'],
                'suggested_rule':'有限重读后换可执行动作；无法恢复由既有故障链路明确停止'})
        self.pending=None;self.last_state=clean(state)
    def finish(self,summary,state=None):
        if self.closed:return
        self.closed=True
        if self.pending:
            self.write('action_unresolved_at_exit',planned=self.pending['action'],confirmation=self.last_receipt,
                status='unconfirmed',terminal_outcome=summary.get('outcome'),credited_planned_damage=0)
        outcome=summary.get('outcome','unknown');outcome=outcome if outcome in ('win','loss','draw') else 'unknown'
        user_interrupted=summary.get('failure_kind') in ('user_stop','intentional_validation_exit')
        technical=bool((summary.get('failure_kind') and not user_interrupted) or any(e['event'] in ('cleanup_error','audit_failure') for e in self.technical))
        normal=bool(summary.get('reached_result') and outcome!='unknown' and not technical)
        counts=Counter(f['status'] for f in self.findings)
        flagged={f['decision_id'] for f in self.findings if f['status']=='confirmed'}
        decisive=sorted(self.decisions,key=lambda d:(d['decision_id'] in flagged,bool(d['certain_lethal'] or d.get('modeled_lethal')),d['score'] or 0),reverse=True)[:3]
        rules=Counter(f['suggested_rule'] for f in self.findings if f['status']=='confirmed')
        report={'game_id':self.game_id,'strategy_version':STRATEGY_VERSION,'deck_version':DECK_VERSION,'log_version':LOG_VERSION,
            'started':self.started,'mode':self.mode,'play_order':(state or {}).get('play_order','unknown'),'outcome':outcome,
            'technical_interruption':technical,'user_interrupted':user_interrupted,'normal_completed':normal,'original_run_summary':summary,
            'decisive_decisions':decisive,'confirmed':counts['confirmed'],'review':counts['review'],
            'technical_events':self.technical,'findings':self.findings,'priority_rules':[{'rule':r,'confirmed_count':n,'decision_ids':sorted({f['decision_id'] for f in self.findings if f['status']=='confirmed' and f['suggested_rule']==r})} for r,n in rules.most_common(3)],
            'automatic_policy_change':False,'hindsight_policy':'仅使用决策时已知状态；输赢、剩PP、未进化不单独构成错误'}
        self.save_json('summary.json',report)
        lines=[f'对局 {self.game_id}',f'结果：{outcome}；正常完成：{normal}；技术中断：{technical}',
            f'confirmed {counts["confirmed"]} / review {counts["review"]}；未自动修改策略。','关键决策（按当时路线评估，非因果胜负归因）：']
        lines.extend(f'- 回合{d["turn"]} {d["decision_id"]}：{d["summary"]}' for d in decisive)
        lines.append('优先修正规则：'+('；'.join(r for r,n in rules.most_common(3)) or '没有当时证据可确认的策略错误；保留review供检查'))
        self.path.joinpath('review.txt').write_text('\n'.join(lines),encoding='utf-8')
        self.write('game_summary',summary=report);aggregate(self.base)

def delta_value(a,b):return b-a if isinstance(a,(int,float)) and isinstance(b,(int,float)) else None

def aggregate(base):
    base=Path(base);groups=defaultdict(Counter);rules=Counter();games=[]
    for p in sorted(base.glob('*/game.json')):
        start=json.loads(p.read_text(encoding='utf-8'));s=p.parent/'summary.json'
        row=json.loads(s.read_text(encoding='utf-8')) if s.exists() else {'game_id':p.parent.name,'outcome':'unknown','normal_completed':False,'technical_interruption':False}
        key=(row.get('strategy_version',start['strategy_version']),row.get('mode',start.get('mode','unknown')),row.get('play_order') or 'unknown')
        g=groups[key];g['starts']+=1;g[row.get('outcome','unknown')]+=1;g['technical_interruptions']+=bool(row.get('technical_interruption'));g['user_interruptions']+=bool(row.get('user_interrupted'));g['normal_completed']+=bool(row.get('normal_completed'))
        if row.get('normal_completed'):g['normal_'+row['outcome']]+=1
        games.append({'game_id':row['game_id'],'outcome':row.get('outcome','unknown'),'summary_present':s.exists()})
        for f in row.get('findings',[]):
            if f['status']=='confirmed':rules[(row.get('strategy_version'),f['type'],f['suggested_rule'])]+=1
    result={'groups':[{'strategy_version':k[0],'mode':k[1],'play_order':k[2],**dict(v)} for k,v in groups.items()],
        'games':games,'repeated_confirmed_rules':[{'strategy_version':k[0],'type':k[1],'rule':k[2],'count':v} for k,v in rules.most_common()],
        'unknown_is_loss':False,'automatic_policy_change':False}
    (base/'statistics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
