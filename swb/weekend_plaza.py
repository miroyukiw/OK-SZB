"""16-player weekend plaza flow. Closed events stop; one event per start."""
import hashlib
import re
import time
from datetime import datetime,timedelta,timezone
import numpy as np
from run import Run,ROOT
from swb.adb_transport import AdbTransport
from swb.atomic_io import write_json
from swb.grand_prix import compact,joined,center,find
from swb.network_recovery import network_evidence,navigation_modal,dialog_button,RecoveryBlocked,publish_reconnect

MODE='weekend_plaza'
LOCAL=timezone(timedelta(hours=8))
CARD_BOXES=((35,120,530,240),(35,245,530,365),(35,370,530,490))
PAYMENT=r'消[費费]|支付|付款|購買|购买|購入|使用.*(?:金[幣币]|水晶|鑽石|钻石|門票|门票)|必要.*(?:金[幣币]|水晶)'
def next_open(card_rows,now=None):
    now=now or datetime.now(LOCAL);future=[]
    for rows in card_rows:
        for row in rows:
            if row['box'][0]>=280 or row.get('score',0)<.8:continue
            for m in re.finditer(r'(20\d{2})/(\d{1,2})/(\d{1,2})\s*(\d{2}):(\d{2})',row['text']):
                try:stamp=datetime(*map(int,m.groups()),tzinfo=LOCAL)
                except ValueError:continue
                if stamp>now:future.append(stamp)
    return min(future).isoformat() if future else None

def blue_control(im,rows,pattern):
    candidates=[]
    for row in rows:
        if row.get('score',0)<.8:continue
        if not re.fullmatch(pattern,compact(row['text']),re.I):continue
        x,y,w,h=row['box'];cx,cy=center(row)
        if not(200<=cx<=1240 and 330<=cy<=700):continue
        a=np.asarray(im)[max(0,y-12):min(720,y+h+12),max(0,x-22):min(1280,x+w+22)].astype(int)
        if a.size and ((a[:,:,2]-a[:,:,0]>30)&(a[:,:,1]-a[:,:,0]>15)&(a[:,:,2]>110)).mean()>.08:
            candidates.append(row)
    return center(candidates[0]) if len(candidates)==1 else None

