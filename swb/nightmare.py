"""Visible-state Nightmare planning. Execute one action and observe again.

Only known deterministic effects are continued in search. Random unresolved
effects end that branch at reobservation; unknown counters disable only the
dependent benefit/action, never the match. Original Dragon policy is separate.
"""
from copy import deepcopy

BRUSH='10501110';CHARM='10052110';CUTE='10851120';MUMMY='10051130'
DEMON='10651110';RABBIT='10851130';CAPTAIN='10951110';BURST='10552310'
BAAL='10452130';ESCAPE='10651120';CONCERT='10753310';ANSE='10851110'
SILK='10853110';VICE='10953110';GARA='10954120';BAT='90051120';GHOST='90051130'
STATS={BRUSH:(1,1,1),CHARM:(1,1,1),CUTE:(1,1,1),MUMMY:(2,2,2),DEMON:(2,2,2),
 RABBIT:(2,1,1),CAPTAIN:(2,2,2),BURST:(2,0,0),BAAL:(3,2,2),ESCAPE:(3,3,2),
 CONCERT:(3,0,0),ANSE:(4,2,3),SILK:(4,3,3),VICE:(6,3,3),GARA:(8,8,8),BAT:(1,1,1),GHOST:(1,1,1)}

def value(obj,key,default=0):return obj.get(key) if obj.get(key) is not None else default
def cid(c):return c.get('id',c.get('card_id'))
def class_key(c):return {'forest':'elf','rune':'witch','portal':'nemesis','royal_guard':'royal'}.get(c,c)
def combat_legal(e):return not(e.get('ambush') or e.get('intimidate') or e.get('attackable') is False)
def target_legal(e):return not(e.get('aura') or e.get('ambush'))
def known_hp(s):return s.get('own_hp') is not None and s.get('enemy_hp') is not None

def mulligan_plan(hand,opponent_class=None,play_order=None,opponent_archetype=None):
    chosen=set();ones=[i for i,c in enumerate(hand) if cid(c) in (CHARM,CUTE,BRUSH)]
    # A standalone Brush is still a legal one-drop, but prefer a partner first.
    ones.sort(key=lambda i:cid(hand[i])==BRUSH)
    for i in ones[:2 if play_order=='second' else 1]:chosen.add(i)
    twos=[i for i,c in enumerate(hand) if cid(c) in (DEMON,CAPTAIN,MUMMY,RABBIT)]
    twos.sort(key=lambda i:(cid(hand[i])==RABBIT,cid(hand[i])==MUMMY))
    if twos:chosen.add(twos[0])
    if chosen and len(chosen)<3 and len(ones)>1 and cid(hand[ones[1]])==BRUSH:chosen.add(ones[1])
    cls=class_key(opponent_class)
    if ones and twos:
        preferences=(BAAL,ESCAPE) if cls in ('elf','nemesis') else (CONCERT,BAAL) if cls in ('royal','nightmare') else (BAAL,)
        for wanted in preferences:
            candidate=next((i for i,c in enumerate(hand) if cid(c)==wanted),None)
            if candidate is not None and len(chosen)<3:chosen.add(candidate);break
    out=[]
    for i,c in enumerate(hand):
        keep=i in chosen
        if cid(c) not in STATS:keep=c.get('cost') is None or c['cost']<=2
        out.append(dict(c,keep=keep,reason='nightmare_early_curve' if keep else 'seek_one_plus_effective_two'))
    return out

