"""Nightmare adapter over the existing Run capture, gestures and receipts."""
import re
import time
from collections import Counter
from copy import deepcopy
from . import nightmare as N
from .nightmare_state import HandMemory

def init(run):
    run.nm_hand=HandMemory();run.nm_failed=set();run.nm_transactions={};run.nm_last_turn=None
    run.nm_journal=None;run.nm_audit_error=None

def snapshot(run,state):
    if not hasattr(run,'nm_hand'):init(run)
    pending=getattr(run,'nm_pending_play',None)
    if pending:
        if pending['turn']==run.turn:
            finalize_play(run,pending,state['pp'],state['hand'])
            flags=('can_face','storm','rush','ignore_ward','drain','ephemeral','summoned_turn','ward')
            for unit in state['own']:
                memory=next((u for u in run.unit_memory if u['uid']==unit['uid']),None)
                if memory:
                    for key in flags:
                        if key in memory:unit[key]=memory[key]
                    if unit.get('storm') and unit['uid'] not in run.attacked:unit['ready']=True
        else:
            run.log('receipt',name='play',verified=False,outcome='unconfirmed',reason='turn_changed_before_receipt')
            run.nm_pending_play=None
    state['hand']=run.nm_hand.observe(state['hand'])
    state['graveyard']=run.v.graveyard(run.im)
    state['evolution_used']=run.evolved
    state['graveyard_basis']='current_visible_counter' if state['graveyard'] is not None else 'unread'
    state['recognition_evidence']=deepcopy(getattr(run.v,'read_evidence',{}))
    state['confidence']={k:v.get('score') for k,v in state['recognition_evidence'].items() if k in ('own_hp','enemy_hp','pp','max_pp','graveyard')}
    try:
        journal=audit_get(run)
        if journal:
            if getattr(run,'started',False):journal.begin_game(state)
            journal.reconcile(state,getattr(run,'im',None))
    except Exception as exc:audit_fault(run,exc)
    return state

def buff_rank(card,state):
    id=N.cid(card);cost=card.get('cost');pp=state.get('pp');hp=state.get('own_hp')
    if cost is None:return -100
    storm=id in (N.ANSE,N.GHOST) or id==N.MUMMY and N.value(state,'graveyard')>=4 or id==N.GARA and hp is not None and hp>2
    ward=any(e.get('ward') for e in state.get('enemy',[]))
    face=storm and (not ward or id==N.ANSE)
    future_cost=max(0,cost-1) if id==N.GARA and hp is not None and hp<=12 else cost
    tier=1000 if face and pp is not None and cost<=pp else 500 if face and future_cost<=min(10,N.value(state,'max_pp')+1) else 100 if storm else 10
    return tier-cost

