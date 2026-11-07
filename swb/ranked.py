"""One-match ranked navigation. No loop can enqueue a second match."""
import re
import time
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from swb.vision import gray
from swb.deck_profile import is_nightmare, deck_name_matches, DECK_NAME_PATTERN

ROOT=Path(__file__).resolve().parents[1]

def ramp_selected(image):
    ref=Image.open(ROOT/'assets/scenes/RANKED_READY.png')
    box=(245,468,378,540)
    return float(cv2.matchTemplate(gray(image.crop((238,461,385,547))),
                 gray(ref.crop(box)),cv2.TM_CCOEFF_NORMED).max())>.85


def configured_selected(run):
    if getattr(run,'ranked_deck_confirmed_once',False):return True
    if ramp_selected(run.im):return True
    text=''.join(r['text'] for r in run.texts((185,400,695,718)))
    if re.search('尚未選擇|尚未选择|請選擇牌組|请选择牌组',text):return False
    if deck_name_matches(text):return True
    return False

def result_screen(vision,image,scene,ranked=False,terminal_known=False):
    if scene=='RESULT':return True
    if not ranked:return False
    # Ranked result contents differ from CPU; the RESULT title is shared.
    text=' '.join(r['text'] for r in vision.ocr(image,(430,20,850,115)))
    if re.search(r'\bRESULT\b',text,re.I):return True
    # WIN heads the settled ranked page too. Require its navigation controls;
    # battle WIN and the MP tally animation are not complete settlement pages.
    controls=' '.join(r['text'] for r in vision.ocr(image,(630,585,1270,710)))
    settled=bool(re.search('返回(?:主[頁页]面|[樂乐][園园國国圓圆圈])',controls) and re.search('[對对][戰战戦][紀纪記记][錄录]',controls))
    if not settled:return False
    # A mission toast can cover WIN. A previously observed terminal outcome
    # plus both settlement controls still proves completion, including turn 0.
    if terminal_known:return True
    rank_info=' '.join(r['text'] for r in vision.ocr(image,(600,140,1270,350)))
    if re.search(r'獲得MP|获得MP|MP[+\d]|Master|指定系列',rank_info,re.I):return True
    header=' '.join(r['text'] for r in vision.ocr(image,(430,0,850,145)))
    return bool(re.search(r'\b(WIN|LOSE|VICTORY|DEFEAT)\b',header,re.I))


def rematch_button(run):
    """The blue ranked battle button on a fully settled result page."""
    rank_text=''.join(re.sub(r'\s+','',r['text']) for r in run.texts((600,140,1270,350)))
    if not re.search(r'獲得MP|获得MP|MP[+\d]|Master|指定系列',rank_text,re.I):return None
    pixels=np.asarray(run.im.convert('RGB'))[350:585,1000:1270].astype(int)
    blue=((pixels[:,:,2]-pixels[:,:,0]>35)&(pixels[:,:,1]-pixels[:,:,0]>20)&(pixels[:,:,2]>120)).mean()
    if blue<=.15:return None
    rows=run.texts((1000,350,1270,585))
    candidates=[r for r in rows if re.search(r'再[戰战戦]|[對对][戰战戦]',re.sub(r'\s+','',r['text']))]
    if len(candidates)==1:
        return run.center(candidates[0])
    disc=np.asarray(run.im.convert('RGB'))[410:575,1030:1195].astype(int)
    disc_blue=((disc[:,:,2]-disc[:,:,0]>35)&(disc[:,:,1]-disc[:,:,0]>20)&(disc[:,:,2]>120)).mean()
    if disc_blue>.32:
        return [1111,493]
    return None


def rematch_ready(run,scene):
    """Do not interpret the preceding game's fading WIN as this game's result."""
    if scene in ('RANKED_READY','RANKED_SETUP','RANKED_DECK_PICK','PRACTICE_DECK_CONFIRM'):
        run.started=False;run.ranked_queue_attempted=False;run.rematch_pending=False
        run.log('rematch_preparation',scene=scene,frame=run.frame)
        return True
    if scene=='MULLIGAN' or run.v.turn(run.im) in ('OUR','ENEMY'):
        run.rematch_pending=False;run.last_progress=time.monotonic()
        run.log('rematch_entered_new_match',scene=scene,frame=run.frame)
        return True
    if time.monotonic()-run.rematch_began>120:
        raise RuntimeError('Ranked rematch did not reach a new match or preparation page within 120 seconds')
    return False