def normalize(state):
    s=deepcopy(state);s.setdefault('graveyard',None);s.setdefault('pp',None)
    s.setdefault('own_hp',None);s.setdefault('enemy_hp',None)
    s.setdefault('hand',[]);s.setdefault('own',[]);s.setdefault('enemy',[])
    # Saved audit states group these public flags; live states use top-level
    # keys. Preserve explicit defenses in both forms when replaying a decision.
    for key in ('enemy_damage_prevention','enemy_auto_heal','public_effects_complete'):
        if s.get(key) is None:s[key]=(s.get('public_effects') or {}).get(key)
    s['certain']=not(s.get('enemy_damage_prevention') or s.get('enemy_auto_heal'))
    if any(u.get('attack') is None or u.get('health') is None or cid(u) not in STATS for u in s['enemy']):
        # Unread values / unmodelled public cards lower proof certainty, not
        # action legality; normal pressure/trading still continues.
        s['certain']=False
    s['random_pending']=False;s['draws']=0;s['serial']=0;s['evolution_used']=bool(s.get('evolution_used'))
    # Current leader-only damage is not intercepted by enemy followers. Track
    # this proof separately from unknown board abilities/last words.
    s['leader_only_route']=True
    # Damage progress is independent of the opponent's unread remaining HP.
    # Each fresh search starts at zero; simulated actions accumulate it.
    s['enemy_damage_dealt']=0
    for i,c in enumerate(s['hand']):
        c.setdefault('iid',f'h{i}');c.setdefault('kind','spell' if cid(c) in (BURST,CONCERT) else 'follower')
        # A verified per-instance hand buff overrides printed attack. No cost
        # is fabricated here: a missing dynamic cost stays unknown.
        c.setdefault('attack',STATS.get(cid(c),(0,0,0))[1]+value(c,'attack_bonus'))
    for side in ('own','enemy'):
        for i,u in enumerate(s[side]):
            u.setdefault('uid',f'{side}{i}');u.setdefault('original_cost',STATS.get(cid(u),(None,0,0))[0])
            if cid(u)==ANSE:u['ignore_ward']=True
            if cid(u)==BAT:u['drain']=True
            if cid(u)==GHOST:u['ephemeral']=True
    for u in s['own']:
        if u['uid'] in s.get('attacked_uids',[]):u['ready']=False
    return s

def leader_damage(s,own=0,enemy=0):
    if own:
        if s['own_hp'] is None:return False
        s['own_hp']-=own
        if s['own_hp']<=0:return False
    if enemy:
        if s.get('enemy_damage_prevention'):s['certain']=False
        else:
            s['enemy_damage_dealt']=s.get('enemy_damage_dealt',0)+enemy
            if s['enemy_hp'] is not None:s['enemy_hp']-=enemy
    return True

def grave_add(s,n):
    if s.get('graveyard') is not None:s['graveyard']+=n

def add_hand(s,id):
    if len(s['hand'])>=9:grave_add(s,1);return
    s['serial']+=1;cost,attack,health=STATS[id]
    s['hand'].append({'id':id,'iid':f"new{ s['serial'] }",'cost':cost,'attack':attack,'kind':'follower'})

def deaths(s):
    # Combat damage is simultaneous; Drain resolves before the resulting
    # last words. A ghost is banished instead of entering the graveyard.
    for side in ('own','enemy'):
        dead=[u for u in s[side] if u.get('health') is not None and u['health']<=0]
        if dead:s['leader_only_route']=False
        for u in dead:
            s[side].remove(u)
            if side=='enemy':
                if u.get('last_words_unknown') or u.get('death_heal'):s['certain']=False
                continue
            if u.get('ephemeral') or cid(u)==GHOST:continue
            grave_add(s,1)
            if cid(u) in (CHARM,CUTE):add_hand(s,BAT)
            elif cid(u)==ESCAPE:add_hand(s,GHOST)
            elif cid(u)==DEMON:
                s['draws']+=1
                if not leader_damage(s,own=1):return False
    return True

def hit(u,n,stat_reduction=False):
    if stat_reduction:
        if u.get('health') is not None:u['health']-=n
    elif u.get('barrier'):u['barrier']=False
    elif not u.get('immune') and u.get('health') is not None:u['health']-=n

def summon(s,c):
    if len(s['own'])>=5:return None
    id=cid(c);cost,atk,hp=STATS.get(id,(c.get('cost'),0,1));s['serial']+=1
    u={'card_id':id,'uid':f"summon{s['serial']}",'attack':value(c,'attack',atk),
       'health':hp,'original_cost':cost,'ready':id in (ANSE,GHOST,ESCAPE),
       'can_face':id in (ANSE,GHOST),'storm':id in (ANSE,GHOST),'rush':id==ESCAPE,
       'ignore_ward':id==ANSE,'drain':id==BAT,'ephemeral':id==GHOST}
    s['own'].append(u);return u

def rabbit(s):
    if known_hp(s) and s['own_hp']>s['enemy_hp']:
        for _ in range(min(2,5-len(s['own']))):summon(s,{'id':BAT,'attack':1})

def evo_modes(s):
    if s.get('evolution_used'):return []
    turn=s.get('turn');second=s.get('play_order')=='second'
    if turn is None:return []
    out=[]
    if s.get('ep_available') and turn>=(4 if second else 5):out.append('evolve')
    if s.get('super_available') and turn>=(6 if second else 7):out.append('super_evolve')
    return out