def resolve_prompt(run,card=None):
    effect=run.active_effect or {};id=(card or {}).get('id',effect.get('id'))
    if id not in (N.BAAL,N.GARA,N.SILK):return False
    serial=getattr(run,'effect_serial',0);tx=run.nm_transactions.setdefault(serial,{})
    if run.outcome!='unknown' or run.v.scene(run.im)[0]=='RESULT':return True
    phase=run.v.turn(run.im)
    is_buff=id==N.SILK and effect.get('kind') in ('evolve','super_evolve')
    if phase in ('OUR','ENEMY') and not is_buff:return True
    rows=run.texts((15,80,1260,660));text=' '.join(r['text'] for r in rows)
    plan=effect.get('plan',{})
    if id in (N.BAAL,N.GARA) and re.search('模式|能力.*選|選.*能力',text):
        if tx.get('mode_sent'):return False
        mode=plan.get('mode',1)
        pattern=('疾馳|疾驰' if mode==1 else '守護|守护') if id==N.GARA else ('其他.*從者|其他.*随从|各.*1|\\+1' if mode==1 else '隨機.*3|随机.*3|3.*傷害|3.*伤害')
        options=[r for r in rows if 170<r['box'][1]<500 and re.search(pattern,r['text'])]
        if not options:
            run.log('local_missing',field='nightmare_mode_choices',card=id);return False
        chosen=min(options,key=lambda r:r['box'][1]);tx['mode_sent']=True
        run.act('choose_mode',run.center(chosen),delay=.5,card=id,mode=mode)
        point=run.button('決定|确定|確認|确认',(420,430,1240,690))
        if point and not tx.get('mode_confirmed'):
            tx['mode_confirmed']=True;run.act('confirm_mode',point,delay=.5)
        settled=run.settle_action(timeout=4)
        if settled:run.expanded=False
        return settled
    if is_buff:
        if tx.get('buff_sent'):return False
        candidates=run.v.picker_cards(run.im)
        header=' '.join(r['text'] for r in run.texts((440,75,1230,170)))
        if not candidates and re.search('手牌',header):candidates=run.v.hand(run.im)
        if phase in ('OUR','ENEMY') and not candidates:return True
        candidates=[c for c in candidates if N.cid(c) in N.STATS and N.cid(c) not in (N.BURST,N.CONCERT)]
        if not candidates:
            run.log('local_missing',field='silk_hand_target');return False
        for c in candidates:c['iid']=run.nm_hand.picker_instance(c,candidates)
        wanted=plan.get('hand_iid')
        chosen=next((c for c in candidates if c.get('iid')==wanted and wanted),None)
        if chosen is None:chosen=max(candidates,key=lambda c:buff_rank(c,run.last_state))
        tx.update(buff_sent=True,buff_iid=chosen.get('iid'))
        run.act('silk_buff_hand',chosen['xy'],delay=.5,card=chosen,instance_id=chosen.get('iid'))
        point=run.button('決定|确定|確認|确认',(420,430,1240,690))
        if point:run.act('silk_buff_confirm',point,delay=.5)
        settled=run.settle_action(timeout=4)
        if settled:run.expanded=False
        # Selecting a card alone does not prove either evolution or +3.
        # Commit this instance buff only after evolve() confirms the effect.
        tx['buff_selection_settled']=bool(settled)
        return settled
    if id==N.SILK:
        if tx.get('target_sent'):return False
        targets=run.v.highlighted_followers(run.im)
        wanted=next((e for e in run.last_state.get('enemy',[]) if e['uid']==plan.get('target_uid')),None)
        if not targets:
            run.log('local_missing',field='silk_enemy_target');return False
        for target in targets:
            old=min(run.last_state.get('enemy',[]),key=lambda e:abs(e['xy'][0]-target['xy'][0]),default=None)
            if old and abs(old['xy'][0]-target['xy'][0])<45:
                target.update({k:old.get(k) for k in ('ward','threat_level','attack','health')})
        chosen=min(targets,key=lambda e:abs(e['xy'][0]-wanted['xy'][0])) if wanted else max(targets,key=lambda e:(N.value(e,'health',99)<=3)*10+bool(e.get('ward'))*8+N.value(e,'attack'))
        tx['target_sent']=True
        run.act('silk_reduce_health',chosen['xy'],delay=.5,target=chosen,stat_reduction=3)
        return run.settle_action(timeout=4)
    return False

def commit_summon(run,state,card,plan):
    before={u['uid'] for u in state['own']}
    run.remember_units(run.v.board(run.im,'own'))
    new=[u for u in run.unit_memory if u['uid'] not in before]
    # Identity first; do not label a summoned bat as the Rabbit body.
    unit=next((u for u in new if u.get('card_id')==card['id']),None)
    if unit is None and len(new)==1:unit=new[0]
    if unit is not None:
        id=card['id'];storm=id in (N.ANSE,N.GHOST) or id==N.GARA and plan.get('mode')==1 or id==N.MUMMY and state.get('graveyard') is not None and state['graveyard']>=4
        unit.update(card_id=id,summoned_turn=run.turn,can_face=storm,storm=storm,rush=id==N.ESCAPE,
                    ignore_ward=id==N.ANSE,drain=id==N.BAT,ephemeral=id==N.GHOST)
        if id==N.GARA and plan.get('mode')==2:unit['ward']=True
    for u in new:
        if u is not unit:u['summoned_turn']=run.turn