def actual_entry(v,im,rows,text):
    """Controls observed on the live 1.9.11 weekend event, before registration."""
    s=compact(text)
    if '顯示QRCode' in s or '显示QRCode' in s:return None
    if '領取結果' in s and '已領取以下道具' in s:
        return {'stage':'REWARD_RECEIPT','point':find(v.ocr(im,(500,610,780,665)),r'OK'),'event_reward':False}
    if re.search('即將結束[賽赛]事並返回廣場',s) and re.search('無法再回到[賽赛]事廣場',s):
        return {'stage':'EXIT_CONFIRM','point':find(v.ocr(im,(650,565,880,625)),r'OK')}
    if '離週末廣場賽結束還有' in s and '回顧閉幕式' in s:
        return {'stage':'FINISHED','point':find(v.ocr(im,(640,165,890,220)),r'移動至廣場'),'closing_room':True}
    ok=find(v.ocr(im,(520,565,760,625)),r'OK')
    if '由於對戰對手棄權' in s and '已判定您為勝利' in s:
        return {'stage':'WALKOVER','point':ok,'outcome':'win'}
    if '對戰已結束' in s and '請等待其他參賽者結束對戰' in s:
        return {'stage':'BATTLE_END','point':ok}
    ready=find(v.ocr(im,(520,565,760,625)),r'準備完成')
    if '對戰開始確認' in s and ready:
        return {'stage':'READY','point':ready,'round':None,'verified':False}
    if '尋找對手中' in s and '待機人數' in s and re.search(r'\d+/16',s):
        return {'stage':'WAITING','round':None,'players':16,'verified':False}
    if '請等待其他玩家結束對戰' in s:
        return {'stage':'WAITING','round':None,'verified':False}
    if '廣場賽戰績' in s:
        match=re.search(r'第([1-4])戰的戰績',compact(joined(v.ocr(im,(835,140,1045,200)))))
        complete=bool(re.search(r'剩[餘余]對戰場數0戰',s) and re.search(r'0組對戰中',s))
        return {'stage':'FINISHED' if complete else 'STANDINGS','round':int(match[1]) if match else None,'point':[44,32]}
    if '舉辦完畢' in s and re.search('敬請期待下次[賽赛]事',s) and re.search('已[參参]加',s):
        return {'stage':'ALREADY_PARTICIPATED','point':None}
    speaker=compact(joined(v.ocr(im,(580,520,700,575))))
    if speaker=='歐絲' and ('這場賽事最重要的目的' in s or '在於讓大家享受闇影詩章' in s):
        return {'stage':'INTRO','point':[640,620]}
    if '僅限報名1次' in s and '最多實施至第4戰' in s and '16人' in s:
        return {'stage':'ENTRY_CONFIRM','point':find(v.ocr(im,(650,565,880,625)),r'報名')}
    # The deck confirmation modal obscures the format label behind it.
    if '使用以下牌組進行遊玩' in s and '確認牌組' in s:
        return {'stage':'DECK_CONFIRM','confirm':find(v.ocr(im,(760,270,910,350)),r'確認牌組'),
            'point':find(v.ocr(im,(650,565,880,625)),r'決定')}
    if re.search('廣場[賽赛]',s) and '指定系列' in compact(joined(v.ocr(im,(570,400,800,520)))):
        detail=compact(joined(v.ocr(im,(550,395,1260,600))))
        point=find(v.ocr(im,(1020,520,1260,590)),r'移動')
        if '週末廣場賽：16人' in detail and point:
            return {'stage':'EVENT_TRAVEL','point':point,'verified':True,'players':16}
    # The selector lists both Rotation and Unlimited 16-player events. The
    # blue hexagon at the lower right is the observed Rotation format badge.
    header=compact(joined(v.ocr(im,(20,20,950,115))))
    if re.search('廣場[賽赛]',header):
        cards=[v.ocr(im,box) for box in CARD_BOXES]
        if all(re.search(r'已[參参]加',compact(joined(card))) for card in cards):
            return {'stage':'ALREADY_PARTICIPATED','point':None}
        choices=[]
        for box,card in zip(CARD_BOXES,cards):
            words=compact(joined(card))
            if not re.search('(?:週末|周末).*16人',words):continue
            a=np.asarray(im)[box[1]+82:box[1]+115,270:305].astype(int)
            rotation=((a[:,:,2]-a[:,:,0]>35)&(a[:,:,2]-a[:,:,1]>15)&(a[:,:,2]>110)).mean()>.65
            if rotation:choices.append([(box[0]+box[2])//2,(box[1]+box[3])//2])
        if len(choices)==1:return {'stage':'EVENT_SELECT','point':choices[0],'players':16,'format_badge':'rotation'}
    if re.search('週末廣場賽舉辦中',s) and '私人' in s and '公會交誼區' in s:
        return {'stage':'PLAZA_PORTAL','point':[640,355]}
    event=find(v.ocr(im,(1060,220,1265,325)),r'[賽赛]事')
    if event and '綜合專區' in s and '對戰' in s:return {'stage':'PLAZA_WORLD','point':event}
    # Read event identity from its fixed details panel, never infer current
    # round from the header's "第4戰（16人）" (the tournament's size/rules).
    rules=compact(joined(v.ocr(im,(730,140,1245,295))))
    if '指定系列' in rules and '16人' in rules and '單一牌組BO1' in rules and '週末' in s:
        free=compact(joined(v.ocr(im,(1080,545,1200,625))))
        choose=find(v.ocr(im,(280,600,465,665)),r'.*[擇择]牌[組组]')
        from swb.deck_profile import DECK_NAME_PATTERN
        if choose is None:choose=find(v.ocr(im,(280,600,465,665)),DECK_NAME_PATTERN)
        confirm=find(v.ocr(im,(280,600,465,665)),r'確認牌組')
        if confirm is None:confirm=find(v.ocr(im,(375,535,500,580)),r'確認')
        return {'stage':'REGISTER' if free=='免費報名' else 'EVENT_DETAIL','players':16,'verified':True,
            'round':None,'point':[1140,590] if free=='免費報名' else None,'choose':choose,'confirm':confirm,
            'actual_signup':True}
    return None

def inspect(v,im,scene=None,owned=False,now=None):
    rows=v.ocr(im,(0,0,1280,720));text=joined(rows);s=compact(text)
    state={'stage':'UNKNOWN','text':text}
    header=compact(joined(v.ocr(im,(20,20,950,115))))
    if network_evidence(text):return {**state,'stage':'NETWORK'}
    if navigation_modal(rows) and re.search(PAYMENT,s):return {**state,'stage':'PAYMENT_BLOCKED'}
    if re.search(r'顯示QR|显示QR',text,re.I):
        return {**state,'stage':'DECK_PREVIEW','deck':compact(joined(v.ocr(im,(35,35,465,95)))),
            'count':compact(joined(v.ocr(im,(755,112,845,153)))),'point':[1228,48]}
    entry=actual_entry(v,im,rows,text)
    if entry:return {**state,**entry}
    if re.search('選[擇择]牌[組组]',header):
        from swb.deck_profile import DECK_NAME_PATTERN
        point=find(v.ocr(im,(80,210,1200,645)),DECK_NAME_PATTERN) or [1007,568]
        if point:return {**state,'stage':'DECK_PICK','point':[1007,568]}
    if re.search('[領领]取結果|领取结果',s) and re.search(r'\bTAP\b',text,re.I):
        receipt=compact(joined(v.ocr(im,(320,20,960,165))))
        return {**state,'stage':'REWARD_RECEIPT','point':[640,651],
            'event_reward':bool(re.search('[領领]取(?:了)?(?:週末|周末)?[廣广][場场][賽赛](?:的)?[獎奖][勵励]',receipt))}
    sizes={int(m[1]) for m in re.finditer(r'(?<!\d)(4|8|16|32)人',s)}
    target=16 in sizes and not({4,8,32}&sizes) and any('16人' in compact(r['text']) and r.get('score',0)>=.85 for r in rows)
    plaza=bool(re.search('廣場[賽赛]|广场赛|ロビー大会',s))
    if plaza and re.search(PAYMENT,s):return {**state,'stage':'PAYMENT_BLOCKED'}
    rotation='指定系列' in s or 'Rotation' in text
    weekend=bool(re.search('週末|周末',s))
    round_match=re.search(r'第([1-4])[輪轮戰战回戦](?:合)?',s)
    round_no=int(round_match[1]) if round_match else None
    if plaza and re.search('廣場[賽赛]|广场赛',header):
        card_rows=[v.ocr(im,box) for box in CARD_BOXES]
        scheduled=[bool(re.search('[將将][於于]以下[時时]段[舉举][辦办瓣]',compact(joined(card)))) for card in card_rows]
        if all(scheduled):return {**state,'stage':'CLOSED','next_open':next_open(card_rows,now),'players_verified':False}
        choices=[]
        for box,card,closed in zip(CARD_BOXES,card_rows,scheduled):
            words=compact(joined(card))
            if closed or not re.search('(?:週末|周末).*16人|16人.*(?:週末|周末)',words):continue
            if re.search('無限制|无限制|私人|Private',words,re.I):continue
            choices.append({'point':[(box[0]+box[2])//2,(box[1]+box[3])//2],'label':words})
        if len(choices)==1:return {**state,'stage':'EVENT_SELECT',**choices[0],'players':16}
        # Only the selector page can conclude that no suitable event is available.
        if sum('週末' in compact(joined(card)) or '周末' in compact(joined(card)) for card in card_rows)>=2:
            return {**state,'stage':'NO_16_EVENT','reason':'未找到唯一可报名的16人周末广场赛'}
    if plaza and sizes and not target:return {**state,'stage':'SIZE_UNVERIFIED' if sizes=={16} else 'WRONG_SIZE','sizes':sorted(sizes)}
    if re.search('私人廣場|私人广场|プライベート',s):return {**state,'stage':'WRONG_EVENT'}
    verified=plaza and weekend and target and rotation
    footer=v.ocr(im,(200,540,1270,710))
    finished=bool(re.search('本(?:次|場|场).*賽事(?:已)?結束|本(?:次|场).*赛事(?:已)?结束|大[賽赛](?:已)?結束|賽事結果|赛事结果|最[終终]排名|最[終终]結果|最終结果',s))
    if (verified or owned and plaza) and finished:
        return {**state,'stage':'FINISHED','players':16 if target else None,'round':round_no,
            'point':blue_control(im,footer,r'[領领]取[獎奖][勵励]|返回[競竞]技[場场]|返回[廣广][場场]|關閉|关闭|確[認认定]')}
    controls=blue_control(im,footer,r'返回(?:[廣广][場场](?:[賽赛])?|[賽赛]事|[據据][點点])|下一(?:[輪轮]|回合)|繼續|继续')
    terminal_header=bool(re.search(r'\b(?:WIN|LOSE|RESULT|VICTORY|DEFEAT)\b',joined(v.ocr(im,(430,0,850,125))),re.I))
    if owned and controls and terminal_header and not re.search('Revenants|獲得MP|获得MP',s,re.I):
        return {**state,'stage':'ROUND_RESULT','point':controls,'round':round_no}
    if verified or owned and plaza:
        ready=blue_control(im,rows,r'準備完畢|准备完毕|準備完成|准备完成|開始對戰|开始对战|對戰開始|对战开始')
        confirm=blue_control(im,rows,r'確認牌組|确认牌组|確認|确认')
        if ready:return {**state,'stage':'READY','round':round_no,'point':ready,'players':16 if target else None,'verified':verified,'confirm':confirm}
        if re.search('等待(?:其他|對手|对手|參賽|参赛|開始|开始)|報名完成|报名完成|已報名|已报名|配對中|匹配中|對戰準備中|对战准备中',s):
            return {**state,'stage':'WAITING','round':round_no,'players':16 if target else None,'verified':verified,'confirm':confirm}
        register=blue_control(im,rows,r'報名|报名|報名參加|报名参加|參加|参加|參賽|参赛')
        if register:
            return {**state,'stage':'REGISTER','point':register,'players':16 if target else None,
                'verified':verified,'choose':blue_control(im,rows,r'請選擇牌組|请选择牌组|選擇牌組|选择牌组|變更牌組|变更牌组'),
                'confirm':blue_control(im,rows,r'確認牌組|确认牌组|確認|确认')}
        return {**state,'stage':'EVENT_DETAIL','verified':verified,'round':round_no,
            'choose':blue_control(im,rows,r'請選擇牌組|请选择牌组|選擇牌組|选择牌组|變更牌組|变更牌组'),
            'confirm':blue_control(im,rows,r'確認牌組|确认牌组|確認|确认')}
    if scene=='HOME':return {**state,'stage':'HOME','point':[517,658]}
    if re.search('2Pick',text,re.I):
        point=find(v.ocr(im,(850,400,1230,550)),r'[廣广][場场][賽赛]')
        if point:return {**state,'stage':'ARENA','point':point}
    if not navigation_modal(rows) and re.search(r'TAP.*START|TOUCH.*START|點擊.*開始|点击.*开始',text,re.I):
        return {**state,'stage':'TITLE','point':[640,540]}
    if re.search(r'Now\s*Loading|LOADING|[讀读]取中|[連连]線中',text,re.I):return {**state,'stage':'LOADING'}
    return state

class WeekendPlazaRun(Run):
    transport_type=AdbTransport
    def __init__(self,args):
        super().__init__(args)
        self.plaza_context=getattr(args,'plaza_context',None)
        self.plaza_owned=bool(self.plaza_context and self.plaza_context.get('players')==16)
        self.plaza_before=None;self.plaza_result=None;self.plaza_end=None;self.plaza_stage=None
        self.plaza_deck_checked=bool(self.plaza_context and self.plaza_context.get('deck_verified'))
        self.plaza_wait_at=None;self.plaza_entered_now=False
        self.plaza_finish_only=bool(getattr(args,'plaza_finish_only',False));self.plaza_reward=False
        self.plaza_battle_seen=False;self.plaza_next_open=None;self.plaza_finished_seen=False
    def proof(self,name):
        self.im.save(self.out/name)
        return {'file':name,'sha256':hashlib.sha256((self.out/name).read_bytes()).hexdigest()}
    def view(self,scene=None):
        if getattr(self,'plaza_view_seq',None)!=self.seq:
            self.plaza_view=inspect(self.v,self.im,scene or self.v.scene(self.im)[0],self.plaza_owned)
            self.plaza_view_seq=self.seq
            if self.plaza_view['stage']!=getattr(self,'plaza_saved_stage',None):
                self.plaza_saved_stage=self.plaza_view['stage']
                self.proof(f'plaza_{self.seq:05d}_{self.plaza_saved_stage.lower()}.png')
        self.plaza_stage=self.plaza_view['stage'];return self.plaza_view
    def end_nav(self,reason):
        self.plaza_end=reason;self.finished=True;self.preview_ready=True
        self.log('weekend_plaza_navigation_end',reason=reason,frame=self.frame)
        return True
    def move(self,name,point):
        self.nav_repeat[name]+=1
        if self.nav_repeat[name]>3:raise RecoveryBlocked('16人广场赛页面未推进：'+name)
        held=getattr(self,'plaza_stage',None) in ('PLAZA_WORLD','INTRO','BATTLE_END','STANDINGS','WALKOVER','FINISHED','EXIT_CONFIRM')
        self.act(name,point,point if held else None,delay=1.2);self.last_progress=time.monotonic();return True
    def expected_round(self,state):
        prior=(self.plaza_context or {}).get('round')
        number=state.get('round') or (prior+1 if prior is not None else 1 if self.plaza_entered_now else None)
        if number not in (1,2,3,4):raise RecoveryBlocked('无法确认16人赛当前轮次，已停止')
        if prior is not None and number!=prior+1:raise RecoveryBlocked('轮次没有推进，不重复开始已完成对局')
        return number
    def commit_battle(self,state,automatic=False):
        self.stop()
        if (ROOT/'PANEL_FINISH').exists():raise InterruptedError('已设置局后停止，不进入下一轮')
        if self.plaza_before:raise RecoveryBlocked('广场赛匹配已经提交，不重复点击')
        number=self.expected_round(state)
        self.plaza_before={'round':number,'players':16,'deck':'nightmare_aggro','automatic':automatic,
            'evidence':self.proof('weekend_plaza_before.png')}
        write_json(self.out/'weekend_plaza_queue_commit.json',self.plaza_before)
        self.started=True;self.ranked_queue_attempted=True;self.ranked_deck_verified=self.plaza_deck_checked
        self.rematch_pending=not automatic;self.rematch_began=time.monotonic()
        self.log('weekend_plaza_queue_commit',**self.plaza_before)
    def observe(self,*args,**kwargs):
        im=super().observe(*args,**kwargs)
        if hasattr(self,'v') and self.plaza_owned and not self.plaza_finish_only and self.v.scene(im)[0]=='MULLIGAN':
            if not self.started:
                if not self.plaza_deck_checked:raise RecoveryBlocked('自动开赛前牌组未核验，停止接管')
                self.commit_battle({'round':None},automatic=True)
            self.plaza_battle_seen=True
        return im
    def navigation(self,scene):
        if self.started:return False
        state=self.view(scene);stage=state['stage']
        if stage=='CLOSED':
            self.plaza_next_open=state['next_open'];self.proof('weekend_plaza_closed.png')
            return self.end_nav('event_complete' if self.plaza_finish_only and (self.plaza_context or {}).get('event_ended') else 'closed_after_rounds' if self.plaza_finish_only else 'not_open')
        if stage=='NO_16_EVENT':return self.end_nav('no_16_player_event')
        if stage=='ALREADY_PARTICIPATED':
            return self.end_nav('event_complete' if self.plaza_finish_only and self.plaza_owned else 'already_participated')
        if stage in ('PAYMENT_BLOCKED','WRONG_SIZE','WRONG_EVENT','SIZE_UNVERIFIED'):
            self.proof('weekend_plaza_blocked.png');return self.end_nav(stage.lower())
        if self.v.turn(self.im) or scene=='MULLIGAN':raise RecoveryBlocked('不接管没有本次报名证据的现有对局')
        if stage in ('WAITING','READY') and not self.plaza_owned and state.get('verified'):
            if not self.plaza_deck_checked:
                if not state.get('confirm'):return self.end_nav('waiting_deck_verification')
                self.plaza_checking_deck=True;return self.move('weekend_plaza_verify_deck',state['confirm'])
            if state.get('round') not in (1,2,3,4):return self.end_nav('unknown_round')
            self.plaza_context={'players':16,'round':state['round']-1,'deck_verified':True,'adopted':True}
            self.plaza_owned=True
            write_json(self.out/'weekend_plaza_adopted.json',{'players':16,'round':state['round'],'evidence':self.proof('weekend_plaza_adopted.png')})
        if stage in ('LOADING','WAITING'):
            if stage=='WAITING' and not self.plaza_owned:raise RecoveryBlocked('候场页缺少本次报名证据，停止接管')
            if self.plaza_wait_at is None or getattr(self,'plaza_wait_stage',None)!=stage:
                self.plaza_wait_at=time.monotonic();self.plaza_wait_stage=stage
            if stage=='WAITING':self.plaza_waiting_verified=True
            limit=90 if stage=='LOADING' else getattr(self.args,'plaza_wait_minutes',30)*60
            if time.monotonic()-self.plaza_wait_at>limit:raise RecoveryBlocked('广场赛等待超时，保留报名状态，不自动退赛')
            self.last_progress=time.monotonic();time.sleep(.5);return True
        self.plaza_wait_at=None
        if stage=='EXIT_CONFIRM':
            if not self.plaza_owned or not (self.plaza_finished_seen or (self.plaza_context or {}).get('event_ended')):
                raise RecoveryBlocked('离开赛事缺少明确结束证据')
            if not state.get('point'):raise RecoveryBlocked('赛事结束确认按钮未识别')
            self.plaza_exit_confirmed=True;return self.move('weekend_plaza_exit_confirm',state['point'])
        if stage=='INTRO':
            if not self.plaza_owned:raise RecoveryBlocked('赛事介绍缺少本次报名证据')
            return self.move('weekend_plaza_intro',state['point'])
        if stage in ('BATTLE_END','STANDINGS','WALKOVER'):
            if not self.plaza_owned:raise RecoveryBlocked('赛后页面缺少本次16人赛上下文')
            if stage=='WALKOVER' and getattr(self,'plaza_waiting_verified',False):
                self.commit_battle(state,automatic=True);self.outcome='win';return True
            if not self.plaza_context:raise RecoveryBlocked('没有上一轮完成记录，不能跳过赛后确认')
            if stage=='WALKOVER' and self.plaza_context.get('previous_kind')!='walkover':
                raise RecoveryBlocked('未确认的新弃权判胜页面，保留现场')
            if not state.get('point'):raise RecoveryBlocked('赛后确认按钮未识别')
            return self.move('weekend_plaza_dismiss_'+stage.lower(),state['point'])
        if stage=='DECK_PREVIEW':
            if not getattr(self,'plaza_checking_deck',False) or state['count']!='40/40':
                raise RecoveryBlocked('广场赛牌组未核验为速攻速攻40/40')
            self.plaza_deck_checked=True;self.proof('weekend_plaza_deck.png')
            return self.move('weekend_plaza_close_deck',state['point'])
        if stage=='DECK_PICK':
            if not getattr(self,'plaza_choosing_deck',False):raise RecoveryBlocked('非本任务打开的牌组选择页')
            self.plaza_selected_deck=True;return self.move('weekend_plaza_select_deck',state['point'])
        if stage=='DECK_CONFIRM':
            if not getattr(self,'plaza_selected_deck',False):raise RecoveryBlocked('牌组确认框缺少本次选择上下文')
            if not self.plaza_deck_checked:
                if not state.get('confirm'):raise RecoveryBlocked('缺少已观察的牌组详情按钮')
                self.plaza_checking_deck=True;return self.move('weekend_plaza_verify_deck',state['confirm'])
            if not state.get('point'):raise RecoveryBlocked('未识别牌组决定按钮')
            return self.move('weekend_plaza_confirm_deck',state['point'])
        if stage=='ENTRY_CONFIRM':
            if not getattr(self,'plaza_entry_committed',False) or not self.plaza_deck_checked:
                raise RecoveryBlocked('最终报名确认缺少本次16人赛和牌组证据')
            if getattr(self,'plaza_registration_confirmed',False):
                raise RecoveryBlocked('最终报名已提交，不重复点击')
            if not state.get('point'):raise RecoveryBlocked('最终报名按钮未识别')
            self.plaza_registration_confirmed=True;self.proof('weekend_plaza_registration_confirm.png')
            return self.move('weekend_plaza_entry_confirm',state['point'])
        if stage=='REWARD_RECEIPT':
            if not self.plaza_owned:raise RecoveryBlocked('没有本次广场赛的领奖上下文')
            if state.get('event_reward') or self.plaza_finished_seen:
                self.plaza_reward=True;self.plaza_finish_only=True
            self.proof('weekend_plaza_reward.png')
            return self.move('weekend_plaza_dismiss_reward',state['point'])
        if self.plaza_finish_only and stage in ('HOME','ARENA','EVENT_SELECT','CLOSED','NO_16_EVENT'):
            return self.end_nav('event_complete' if self.plaza_reward or self.plaza_finished_seen or (self.plaza_context or {}).get('event_ended') else 'unverified_finish')
        if stage=='FINISHED':
            if not self.plaza_owned:raise RecoveryBlocked('未核验的广场赛结束页面')
            if getattr(self.args,'plaza_preview',False):return self.end_nav('preview_ready')
            self.plaza_finish_only=True;self.plaza_finished_seen=True;self.proof('weekend_plaza_finished.png')
            if not state.get('point'):return self.end_nav('event_complete')
            return self.move('weekend_plaza_finish_event',state['point'])
        if stage=='ROUND_RESULT':
            if not self.plaza_owned:raise RecoveryBlocked('没有16人广场赛对局上下文')
            return self.move('weekend_plaza_return_event',state['point'])
        if stage in ('EVENT_DETAIL','REGISTER','READY'):
            if self.plaza_finish_only:raise RecoveryBlocked('赛事已结束，不进入下一场广场赛')
            if not self.plaza_owned and not state.get('verified'):raise RecoveryBlocked('必须确认周末、16人及指定系列后才能报名')
            if not self.plaza_deck_checked:
                if state.get('confirm') and getattr(self,'plaza_selected_deck',False):
                    self.plaza_checking_deck=True;return self.move('weekend_plaza_verify_deck',state['confirm'])
                if state.get('choose'):
                    self.plaza_choosing_deck=True;return self.move('weekend_plaza_choose_deck',state['choose'])
                if state.get('confirm'):
                    self.plaza_checking_deck=True;return self.move('weekend_plaza_verify_deck',state['confirm'])
                raise RecoveryBlocked('16人赛内部牌组控件尚未核验，已保存画面并停止')
            if getattr(self.args,'plaza_preview',False):return self.end_nav('preview_ready')
            if stage=='REGISTER':
                if self.plaza_owned or getattr(self,'plaza_entry_committed',False):
                    if getattr(self,'plaza_entry_committed',False):
                        if getattr(self,'plaza_registration_wait_at',None) is None:self.plaza_registration_wait_at=time.monotonic()
                        if time.monotonic()-self.plaza_registration_wait_at>30:raise RecoveryBlocked('报名提交后页面未推进，不重复报名')
                        time.sleep(.4);return True
                    raise RecoveryBlocked('已报名，不重复创建赛事报名')
                self.plaza_entry_committed=True
                write_json(self.out/'weekend_plaza_entry_commit.json',{'players':16,'format':'rotation','evidence':self.proof('weekend_plaza_entry.png')})
                self.plaza_owned=True;self.plaza_entered_now=True
                return self.move('weekend_plaza_entry',state['point'])
            if stage=='READY':
                if not self.plaza_owned:raise RecoveryBlocked('准备按钮缺少已报名的16人赛上下文')
                self.commit_battle(state);self.act('start_weekend_plaza',state['point'],state['point'],delay=.5);return True
            return self.end_nav('unrecognized_event_controls')
        if stage=='EVENT_SELECT':
            if self.plaza_owned:raise RecoveryBlocked('已报名赛事无法确认，禁止另报下一场')
            return self.move('weekend_plaza_open_16_event',state['point'])
        if stage in ('HOME','ARENA','TITLE','EVENT_TRAVEL','PLAZA_PORTAL','PLAZA_WORLD'):
            return self.move('weekend_plaza_'+stage.lower(),state['point'])
        if re.search('廣場|广场|16人',state['text']):raise RecoveryBlocked('16人赛新页面尚未核验，已保存画面并停止')
        return False
    def match_ready(self,scene):
        if self.view(scene)['stage']=='WALKOVER' and self.plaza_owned and self.plaza_before:
            self.outcome='win';self.rematch_pending=False;self.last_progress=time.monotonic();return True
        if scene=='MULLIGAN' or self.v.turn(self.im):
            self.rematch_pending=False;self.plaza_battle_seen=True;self.last_progress=time.monotonic();return True
        if time.monotonic()-self.rematch_began>90:raise RecoveryBlocked('16人赛准备后没有进入新对局，不重复匹配')
        return False
    def result_reached(self,scene):
        if self.v.turn(self.im) or not self.plaza_before:return False
        state=self.view(scene)
        walkover=state['stage']=='WALKOVER' and self.plaza_owned
        if walkover:self.outcome='win'
        if self.outcome not in ('win','loss') or not(self.plaza_battle_seen or walkover):return False
        if state['stage'] not in ('ROUND_RESULT','FINISHED','BATTLE_END','STANDINGS','WALKOVER'):return False
        if state['stage']=='STANDINGS' and state.get('round')!=self.plaza_before['round']:return False
        self.plaza_result={'round':self.plaza_before['round'],'players':16,'outcome':self.outcome,
            'kind':'walkover' if walkover else 'battle','battle_seen':self.plaza_battle_seen,
            'event_ended':state['stage']=='FINISHED','evidence':self.proof('weekend_plaza_result.png')}
        return True
    def summary_extra(self):
        return {'weekend_plaza':{'before':self.plaza_before,'result':self.plaza_result,'owned':self.plaza_owned,
            'end':self.plaza_end,'stage':self.plaza_stage,'next_open':self.plaza_next_open,'reward_received':self.plaza_reward}}
    def recover_unknown_page(self,scene,terminal=False):
        state=self.view(scene)
        if state['stage']!='UNKNOWN' or re.search('廣場|广场|16人',state['text']):return False
        return super().recover_unknown_page(scene,terminal)
    def reconnect(self,trigger):
        began=time.monotonic();self._reconnecting=True
        try:
            for attempt in range(1,6):
                publish_reconnect(ROOT,self.out,attempt,'trying',f'16人广场赛重新连接 {attempt}/5',getattr(self.args,'batch',None))
                deadline=time.monotonic()+12;clicked=False
                while time.monotonic()<deadline:
                    self.observe('plaza_reconnect',_network_check=False);rows=self.texts((20,20,1250,700))
                    if network_evidence(joined(rows)):
                        button=dialog_button(rows)
                        if button and not clicked:clicked=True;self.act('weekend_plaza_retry',center(button),center(button),delay=.5)
                    elif self.started:
                        if self.v.turn(self.im) or self.v.scene(self.im)[0]=='MULLIGAN' or self.result_reached(self.v.scene(self.im)[0]):break
                        if self.view()['stage'] in ('HOME','ARENA','CLOSED','EVENT_SELECT'):raise RecoveryBlocked('重连离开了未确认的广场赛，不重复对局')
                    elif self.view()['stage'] in ('HOME','ARENA','CLOSED','EVENT_SELECT','REGISTER','READY','WAITING','INTRO'):break
                    self.stop();time.sleep(.4)
                else:continue
                self.network_watch.reset();self.last_progress=time.monotonic()
                publish_reconnect(ROOT,self.out,attempt,'recovered',f'16人广场赛重新连接成功 {attempt}/5',getattr(self.args,'batch',None));return
            raise RecoveryBlocked('16人广场赛重新连接失败5/5；不重启UU，不重复报名')
        finally:
            self._reconnecting=False;elapsed=time.monotonic()-began;self.began+=elapsed
            if self.turn_began is not None:self.turn_began+=elapsed