def actions(s):
    out=[];pp=s.get('pp')
    if any(s.get(k) is not None and s[k]<=0 for k in ('own_hp','enemy_hp')):return out
    if pp is not None:
        for c in s['hand']:
            id=cid(c);cost=c.get('cost')
            if c['iid'] in s.get('failed_plays',[]):continue
            if id not in STATS or cost is None or cost>pp:continue
            if c['kind']=='follower' and len(s['own'])>=5:continue
            if id in (CAPTAIN,VICE) and (s['own_hp'] is None or s['own_hp']<=(2 if id==CAPTAIN else 3)):continue
            if id==BURST and (not known_hp(s) or s['own_hp']<=3 and s['own_hp']<=s['enemy_hp']):continue
            if id==CONCERT and not s['enemy'] and (s.get('graveyard') is None or s['graveyard']<6):continue
            options=[{}]
            if id==SILK:options=[{'target_uid':u['uid']} for u in s['enemy'] if target_legal(u)] or [{}]
            elif id==GARA:
                options=[{'mode':2}]
                if s['own_hp'] is not None and s['own_hp']>2:options.append({'mode':1})
            elif id==BAAL:options=[{'mode':1}]+([{'mode':2}] if s['enemy'] else [])
            out.extend({'kind':'play','iid':c['iid'],'card_id':id,**o} for o in options)
    wards=[e for e in s['enemy'] if e.get('ward') and combat_legal(e)]
    for u in s['own']:
        if not u.get('ready') or u.get('attack') is None or u['attack']<=0:continue
        if cid(u)==CUTE and (s['own_hp'] is None or s['own_hp']<=1):continue
        candidates=[e for e in (s['enemy'] if u.get('ignore_ward') or not wards else wards) if combat_legal(e)]
        if u.get('can_face') and (u.get('ignore_ward') or not wards):candidates=candidates+[None]
        for e in candidates:
            point=e.get('xy') if e else [640,80]
            if point and any(uid==u['uid'] and abs(point[0]-xy[0])<38 and abs(point[1]-xy[1])<25 for uid,xy in s.get('blocked_attacks',[])):continue
            out.append({'kind':'attack','uid':u['uid'],'target_uid':e['uid'] if e else None})
    for mode in evo_modes(s):
        for u in s['own']:
            if u.get('evolved') or u.get('super') or u.get('no_evolve'):continue
            if (mode,u['uid']) in s.get('failed_evolutions',[]):continue
            if cid(u)==VICE and mode=='super_evolve' and (s['own_hp'] is None or s['own_hp']<=1):continue
            targets=[{}]
            if cid(u)==SILK:targets=[{'hand_iid':c['iid']} for c in s['hand'] if c['kind']=='follower'] or [{}]
            out.extend({'kind':mode,'uid':u['uid'],**t} for t in targets)
    phase='before_turn6' if value(s,'turn')<6 else 'turn6_onward'
    if pp is not None and s.get('extra_pp',{}).get('enabled') and phase not in s.get('extra_pp_attempted_phases',[]) and not s.get('extra_used'):
        out.append({'kind':'extra_pp'})
    return out