def finalize_play(run,pending,pp,after):
    state=pending['state'];card=pending['card'];action=pending['action']
    counts=Counter(c['id'] for c in (after or []));before=Counter(c['id'] for c in state['hand'])
    played=(pp is not None and state['pp'] is not None and pp<state['pp']) or (after is not None and len(after)==len(state['hand'])-1 and before-counts==Counter({card['id']:1}))
    run.confirm('play',played,card=card['id'],instance_id=card['iid'],before_pp=state['pp'],after_pp=pp)
    run.nm_pending_play=None;run.active_effect=None;run.expanded=False
    if played:
        run.nm_hand.consumed(card['iid']);run.nm_failed.clear()
        if card['kind']=='follower':commit_summon(run,state,card,action)

def play(run,state,action):
    card=next((c for c in state['hand'] if c['iid']==action['iid']),None)
    if card is None:return
    run.nm_failed.add(('play',card['iid']))
    run.effect_serial=getattr(run,'effect_serial',0)+1
    run.active_effect={'id':card['id'],'kind':'play','plan':action}
    run.nm_pending_play={'state':deepcopy(state),'card':dict(card),'action':dict(action),'turn':run.turn}
    run.act('play',card['xy'],[640,355],delay=.35,card=card,pp=state['pp'],strategy='nightmare_aggro')
    settled=run.settle_action(card['id'])
    was_field=run.v.turn(run.im)=='OUR'
    before_prompt=run.im
    prompt_settled=resolve_prompt(run,card)
    # A real choice already returned a stable battlefield receipt. Preserve it
    # when no later input occurred instead of settling the same action twice.
    if prompt_settled and run.im is not before_prompt and run.v.turn(run.im)=='OUR':
        settled=True;was_field=True
    if run.outcome!='unknown' or run.v.turn(run.im)=='ENEMY':return
    if not (settled and was_field):settled=run.settle_action(timeout=5)
    if not settled:
        run.log('local_missing',field='unresolved_play',card=card['id']);return
    pp,_=run.v.pp(run.im)
    # A verified PP decrease already proves this play. Do not run the full
    # hand-template search again merely to provide the same receipt.
    after=None if pp is not None and state['pp'] is not None and pp<state['pp'] else run.v.hand(run.im)
    finalize_play(run,run.nm_pending_play,pp,after)

