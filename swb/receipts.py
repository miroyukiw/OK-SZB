"""Evidence reports only. Unconfirmed effects never block unrelated next actions."""
import cv2
import numpy as np
import re

def attack_denial(v,image):
    rows=v.ocr(image,(260,140,1030,205))
    text=' '.join(r['text'] for r in rows)
    if not re.search('無法|无法|不能',text) and rows:
        # Brief dark toasts can be missed by the first OCR pass.
        from PIL import Image
        patch=np.asarray(image.crop((240,135,1050,220)))
        mono=cv2.cvtColor(patch,cv2.COLOR_RGB2GRAY)
        boosted=Image.fromarray(cv2.convertScaleAbs(mono,alpha=1.7,beta=10)).convert('RGB').resize((1620,170))
        text+=' '+' '.join(r['text'] for r in v.ocr(boosted))
    if re.search(r'(無法|无法).*(成為|成为).*(攻擊|攻击).*(目標|目标)',text):
        return {'kind':'target','text':text}
    if re.search(r'(無法|无法|不能).*(攻擊|攻击)',text):
        return {'kind':'source_or_rule','text':text}
    return None

def terminal_outcome(v,image,leader_refs=None):
    text=' '.join(r['text'] for r in v.ocr(image,(150,180,1130,570)))
    # A draw is only accepted on the existing result surface, never from
    # generic card text containing the English word 'draw'.
    if re.search(r'\b(DRAW|TIE)\b|平局|引き分け',text,re.I) and v.scene(image)[0]=='RESULT':
        return {'outcome':'draw','evidence':'explicit_draw_on_result_surface','text':text}
    if re.search(r'\b(WINS?|VICTORY|LOSE|DEFEAT)\b',text,re.I):
        return {'outcome':'win' if re.search(r'\b(WINS?|VICTORY)\b',text,re.I) else 'loss',
                'evidence':'terminal_text','text':text}
    if leader_refs and re.search(r'GAME.*SET',text,re.I):
        a=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
        own=float(cv2.matchTemplate(a[530:630,575:705],leader_refs['own'],cv2.TM_CCOEFF_NORMED).max())
        enemy=float(cv2.matchTemplate(a[0:105,575:705],leader_refs['enemy'],cv2.TM_CCOEFF_NORMED).max())
        verdict='loss' if own<.4 and enemy>.7 else 'win' if enemy<.4 and own>.7 else None
        if verdict:return {'outcome':verdict,'evidence':'GAME SET and defeated leader disappearance','own_presence':own,'enemy_presence':enemy}
    full=' '.join(r['text'] for r in v.ocr(image,(20,20,1250,700)))
    if re.search(r'\bRESULT\b',full,re.I) and re.search(r'返回(?:主[頁页]面|[樂乐][園园國国圓圆圈])|[對对][戰战戦][紀纪記记][錄录]|對戰通行證|对战通行证',full):
        if re.search(r'\bWINS?\b',full,re.I):
            return {'outcome':'win','evidence':'ranked_result_wins_badge','text':full}
        if re.search(r'獲得MP|获得MP',full) and re.search(r'返回(?:主[頁页]面|[樂乐][園园國国圓圆圈])',full):
            mp_gain=re.search(r'\+([1-9]\d*)\b',full)
            return {'outcome':'win' if mp_gain else 'loss','evidence':'ranked_result_mp_settlement','text':full}
    return None

def portrait(image,xy):
    x,y=xy
    return cv2.cvtColor(np.asarray(image.crop((x-22,y-65,x+22,y-20))),cv2.COLOR_RGB2GRAY)

def presence(before,after,xy):
    t=portrait(before,xy)
    a=cv2.cvtColor(np.asarray(after),cv2.COLOR_RGB2GRAY)
    y=xy[1]
    scores=cv2.matchTemplate(a[y-83:y,275:1050],t,cv2.TM_CCOEFF_NORMED)
    _,score,_,point=cv2.minMaxLoc(scores)
    return float(score),point[0]+275+22