def apply(s,action):
    s=deepcopy(s);kind=action['kind'];s['random_pending']=False
    leader_only=bool(s.get('leader_only_route'));s['leader_only_route']=leader_only and kind=='extra_pp'
    if kind=='extra_pp':s['pp']+=1;s['extra_used']=True;return s
    if kind=='play':
        c=next((c for c in s['hand'] if c['iid']==action['iid']),None)
        if c is None:return None
        s['hand'].remove(c);s['pp']-=c['cost'];id=cid(c);u=summon(s,c) if c['kind']=='follower' else None
        if id in (BURST,CAPTAIN):s['leader_only_route']=leader_only
        if id==BRUSH and any(x is not u and x.get('original_cost')==1 for x in s['own']+s['enemy']):u['attack']+=1;u['health']+=1
        elif id==MUMMY and s['graveyard'] is not None and s['graveyard']>=4:
            s['graveyard']-=4;u.update(storm=True,can_face=True,ready=True)
        elif id==RABBIT:rabbit(s)
        elif id==CAPTAIN:
            if not leader_damage(s,2,2):return None
        elif id==BURST:
            if not known_hp(s):return None
            low=min(s['own_hp'],s['enemy_hp'])
            if not leader_damage(s,3 if s['own_hp']==low else 0,3 if s['enemy_hp']==low else 0):return None
        elif id==SILK:
            target=next((e for e in s['enemy'] if e['uid']==action.get('target_uid')),None)
            if target:hit(target,3,stat_reduction=True)
        elif id==BAAL:
            if action['mode']==1:
                u['attack']+=1;u['health']+=1;others=[x for x in s['own'] if x is not u]
                if len(others)==1:others[0]['attack']=value(others[0],'attack')+1;others[0]['health']=value(others[0],'health',1)+1
                elif others:s['random_pending']=True
            elif len(s['enemy'])==1:hit(s['enemy'][0],3)
            elif s['enemy']:s['random_pending']=True;s['random_removal_value']=3
        elif id==CONCERT:
            # Six one-point assignments stop hitting destroyed units. Only a
            # single target or guaranteed full clear is predicted exactly.
            enemies=s['enemy'];need=sum(value(e,'health',99)+bool(e.get('barrier')) for e in enemies)
            if len(enemies)==1 and not enemies[0].get('immune'):
                n=6-int(bool(enemies[0].get('barrier')));enemies[0]['barrier']=False;hit(enemies[0],n)
            elif enemies and need<=6 and not any(e.get('immune') for e in enemies):
                for e in enemies:e['health']=0
            elif enemies:s['random_pending']=True;s['random_removal_value']=6
            if s['graveyard'] is not None and s['graveyard']>=6:
                s['graveyard']-=6
                if not leader_damage(s,enemy=2):return None
        elif id==VICE:
            for e in s['enemy']:hit(e,3)
            if not leader_damage(s,3,3):return None
        elif id==GARA:
            if action['mode']==1:
                u.update(storm=True,can_face=True,ready=True)
                if not leader_damage(s,own=2):return None
            else:
                u['ward']=True
                for e in s['enemy']:hit(e,8)
        if not deaths(s):return None
        if c['kind']=='spell':grave_add(s,1)
    elif kind in ('evolve','super_evolve'):
        u=next((x for x in s['own'] if x['uid']==action['uid']),None)
        if u is None:return None
        gain=3 if kind=='super_evolve' else 2
        u['attack']=value(u,'attack')+gain;u['health']=value(u,'health',1)+gain
        u['evolved']=True;u['super']=kind=='super_evolve';u['immune']=u['super'];s['evolution_used']=True
        # Evolution grants Rush but does not refresh a consumed attack.
        if u['uid'] not in s.get('attacked_uids',[]):u['ready']=True
        if cid(u)==RABBIT:rabbit(s)
        elif cid(u)==SILK:
            c=next((c for c in s['hand'] if c['iid']==action.get('hand_iid')),None)
            if c:c['attack']=value(c,'attack')+3
        elif cid(u)==VICE and kind=='super_evolve':
            s['draws']+=2
            if not leader_damage(s,1,1):return None
    elif kind=='attack':
        u=next((x for x in s['own'] if x['uid']==action['uid']),None)
        e=next((x for x in s['enemy'] if x['uid']==action.get('target_uid')),None)
        if u is None:return None
        u['ready']=False;s.setdefault('attacked_uids',[]).append(u['uid'])
        if cid(u)==CUTE and not leader_damage(s,1,1):return None
        if s['enemy_hp'] is not None and s['enemy_hp']<=0:return s
        damage=value(u,'attack')
        if e is None:
            if not leader_damage(s,enemy=damage):return None
        else:
            blocked=bool(e.get('barrier') or e.get('immune'));hit(e,damage)
            if u.get('bane') and not blocked:e['health']=0
            if not u.get('super'):
                own_blocked=bool(u.get('barrier') or u.get('immune'));hit(u,value(e,'attack'))
                if e.get('bane') and not own_blocked:u['health']=0
            if u.get('super') and e.get('health') is not None and e['health']<=0:
                if not leader_damage(s,enemy=1):return None
            if blocked:damage=0
        if u.get('drain') and s['own_hp'] is not None:s['own_hp']=min(s.get('max_hp',20),s['own_hp']+damage)
        if not deaths(s):return None
    return s

def end_turn(s):
    s=deepcopy(s)
    s['own']=[u for u in s['own'] if not u.get('ephemeral') and cid(u)!=GHOST]
    if s.get('own_hp') is not None and s['own_hp']<=12:
        for c in s['hand']:
            if cid(c)==GARA and c.get('cost') is not None:c['cost']=max(0,c['cost']-1)
    return s

