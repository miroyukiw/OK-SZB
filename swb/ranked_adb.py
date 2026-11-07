"""Ranked adapter: Android input and observed recovery, without emulator tabs."""
import re
import time
import numpy as np
from run import Run,ROOT
from swb.adb_transport import AdbTransport,AdbGameLifecycle
from swb.network_recovery import (network_evidence,dialog_button,confirmation_button,
    navigation_modal,reconnect_five,publish_reconnect,RecoveredToLobby,RecoveryBlocked,
    battle_reentry,server_adjudication,park_reentry)
from swb.atomic_io import write_json


class RankedAdbRun(Run):
    transport_type=AdbTransport

    def result_reached(self,scene):
        receipt=getattr(self,'server_result_receipt',None)
        if receipt and self.started and self.outcome==receipt['outcome'] and getattr(self,'server_result_acknowledged',False):
            return True
        return super().result_reached(scene)

    def reconnect(self,trigger):
        began=time.monotonic();self._reconnecting=True;apps_restarted=False
        if trigger=='no_game_progress_90s':
            if getattr(self,'_stall_turn',None)!=self.turn:
                self._stall_turn=self.turn;self._stall_count=0
            self._stall_count=getattr(self,'_stall_count',0)+1
            frozen=False
            if self.im is not None:
                im0=np.asarray(self.im,dtype=int)
                time.sleep(1.5)
                im1=np.asarray(self.observe('ranked_freeze_probe',_network_check=False),dtype=int)
                frozen=int(np.abs(im0-im1).max())==0
            if frozen or self._stall_count>=2:
                self.log('client_freeze_restart',trigger=trigger,frozen=frozen,stall_count=self._stall_count,turn=self.turn,frame=self.frame)
                life=AdbGameLifecycle(self.config,self.out/'freeze_recovery',self.stop)
                life.close_game();time.sleep(1);self.t.adb.launch_game()
                apps_restarted=True;self._stall_count=0
                try:self.t.refresh_process(timeout=20)
                except Exception as exc:self.log('refresh_process_warning',reason=str(exc))
        def progress(number,status,message):
            publish_reconnect(ROOT,self.out,number,status,message,getattr(self.args,'batch',None))
        def attempt(number):
            end=time.monotonic()+25;clicked=set()
            while time.monotonic()<end:
                self.observe('ranked_reconnect',_network_check=False)
                rows=self.texts((20,20,1250,700));text=' '.join(r['text'] for r in rows)
                if re.search('系統.*維護中|系统.*维护中',text):
                    self.im.save(self.out/'maintenance.png')
                    raise RecoveryBlocked('游戏服务器维护中：'+re.sub(r'\s+',' ',text))
                scene=self.v.scene(self.im)[0]
                reentry=battle_reentry(rows);adjudication=server_adjudication(rows)
                if reentry or adjudication:
                    if not (self.started and self.ranked_queue_attempted):
                        raise RecoveryBlocked('Existing-match dialog has no owned queue receipt; no input')
                    if adjudication:
                        if self.outcome not in ('unknown',adjudication['outcome']):
                            raise RecoveryBlocked('Server adjudication contradicts recorded outcome')
                        if not getattr(self,'server_result_receipt',None):
                            frame=self.out/'server_adjudication.png';self.im.save(frame)
                            self.server_result_receipt={'outcome':adjudication['outcome'],'text':adjudication['text'],
                                'frame':frame.name,'queue_owned':True,'new_match_started':False}
                            write_json(self.out/'server_adjudication.json',self.server_result_receipt)
                            self.outcome=adjudication['outcome'];self.rematch_pending=False
                            self.log('server_adjudicated_result',**self.server_result_receipt)
                        button=adjudication['button'];key='server_result_ack'
                        if button and key not in clicked:
                            clicked.add(key);p=self.center(button)
                            self.act('ranked_ack_server_result',p,p,delay=1)
                        self.stop();time.sleep(.3);continue
                    button=reentry['button'];key='owned_match_reentry'
                    if button and key not in clicked:
                        clicked.add(key);self.im.save(self.out/'owned_match_reentry.png');p=self.center(button)
                        self.owned_reentry_sent=True
                        self.act('ranked_reenter_owned_match',p,p,delay=2)
                    self.stop();time.sleep(.3);continue
                if getattr(self,'server_result_receipt',None) and (park_reentry(rows) or not navigation_modal(rows)):
                    self.server_result_acknowledged=True
                    return {'mode':'continue','safe_lobby':False,'server_adjudication':self.server_result_receipt}
                restore_park=park_reentry(rows)
                if restore_park and restore_park.get('button'):
                    if 'park_reentry' not in clicked:
                        clicked.add('park_reentry');p=self.center(restore_park['button'])
                        self.act('ranked_reenter_park_lobby',p,p,delay=2)
                    self.stop();time.sleep(.4);continue
                button=dialog_button(rows) if network_evidence(text) else confirmation_button(rows)
                if button:
                    key=re.sub(r'\s+','',button['text'])
                    if key not in clicked:
                        clicked.add(key);self.act('ranked_retry_network',self.center(button),delay=.8)
                elif not navigation_modal(rows) and not network_evidence(text):
                    if scene=='MULLIGAN' or self.v.turn(self.im) in ('OUR','ENEMY'):
                        if not self.started:raise RecoveryBlocked('Unowned battle found during reconnect')
                        if scene!='MULLIGAN' and getattr(self,'owned_reentry_sent',False) and not self.mulligan_done:
                            # The server may have advanced the opening hand
                            # while disconnected. Read the live field afresh;
                            # do not wait forever for a mulligan that has ended.
                            self.mulligan_done=True;self.rematch_pending=False;self.last_phase=None
                            self.log('owned_reentry_field_adopted',frame=self.frame,
                                mulligan='server_already_advanced',turn_count_basis='observations_after_reentry')
                        return {'mode':'continue','safe_lobby':False}
                    if self.started and not getattr(self,'rematch_pending',False) and self.result_reached(scene):
                        return {'mode':'continue','safe_lobby':False}
                    if scene in ('HOME','RANKED_READY','RANKED_SETUP','RANKED_DECK_PICK'):
                        return {'mode':'safe_lobby','safe_lobby':True}
                    from swb.paradise_quests import inspect as park_inspect
                    if park_inspect(self.v,self.im)['stage'] in ('WORLD','NPC_INTERACT','HUB'):
                        return {'mode':'safe_lobby','safe_lobby':True}
                    from swb.adb_transport import game_data_download
                    dl=game_data_download(rows)
                    if dl and 'download_ok' not in clicked:
                        clicked.add('download_ok');self.act('ranked_reconnect_download',dl['point'],delay=2)
                        self.stop();time.sleep(1);continue
                    is_title=bool(re.search(r'TAP.*START|TOUCH.*START|TOSTART|點擊.*開始|点击.*开始',text,re.I) or
                                  (re.search(r'SHADOWVERSE|WORLDS[:\.]?BEYOND',text,re.I) and ('Ver1.' in text or 'CRIWARE' in text or 'ID' in text)))
                    if is_title:
                        title_clicks=getattr(self,'_reconnect_title_clicks',0)
                        last_title_time=getattr(self,'_reconnect_last_title_time',0)
                        if title_clicks<3 and time.monotonic()-last_title_time>3:
                            self._reconnect_title_clicks=title_clicks+1
                            self._reconnect_last_title_time=time.monotonic()
                            self.act('ranked_reconnect_title',[640,620],delay=2)
                    elif self.started and self.outcome!='unknown' and self.recover_unknown_page(scene,terminal=True):
                        # A later promotion overlay is still a local UI page,
                        # even if recovery was entered from a terminal timeout.
                        # Keep the same bounded per-episode budget here.
                        continue
                self.stop();time.sleep(.4)
            raise RecoveryBlocked('ADB reconnect did not verify a battle or safe lobby; UU retained')
        try:
            receipt=reconnect_five(attempt,self.stop,progress)
            receipt.update(apps_restarted=apps_restarted,accelerator_touched=False,
                interrupted_outcome='unknown',replay_pending=False,trigger=trigger)
            self.network_recovery_receipt=receipt
            if receipt['mode']=='safe_lobby' and (self.started or self.ranked_queue_attempted):
                raise RecoveredToLobby('ADB verified safe lobby; interrupted outcome remains unknown')
            self.network_watch.reset();self.nav_repeat.clear();self.last_nav_scene=None
            self.last_progress=time.monotonic()
            self.log('reconnect_continued',trigger=trigger,receipt=receipt)
        finally:
            self._reconnecting=False;elapsed=time.monotonic()-began;self.began+=elapsed
            if self.turn_began is not None:self.turn_began+=elapsed


def receipt_only_recovery(root,args,worker,stop,event):
    """Never fall back to legacy app restarts after an ADB technical stop."""
    stop()
    raise RecoveryBlocked('No verified ADB safe-lobby receipt; attempt retained without replay')