def evolve(run,state,action):
    unit=next((u for u in state['own'] if u['uid']==action['uid']),None)
    if unit is None:return
    mode=action['kind'];run.nm_failed.add((mode,unit['uid']))
    run.act('select_evolution_source',unit['xy'],delay=.2)
    options=run.v.evolution_buttons(run.im)
    point=options.get(mode)
    if point is None or not run.v.evolution_enabled(run.im,point,mode):
        run.act('close_card_panel',[1020,405],delay=.3);run.expanded=False;return
    before=run.v.evolution_resource(run.im,mode)
    run.effect_serial=getattr(run,'effect_serial',0)+1
    run.active_effect={'id':unit.get('card_id'),'kind':mode,'source':unit,'plan':action}
    run.act(mode,point,delay=.35,source=unit,strategy='nightmare_aggro')
    # Latch the shared per-turn opportunity on dispatch, including uncertain UI.
    run.evolved=True
    initial_settled=run.settle_action(unit.get('card_id'));stable_image=run.im
    resolve_prompt(run,{'id':unit.get('card_id')})
    if run.outcome!='unknown':return
    tx=run.nm_transactions.get(run.effect_serial,{})
    choice_sent=any(tx.get(k) for k in ('buff_sent','target_sent','mode_sent'))
    if initial_settled and run.im is stable_image and not choice_sent:
        settled=True
    else:settled=run.settle_action(timeout=5)
    after=run.v.evolution_resource(run.im,mode)
    used=after['filled']<before['filled'];units=run.v.board(run.im,'own')
    changed=min(units,key=lambda u:abs(u['xy'][0]-unit['xy'][0]),default=None)
    gain=changed and abs(changed['xy'][0]-unit['xy'][0])<55 and changed.get('attack') is not None and unit.get('attack') is not None and changed['attack']>=unit['attack']+(3 if mode=='super_evolve' else 2)
    applied=bool(settled and (used or gain))
    run.confirm(mode,applied,source=unit,resource_before=before,resource_after=after)
    run.remember_units(units)
    if applied:
        target=next((u for u in run.unit_memory if u['uid']==unit['uid']),None)
        if target is None:
            candidates=[u for u in run.unit_memory if u.get('card_id')==unit.get('card_id') and abs(u['xy'][0]-unit['xy'][0])<35]
            if len(candidates)==1:target=candidates[0]
        if target is None and gain:
            # Gold evolution frames can temporarily hide portrait identity.
            # Recover only the body whose confirmed stat gain proved this effect.
            matches=[u for u in run.unit_memory if abs(u['xy'][0]-changed['xy'][0])<10]
            if len(matches)==1:target=matches[0]
        if target:
            for key in ('card_id','summoned_turn','storm','rush','ignore_ward','drain','ephemeral'):
                if key in unit:target[key]=unit[key]
            target.update(evolved=True,super=mode=='super_evolve',can_face=unit.get('can_face',False))
        run.nm_failed.clear()
    tx=run.nm_transactions.get(run.effect_serial,{})
    if applied and tx.get('buff_sent') and settled and run.v.turn(run.im)=='OUR' and not run.v.picker_cards(run.im):
        tx['buff_selection_settled']=True  # The final receipt may settle after the picker's short wait.
    if applied and unit.get('card_id')==N.SILK and tx.get('buff_iid') and tx.get('buff_selection_settled'):
        run.nm_hand.buff(tx['buff_iid'],run.effect_serial);tx['buff_recorded']=True
        run.log('hand_buff_confirmed',instance_id=tx['buff_iid'],attack_bonus=3,basis='evolution_and_real_picker_receipts')
    if settled and not applied and not tx.get('buff_sent'):
        # One bounded second observation distinguishes a missed input from a
        # still-settling evolution. Never replay the same failed uid/mode.
        with run.metrics.span('animation_wait'):time.sleep(.25)
        run.observe('evolution_receipt_reread')
        resource=run.v.evolution_resource(run.im,mode);fresh=run.v.board(run.im,'own')
        same=min(fresh,key=lambda u:abs(u['xy'][0]-unit['xy'][0]),default=None)
        unchanged=same and abs(same['xy'][0]-unit['xy'][0])<35 and all(same.get(k) is not None and same.get(k)==unit.get(k) for k in ('attack','health'))
        if resource['filled']==before['filled'] and unchanged and run.v.turn(run.im)=='OUR' and not run.v.card_panel_open(run.im):
            run.evolved=False
            run.log('evolution_not_applied',mode=mode,source_uid=unit['uid'],resources=resource,basis='stable_same_resource_and_body',opportunity_restored=True)
    if settled:run.active_effect=None
    run.expanded=False

def battle_step(run,state):
    return _battle_step(run,state)

