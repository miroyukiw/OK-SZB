"""Editable ramp-dragon heuristics. Unknown local facts never invalidate the whole turn."""
from copy import deepcopy
RAMP={'10042310','10741310'}
ZOE='10444120';WILNAS='10444110';STORM='10644110';MODE='10944120'
GOLD='10844120';NINE='10744110';JUSTICE='10544110';RED='10642310';ABYSS='90044330'
ORACLE='10042310'
FAST_MULLIGAN_CLASSES={'royal','nightmare'}

def mulligan_plan(hand,opponent_class=None):
    """Seek the three-PP ramp into base Zoe; class exceptions keep cheap cards.

    GOLD is kept for its printed three-PP Accelerate, never its eight-PP body.
    An unread slot falls back locally and does not hold up the other slots.
    """
    result=[]
    for original in hand:
        card=dict(original);cid=card.get('card_id');cost=card.get('cost')
        if cid in (ORACLE,GOLD,ZOE):
            keep=True;reason='keep_three_pp_ramp_or_zoe'
        elif cid is None:
            keep=cost is None or cost<5;reason='unknown_identity_local_cost_fallback'
        elif opponent_class in FAST_MULLIGAN_CLASSES:
            keep=cost is None or cost<5;reason='fast_class_existing_low_cost_keep'
        else:
            keep=False;reason='seek_three_pp_ramp_and_zoe'
        card.update(keep=keep,reason=reason);result.append(card)
    return result

def val(u,k,default=0):return u.get(k) if u.get(k) is not None else default
def danger(state):return state.get('own_hp') is not None and sum(val(e,'attack') for e in state.get('enemy',[]))>=state['own_hp']
def targetable(e):return e.get('aura') is not True
def combat_targetable(e):return e.get('attackable') is not False and not e.get('intimidate') and not e.get('ambush')
def threat(e):return val(e,'attack')+val(e,'threat_level',1)*2+bool(e.get('ward'))*2

def ocean_summoned(units):
    """Printed trigger belongs to each Jellyfish already on the field."""
    for unit in units:
        if unit.get('card_id')=='10542120':unit.update(bane=True,rush=True,ready=True)

