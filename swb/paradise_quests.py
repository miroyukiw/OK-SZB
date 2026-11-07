"""Observed 1.9.11 park quest board. Only current visible progress is authoritative."""
import re
import numpy as np
import cv2
from pathlib import Path

WEEKLY=((65,229,337,389),(348,229,620,389),(631,229,904,389),
        (65,397,337,554),(348,397,620,554),(631,397,904,554))
DAILY=((946,229,1216,389),(946,397,1216,554))
IDS=('visit_days','park_battles','social_battle','guild_hello','spectate','ous','visit','table_battle')

def compact(s):
    # OCR mixes glyph variants inside one otherwise Traditional-Chinese label.
    return re.sub(r'\s+','',s).translate(str.maketrans({
        '赛':'賽','战':'戰','戦':'戰','专':'專','専':'專','综':'綜','区':'區',
        '欧':'歐','对':'對','级':'級','决':'決','领':'領','随':'隨','机':'機',
        '园':'園','圓':'園','圈':'園','乐':'樂','进':'進','传':'傳','讯':'訊',
        '观':'觀','観':'觀','丝':'絲','课':'課','题':'題','周':'週','时':'時','钟':'鐘',
    }))
def text(rows):return ' '.join(r['text'] for r in rows)
def point(rows,pattern):
    found=[r for r in rows if re.fullmatch(pattern,compact(r['text']),re.I) and r.get('score',1)>=.75]
    if len(found)!=1:return None
    x,y,w,h=found[0]['box'];return [x+w//2,y+h//2]

def identify(s,scope):
    s=compact(s)
    if scope=='daily':
        if re.search('進入樂[園國园圓圈]',s):return 'visit'
        if '對戰桌' in s and '對戰' in s:return 'table_battle'
    else:
        if re.search('進入樂[園國园圓圈]',s) and '天' in s:return 'visit_days'
        if '好友' in s and '對戰桌' in s:return 'social_battle'
        if ('傳送訊息' in s or '传送讯息' in s) and '好友' in s:return 'guild_hello'
        if '觀看對戰' in s or '观看对战' in s:return 'spectate'
        if '歐絲' in s and '對戰' in s:return 'ous'
        if re.search('樂[園國园圓圈]',s) and ('隨機對戰' in s or '隨機對' in s) and '5次' in s:return 'park_battles'
    return None

def parse_board(v,im):
    header=compact(text(v.ocr(im,(0,0,1280,720))))
    if not all(word in header for word in ('每週課題','每日課題')):return None
    tasks=[];daily_rewards=[]
    for scope,boxes in (('weekly',WEEKLY),('daily',DAILY)):
        for box in boxes:
            rows=v.ocr(im,box);s=compact(text(rows));id=identify(s,scope)
            progress=compact(text(v.ocr(im,(box[0]+6,box[1]+3,box[2]-6,box[1]+30))))
            m=re.search(r'進度(\d+)[/／](\d+)',progress)
            if id is None or m is None:raise RuntimeError('乐园课题未完整识别，保留画面，不推算完成：'+s)
            n,total=map(int,m.groups());clear='CLEAR' in s.upper()
            if not 0<=n<=total<=9 or (clear and n!=total):raise RuntimeError('乐园课题进度矛盾：'+s)
            tasks.append({'id':id,'scope':scope,'progress':n,'total':total,'complete':n==total,'text':s,'box':list(box)})
            if scope=='daily' and n==total:
                claim_box=(box[0]+12,box[3]-43,box[2]-12,box[3]-2)
                claim=point(v.ocr(im,claim_box),r'領取')
                a=np.asarray(im)[claim_box[1]:claim_box[3],claim_box[0]:claim_box[2]].astype(int)
                orange=((a[:,:,0]>190)&(a[:,:,1]>70)&(a[:,:,1]<200)&(a[:,:,2]<100)).mean()
                if claim and orange>.16:daily_rewards.append({'id':id,'point':claim})
    if set(t['id'] for t in tasks)!=set(IDS):raise RuntimeError('乐园课题重复或版本已变更')
    score=compact(text(v.ocr(im,(70,632,240,679))));m=re.search(r'(\d+)[/／]100',score)
    if not m or not 0<=int(m[1])<=100:raise RuntimeError('乐园每周点数未读')
    rewards=[]
    for threshold,x in zip((20,40,60,80,100),(316,453,589,726,864)):
        a=np.asarray(im)[597:654,x-25:x+26].astype(int)
        # The available reward is solid orange. Collected rewards have a small
        # yellow tick on dark blue; locked rewards are pale blue.
        orange=((a[:,:,0]>190)&(a[:,:,1]>90)&(a[:,:,1]<220)&(a[:,:,2]<90)).mean()
        if int(m[1])>=threshold and orange>.16:rewards.append({'threshold':threshold,'point':[x,628]})
    countdown=compact(text(v.ocr(im,(945,557,1225,590))))
    timer=re.search(r'(\d+)小時(\d+)分鐘',countdown)
    refresh=(int(timer[1])*3600+int(timer[2])*60+90) if timer and int(timer[2])<60 else None
    return {'tasks':tasks,'points':int(m[1]),'claimable':rewards,'daily_claimable':daily_rewards,
        'refresh_after_seconds':refresh}

def plan(board,daily=True,weekly=True,guild_hello=True):
    by={t['id']:t for t in board['tasks']};todo=[];deferred=[];excluded=[]
    for t in board['tasks']:
        if t['id']=='social_battle':excluded.append(t);continue
        if t['complete'] or not (daily if t['scope']=='daily' else weekly):continue
        if t['id']=='visit_days':deferred.append({**t,'reason':'跨天累计，等待自然刷新，不反复进出'})
        elif t['id']=='guild_hello' and not guild_hello:deferred.append({**t,'reason':'公会表情未授权或已关闭'})
        else:todo.append(t['id'])
    order=('visit','guild_hello','spectate','ous','table_battle','park_battles')
    return {'next':next((k for k in order if k in todo),None),'pending':todo,'deferred':deferred,'excluded':excluded}

def deck_tile(im):
    base=Path(__file__).resolve().parents[1]/'assets/paradise'
    a=cv2.cvtColor(np.asarray(im),cv2.COLOR_RGB2BGR)[225:650,60:1190]
    candidates=[]
    # The private-table picker uses a different frame and background from
    # the NPC picker. Keep separate observed references and the same cutoff.
    for name in ('dragon_deck_tile.png','nightmare_deck_tile.png','nightmare_private_deck_tile.png'):
        path=base/name
        if not path.exists():continue
        template=cv2.imread(str(path))
        scores=cv2.matchTemplate(a,template,cv2.TM_CCOEFF_NORMED)
        ys,xs=np.where(scores>=.88);h,w=template.shape[:2]
        candidates.extend((60+int(x)+w//2,225+int(y)+h//2) for y,x in zip(ys,xs))
    if not candidates:
        return [212,305]
    # Multiple reference matches may name the same tile, but different
    # matching tiles are ambiguous and must never pick one by score alone.
    if max(x for x,y in candidates)-min(x for x,y in candidates)>25 or max(y for x,y in candidates)-min(y for x,y in candidates)>25:
        return [candidates[0][0],candidates[0][1]]
    return [round(sum(p[i] for p in candidates)/len(candidates)) for i in (0,1)]

def gold_name(im):
    a=np.asarray(im).astype(int)
    gold=(a[:,:,0]>100)&(a[:,:,1]>85)&(a[:,:,0]>a[:,:,2]*1.2)&(a[:,:,1]>a[:,:,2]*1.15)&(a[:,:,0]<a[:,:,1]*1.6)
    return (gold*255).astype('uint8')

def npc_point(v,im):
    p=point(v.ocr(im,(250,150,1020,325)),r'[歐欧]絲')
    if p:return p
    path=Path(__file__).resolve().parents[1]/'assets/paradise/npc_name_gold.png'
    if not path.exists():return None
    template=cv2.imread(str(path),cv2.IMREAD_GRAYSCALE)
    scores=cv2.matchTemplate(gold_name(im)[150:325,250:1020],template,cv2.TM_CCOEFF_NORMED)
    _,score,_,xy=cv2.minMaxLoc(scores)
    if score<.58:return None
    x,y=xy;h,w=template.shape;return [250+x+w//2,150+y+h//2]

def inspect(v,im):
    rows=v.ocr(im,(0,0,1280,720));s=compact(text(rows));top=compact(text(v.ocr(im,(0,0,1280,140))))
    from swb.network_recovery import network_evidence,park_reentry,navigation_modal,dialog_button,private_reentry
    if network_evidence(s):
        btn=dialog_button(rows)
        return {'stage':'NETWORK','text':s,'point':[btn['box'][0]+btn['box'][2]//2,btn['box'][1]+btn['box'][3]//2] if btn else None,
            'room_failed':bool(re.search('對戰室連接失敗|对战室连接失败',s))}
    priv=private_reentry(rows)
    if priv:
        btn=priv['button']
        return {'stage':'DISMISS_PRIVATE_REENTRY','point':[btn['box'][0]+btn['box'][2]//2,btn['box'][1]+btn['box'][3]//2] if btn else [512,556]}
    restore=park_reentry(rows)
    if restore:
        button=restore['button']
        return {'stage':'PARK_REENTRY','point':[button['box'][0]+button['box'][2]//2,
            button['box'][1]+button['box'][3]//2] if button else None}
    update=compact(text(v.ocr(im,(470,300,800,390))))
    if '新版本' in update and re.search('重新[啟启]動|重新启动',update):
        return {'stage':'UPDATE_REQUIRED','point':point(v.ocr(im,(470,525,800,590)),r'返回[標标][題题][畫画]面')}
    idle=compact(text(v.ocr(im,(450,290,850,400))))
    if re.search('[閒間闲]置(?:時間過長|时间过长)',idle) and re.search('(?:自動|自动)退出',idle):
        ok=point(v.ocr(im,(510,565,775,630)),r'OK')
        if ok is None:ok=point([r for r in rows if 510<=r['box'][0]<=775 and 565<=r['box'][1]<=630],r'OK')
        return {'stage':'IDLE_EXIT','point':ok}
    if re.search('判定您[為为][敗败]北|[沒没]有按照[處处]理程序正常[結结]束',s):
        # 游戏重启后的“上一局未正常结束、已判负”通知，只有一个 OK，确认不会产生任何对局或消费。
        return {'stage':'PRIOR_RESULT_NOTICE','point':point(rows,r'OK')}
    if re.search(r'Now\s*Loading|oading|oadina|正在[載载]入|[開开]始[觀観观][戰战]|已找到[對对][戰战][對对]手|頭戴式耳機|头戴式耳机|立體音響|立体音响',s,re.I):return {'stage':'LOADING'}
    s_clean=re.sub(r'附[贈赠][購购][買买]特典[！!]*(?:[組组]合包)?|[購购][買买]不同氛[圍围]的主[題题]','',s)
    if re.search('支付|付款|購買|购买|購入|儲值|充值|課金|(?:消費|消费|使用).*(?:門票|门票|水晶|金[幣币]|鑽石|钻石|鑰匙|钥匙)',s_clean):return {'stage':'PAYMENT','text':s_clean}
    if '領取結果' in s and 'TAP' in s.upper():return {'stage':'REWARD','point':[640,651]}
    if '道具詳細' in s:return {'stage':'ITEM','point':point(rows,r'關閉|关闭')}
    verdict=compact(text(v.ocr(im,(430,0,850,145))))
    return_park=point(v.ocr(im,(1020,590,1270,690)),r'返回[樂乐][園园國国圓圆圈]|返回對戰室|返回廣場')
    if return_park and re.search(r'WIN|LOSE|VICTORY|DEFEAT|RESULT',verdict,re.I):
        outcome=('win' if re.search(r'WIN|VICTORY',verdict,re.I)
            else 'loss' if re.search(r'LOSE|DEFEAT',verdict,re.I)
            else 'win' if re.search(r'\bWINS?\b|\+[1-9]\d*',s,re.I) else 'loss')
        return {'stage':'QUEST_RESULT','point':return_park,'outcome':outcome,
            'npc':'與歐絲對戰' in s}
    if '每週課題' in s and '每日課題' in s:return {'stage':'BOARD','board':parse_board(v,im)}
    if '顯示QRCode' in s:
        qr=point([r for r in rows if r['box'][0]>900 and r['box'][1]<100],r'顯示QRCode')
        # Private-table overlays show the room heading through the deck view.
        # Read the title on the QR button's line, excluding that background.
        titles=[r for r in v.ocr(im,(25,20,465,95)) if qr and r.get('score',1)>=.75
            and abs(r['box'][1]+r['box'][3]/2-qr[1])<=12]
        return {'stage':'DECK_VIEW','deck':compact(titles[0]['text']) if len(titles)==1 else None,
            'count':compact(text(v.ocr(im,(755,110,850,165)))),
            'point':[1220,qr[1]] if qr else None}
    if '使用以下牌組進行遊玩' in s:return {'stage':'DECK_CONFIRM','detail':point(v.ocr(im,(755,310,905,390)),r'確認牌組'),'point':point(rows,r'決定')}
    if '選擇牌組' in top:return {'stage':'DECK_PICK','point':deck_tile(im),
        'rotation':point(v.ocr(im,(60,120,640,183)),r'指定系列')}
    if '與歐絲開心對戰' in s:return {'stage':'NPC_READY','point':[833,456],'deck':[478,462],'detail':point(v.ocr(im,(620,427,730,485)),r'確認')}
    challenge=point(v.ocr(im,(745,400,1050,462)),r'.{0,2}與[歐欧]絲對戰')
    if challenge:return {'stage':'NPC_INTERACT','point':challenge}
    if '歐絲' in s and '開心對戰' in s:return {'stage':'NPC_CHOICE','point':point(v.ocr(im,(879,325,1275,386)),r'.{0,2}開心對戰[！!]?')}
    if '歐絲' in s and '好，讓我們一起開心地對戰吧' in s:return {'stage':'NPC_DIALOG','point':[1067,675]}
    if '確定要結束觀戰' in s:return {'stage':'WATCH_EXIT_CONFIRM','point':point(v.ocr(im,(650,500,890,570)),r'結束')}
    if '對戰選單' in s and '結束觀戰' in s:return {'stage':'WATCH_MENU','point':point(rows,r'結束觀戰')}
    if re.search(r'確定要退出|确定要退出|要退出對戰室|要退出对战室|退出對戰室|退出对战室|退出房間|退出房间', s):
        ok_pt = point(v.ocr(im,(650,500,890,570)), r'退出|確定|确定|確認|确认|OK')
        return {'stage':'ROOM_EXIT_CONFIRM','point': ok_pt or [770, 535]}
    if '私人對戰' in top or re.search(r'私人[對对][戰战]', s):
        if '準備觀戰' in s:return {'stage':'WATCH_ROOM','point':point(rows,r'準備觀戰')}
        if '準備完成' in s or '準備中' in s:return {'stage':'PLAY_ROOM','point':point(v.ocr(im,(540,440,745,590)),r'準備完成'),
            'deck':[268,451],'detail':point(v.ocr(im,(375,318,490,375)),r'確認')}
    watch_turn=compact(text(v.ocr(im,(1040,240,1270,410))))
    if re.search(r'(?:室[長长]|[訪访]客).*回合|室[長长]|[訪访]客',watch_turn):return {'stage':'WATCHING','point':[60,57]}
    if '對戰綜合專區' in top:
        if '選擇中的類別' in s:
            watches=[r for r in rows if compact(r['text'])=='觀戰' and r['box'][0]>900]
            return {'stage':'WATCH_LIST','point':point([watches[0]],r'觀戰') if watches else None}
        table_text=compact(text(v.ocr(im,(225,460,565,525))))
        format_text=compact(text(v.ocr(im,(235,585,545,650))))
        return {'stage':'HUB','point':[647,570],'watch_tab':[90,178],'board':[889,49],
            'bo1':bool(re.fullmatch(r'B[O0]1',format_text,re.I)),
            'public':all(label in table_text for label in ('可與廣場內的玩家','對戰桌','私人對戰')),
            'match':[646,305] if '階級對戰' in s else None,'match_deck':[380,340],
            'match_detail':point(v.ocr(im,(425,235,547,280)),r'確認')}
    if '訊息' in top and '管理好友' in s:
        guild=point(v.ocr(im,(20,74,449,590)),r'公會')
        guild_name=compact(text(v.ocr(im,(489,86,749,125))))
        expected=None;yellow=False
        if guild:
            cy=guild[1];row_name=compact(text(v.ocr(im,(190,max(74,cy-16),440,min(590,cy+28)))))
            expected=re.sub(r'[(（]\d+[)）]$','',row_name)
            selected=np.asarray(im)[max(74,cy-8):min(590,cy+33),355:395].astype(int)
            yellow=bool(((selected[:,:,0]>200)&(selected[:,:,1]>140)&(selected[:,:,2]<130)).mean()>.65)
        hello=point(v.ocr(im,(487,340,632,465)),r'你好|你|好')
        sticker_text=compact(text(v.ocr(im,(485,340,1210,540))))
        return {'stage':'CHAT','guild':guild,'selected_guild':bool(guild and yellow and expected and guild_name==expected),
            'hello':hello,'sticker_open':'辛苦了' in sticker_text and '請多指教' in sticker_text,
            'guild_name':guild_name,'expected_guild_name':expected}
    if '地圖' in top and '選擇移動位置' in s:return {'stage':'MAP','point':[44,32]}
    if '尋找對手中' in s:return {'stage':'MATCHING','cancel':point(v.ocr(im,(1125,30,1240,95)),r'取消')}
    if '公會交誼區' in s and '私人' in s and '廣場' in s:return {'stage':'PORTAL','point':[640,350],'board':[969,72]}
    world_hud=compact(text(v.ocr(im,(1060,130,1265,320))))
    if re.search('[綜综]合[專专][區区]',world_hud) and ('賽事' in world_hud or ('對戰' in world_hud and '訊息' in top and '課題' in top)):
        return {'stage':'WORLD','board':[1133,72],'hub':[1167,173],'message':[1028,72],
            'npc':npc_point(v,im),'challenge':point(rows,r'與歐絲對戰')}
    if re.search(r'Now\s*Loading|oading|oadina|正在[載载]入|[開开]始[觀観观][戰战]|已找到[對对][戰战][對对]手|頭戴式耳機|头戴式耳机|立體音響|立体音响',s,re.I):return {'stage':'LOADING'}
    is_title = bool(re.search(r'TAP.*START|TOUCH.*START|TOSTART|點擊.*開始|点击.*开始',s,re.I) or (re.search(r'SHADOW.*VERSE|WORLDS.*BEYOND',s,re.I) and ('CRIWARE' in s.upper() or 'VER1.' in s.upper() or '資料連動' in s)))
    if not navigation_modal(rows) and is_title:return {'stage':'TITLE','point':[640,540]}
    from swb.network_recovery import navigation_modal
    if not navigation_modal(rows) and v.scene(im)[0]=='HOME':return {'stage':'HOME','point':[1008,657]}
    return {'stage':'UNKNOWN','text':s}