def portrait_copies(image,board,template):
    """Count distinct detected units carrying the target portrait, not NCC peaks."""
    a=cv2.cvtColor(np.asarray(image),cv2.COLOR_RGB2GRAY)
    count=0
    for unit in board:
        x,y=unit['xy']
        patch=a[max(0,y-80):y-10,max(0,x-38):x+38]
        if patch.shape[0]<template.shape[0] or patch.shape[1]<template.shape[1]:continue
        if cv2.matchTemplate(patch,template,cv2.TM_CCOEFF_NORMED).max()>.85:count+=1
    return count


def attack_evidence(v,before,afters,target):
    if target==[640,80]:
        hp=v.enemy_leader_hp(before);values=[v.enemy_leader_hp(im) for im in afters]
        stable=len(values)>=2 and values[-1] is not None and values[-1]==values[-2]
        outcome='applied' if stable and hp is not None and values[-1]<hp else 'unconfirmed'
        if stable and hp is not None and values[-1]==hp:outcome='not_applied'
        return {'outcome':outcome,'evidence':'leader_health','before_hp':hp,'after_hp':values}
    old=v.board(before,'enemy')
    original=min(old,key=lambda u:abs(u['xy'][0]-target[0]),default=None)
    if original is None or abs(original['xy'][0]-target[0])>45:
        return {'outcome':'unconfirmed','evidence':'target_unreadable'}
    template=portrait(before,original['xy'])
    original_copies=portrait_copies(before,old,template)
    rows=[]
    for im in afters:
        score,x=presence(before,im,target);board=v.board(im,'enemy')
        # Similar dark portraits can correlate; exclude a match positively owned by another old unit.
        other_match=False
        for other in old:
            if abs(other['xy'][0]-target[0])<40:continue
            other_score,other_x=presence(before,im,other['xy'])
            if abs(other_x-x)<40 and other_score>score+.12:other_match=True
        if other_match:score=0.
        unit=min(board,key=lambda u:abs(u['xy'][0]-x),default=None)
        hp=unit['health'] if unit and abs(unit['xy'][0]-x)<38 and score>.65 else None
        rows.append({'presence':round(score,3),'health':hp,'count':len(board),'matched_other':other_match,
                     'target_portrait_copies':portrait_copies(im,board,template)})
    if len(rows)<2:return {'outcome':'unconfirmed','evidence':'insufficient_settled_frames','observations':rows}
    stable_hp=len(rows)>=2 and rows[-1]['health'] is not None and rows[-1]['health']==rows[-2]['health']
    absent=rows[-1]['presence']<.55 or all(r['presence']<.70 and r['health'] is None for r in rows)
    killed=absent and rows[-1]['count']<len(old) and (rows[-1]['count']==rows[-2]['count'] or (rows[-1]['target_portrait_copies']==0 and rows[-2]['target_portrait_copies']==0))
    # Identical copies cannot be assigned individually by maximum correlation.
    # A stable loss of exactly one copy AND one board unit proves a removal;
    # merely removing an unrelated unit does not satisfy this condition.
    copy_removed=original_copies>=2 and all(r['count']==len(old)-1 and
                 r['target_portrait_copies']==original_copies-1 for r in rows[-2:])
    damage=stable_hp and original['health'] is not None and rows[-1]['health']<original['health']
    unchanged=stable_hp and rows[-1]['health']==original['health'] and all(
        r['presence']>.75 and r['count']==len(old) for r in rows)
    outcome='applied' if killed or copy_removed or damage else 'not_applied' if unchanged else 'unconfirmed'
    return {'outcome':outcome,'evidence':'target_copy_removed' if copy_removed else 'target_disappeared' if killed else 'target_health',
            'target':target,'before_health':original['health'],'before_portrait_copies':original_copies,'observations':rows}