def ordered_cards(hand,pp,maximum,own_count,state=None,settings=None):
    if pp is None:return []
    s=state or {};cfg=settings or {};enemy=s.get('enemy',[]);hp=s.get('own_hp');ehp=s.get('enemy_hp')
    low=hp is not None and hp<=cfg.get('low_health',10);risk=danger(s)
    legal=[e for e in enemy if targetable(e)];wards=[e for e in enemy if e.get('ward') and combat_targetable(e)]
    face=sum(val(u,'attack') for u in s.get('own',[]) if u.get('ready') and u.get('can_face')
             and u.get('uid') not in s.get('attacked_uids',[]))
    candidates=[]
    for original in hand:
        c=dict(original);cid=c['id'];cost=c.get('cost')
        if cost is None or cost>pp or (c['kind']=='follower' and own_count>=5):continue
        score=20+cost*2;reason='playable_curve'
        ramp=cid in RAMP or (cid==GOLD and c['form']=='accelerate') or (cid==ZOE and c['form']=='base')
        if ramp and maximum is not None and maximum<10:
            score=(850 if maximum<7 else cfg.get('late_ramp_score',25))+(45 if cid==ZOE else 0);reason='ramp_zoe_body' if cid==ZOE else 'ramp'
        if ramp and maximum is not None and maximum>=7 and cid!=ZOE:
            score=cfg.get('late_ramp_score',25) if maximum<10 else 10
            reason='spare_pp_ramp_or_cycle'
        if cid==STORM:
            score=900 if pp>=8 and legal and (wards or risk) else 620
            reason='storm_then_red_removal' if pp>=8 and legal and (wards or risk) else 'storm_pressure'
        if cid==WILNAS:
            score=920 if maximum==7 else 510
            score+=min(len(legal),3)*18;reason='on_curve_wilnas_normal_evolution' if maximum==7 else 'wilnas_removal'
        if cid==MODE:
            score=980 if risk and sum(val(e,'health',99)<=4 for e in enemy)>=2 else 740 if low else 680 if len(enemy)>=cfg.get('wide_board',3) else 220
            reason='mode_sweep' if len(enemy)>=cfg.get('wide_board',3) and sum(val(e,'health',99)<=4 for e in enemy)>=2 else 'mode_heal' if low else 'mode_value'
        if cid==GOLD and c['form']=='base':
            # User deliberately reserves the 8-cost body for a four-damage lethal line.
            lethal=ehp is not None and (ehp<=4 or (all(val(w,'health',99)<=4 for w in wards) and ehp<=face+4))
            if not lethal:continue
            score=1400;reason='gold_four_damage_lethal'
        if cid==NINE:
            score=1100 if s.get('super_available') and not s.get('emblem_done') else 700 if len(enemy)>=3 else 300
            reason='nine_emblem_once' if s.get('super_available') and not s.get('emblem_done') else 'nine_board_clear'
        if cid==JUSTICE:
            healthy=hp is not None and hp>=cfg.get('healthy_health',12)
            preserve=risk or (hp is not None and hp<=cfg.get('fragile_health',4))
            score=1250 if preserve else 1040 if healthy and (s.get('ep_available') or s.get('super_available')) else 850 if low else 400
            reason='justice_hold_ward_heal' if preserve or not healthy else 'justice_evolve_face_eight'
        if cid==ZOE and c['form']=='enhance':
            # Enhancement is forced by available PP; don't collapse life to 1 casually.
            clear=any(x['id'] in (NINE,MODE,WILNAS) and val(x,'cost',99)<=pp for x in hand)
            lethal=ehp is not None and not wards and ehp<=face+5
            if not lethal and not(risk and not clear):continue
            score=1500;reason='emergency_zoe_no_evolution' if not lethal else 'zoe_storm_lethal'
        if cid==RED:
            if not legal or len(hand)<2:continue
            score=1000 if wards or risk else 600+max(threat(e) for e in legal)*4
            reason='red_destroy_legal_target_preserve_attackers'
        if cid=='10742310':score=780 if low else 35;reason='heal_draw' if low else 'cycle'
        if cid==ABYSS:score=1350 if ehp is not None and ehp<=1 else 780 if low else 100;reason='abyss_drain'
        if cid=='10843310':
            killable=[e for e in legal if val(e,'health',99)<=1]
            score=1400 if ehp is not None and ehp<=1 else 950 if any(e.get('ward') for e in killable) else 500 if killable else 40
            reason='one_damage_lethal_or_cleanup'
        if cid=='10543310':
            # Before Awakening this only damages followers. An empty opposing
            # board makes it a no-effect play, including when spending bonus PP.
            if not enemy and maximum is not None and maximum<7:continue
            score=1300 if ehp is not None and ehp<=2 and maximum and maximum>=7 else 650 if len(enemy)>=2 else 100;reason='random_removal_and_face'
        if cid=='90041130' and any(u.get('card_id')=='10542120' for u in s.get('own',[])):
            score=760 if wards or risk else 210;reason='enable_jellyfish_bane_rush'
        if cid=='10403120' and c['form']=='enhance':score=820 if own_count<=3 else 500;reason='enhance_draw_refund'
        c['policy_score']=score;c['policy_reason']=reason;candidates.append(c)
    return sorted(candidates,key=lambda c:(c['policy_score'],c['cost']),reverse=True)

def choose_effect_target(candidates,kind,state=None):
    if not candidates:return None
    damage=8 if kind==WILNAS else 1 if kind=='10843310' else 99
    def score(e):
        hp=val(e,'health',99);kill=hp<=damage or kind==RED
        return (bool(e.get('ward'))*16+threat(e)*2+kill*12-min(max(damage-hp,0),10)*.4, val(e,'attack'),hp)
    return max(candidates,key=score)

def choose_mode(state,settings=None):
    cfg=settings or {};enemy=state.get('enemy',[]);hp=state.get('own_hp')
    kills=sum(val(e,'health',99)<=4 for e in enemy)
    if kills>=2 or (danger(state) and kills):return 2
    if hp is not None and hp<=cfg.get('low_health',10):return 1
    return 2 if len(enemy)>=cfg.get('wide_board',3) else 1

def discard_rank(card,state):
    cid=card.get('id');cost=val(card,'cost',0)
    if cid=='10644120':return 1000
    if cid==ABYSS:return 950
    if cid==GOLD and val(state,'max_pp')>=7:return 800
    if cid==ZOE and val(state,'max_pp')>=9:return 650
    if cid in RAMP and val(state,'max_pp')>=9:return 600
    if cid==NINE and not state.get('emblem_done'):return -200
    if cid==WILNAS and val(state,'max_pp')<8:return -150
    return cost*5