def modeled_lethal(s):
    """A terminal candidate in the visible model, not proof of an actual win."""
    return (s.get('enemy_hp') is not None and s['enemy_hp']<=0
        and not(s.get('own_hp') is not None and s['own_hp']<=0)
        and not s.get('enemy_damage_prevention') and not s.get('enemy_auto_heal'))

def leader_damage_certain(s):
    return not(s.get('enemy_damage_prevention') or s.get('enemy_auto_heal')) and bool(s['certain'] or s.get('leader_only_route'))

def lethal_proven(s):
    return modeled_lethal(s) and leader_damage_certain(s)

def score(s,initial,path):
    if s.get('own_hp') is not None and s['own_hp']<=0:return -1e9
    if lethal_proven(s):
        return 1e6-len(path)*10-sum(a['kind']=='super_evolve' for a in path)
    # An unidentified enemy follower must not turn a lethal face-damage route
    # into an ordinary +15 trade-off against clearing the board. Try the best
    # modeled lethal one action at a time, with fresh observation; keep the
    # stronger certainty label false when effects remain unknown.
    if modeled_lethal(s):return 9e5-len(path)*10-sum(a['kind']=='super_evolve' for a in path)
    damage=(value(initial,'enemy_hp')-value(s,'enemy_hp') if initial.get('enemy_hp') is not None
            else s.get('enemy_damage_dealt',0)-initial.get('enemy_damage_dealt',0))
    incoming=sum(value(e,'attack') for e in s['enemy']);ward=sum(value(u,'health') for u in s['own'] if u.get('ward'))
    risk=max(0,incoming-ward);hp=s.get('own_hp');cls=class_key(s.get('opponent_class'))
    trade_weight=1.7 if cls in ('elf','royal','nightmare','nemesis') else 1.4
    removed=sum(value(e,'attack')+value(e,'threat_level',1) for e in initial['enemy'])-sum(value(e,'attack')+value(e,'threat_level',1) for e in s['enemy'])
    def board_value(u):
        attack=value(u,'attack');health=value(u,'health')
        # A fresh non-Storm glass cannon can be exchanged by a visible enemy
        # next turn. Its full attack is not already-realized damage.
        exposed=(not u.get('can_face') and not u.get('ward') and health>0 and
                 any(value(e,'attack')>=health for e in s['enemy']))
        survival=.5 if exposed else 1.
        return attack*1.7*survival+min(health,6)*.5+(1 if u.get('drain') else 0)
    result=damage*5+removed*trade_weight+sum(board_value(u) for u in s['own'] if not u.get('ephemeral'))
    if hp is not None:
        result+=min(hp,12)*.6
        if risk>=hp:result-=180+(risk-hp)*15
        elif risk>=hp-3:result-=30
    # Next-turn damage is a preference, never a current lethal component.
    future=end_turn(s);budget=min(10,value(s,'max_pp',value(s,'turn'))+1);future_grave=value(s,'graveyard');future_hp=hp
    for c in sorted(future['hand'],key=lambda c:value(c,'cost',99)):
        cost=c.get('cost');id=cid(c)
        if cost is None or cost>budget:continue
        storm=id in (ANSE,GHOST) or id==MUMMY and future_grave>=4 or id==GARA and future_hp is not None and future_hp>2
        if storm:
            result+=min(value(c,'attack'),max(0,value(s,'enemy_hp',20)))*1.2;budget-=cost
            if id==MUMMY:future_grave-=4
            if id==GARA:future_hp-=2
    result+=s.get('draws',0)*1.4+s.get('random_removal_value',0)*.65
    result-=sum(a['kind']=='super_evolve' for a in path)*2+sum(a['kind']=='extra_pp' for a in path)*3
    if value(initial,'turn')<=3 and value(initial,'enemy_hp',20)>8:
        symmetric_burn=sum(a['kind']=='play' and a.get('card_id')==BURST for a in path)
        self_loss=max(0,value(initial,'own_hp')-value(s,'own_hp'))
        if symmetric_burn and self_loss:result-=12*symmetric_burn
    for action in path:
        if action['kind'] not in ('evolve','super_evolve'):continue
        source=next((u for u in initial['own'] if u['uid']==action['uid']),None)
        if source and source['uid'] in initial.get('attacked_uids',[]) and cid(source) not in (RABBIT,SILK,VICE):
            result-=8
    result-=len(path)*.08
    return result

def choose_action(state,settings=None):
    from .nightmare_search import choose_action as search_routes
    return search_routes(state,settings)
