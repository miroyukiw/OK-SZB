"""Budgeted route comparison; all projections describe the state known at decision time."""
import time
from . import nightmare as N

STRATEGY_VERSION='nightmare-routes-2.3.0'
DECK_VERSION='sugong40-official-20261017-v1'

def delta(before,after,key):
    return after.get(key)-before.get(key) if before.get(key) is not None and after.get(key) is not None else None

def category(line,before,after):
    if not line:return '保留资源'
    if N.lethal_proven(after):return '确定斩杀'
    if N.modeled_lethal(after):return '模型致死候选'
    if before.get('own_hp') is not None and sum(N.value(e,'attack') for e in before['enemy'])>=before['own_hp'] and len(after['enemy'])<len(before['enemy']):return '防守'
    if any(a['kind']=='attack' and a.get('target_uid') is not None for a in line):return '交换'
    if (N.value(before,'enemy_hp')>N.value(after,'enemy_hp') or
            after.get('enemy_damage_dealt',0)>before.get('enemy_damage_dealt',0)):return '抢血'
    if len(after['own'])>len(before['own']) or any(a['kind']=='play' for a in line):return '展开'
    if any(a['kind'] in ('evolve','super_evolve') for a in line):return '进化准备'
    return '资源调度'

def effects(before,after,line):
    face=delta(before,after,'enemy_hp');hp=delta(before,after,'own_hp')
    modeled_face=-face if face is not None else after.get('enemy_damage_dealt',0)-before.get('enemy_damage_dealt',0)
    removed=[{'uid':e['uid'],'card_id':N.cid(e),'attack':e.get('attack'),'health':e.get('health')} for e in before['enemy'] if not any(x['uid']==e['uid'] for x in after['enemy'])]
    lost=[u['uid'] for u in before['own'] if not any(x['uid']==u['uid'] for x in after['own'])]
    future=N.end_turn(after);budget=min(10,N.value(after,'max_pp',N.value(after,'turn'))+1);hand_potential=0;future_grave=N.value(after,'graveyard');future_hp=after.get('own_hp')
    for c in sorted(future['hand'],key=lambda c:N.value(c,'cost',99)):
        cost=c.get('cost');cid=N.cid(c)
        if cost is None or cost>budget:continue
        storm=cid in (N.ANSE,N.GHOST) or cid==N.MUMMY and future_grave>=4 or cid==N.GARA and future_hp is not None and future_hp>2
        if storm:
            hand_potential+=N.value(c,'attack');budget-=cost
            if cid==N.MUMMY:future_grave-=4
            if cid==N.GARA:future_hp-=2
    board_potential=sum(N.value(u,'attack') for u in future['own'])
    incoming=sum(N.value(e,'attack') for e in after['enemy']);ward=sum(N.value(u,'health') for u in after['own'] if u.get('ward'))
    grave_spent=0;pp_spent=0;cursor=before
    for action in line:
        if action['kind']=='end_turn':break
        if action['kind']=='play':
            card=next((c for c in cursor['hand'] if c['iid']==action['iid']),{})
            pp_spent+=N.value(card,'cost')
            need=4 if N.cid(card)==N.MUMMY else 6 if N.cid(card)==N.CONCERT else 0
            if need and cursor.get('graveyard') is not None and cursor['graveyard']>=need:grave_spent+=need
        cursor=N.apply(cursor,action)
        if cursor is None or cursor.get('random_pending'):break
    return {'face_damage':{'certain':-face if face is not None and N.leader_damage_certain(after) else None,'modeled':modeled_face},
        'lethal':{'certain':N.lethal_proven(after),'modeled':N.modeled_lethal(after),'basis':'leader_only_damage' if after.get('leader_only_route') else 'modeled_sequence'},
        'own_hp_change':hp,'own_hp_after':after.get('own_hp'),
        'board':{'enemy_removed':removed,'own_lost':lost,'own_preserved':[u['uid'] for u in before['own'] if any(x['uid']==u['uid'] for x in after['own'])],'own_after':[{k:u.get(k) for k in ('uid','card_id','attack','health','ward','ready')} for u in after['own']],
                 'enemy_after':[{k:e.get(k) for k in ('uid','card_id','attack','health','ward')} for e in after['enemy']]},
        'resources':{'pp_spent':pp_spent,'graveyard_spent':grave_spent,'ep_spent':sum(a['kind']=='evolve' for a in line),'sep_spent':sum(a['kind']=='super_evolve' for a in line),'pp_net':-delta(before,after,'pp') if delta(before,after,'pp') is not None else None,
            'graveyard_net':delta(before,after,'graveyard'),'manual_evolution':any(a['kind'] in ('evolve','super_evolve') for a in line),
            'extra_pp':any(a['kind']=='extra_pp' for a in line),'hand_used':[a['iid'] for a in line if a['kind']=='play']},
        'next_turn':{'damage_range':[0,hand_potential+board_potential],'hand_potential':hand_potential,'surviving_board_potential':board_potential,
            'assumptions':['留场能存活','下回合费用与目标允许；对方未知行动可改变结果'],'guaranteed_damage':0},
        'risk':{'visible_enemy_attack':incoming,'ward_health_estimate':ward,'visible_pressure_after_ward_estimate':max(0,incoming-ward),
            'hidden_damage':'unknown','lethal_pressure':not N.lethal_proven(after) and after.get('own_hp') is not None and max(0,incoming-ward)>=after['own_hp']},
        'random':{'pending':after.get('random_pending',False),'branch':'结算后重读；不指定随机命中目标' if after.get('random_pending') else None},
        'unknown_draws_not_credited':after.get('draws',0)}