def evolution_order(units,state,settings=None):
    healthy=state.get('own_hp') is not None and state['own_hp']>=(settings or {}).get('healthy_health',12)
    def score(u):
        cid=u.get('card_id');n=val(u,'attack')+bool(u.get('ready'))*5
        if cid==WILNAS and state.get('max_pp')==7:n+=500
        if cid==NINE and not state.get('emblem_done') and state.get('super_available'):n+=700
        if cid==JUSTICE and healthy:n+=600
        if cid==STORM and not state.get('enemy'):n+=400
        if cid==STORM and state.get('emblem_done'):n+=450
        if u.get('bane') and any(e.get('ward') for e in state.get('enemy',[])):n+=100
        return n
    return sorted([u for u in units if not u.get('no_evolve')],key=score,reverse=True)

def preferred_evolution(unit,state,available,settings=None):
    cid=unit.get('card_id');normal='evolve' in available;sup='super_evolve' in available
    reserve=not state.get('emblem_done') and any(c['id']==NINE for c in state.get('hand',[])) and state.get('sep_left',2)<=1
    if cid==NINE and not state.get('emblem_done'):return 'super_evolve' if sup else None
    if cid==NINE and state.get('emblem_done'):return 'evolve' if normal else None
    if cid==JUSTICE:
        lethal=state.get('enemy_hp') is not None and state['enemy_hp']<=8
        if not lethal and (danger(state) or state.get('own_hp') is None or state['own_hp']<(settings or {}).get('healthy_health',12)):return None
    if cid==WILNAS and state.get('max_pp')==7 and normal:return 'evolve'
    if cid==STORM and not state.get('enemy') and normal:return 'evolve'
    if sup and not reserve:return 'super_evolve'
    return 'evolve' if normal else None

def plan_attacks(state,attacked=(),beam_width=40):
    """Small beam search over legal attacks; combat Aura never blocks ordinary attacks."""
    own=[dict(u,_i=i) for i,u in enumerate(state.get('own',[]))
         if u.get('ready') and u.get('uid') not in attacked and (val(u,'attack')>0 or u.get('bane'))]
    enemy=[dict(e,_i=i) for i,e in enumerate(state.get('enemy',[]))]
    if not own:return None
    def pair_allowed(u,point):
        return not any(uid==u.get('uid') and abs(point[0]-xy[0])<38 and abs(point[1]-xy[1])<25
                       for uid,xy in state.get('blocked_attacks',[]))
    # A proven immediate face lethal wins before optional trades or card plays.
    # Current turn's sickness and already-consumed attacks are retained above.
    wards=[e for e in enemy if combat_targetable(e) and e.get('ward')]
    face_units=sorted([u for u in own if u.get('can_face') and val(u,'attack')>0 and pair_allowed(u,[640,80])],key=lambda u:val(u,'attack'),reverse=True)
    ehp=state.get('enemy_hp')
    if not wards and ehp is not None and sum(val(u,'attack') for u in face_units)>=ehp:
        sequence=[];damage=0
        for u in face_units:
            sequence.append({'source':u['xy'],'target':[640,80],'reason':'immediate_legal_face_lethal'})
            damage+=val(u,'attack')
            if damage>=ehp:break
        return {'source':face_units[0],'target':[640,80],'reason':'immediate_legal_face_lethal',
                'score':10000,'projected_face_damage':damage,'lethal':True,'sequence':sequence}
    initial_own=sum(val(u,'attack')+val(u,'health')*.25 for u in own)
    initial_enemy=sum(threat(e) for e in enemy);hp=state.get('own_hp');ehp=state.get('enemy_hp')
    def evaluate(o,e,damage):
        if ehp is not None and damage>=ehp:return 10000+damage
        removed=initial_enemy-sum(threat(t) for t in e)
        lost=initial_own-sum(val(u,'attack')+val(u,'health')*.25 for u in o)
        score=damage*3+removed*1.7-lost*1.1
        if hp is not None:
            incoming=sum(val(t,'attack') for t in e)
            if incoming>=hp:score-=100+(incoming-hp)*3
        return score
    beam=[(own,enemy,0,[],set())];best=None
    for _ in range(min(len(own),5)):
        expanded=[]
        for o,e,damage,path,used in beam:
            legal=[t for t in e if combat_targetable(t)]
            wards=[t for t in legal if t.get('ward')]
            for u in o:
                if u['_i'] in used:continue
                targets=wards or legal
                options=list(targets)+([None] if not wards and u.get('can_face') else [])
                for target in options:
                    if not pair_allowed(u,target['xy'] if target else [640,80]):continue
                    if target is None and val(u,'attack')<=0:continue
                    no=deepcopy(o);ne=deepcopy(e);nu=next(x for x in no if x['_i']==u['_i']);nd=damage
                    if target is None:nd+=val(u,'attack');point=[640,80];why='face_pressure'
                    else:
                        nt=next(x for x in ne if x['_i']==target['_i']);point=target['xy']
                        dead=bool(u.get('bane')) or val(u,'attack')>=val(nt,'health',99)
                        if nt.get('barrier'):dead=False;nt['barrier']=False
                        elif dead:ne.remove(nt)
                        else:nt['health']=val(nt,'health',99)-val(u,'attack')
                        if not u.get('super'):
                            if nt.get('bane') or val(nt,'attack')>=val(nu,'health',99):no.remove(nu)
                            else:nu['health']=val(nu,'health',99)-val(nt,'attack')
                        elif dead:nd+=1
                        why='bane_clear_ward_preserve_face' if u.get('bane') and target.get('ward') else 'clear_ward' if target.get('ward') else 'favorable_trade'
                    action={'source':u,'target':point,'reason':why}
                    np=path+[action];score=evaluate(no,ne,nd)
                    if ehp is not None and nd>=ehp:
                        # Do not improve a won line by adding needless trades.
                        score=10000-len(np)*10-sum(a['target']!=[640,80] for a in np)
                    row=(no,ne,nd,np,used|{u['_i']});expanded.append((score,row))
                    if best is None or score>best[0]:best=(score,row)
        if not expanded:break
        beam=[row for _,row in sorted(expanded,key=lambda x:x[0],reverse=True)
              if ehp is None or row[2]<ehp][:beam_width]
    if best is None:return None
    score,(o,e,damage,path,used)=best
    return {**path[0],'score':round(score,2),'projected_face_damage':damage,'sequence':[{'source':a['source']['xy'],'target':a['target'],'reason':a['reason']} for a in path]}


