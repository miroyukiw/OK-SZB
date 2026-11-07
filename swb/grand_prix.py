"""Observed Rotation Grand Prix flow; one admitted challenge, free entry only."""
import hashlib
import re
import time
import numpy as np
from run import Run,ROOT
from swb.adb_transport import AdbTransport
from swb.atomic_io import write_json
from swb.network_recovery import network_evidence,navigation_modal,dialog_button,RecoveryBlocked,publish_reconnect

def compact(text):return re.sub(r'\s+','',text)
def joined(rows):return ' '.join(r['text'] for r in rows)
def center(row):
    x,y,w,h=row['box'];return [x+w//2,y+h//2]
def find(rows,pattern):
    candidates=[r for r in rows if re.fullmatch(pattern,compact(r['text']),re.I)]
    return center(candidates[0]) if len(candidates)==1 else None
def wins(v,im,box):
    values=[int(r['text']) for r in v.ocr(im,box) if re.fullmatch('[0-5]',r['text'].strip()) and r.get('score',0)>=.8]
    return values[0] if len(values)==1 else None

def inspect(v,im,scene=None):
    rows=v.ocr(im,(0,0,1280,720));text=joined(rows);s=compact(text)
    result={'stage':'UNKNOWN','text':text}
    if network_evidence(text):return {**result,'stage':'NETWORK'}
    top=joined(v.ocr(im,(380,20,950,115)))
    if re.search('顯示QR|显示QR',text,re.I):
        name=compact(joined(v.ocr(im,(35,35,465,95))))
        count=compact(joined(v.ocr(im,(755,122,840,163))))
        return {**result,'stage':'DECK_PREVIEW','deck':name,'count':count,'point':[1214,70]}
    if re.search('領取結果|领取结果',text) and re.search('已[領领]取.*大[賽赛]',s) and re.search(r'\bTAP\b',text,re.I):
        return {**result,'stage':'REWARD_RECEIPT','point':[640,651]}
    status_title=joined(v.ocr(im,(500,120,780,190)))
    status_button=find(v.ocr(im,(490,510,790,595)),r'[競竞]技[場场]')
    if re.search('狀況|状况',status_title) and status_button and re.search('本日.*挑[戰战]|[晉晋][級级]決[賽赛]|[晉晋][級级]决[賽赛]',s):
        return {**result,'stage':'FINISHED_STATUS','point':status_button}
    if re.search('選擇形式|选择形式',compact(top)):
        a=np.asarray(im)[258:289,282:311].astype(int)
        selected=float(((a[:,:,2]>150)&(a[:,:,1]>110)&(a[:,:,0]<140)).mean())>.15
        return {**result,'stage':'FORMAT','selected':selected,'point':[769,635] if selected else [296,273]}
    if re.search('參加大賽|参加大赛',compact(top)):
        free=find(v.ocr(im,(450,470,830,555)),r'免[費费]入[場场]')
        return {**result,'stage':'REGISTER','free':bool(free),'point':free,
            'needs_deck':bool(re.search('請選擇牌組|请选择牌组',s)),
            'supported':bool(re.search('指定系列',top) and re.search(r'第[12][輪轮]',s))}
    from swb.deck_profile import DECK_NAME_PATTERN
    pick=find(v.ocr(im,(80,210,1200,645)),DECK_NAME_PATTERN) or find(v.ocr(im,(925,590,1130,628)),r'.+')
    if pick and re.search('牌[組组]',top):return {**result,'stage':'DECK_PICK','point':pick}
    button_rows=v.ocr(im,(1010,365,1255,585));buttons=compact(joined(button_rows))
    next_game=re.search(r'第([1-5])[戰战戦]',buttons)
    ended=bool(re.search('挑[戰战]結束|挑战结束',buttons))
    claim=bool(re.search('[領领]取[獎奖][勵励]',buttons))
    footer=compact(joined(v.ocr(im,(630,585,1270,710))))
    round_match=re.search(r'第([12])[輪轮]',s);group=re.search(r'([AB])[組组]',s)
    rotation=bool(re.search('指定系列',s))
    if round_match and group and rotation and re.search('[對对][戰战戦][紀纪][錄录]',footer) and re.search('返回[據据][點点]',footer) and (next_game or ended):
        score=wins(v,im,(680,250,800,363))
        return {**result,'stage':'RESULT' if score is not None else 'UNREADABLE_GP','wins':score,
            'next':int(next_game[1]) if next_game else None,'ended':ended,'round':int(round_match[1]),'group':group[1],
            'point':center(next(r for r in button_rows if re.search(r'第[1-5][戰战戦]',compact(r['text'])))) if next_game else [1153,637],
            'confirm':[911,430]}
    if round_match and group and rotation and '使用牌組' in s and (next_game or claim):
        score=wins(v,im,(745,135,850,239))
        return {**result,'stage':('CLAIM' if claim else 'HUB') if score is not None else 'UNREADABLE_GP','wins':score,
            'next':int(next_game[1]) if next_game else None,'ended':claim,'round':int(round_match[1]),'group':group[1],
            'point':[1156,459],'confirm':[723,420]}
    entry=find(v.ocr(im,(420,455,860,590)),r'入[場场]')
    if entry and re.search('大[賽赛]',joined(v.ocr(im,(20,20,220,100)))) and re.search('Cup',text,re.I):
        return {**result,'stage':'ENTRY','free':bool(re.search('首次免[費费]',s)),
            'format_missing':bool(re.search('未選擇|未选择|選擇形式|选择形式',s)),
            'rotation':rotation,'round':int(round_match[1]) if round_match else None,'point':entry}
    arena=find(v.ocr(im,(440,390,840,550)),r'大[賽赛]')
    if arena and re.search('2Pick',text,re.I):return {**result,'stage':'ARENA','point':arena}
    if scene=='HOME':return {**result,'stage':'HOME','point':[517,658]}
    if re.search(r'Now\s*Loading|正在[載载]入|[讀读]取中|配[對对]中',text,re.I):return {**result,'stage':'LOADING'}
    if not navigation_modal(rows) and re.search(r'TOUCH.*START|TAP.*START|點擊.*開始|点击.*开始',text,re.I):
        return {**result,'stage':'TITLE','point':[640,540]}
    return result

class GrandPrixRun(Run):
    transport_type=AdbTransport
    def __init__(self,args):
        super().__init__(args)
        self.gp_before=None;self.gp_result=None;self.gp_stage=None;self.gp_deck_checked=False
        self.gp_end=None;self.gp_candidate=None;self.gp_terminal=None;self.gp_receipt=False
        self.gp_cleanup=bool(getattr(args,'gp_finish_only',False));self.gp_claimed=False
    def view(self,scene=None):
        if getattr(self,'gp_view_seq',None)!=self.seq:
            self.gp_view=inspect(self.v,self.im,scene or self.v.scene(self.im)[0]);self.gp_view_seq=self.seq
        self.gp_stage=self.gp_view['stage'];return self.gp_view
    def proof(self,name):
        path=self.out/name;self.im.save(path)
        return {'file':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    def summary_extra(self):
        return {'grand_prix':{'before':self.gp_before,'result':self.gp_result,'stage':self.gp_stage,
            'end':self.gp_end,'reward_receipt':self.gp_receipt,'reward_claimed':self.gp_claimed,'finish_only':self.gp_cleanup,
            'challenge':getattr(self,'gp_challenge',None)}}
    def wait_page(self,stage,limit=45):
        if getattr(self,'gp_wait_key',None)!=stage:self.gp_wait_key=stage;self.gp_wait_at=time.monotonic()
        if time.monotonic()-self.gp_wait_at>limit:raise RecoveryBlocked('Grand Prix page did not settle: '+stage)
        time.sleep(.4);return True
    def end_navigation(self,reason):
        self.gp_end=reason;self.preview_ready=True;self.finished=True
        self.log('grand_prix_navigation_end',reason=reason,frame=self.frame)
        return True
    def move(self,name,point):
        self.nav_repeat[name]+=1
        if self.nav_repeat[name]>4:raise RecoveryBlocked('Grand Prix page did not advance: '+name)
        self.act(name,point,delay=1.3);self.last_progress=time.monotonic();return True
    def navigation(self,scene):
        if self.started:return False
        state=self.view(scene);stage=state['stage']
        if self.v.turn(self.im) or scene=='MULLIGAN':raise RecoveryBlocked('Existing Grand Prix battle cannot be resumed automatically')
        if stage in ('LOADING','UNREADABLE_GP'):return self.wait_page(stage,90 if stage=='LOADING' else 15)
        if stage=='DECK_PREVIEW':
            if state['count']!='40/40':raise RecoveryBlocked('Grand Prix deck is not verified 速攻速攻 / 40/40')
            if not getattr(self,'gp_checking_deck',False):raise RecoveryBlocked('Deck preview has no Grand Prix navigation context')
            self.gp_deck_checked=True;self.proof('grand_prix_deck.png')
            return self.move('grand_prix_close_deck',state['point'])
        if stage=='REWARD_RECEIPT':
            self.gp_receipt=True;self.proof('grand_prix_reward.png')
            return self.move('grand_prix_reward_dismiss',state['point'])
        if stage=='FINISHED_STATUS':
            if not self.gp_receipt and not self.gp_cleanup:raise RecoveryBlocked('Unowned Grand Prix status dialog')
            self.proof('grand_prix_finished_status.png');self.gp_claimed=True
            return self.move('grand_prix_leave_status',state['point'])
        if self.gp_cleanup and (stage=='ENTRY' or stage=='ARENA' and (self.gp_claimed or self.gp_receipt)):
            return self.end_navigation('challenge_complete' if self.gp_claimed or self.gp_receipt else 'reward_already_closed')
        if self.gp_cleanup and stage in ('HOME','ARENA'):return self.move('grand_prix_'+stage.lower(),state['point'])
        if stage=='CLAIM':
            if getattr(self.args,'gp_preview',False):return self.end_navigation('preview_ready')
            self.gp_challenge={'wins':state['wins'],'played':5};self.gp_cleanup=True
            if getattr(self,'gp_claim_sent',False):return self.wait_page('claim_animation',45)
            self.gp_claim_sent=True;return self.move('grand_prix_claim_reward',state['point'])
        if stage=='RESULT' and state['ended']:
            if getattr(self.args,'gp_preview',False):return self.end_navigation('preview_ready')
            self.gp_cleanup=True;return self.move('grand_prix_return_hub',state['point'])
        if self.gp_cleanup:
            if stage=='UNKNOWN':return self.wait_page('reward_animation',45)
            raise RecoveryBlocked('Reward cleanup cannot enter another Grand Prix battle')
        if stage in ('HUB','RESULT'):
            if state['next'] is None or state['wins']>state['next']-1:raise RecoveryBlocked('Invalid Grand Prix progress')
            if not self.gp_deck_checked:
                self.gp_checking_deck=True;return self.move('grand_prix_check_deck',state['confirm'])
            if getattr(self.args,'gp_preview',False):return self.end_navigation('preview_ready')
            self.stop()
            if (ROOT/'PANEL_FINISH').exists():raise InterruptedError('Finish requested before Grand Prix queue')
            self.gp_before={k:state[k] for k in ('next','wins','round','group')}
            self.gp_before['evidence']=self.proof('grand_prix_before_queue.png')
            write_json(self.out/'grand_prix_queue_commit.json',self.gp_before)
            self.ranked_queue_attempted=True;self.ranked_deck_verified=True
            self.rematch_pending=True;self.rematch_began=time.monotonic();self.started=True
            self.log('grand_prix_queue_commit',**self.gp_before)
            self.act('start_grand_prix',state['point'],delay=2)
            self.last_progress=time.monotonic();return True
        if stage=='ENTRY':
            if not state['format_missing'] and state.get('round') not in (1,2):raise RecoveryBlocked('Only Grand Prix rounds 1/2 are adapted; finals require manual play')
            if getattr(self.args,'gp_preview',False):return self.end_navigation('free_entry_available' if state['free'] else 'waiting_manual_entry')
            if not state['free']:return self.end_navigation('waiting_manual_entry')
            if not state['format_missing'] and not state['rotation']:raise RecoveryBlocked('Only Rotation Grand Prix is supported')
            return self.move('grand_prix_open_entry',state['point'])
        if stage=='FORMAT':return self.move('grand_prix_format_confirm' if state['selected'] else 'grand_prix_select_rotation',state['point'])
        if stage=='REGISTER':
            if not state.get('supported'):raise RecoveryBlocked('Only Rotation Grand Prix rounds 1/2 support automatic admission')
            if not state['free']:return self.end_navigation('waiting_manual_entry')
            if state['needs_deck'] or not getattr(self,'gp_selected_deck',False):
                self.gp_waiting_deck=True;return self.move('grand_prix_choose_deck',[539,327])
            if not self.gp_deck_checked:
                self.gp_checking_deck=True;return self.move('grand_prix_check_registration',[823,327])
            if getattr(self.args,'gp_preview',False):return self.end_navigation('free_entry_available')
            if getattr(self,'gp_admission_committed',False):raise RecoveryBlocked('Uncertain free admission is never replayed')
            self.gp_admission_committed=True;write_json(self.out/'grand_prix_free_commit.json',self.proof('grand_prix_free_entry.png'))
            return self.move('grand_prix_free_entry',state['point'])
        if stage=='DECK_PICK':
            if not getattr(self,'gp_waiting_deck',False):raise RecoveryBlocked('Unowned deck selection page')
            self.gp_selected_deck=True;return self.move('grand_prix_select_nightmare',state['point'])
        if stage in ('HOME','ARENA','TITLE'):return self.move('grand_prix_'+stage.lower(),state['point'])
        return False
    def advanced(self,state):
        before=self.gp_before
        return bool(before and state['stage']=='RESULT' and state.get('round')==before['round'] and state.get('group')==before['group']
            and ((before['next']<5 and state.get('next')==before['next']+1 and not state['ended'])
                 or (before['next']==5 and state['ended'])))
    def match_ready(self,scene):
        if not self.v.turn(self.im) and self.advanced(self.view(scene)):
            self.rematch_pending=False;return True
        return super().match_ready(scene)
    def result_reached(self,scene):
        if self.v.turn(self.im):return False
        state=self.view(scene)
        if not self.advanced(state):return False
        delta=state['wins']-self.gp_before['wins']
        if delta not in (0,1):raise RecoveryBlocked('Grand Prix wins changed inconsistently; no result recorded')
        outcome='win' if delta else 'loss';key=(state['next'],state['wins'],state['ended'])
        if self.gp_candidate is None:
            self.gp_terminal=self.outcome if self.outcome!='unknown' else None
        if self.gp_candidate is None or self.gp_candidate[0]!=key:
            self.gp_candidate=(key,time.monotonic())
        # Hold the combat loop at settlement while the animated count stabilizes.
        if self.gp_terminal is None:self.outcome=outcome
        if time.monotonic()-self.gp_candidate[1]<1.5:return False
        if self.gp_terminal and self.gp_terminal!=outcome:raise RecoveryBlocked('Battle verdict disagrees with Grand Prix score')
        self.outcome=outcome
        self.gp_result={k:state[k] for k in ('next','wins','round','group','ended')}
        self.gp_result.update(outcome=outcome,played=self.gp_before['next'],evidence=self.proof('grand_prix_result.png'))
        return True
    def recover_unknown_page(self,scene,terminal=False):
        state=self.view(scene)
        if state['stage']!='UNKNOWN':return False
        if re.search('大[賽赛]|入[場场]|[領领]取[獎奖][勵励]',state['text']):return False
        return super().recover_unknown_page(scene,terminal)
    def reconnect(self,trigger):
        began=time.monotonic();self._reconnecting=True
        try:
            for number in range(1,6):
                publish_reconnect(ROOT,self.out,number,'trying',f'ADB 大赛重新连接 {number}/5',getattr(self.args,'batch',None))
                end=time.monotonic()+12;clicked=False
                while time.monotonic()<end:
                    self.observe('gp_reconnect',_network_check=False)
                    rows=self.texts((20,20,1250,700));text=joined(rows)
                    if network_evidence(text):
                        button=dialog_button(rows)
                        if button and not clicked:
                            clicked=True;self.act('grand_prix_retry_network',center(button),delay=.5)
                    elif self.started:
                        if self.v.turn(self.im) or self.v.scene(self.im)[0]=='MULLIGAN' or self.advanced(self.view()):break
                        if self.view()['stage'] in ('HOME','HUB','RESULT','ENTRY'):
                            raise RecoveryBlocked('Reconnected outside the unconfirmed battle; no automatic replay')
                    elif self.view()['stage'] in ('HOME','HUB','RESULT','ENTRY','ARENA','TITLE'):break
                    self.stop();time.sleep(.4)
                else:continue
                self.network_watch.reset();self.last_progress=time.monotonic()
                publish_reconnect(ROOT,self.out,number,'recovered',f'ADB 大赛重新连接成功 {number}/5',getattr(self.args,'batch',None));return
            raise RecoveryBlocked('ADB 大赛重新连接失败 5/5；已保留当前画面，不重启 UU')
        finally:
            self._reconnecting=False;elapsed=time.monotonic()-began;self.began+=elapsed
            if self.turn_began is not None:self.turn_began+=elapsed
