"""Declarative sidecar rules. No eval/exec; baseline legality remains authoritative."""
import json
import math
from pathlib import Path

FIELDS = ('pp','max_pp','own_hp','enemy_hp','turn','enemy_count','own_count','hand_count','form','opponent_class')
OPS = ('eq','ne','lt','le','gt','ge')
ACTIONS = ('set_score','add_score','hold')

def validate(value):
    if not isinstance(value,dict) or set(value)-{'schema_version','cards'} or value.get('schema_version')!=1:
        raise ValueError('规则须为 schema_version=1，包含 cards 对象')
    if not isinstance(value.get('cards'),dict):raise ValueError('cards 必须是对象')
    for cid,rules in value['cards'].items():
        if not isinstance(cid,str) or not cid.isdigit() or not isinstance(rules,list):raise ValueError('卡牌 ID 或规则列表无效')
        for rule in rules:
            if not isinstance(rule,dict) or set(rule)-{'enabled','when','action','value','note'}:raise ValueError('未知规则字段')
            if type(rule.get('enabled',True)) is not bool:raise ValueError('enabled 必须为布尔值')
            if rule.get('action') not in ACTIONS:raise ValueError('动作仅支持 set_score / add_score / hold')
            v=rule.get('value',0)
            if type(v) not in (int,float) or not math.isfinite(v):raise ValueError('优先级必须为有限数值')
            if not isinstance(rule.get('note',''),str):raise ValueError('note 必须为文本')
            if not isinstance(rule.get('when',[]),list):raise ValueError('when 必须为条件列表')
            for c in rule.get('when',[]):
                if not isinstance(c,dict) or set(c)!={'field','op','value'} or c['field'] not in FIELDS or c['op'] not in OPS:
                    raise ValueError('条件字段或比较符无效')
                if c['field'] in ('form','opponent_class'):
                    if not isinstance(c['value'],str) or c['op'] not in ('eq','ne'):raise ValueError('文本条件只支持 eq/ne')
                elif type(c['value']) not in (int,float) or not math.isfinite(c['value']):raise ValueError('数值条件无效')
    return value

def load(path):
    p=Path(path)
    return validate(json.loads(p.read_text(encoding='utf-8-sig'))) if p.exists() else {'schema_version':1,'cards':{}}

def apply(candidates,state,rules):
    context={**state,'enemy_count':len(state.get('enemy',[])), 'own_count':len(state.get('own',[])), 'hand_count':len(state.get('hand',[]))}
    result=[]
    for original in candidates:
        card=dict(original);ctx={**context,'form':card.get('form')};held=False
        for rule in rules.get('cards',{}).get(str(card['id']),[]):
            if not rule.get('enabled',True):continue
            matched=True
            for cond in rule.get('when',[]):
                a=ctx.get(cond['field']);b=cond['value'];op=cond['op']
                if a is None:matched=False;break
                try:ok={'eq':lambda:a==b,'ne':lambda:a!=b,'lt':lambda:a<b,'le':lambda:a<=b,'gt':lambda:a>b,'ge':lambda:a>=b}[op]()
                except TypeError:ok=False
                if not ok:matched=False;break
            if not matched:continue
            action=rule['action']
            if action=='hold':held=True;break
            card['policy_score']=rule.get('value',0)+(card['policy_score'] if action=='add_score' else 0)
            card['policy_reason']+=' | user:'+action+':'+rule.get('note','')
        if not held:result.append(card)
    return sorted(result,key=lambda c:(c['policy_score'],c['cost']),reverse=True)