def navigate(run,scene):
    if run.started:return False
    nightmare=is_nightmare(getattr(run,'deck_profile','needle_dragon'))
    deck='ドラゴン'
    name=point=None
    if scene=='UNKNOWN':
        u_text=''.join(re.sub(r'\s+','',r['text']) for r in run.texts((0,0,1280,720)))
        if '使用以下牌組進行遊玩' in u_text or ('確認牌組' in u_text and ('決定' in u_text or '决定' in u_text)):
            scene='PRACTICE_DECK_CONFIRM'
        elif '選擇牌組' in u_text and ('指定系列' in u_text or '快龙' in u_text):
            scene='RANKED_DECK_PICK'
        elif '階級對戰' in u_text and ('尚未選擇牌組' in u_text or '請選擇牌組' in u_text or '使用牌組' in u_text):
            scene='RANKED_SETUP'
        elif re.search(r'私人[對对][戰战]|不重返.*[主頁主页][面]',u_text):
            scene='DISMISS_PRIVATE_REENTRY'
        elif re.search(r'SHADOWVERSE|WORLDS:BEYOND|點擊即顯示|点击即显示|TaptoStart',u_text,re.I) and 'Ver1.' in u_text:
            scene='TITLE_SCREEN'
        elif re.search(r'[對对][戰战]?[綜综]合[專专][區区]',u_text) or (re.search(r'[綜综]合[專专][區区]',u_text) and re.search(r'[對对][戰战]桌',u_text)):
            scene='PARK_HUB'
        elif re.search(r'[綜综]合[專专][區区]',u_text) or ('賽事' in u_text and ('觀看' in u_text or '課題' in u_text or '訊息' in u_text or '果匙' in u_text)):
            scene='PARK_WORLD'
    if scene in ('RANKED_READY','RANKED_SETUP'):
        if configured_selected(run):
            run.ranked_deck_verified=True
            if getattr(run.args,'ranked_preview',False):
                run.preview_ready=True;run.finished=True
                run.log('ranked_preview_ready',frame=run.frame,deck=deck,matchmaking_started=False)
                return True
            if getattr(run,'ranked_queue_attempted',False):return False
            run.ranked_queue_attempted=True
            run.log('ranked_queue_commit',frame=run.frame,limit=1,deck=deck)
            run.act('start_ranked',[1149,470],delay=3)
            run.started=True;run.last_progress=time.monotonic();return True
        name,point='ranked_choose_deck',[285,500]
    elif scene=='RANKED_DECK_PICK':
        rows=run.texts((80,200,1200,650))
        row=next((r for r in rows if deck_name_matches(r['text'])),None)
        name,point='ranked_select_existing_ramp',(run.center(row) if row else [273,305])
    elif scene=='PRACTICE_DECK_CONFIRM':
        text=' '.join(r['text'] for r in run.texts())
        if not (deck_name_matches(text) or re.search('使用以下牌[組组]',re.sub(r'\s+','',text))):
            run.log('local_missing',field='configured_deck_confirmation',expected=deck);return False
        run.ranked_deck_confirmed_once=True
        name,point='ranked_confirm_deck',[767,552]
    elif scene=='BATTLE_MENU':name,point='open_ranked_setup',[450,350]
    elif scene=='DISMISS_PRIVATE_REENTRY':
        rows=run.texts((350,450,650,650))
        btn=next((r for r in rows if '不重返' in r['text']),None)
        name,point='dismiss_private_reentry',(run.center(btn) if btn else [512,556])
    elif scene=='TITLE_SCREEN':
        name,point='tap_title_to_start',[640,360]
    elif scene=='HOME':
        rows=run.texts((760,360,1190,605))
        row=next((r for r in rows if re.search('階級|阶级',r['text'])),None)
        if row:name,point='open_ranked',run.center(row)
    elif scene=='PARK_WORLD':name,point='open_park_hub',[1167,173]
    elif scene=='PARK_HUB':name,point='open_ranked_from_park',[646,305]
    elif scene=='RESULT' or (scene=='UNKNOWN' and result_screen(run.v,run.im,scene,True)):
        context=getattr(run.args,'ranked_rematch_from',None)
        if (not getattr(run.args,'ranked_preview',False) and context
                and context.get('deck_verified') and context.get('deck_profile')==getattr(run,'deck_profile',None)):
            point=rematch_button(run)
            if not point and not getattr(run,'ranked_queue_attempted',False):
                for _ in range(3):
                    time.sleep(.5)
                    run.observe('rematch_button_wait',_network_check=False)
                    point=rematch_button(run)
                    if point:break
            if point and not getattr(run,'ranked_queue_attempted',False):
                # The new batch slot and runner already exist. Keep the same
                # final input guard as an ordinary ranked queue operation.
                run.ranked_deck_verified=True;run.ranked_queue_attempted=True;run.rematch_pending=True
                run.rematch_began=time.monotonic()
                run.log('ranked_queue_commit',frame=run.frame,limit=1,deck=deck,
                        entry='settlement_rematch',previous_run=context['previous_run'])
                run.act('start_ranked',point,delay=3,entry='settlement_rematch')
                run.started=True;run.last_progress=time.monotonic();return True
        name,point='return_home',[1145,635]
    elif scene=='REWARD_RECEIVED':name,point='dismiss_reward',[640,653]
    elif scene=='GUILD_EVENT':name,point='close_event',[1175,72]
    elif scene in ('SOLO','PRACTICE_MENU','PRACTICE_SETUP','PRACTICE_READY','PRACTICE_OWN_READY'):
        point=run.button('^(主頁面|主页面)$',(550,590,730,715))
        if point:name='ranked_return_home'
    if not name:return False
    if run.last_nav_scene==scene and time.monotonic()-run.last_nav_at<7:
        time.sleep(.7);return True
    run.nav_repeat[name]+=1
    if run.nav_repeat[name]>4:raise RuntimeError('Ranked navigation did not advance: '+name)
    run.last_nav_scene=scene;run.last_nav_at=time.monotonic()
    run.act(name,point,delay=3);run.last_progress=time.monotonic();return True