def explain(row):
    e=row['effects'];parts=[]
    if row['category']=='模型致死候选':parts.append('优先尝试可见模型致死路线；未知效果保留，执行后确认实际结果')
    face=e['face_damage']['certain']
    if row['category']=='确定斩杀':parts.append('当前直伤或已建模路线足以结束对战')
    if face:parts.append(f'可见效果下打脸{face}')
    elif e['face_damage']['modeled'] and face is None:
        parts.append(f"已建模打脸伤害{e['face_damage']['modeled']}；未知剩余HP或效果不作斩杀结论")
    if e['own_hp_change']:parts.append(f"己方HP变化{e['own_hp_change']:+}，结算后{e['own_hp_after']}")
    removed=e['board']['enemy_removed']
    if removed:
        threat=sum(x.get('attack') or 0 for x in removed)
        parts.append(f"移除目标{[x['uid'] for x in removed]}，减少可见攻击压力{threat}，保留己方{e['board']['own_preserved']}，己方损失{len(e['board']['own_lost'])}个随从")
    if row['category']=='保留资源':parts.append('保留手牌/进化；已比较当前合法动作，未执行动作不算收益')
    if row['category']!='确定斩杀':parts.append(f"下回合伤害估计0～{e['next_turn']['damage_range'][1]}，需要留场/费用/目标条件")
    if e['risk']['lethal_pressure']:parts.append('仍有可见反杀压力')
    if e['random']['pending']:parts.append('随机结算后必须重规划')
    return '；'.join(parts)

def fingerprint(s):
    return repr((s['pp'],s['own_hp'],s['enemy_hp'],s.get('enemy_damage_dealt',0),s['graveyard'],s['evolution_used'],s.get('extra_used'),s.get('ep_available'),s.get('super_available'),s.get('extra_pp',{}).get('enabled'),
        s.get('enemy_damage_prevention'),s.get('enemy_auto_heal'),s.get('public_effects_complete'),s.get('certain'),s.get('leader_only_route'),
        [(N.cid(c),c['iid'],c.get('cost'),c.get('attack')) for c in s['hand']],
        [[(u['uid'],u.get('attack'),u.get('health'),u.get('ready'),u.get('barrier'),u.get('super'),u.get('can_face'),u.get('ward')) for u in s[k]] for k in ('own','enemy')]))