def _battle_step(run,state):
    if run.nm_last_turn!=run.turn:run.nm_failed.clear();run.nm_last_turn=run.turn
    # Failed local inputs are excluded without cancelling other legal actions.
    state['failed_plays']=[key for kind,key in run.nm_failed if kind=='play']
    state['failed_evolutions']=[(kind,key) for kind,key in run.nm_failed if kind in ('evolve','super_evolve')]
    with run.metrics.span('decision'):plan=N.choose_action(state,run.nm_settings)
    if (plan['certain_lethal'] or plan.get('modeled_lethal')) and hasattr(run,'observe') and hasattr(run,'snapshot'):
        from .nightmare_search import fingerprint
        signature=(run.turn,fingerprint(N.normalize(state)))
        if getattr(run,'nm_lethal_checked',None)!=signature:
            with run.metrics.span('animation_wait'):time.sleep(.3)
            run.observe('nightmare_lethal_recheck');fresh=run.snapshot()
            fresh_signature=(run.turn,fingerprint(N.normalize(fresh)))
            run.nm_lethal_checked=fresh_signature if fresh_signature==signature else None
            run.log('lethal_recheck',stable=fresh_signature==signature,before_hp=[state.get('own_hp'),state.get('enemy_hp')],after_hp=[fresh.get('own_hp'),fresh.get('enemy_hp')],certain_lethal=plan['certain_lethal'],modeled_lethal=plan.get('modeled_lethal',False))
            if fresh_signature!=signature:return
            state=fresh  # Always use the fresh coordinates, even if values agree.
        plan['lethal_observation_check']='stable_second_read'
    run.log('nightmare_plan',frame=run.frame,**plan)
    a=plan['action'];kind=a['kind'];run.nm_action_kind=kind
    if kind=='play':play(run,state,a);return
    if kind=='attack':
        source=next(u for u in state['own'] if u['uid']==a['uid'])
        target=next((e['xy'] for e in state['enemy'] if e['uid']==a.get('target_uid')),[640,80])
        run.attack(state,{'source':source,'target':target,'reason':'nightmare_sequence','sequence':plan['line'],'lethal':plan['certain_lethal']})
        run.expanded=False  # Board gestures collapse the hand; re-open before next planning.
        return
    if kind in ('evolve','super_evolve'):evolve(run,state,a);return
    if kind=='extra_pp':
        phase='before_turn6' if run.turn<6 else 'turn6_onward';run.extra_pp_attempts.add(phase)
        run.act('extra_pp',state['extra_pp']['xy'],delay=.5,phase=phase,plan=plan)
        run.settle_action(timeout=3);pp,_=run.v.pp(run.im)
        run.confirm('extra_pp',pp is not None and pp==state['pp']+1,before_pp=state['pp'],after_pp=pp)
        run.expanded=False;return
    # One local refresh for incomplete resources, then use the actions that
    # remain provably legal or finish this turn; never abort the whole game.
    missing=[k for k in ('pp','own_hp','enemy_hp','graveyard') if state.get(k) is None]
    key=(run.turn,tuple(missing),len(state['hand']),len(state['own']))
    if missing and getattr(run,'nm_refresh_key',None)!=key:
        run.nm_refresh_key=key;run.observe('nightmare_local_reread');return
    text=' '.join(r['text'] for r in run.texts((1100,265,1230,380)))
    if not re.search('結束|结束',text):run.log('local_wait',field='end_turn_label');return
    # Re-read a final time: an animation/draw/resource change invalidates the
    # end-turn decision, while unchanged resources may be intentionally kept.
    run.observe('nightmare_end_turn_recheck')
    if run.v.turn(run.im)!='OUR':
        run.log('end_turn_replan',reason='phase_changed_before_end_turn');return
    fresh=run.snapshot()
    from .nightmare_search import fingerprint
    if fingerprint(N.normalize(fresh))!=fingerprint(N.normalize(state)):
        with run.metrics.span('decision'):fresh_plan=N.choose_action(fresh,run.nm_settings)
        if fresh_plan['action']['kind']!='end_turn':
            run.log('end_turn_replan',reason='fresh_state_has_action',action=fresh_plan['action']);return
        state=fresh;plan=fresh_plan
        run.log('end_turn_revalidated',reason='fresh_state_still_chooses_end_turn')
    run.log('end_turn_resource_check',check=plan.get('end_turn_check'),reason=plan.get('summary'))
    run.act('end_turn',[1166,325],delay=.8)
    point=run.button('^(結束|结束|確定|確認)$',(620,515,910,590))
    if point:run.act('confirm_end_turn',point,delay=1)
    run.wait_end_turn()


def audit_get(run):
    return None

def audit_event(run,kind,data):
    return None

def audit_observe(run):
    return None

def audit_fault(run,exc):
    return None

def audit_finish(run,summary):
    return None

def audit_enemy(run):
    return None