def hand_for_pp(hand,pp):
    """Project only the three known automatic cost modes when +1 crosses them."""
    result=[]
    for original in hand:
        c=dict(original);cid=c['id'];form=c.get('form','base')
        if cid==GOLD:new,cost,kind=('base',8,'follower') if pp>=8 else ('accelerate',3,'spell')
        elif cid==ZOE:new,cost,kind=('enhance',10,'follower') if pp>=10 else ('base',5,'follower')
        elif cid=='10403120':new,cost,kind=('enhance',8,'follower') if pp>=8 else ('base',2,'follower')
        else:result.append(c);continue
        if new!=form:c.update(form=new,cost=cost,expected_cost=cost,kind=kind)
        result.append(c)
    return result


def bonus_pp_plan(state,settings=None):
    pp=state.get('pp')
    if pp is None or not state.get('extra_pp',{}).get('enabled'):return None
    hand=hand_for_pp(state.get('hand',[]),pp+1)
    projected={**state,'pp':pp+1,'hand':hand}
    candidates=ordered_cards(hand,pp+1,state.get('max_pp'),len(state.get('own',[])),projected,settings)
    current=ordered_cards(state.get('hand',[]),pp,state.get('max_pp'),len(state.get('own',[])),state,settings)
    newly=[c for c in candidates if pp<val(c,'cost',99)<=pp+1]
    if not newly:return None
    chosen=newly[0]
    if current and current[0]['policy_score']>chosen['policy_score']:return None
    # On the non-fast opening, save the single early bonus for 2 -> 3 PP ramp.
    # A real lethal or an immediately dangerous board can still take priority.
    if (state.get('max_pp') is not None and state['max_pp']<3
            and state.get('opponent_class') not in FAST_MULLIGAN_CLASSES
            and not danger(state) and chosen['policy_score']<1300
            and chosen['policy_reason'] not in ('ramp','ramp_zoe_body')):
        return None
    return {'card':chosen,'before_pp':pp,'projected_pp':pp+1,'max_pp_unchanged':state.get('max_pp'),
            'reason':chosen['policy_reason']}

def target_for(unit,enemies,super_evolved=False,order=('attack','health')):
    # Compatibility for old real-frame checks; all new combat uses plan_attacks.
    wards=[e for e in enemies if e.get('ward')];candidates=wards or enemies
    if candidates and (wards or super_evolved or not unit.get('can_face',True)):
        return max(candidates,key=lambda e:tuple(val(e,k,-1) for k in order))['xy']
    return [640,80] if unit.get('can_face',True) else None