def choose_action(state,settings=None):
    settings=settings or {};start=time.monotonic();budget=max(.001,settings.get('time_budget_ms',900)/1000)
    deadline=start+budget;initial=N.normalize(state);width=settings.get('beam_width',32);limit=settings.get('max_nodes',2400);depth_limit=settings.get('max_depth',15)
    best=(N.score(initial,initial,[]),initial,[]);pool=[best];beam=[(initial,[])];nodes=0;truncated=set();found=False;depth=0
    for depth in range(1,depth_limit+1):
        expanded=[]
        for s,path in beam:
            for a in N.actions(s):
                if time.monotonic()>=deadline:truncated.add('time_budget');break
                if nodes>=limit:truncated.add('node_budget');break
                nxt=N.apply(s,a);nodes+=1
                if nxt is None:continue
                line=path+[a];value=N.score(nxt,initial,line);row=(value,nxt,line);pool.append(row)
                if value>best[0]:best=row
                if value>=999000:best=row;found=True;break
                if nxt.get('enemy_hp') is not None and nxt['enemy_hp']<=0:continue
                if not nxt['random_pending']:expanded.append(row)
                else:truncated.add('random_reobservation_boundary')
            if found or truncated.intersection(('time_budget','node_budget')):break
        if found or truncated.intersection(('time_budget','node_budget')) or not expanded:break
        seen=set();beam=[]
        for value,s,line in sorted(expanded,key=lambda r:-r[0]):
            sig=fingerprint(s)
            if sig in seen:continue
            seen.add(sig)
            if len(beam)>=width:truncated.add('beam_pruning');continue
            beam.append((s,line))
        if not beam:break
    else:
        if beam:truncated.add('depth_budget')
    unknown=[k for k in ('pp','own_hp','enemy_hp','graveyard') if initial.get(k) is None]
    if unknown:truncated.add('unknown_state')
    if not initial['certain']:truncated.add('unmodelled_public_effect_or_unread_stat')
    # Keep one best route for each first-action/intent, then up to three
    # representative alternatives including the strongest discarded route.
    rows=[];seen=set()
    for value,s,line in sorted(pool,key=lambda r:-r[0]):
        cat=category(line,initial,s);key=(cat,repr(line[0] if line else None))
        if key in seen:continue
        seen.add(key);rows.append({'line':line,'category':cat,'score':round(value,3),'_state':s})
    selected_line=best[2];modeled=bool(selected_line) and N.modeled_lethal(best[1]);selected=next(r for r in rows if r['line']==selected_line)
    rejected=[r for r in rows if r is not selected];alternatives=rejected[:1]
    for r in rejected[1:]:
        if len(alternatives)>=3:break
        if r['category'] not in {selected['category'],*(a['category'] for a in alternatives)}:alternatives.append(r)
    for r in rejected:
        if len(alternatives)>=3:break
        if r not in alternatives:alternatives.append(r)
    for row in [selected,*alternatives]:
        row['effects']=effects(initial,row.pop('_state'),row['line']);row['reason']=explain(row)
    legal=N.actions(initial)
    end_check={'attack_options':[a for a in legal if a['kind']=='attack'],'pp':initial['pp'],
        'play_options':[a for a in legal if a['kind']=='play'],'evolution_options':[a for a in legal if a['kind'] in ('evolve','super_evolve')],
        'reason':selected['reason'],'allowed_to_preserve':True}
    return {'strategy_version':STRATEGY_VERSION,'deck_version':DECK_VERSION,
        'action':selected_line[0] if selected_line else {'kind':'end_turn'},'line':selected_line,'certain_lethal':found,'modeled_lethal':modeled,
        'score':selected['score'],'nodes':nodes,'selected':selected,'alternatives':alternatives,'alternatives_basis':'已搜索路线中的最强被弃方案及代表类别；预算不完整时不代表全局最优',
        'search':{'status':'certain_lethal_found' if found else 'modeled_lethal_candidate' if modeled else 'search_incomplete' if truncated else 'complete_no_lethal_in_known_model',
            'elapsed_ms':round((time.monotonic()-start)*1000,2),'budget_ms':int(budget*1000),'nodes':nodes,'depth':depth,
            'incomplete_reasons':sorted(truncated),'unknown_fields':unknown,'proof_scope':'已读生命、费用与纯主战者直伤；不依赖敌方随从能力与亡语' if found and best[1].get('leader_only_route') else '当前可见已建模效果；不含未知抽牌和留场存活'},
        'end_turn_check':end_check if not selected_line else None,'summary':selected['reason']}
