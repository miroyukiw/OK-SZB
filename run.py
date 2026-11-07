"""Freeze once, run one CPU match autonomously; never imports an old project."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
import traceback
import sys
import subprocess

import numpy as np
import cv2
from PIL import Image
from maa.library import Library
from maa.tasker import Tasker
from maa.toolkit import Toolkit
from swb.transport import Transport
from swb.vision import Vision
from swb.policy import (ordered_cards,choose_effect_target,choose_mode,discard_rank,
                        evolution_order,preferred_evolution,plan_attacks,ocean_summoned,bonus_pp_plan,mulligan_plan,
                        evolve_before_play,discard_plan,choose_own_board_target,hand_buff_plan)
from swb.metrics import Metrics
from swb.deck_profile import active as active_deck,is_nightmare
from swb.dragon_telemetry import DragonTelemetry
from swb import nightmare,nightmare_runtime
from swb.recovery import concede_failed_cpu
from swb.receipts import attack_evidence,attack_denial,terminal_outcome,presence
from swb.failures import classify
from swb.telemetry import HeartbeatPublisher
from swb.user_rules import load as load_user_rules
from swb.panel_evidence import Evidence
from swb.ranked import navigate as ranked_navigation, result_screen, rematch_ready
from swb.atomic_io import write_json
from swb.network_recovery import NetworkWatch, NetworkStalled, RecoveredToLobby

ROOT=Path(__file__).resolve().parent

def dump(path,data):
    write_json(path,data)

def version():
    files=[ROOT/'run.py',ROOT/'panel_worker.py',ROOT/'ranked_once.py',ROOT/'config.json',ROOT/'batch_runner.py',ROOT/'technical_validation.py',ROOT/'technical_exit.py',ROOT/'discard_validation.py']+sorted((ROOT/'swb').glob('*.py'))
    files+=sorted(p for p in (ROOT/'assets').rglob('*') if p.is_file())
    hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    return {'sha256':hashlib.sha256(json.dumps(hashes,sort_keys=True).encode()).hexdigest(),
            'files':hashes,'maa':Library.version()}

class Run:
    def __init__(self,args):
        self.args=args
        batch_lock=ROOT/'BATCH.lock'
        if batch_lock.exists() and str(getattr(args,'batch_owner',None))!=batch_lock.read_text().strip():
            raise RuntimeError('Independent batch owns the game; second controller refused')
        self.out=ROOT/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        self.out.mkdir(parents=True)
        self.panel_managed=getattr(args,'panel_managed',False)
        self.detail_logs=getattr(args,'detail_logs',True)
        self.evidence=Evidence(self.out,self.detail_logs)
        if self.panel_managed:dump(self.out/'panel_evidence.json',{'detail_logs':self.detail_logs,'per_match_images_mb':256,'total_detail_mb':2048})
        self.seq=0; self.actions=Counter(); self.verified=Counter()
        self.started=False; self.finished=False; self.turn=0; self.outcome='unknown'
        self.ranked_queue_attempted=False;self.ranked_deck_verified=False;self.preview_ready=False
        self.last_phase=None; self.bad_cards=set(); self.attacked=set(); self.evolved=False
        self.super_unit=None; self.expanded=False; self.mulligan_done=False
        self.opponent_class=None
        self.rejected_targets=[];self.last_attack_denial=None
        self.blocked_attacks=[];self.extra_pp_attempts=set();self.temporary_max_bonus=0
        self.last_progress=time.monotonic(); self.began=time.monotonic()
        self.pending=None; self.t=None; self.im=None
        self.network_watch=NetworkWatch();self.network_incident=None
        self.active_effect=None;self.effect_attempts=set()
        self.concession=None
        self.config=json.loads((ROOT/'config.json').read_text(encoding='utf-8'))
        if getattr(args,'panel_config',None):self.config.update(args.panel_config)
        self.nav_repeat=Counter()
        self.unit_memory=[];self.next_unit=0
        self.leader_refs=None
        self.metrics=Metrics();self.turn_times=[];self.turn_began=None
        self.emblem_done=False;self.cpu_verified=False;self.last_hand=None;self.last_state={}
        self.attacked_leader_last_turn=False;self.attacked_leader_this_turn=False
        self.strategy=json.loads((ROOT/'assets/strategy.json').read_text(encoding='utf-8'))
        self.settings=self.strategy['settings']
        self.deck_profile=self.config.get('deck_profile') or active_deck(ROOT)
        self.is_nightmare=is_nightmare(self.deck_profile)
        if self.is_nightmare:
            self.detail_logs=False
            self.evidence.detail=False
            overlay=json.loads((ROOT/'assets/nightmare/strategy.json').read_text(encoding='utf-8'))
            self.strategy['cards'].update(overlay['cards'])
            self.nm_settings=overlay['settings']
            nightmare_runtime.init(self)
        else:
            mode_name=getattr(args,'mode',None) or ('ranked' if getattr(args,'ranked',False) else 'practice_cpu')
            self.dragon_telemetry=DragonTelemetry(ROOT,self.out.name,mode_name)
        self.user_rules=load_user_rules(ROOT/'user_data/rules.json')
        self.rules_sha=hashlib.sha256(json.dumps(self.user_rules,sort_keys=True).encode()).hexdigest()
        self.settings['_panel_rules']=self.user_rules
        dump(self.out/'rules.json',self.user_rules)
        self.last_nav_scene=None; self.last_nav_at=0
        self.manifest=version(); dump(self.out/'version.json',self.manifest)
        for relative in self.manifest['files']:
            if Path(relative).parts[0]=='assets':continue
            source_root=ROOT/'user_data/versions'/self.manifest['sha256'] if self.panel_managed else self.out/'source'
            target=source_root/relative;target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():shutil.copy2(ROOT/relative,target)
        self.lock=ROOT/'RUNNING.lock'
        fd=os.open(self.lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
        os.write(fd,str(os.getpid()).encode()); os.close(fd)

    def stop(self):
        if (ROOT/'STOP').exists():raise InterruptedError('STOP requested')
        if (getattr(self,'panel_managed',False) and (ROOT/'PANEL_FINISH').exists()
                and not self.started and not self.ranked_queue_attempted):
            raise InterruptedError('Finish-after-match requested before queue')
        if getattr(self,'panel_managed',False) and (ROOT/'PANEL_PAUSE').exists():
            paused=time.monotonic()
            while (ROOT/'PANEL_PAUSE').exists():
                if (ROOT/'STOP').exists():raise InterruptedError('STOP requested')
                time.sleep(.1)
            elapsed=time.monotonic()-paused
            self.began+=elapsed;self.last_progress+=elapsed
            if self.turn_began is not None:self.turn_began+=elapsed
        if (self.out/'WATCHDOG_STOP').exists():raise TimeoutError('Watchdog requested stop')
        if not getattr(self,'_reconnecting',False) and time.monotonic()-self.began>self.args.max_minutes*60:
            raise TimeoutError('Whole-run deadline')

    def log(self,kind,**data):
        row={'time':datetime.now(timezone.utc).isoformat(),'event':kind,'turn':self.turn,**data}
        with self.metrics.span('log_io'):self.evidence.event(row)
        if getattr(self,'is_nightmare',False):nightmare_runtime.audit_event(self,kind,data)
        if not self.panel_managed:print(json.dumps(row,ensure_ascii=False),flush=True)

    def observe(self,label='frame',_network_check=True):
        self.stop()
        if not hasattr(self,'heartbeat_publisher'):
            self.heartbeat_publisher=HeartbeatPublisher(self.out/'heartbeat.json',self.log,self.stop)
        self.heartbeat_publisher.publish({'pid':os.getpid(),'time':time.time(),'turn':self.turn,
             'cpu_verified':self.cpu_verified,'started_cpu':self.started and not getattr(self.args,'ranked',False),
             'started_ranked':self.started and getattr(self.args,'ranked',False) and getattr(self.args,'mode',None) in (None,'ranked'),
             'started_grand_prix':self.started and getattr(self.args,'mode',None)=='grand_prix',
             'started_weekend_plaza':self.started and getattr(self.args,'mode',None)=='weekend_plaza','pending':self.pending})
        deadline=time.monotonic()+30
        while True:
            with self.metrics.span('capture'):raw=self.t.capture_raw()
            if abs(raw.width/raw.height-16/9)<.03:
                with self.metrics.span('frame_normalize'):self.im=raw.resize((1280,720))
                break
            if time.monotonic()>deadline:
                self.evidence.image(raw,'unexpected_orientation.png')
                raise RuntimeError('Portrait page persisted for 30 seconds; no combat input sent')
            self.stop();time.sleep(1)
        for event in self.t.window.events:
            self.log('window_recovery',**{k:v for k,v in event.items() if k!='event'},kind_detail=event['event'])
        self.t.window.events.clear()
        self.seq+=1
        self.frame=f'{self.seq:05d}_{label}.png'
        if not getattr(self,'is_nightmare',False):
            with self.metrics.span('evidence_png'):self.evidence.image(self.im,self.frame)
        # Nightmare keeps lightweight events and only decision/anomaly images.
        if getattr(self,'is_nightmare',False):nightmare_runtime.audit_observe(self)
        if _network_check and hasattr(self,'v') and not self.args.inspect:
            self.check_network()
        # Terminal animations also occur inside action settling, before the main
        # loop sees RESULT. Latch evidence here without sending any extra input.
        if self.started and self.outcome=='unknown' and hasattr(self,'v') and not getattr(self,'rematch_pending',False):
            # A current readable turn button proves the normal battlefield.
            # Broad terminal OCR is needed when that surface disappears, not
            # after every intermediate gesture/capture on the same turn.
            local_ui=label in ('input_attack_target','input_attack_toast','after_expand_hand','after_close_hover_panel',
                               'after_select_evolution_source','after_close_card_panel','reread_enemy_ward')
            field_phase=True if local_ui else self.v.turn(self.im)
            if field_phase is None and label in ('action_settling','discard_settling','after_play','after_evolve','after_super_evolve','after_attack'):
                anchor=cv2.cvtColor(np.asarray(self.im.crop((1105,410,1155,450))),cv2.COLOR_RGB2GRAY)
                if float(cv2.matchTemplate(anchor,self.v.turn_marker,cv2.TM_CCOEFF_NORMED).max())>.78 and self.seq%4!=0:
                    field_phase='SETTLING'
            verdict=terminal_outcome(self.v,self.im,self.leader_refs) if field_phase is None else None
            if verdict:
                self.outcome=verdict['outcome'];self.log('outcome',frame=self.frame,**verdict)
        return self.im

    def check_network(self):
        if getattr(self,'_reconnecting',False):return
        if self.started and self.v.turn(self.im) in ('OUR','ENEMY'):
            self.network_watch.reset()
            return
        now=time.monotonic()
        if now-getattr(self,'network_sample_at',0)<1.5:return
        self.network_sample_at=now
        text=' '.join(r['text'] for r in self.v.ocr(self.im,(150,100,1130,640)))
        if self.network_watch.sample(text,time.monotonic())!='clear':
            self.reconnect('network_dialog')

    def reconnect(self,trigger):
        from swb.android_recovery import recover_in_run
        began=time.monotonic();self._reconnecting=True
        try:
            receipt=recover_in_run(ROOT,self,trigger)
            self.network_recovery_receipt=receipt
            if receipt.get('mode')=='safe_lobby' and (self.started or receipt.get('apps_restarted')):
                raise RecoveredToLobby('Reconnect verified a safe lobby; old battle actions will not be replayed')
            self.network_watch.reset();self.nav_repeat.clear();self.last_nav_scene=None
            self.last_progress=time.monotonic()
            self.log('reconnect_continued',trigger=trigger,receipt=receipt)
        finally:
            self._reconnecting=False
            elapsed=time.monotonic()-began;self.began+=elapsed
            if self.turn_began is not None:self.turn_began+=elapsed

    def reset_unknown_page_recovery(self):
        self.unknown_page_counts={};self.unknown_page_taps=0;self.battle_unknown_since=None

    def recover_unknown_page(self,scene,terminal=False):
        """Bounded overlay dismissal before escalating an unidentified page.

        Each tap goes through the normal STOP/checkpoint/observation boundary.
        A readable battlefield or unresolved effect still owns its own inputs.
        """
        if scene!='UNKNOWN' or (self.active_effect and not terminal) or self.v.turn(self.im):return False
        rows=self.texts((20,20,1250,700))
        text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
        from swb.network_recovery import network_evidence,navigation_modal
        if network_evidence(text):return False
        # Never dismiss purchase/security prompts or tap during matching/loading.
        text_clean=re.sub(r'附[贈赠][購购][買买]特典[！!]*(?:[組组]合包)?|[購购][買买]不同氛[圍围]的主[題题]','',text)
        if re.search(r'購買|购买|購入|支付|付款|儲值|充值|課金|消費|消费|認證|认证|驗證|验证|授權|授权|刪除|删除|配對中|匹配中|讀取中|读取中|連線中|连线中|LOADING|MATCHING',text_clean,re.I):
            return False
        if navigation_modal(rows):return False
        # Do not interfere with the pre-mulligan deal/versus animation.
        if self.started and not self.mulligan_done and not terminal:return False
        now=time.monotonic()
        if self.started and self.mulligan_done and not terminal:
            since=getattr(self,'battle_unknown_since',None)
            if since is None:
                self.battle_unknown_since=now;return False
            if now-since<4.5:return False
        if terminal and now-getattr(self,'terminal_began',now)<2:return False
        episode='terminal' if terminal else 'navigation'
        if terminal and re.search(r'分[組组][升降][級级]|[階阶][級级]升[級级]|升格[戰战]|グループランク|ランクアップ|RANK\s*UP|GROUP\s*UP|Grand\s*Master|Master|評級|评级',text,re.I):episode='terminal_group_promotion'
        limit=8 if episode=='terminal_group_promotion' else 2
        counts=getattr(self,'unknown_page_counts',{})
        attempts=counts.get(episode,0)
        if attempts>=limit or now-getattr(self,'unknown_page_last_tap',-10)<1:return False
        counts[episode]=attempts+1;self.unknown_page_counts=counts
        self.unknown_page_taps=attempts+1;self.unknown_page_last_tap=now
        if terminal:self.terminal_began=now
        self.log('unknown_page_recovery',attempt=attempts+1,limit=limit,episode=episode,
                 terminal=terminal,scene=scene,frame=self.frame,text=text)
        # Central neutral screen region, away from matchmaking/settlement buttons.
        tap_pt=[640,560]
        if episode=='terminal_group_promotion':
            for r in rows:
                t_btn=re.sub(r'\s+','',r.get('text',''))
                cx,cy=r.get('center',[0,0])
                if re.fullmatch(r'OK|關閉|关闭|確認|确认|NEXT',t_btn,re.I) and 480<=cx<=800 and 500<=cy<=680:
                    tap_pt=[int(cx),int(cy)];break
        self.act('dismiss_unknown_page',tap_pt,tap_pt,delay=1)
        self.log('unknown_page_reobserved',attempt=attempts+1,
                 scene=self.v.scene(self.im)[0],frame=self.frame)
        return True

    def act(self,name,start,end=None,delay=.8,**facts):
        self.stop()
        if name in ('start_ranked','start_grand_prix','grand_prix_free_entry','start_weekend_plaza','weekend_plaza_entry') and self.panel_managed and (ROOT/'PANEL_FINISH').exists():
            raise InterruptedError('Finish-after-match requested before matchmaking input')
        row={'name':name,'start':start,'end':end,'before':self.frame,'facts':facts,'stage':'entered'}
        self.pending=row; dump(self.out/'pending.json',row)
        def trace(stage):
            if stage=='target':
                time.sleep(.08)
                self.observe('input_attack_toast',_network_check=False)
                self.log('input_trace',name=name,stage=stage,frame=self.frame)
                self.last_attack_denial=attack_denial(self.v,self.im)
                if self.last_attack_denial:self.log('attack_denied',frame=self.frame,rejection=self.last_attack_denial)
        if name=='attack':self.last_attack_denial=None
        with self.metrics.span('input'):
            if name=='attack':self.t.gesture(start,end,trace=trace)
            else:self.t.gesture(start,end)
        self.actions[name]+=1
        self.last_input_name=name
        row['stage']='dispatched'; dump(self.out/'pending.json',row)
        with self.metrics.span('animation_wait'):time.sleep(delay)
        self.observe('after_'+name)
        row['after']=self.frame; row['stage']='observed';row['outcome']='sent'
        self.log('action',**row)
        self.pending=None; dump(self.out/'pending.json',None)
        return self.im

    def texts(self,box=None):return self.v.ocr(self.im,box)

    @staticmethod
    def center(row):
        x,y,w,h=row['box'];return [x+w//2,y+h//2]

    def button(self,pattern,box=None):
        for r in self.texts(box):
            if re.search(pattern,r['text']):return self.center(r)
        return None

    def confirm(self,name,condition,**data):
        outcome=data.pop('outcome','applied' if condition else 'unconfirmed')
        self.log('receipt',name=name,verified=bool(condition),outcome=outcome,**data)
        if condition:self.verified[name]+=1;self.last_progress=time.monotonic()

    def navigation(self,scene):
        if not self.started:
            title=self.button('TAP.*START|TOUCH.*START|點擊.*開始|点击.*开始',(350,560,930,680))
            if title:
                self.act('title_start',title,delay=2);self.last_progress=time.monotonic();return True
        if getattr(self.args,'ranked',False):return ranked_navigation(self,scene)
        if scene in ('RANKED_READY','RANKED_SETUP') and not self.started:
            self.act('leave_rank_setup_for_home',[640,650],delay=2)
            self.last_progress=time.monotonic();return True
        if scene=='UNKNOWN' and not self.started:
            home=self.button('^(主頁面|主页面)$',(560,590,725,715))
            if home and self.button('階級對戰|阶级对战',(40,80,550,240)):
                self.act('leave_rank_setup_for_home',home,delay=1)
                self.last_progress=time.monotonic();return True
        if scene=='UNKNOWN' and self.button('顯示QR|显示QR',(990,35,1190,95)) and self.button('[龍龙]族.*高[級级]',(25,35,460,100)):
            self.act('close_cpu_deck_preview',[1214,70],delay=1);return True
        if scene=='UNKNOWN' and not self.started and self.button('練習模式|练习模式',(30,20,330,90)):
            if self.button('請選擇牌組|请选择牌组',(50,450,460,590)):scene='PRACTICE_SETUP'
            else:scene='PRACTICE_READY' if self.cpu_verified else 'PRACTICE_OWN_READY'
        if scene=='CPU_PICK':
            if not any('2' in r['text'] for r in self.texts((525,195,765,253))):
                self.act('cpu_next_page',[1208,430],delay=1.2);return True
        if self.last_nav_scene=='CPU_PICK' and self.button('^(決定|确定)$',(635,515,900,590)):
            scene='CPU_CONFIRM'
        if scene=='CPU_CONFIRM' or (scene=='UNKNOWN' and self.button('高[級级]',(380,330,760,440))):
            label=' '.join(r['text'] for r in self.texts((380,330,760,440)))
            if not re.search('[龍龙]族.*高[級级]',label):
                raise RuntimeError('CPU confirmation is not verified advanced Dragon: '+label)
            self.cpu_verified=True;self.log('cpu_verified',label=label,frame=self.frame)
            scene='CPU_CONFIRM'
        if scene=='PRACTICE_READY' and not self.cpu_verified:
            self.act('reselect_cpu',[1010,523],delay=2);return True
        routes={'HOME':('open_solo',[272,652]),'SOLO':('open_practice',[255,329]),
                'PRACTICE_MENU':('practice_mode',[548,339]),
                'PRACTICE_SETUP':('choose_deck',[268,525]),
                'PRACTICE_DECK_PICK':('select_existing_deck',[1007,568] if self.is_nightmare else [1007,452]),
                'PRACTICE_DECK_CONFIRM':('confirm_deck',[767,552]),
                'PRACTICE_OWN_READY':('choose_cpu',[1010,523]),
                'CPU_PICK':('choose_advanced_cpu',self.config['cpu_pick']),
                'CPU_CONFIRM':('confirm_cpu',[767,551]),
                'PRACTICE_READY':('start_cpu',[641,515]),
                'REWARD_RECEIVED':('dismiss_reward',[640,653]),
                'GUILD_EVENT':('close_event',[1175,72])}
        if scene=='RESULT' and not self.started:
            # Select the CPU explicitly; the retained result may refer to a different difficulty.
            self.nav_repeat['return_home_result']+=1
            if self.nav_repeat['return_home_result']>4:raise RuntimeError('Navigation did not advance: return_home_result')
            self.act('return_home',[1145,635],delay=3)
            self.last_progress=time.monotonic();return True
        if scene in routes:
            # A loading frame can still look exactly like the preceding page.
            if scene==self.last_nav_scene and time.monotonic()-self.last_nav_at<7:
                time.sleep(.7);return True
            name,point=routes[scene]
            self.nav_repeat[name]+=1
            if self.nav_repeat[name]>4:
                raise RuntimeError(f'Navigation did not advance: {name}')
            self.last_nav_scene=scene;self.last_nav_at=time.monotonic()
            self.act(name,point,delay=3)
            if name=='start_cpu':self.started=True
            self.last_progress=time.monotonic();return True
        return False

    def mulligan(self):
        if self.mulligan_done:return
        opening=[]
        for x in (273,477,681,885):
            self.observe('mulligan_slot')
            cost=self.v.cost_at(self.im,(x-98,403,x-49,457))
            ident=self.v.identify_patch(self.im,(x-18,478,x+18,518),self.v.picker_refs)
            cid=ident.get('card_id')
            opening.append({'xy':[x,520],'cost':cost,'card_id':cid,'frame':self.frame})
        order_text=' '.join(r['text'] for r in self.texts((0,0,1280,720)))
        self.play_order='second' if re.search('後攻|后攻|後手|后手',order_text) else 'first' if re.search('先攻|先手',order_text) else None
        opening=(nightmare.mulligan_plan if self.is_nightmare else mulligan_plan)(opening,getattr(self,'opponent_class',None),self.play_order)
        self.log('opening_hand',cards=opening,opponent_class=getattr(self,'opponent_class',None),
                 unknown_identity_is_not_a_gate=True)
        if hasattr(self,'dragon_telemetry'):
            self.dragon_telemetry.record_mulligan(opening,getattr(self,'opponent_class',None),self.play_order)
        for card in opening:
            if not card['keep']:
                x=card['xy'][0]
                self.act('mulligan_replace',[x,520],[x,205],delay=.6,cost=card['cost'],
                         card_id=card['card_id'],reason=card['reason'])
        self.act('mulligan_confirm',[1152,365],delay=2)
        self.mulligan_done=True;self.started=True;self.last_progress=time.monotonic()

    def annotate(self,unit):
        if not unit.get('card_id'):unit.update(self.v.identify_board(self.im,unit))
        cid=unit.get('card_id');meta=self.strategy['cards'].get(cid,{})
        unit['threat_level']=meta.get('threat_level',1)
        unit['threat_basis']=meta.get('confidence','unknown_generic')
        flags=meta.get('abilities',{})
        for k,value in flags.items():unit.setdefault(k,value)
        keywords=getattr(self.v,'cards',{}).get(cid,{}).get('keywords',{}).get('chs',{}).get('common',[])
        if '威慑' in keywords or unit.get('intimidate'):
            unit.update(intimidate=True,attackable=False,ward=False)
        if '灵气' in keywords:unit['aura']=True
        if '毁灭' in keywords:unit.setdefault('bane',True)
        if '潜行' in keywords:unit.setdefault('stealth',True)
        if '屏障' in keywords:unit.setdefault('barrier',True)
        return unit

    def snapshot(self,skip_hand=False):
        if self.is_nightmare:self.v.read_evidence={}
        enemy=self.v.board(self.im,'enemy')
        if any(u.get('super') and not u['ward'] for u in enemy):
            for _ in range(2):
                with self.metrics.span('animation_wait'):time.sleep(.2)
                self.observe('reread_enemy_ward',_network_check=False)
                for fresh in self.v.board(self.im,'enemy'):
                    old=next((u for u in enemy if abs(u['xy'][0]-fresh['xy'][0])<35),None)
                    if old:
                        old['ward']=old['ward'] or fresh['ward']
                        for k in ('attack','health'):
                            if old[k] is None:old[k]=fresh[k]
        for saved,point in getattr(self,'rejected_targets',[]):
            score,x=presence(saved,self.im,point)
            if score>.8:
                target=min(enemy,key=lambda u:abs(u['xy'][0]-x),default=None)
                if target and abs(target['xy'][0]-x)<35:
                    target.update(attackable=False,ward=False,attackability_basis='explicit_game_rejection_this_turn')
        pp,maximum=self.v.pp(self.im)
        display_maximum=maximum
        # Vision.pp returns the printed permanent denominator, even with +1 PP.
        own=self.remember_units(self.v.board(self.im,'own'),existing=(getattr(self,'turn_summon_actions',0)==0))
        hand=list(self.last_state.get('hand') or []) if (skip_hand and self.last_state.get('hand') is not None) else self.v.hand(self.im)
        ep_evidence=self.v.evolution_resource(self.im,'evolve');ep=ep_evidence['filled']
        sep_evidence=self.v.evolution_resource(self.im,'super_evolve');sep=sep_evidence['filled']
        extra=self.v.extra_pp(self.im)
        if extra.get('enabled'):self.play_order='second'
        for u in enemy:self.annotate(u)
        if any(u.get('card_id')=='10744110' and u.get('super') for u in enemy):self.own_needle=True
        state={'pp':pp,'max_pp':maximum,'hand':hand,'own':own,
               'play_order':getattr(self,'play_order',None),'own_needle':getattr(self,'own_needle',None),
               'lethal_evolution_card':getattr(self,'turn_finish_evolution',None),
               'enemy':[self.annotate(u) for u in enemy],
               'own_hp':self.v.own_leader_hp(self.im),'enemy_hp':self.v.enemy_leader_hp(self.im),
               'ep_available':ep>0 and not self.evolved,'super_available':sep>0 and not self.evolved,
               'ep_left':ep,'sep_left':sep,'evolution_resource_evidence':{'ep':ep_evidence,'sep':sep_evidence},'emblem_done':self.emblem_done,
               'matchup':'ranked' if getattr(self.args,'ranked',False) else 'dragon_advanced','turn':self.turn,
               'opponent_class':getattr(self,'opponent_class',None),
               'display_max_pp':display_maximum,'extra_pp':extra,
               'extra_pp_attempted_phases':sorted(getattr(self,'extra_pp_attempts',set())),
               'blocked_attacks':list(getattr(self,'blocked_attacks',[])),
               'attacked_uids':sorted(self.attacked),
               'attacked_leader_last_turn':bool(getattr(self,'attacked_leader_last_turn',False)),
               'attacked_leader_this_turn':bool(getattr(self,'attacked_leader_this_turn',False))}
        signature=Counter(c['id'] for c in hand)
        if signature!=self.last_hand:
            added=signature-(self.last_hand or Counter());removed=(self.last_hand or Counter())-signature
            self.log('hand_change',observed_added=dict(added),observed_removed=dict(removed),
                     cards=hand,frame=self.frame,basis='observed identities; draw/add/transform not distinguished')
            self.last_hand=signature
        if self.is_nightmare:
            for i,u in enumerate(state['enemy']):u['uid']='enemy'+str(i)
            state=nightmare_runtime.snapshot(self,state)
        self.last_state=state
        return state

    def remember_units(self,units,existing=False):
        # A transition frame is not evidence that every follower died.
        if not units and (self.v.turn(self.im) is None or existing):return []
        unused=list(self.unit_memory);memory=[]
        for unit in sorted(units,key=lambda u:u['xy'][0]):
            x,y=unit['xy']
            patch=cv2.cvtColor(np.asarray(self.im.crop((x-25,y-53,x+25,y-3))),cv2.COLOR_RGB2GRAY)
            if not unit.get('card_id'):
                # Match the current portrait pixels before invoking the full
                # card-art bank. This only shortcuts identity, never HP,
                # readiness, targets, or the existing uid reconciliation.
                portrait_matches=[(float(cv2.matchTemplate(patch,m['patch'],cv2.TM_CCOEFF_NORMED)[0,0]),m)
                                  for m in unused if m.get('card_id') and m['patch'].shape==patch.shape]
                portrait_matches.sort(key=lambda row:row[0],reverse=True)
                if portrait_matches:
                    score,known=portrait_matches[0]
                    different=max((s for s,m in portrait_matches if m['card_id']!=known['card_id']),default=-1.)
                    if score>=.92 and score-different>=.06:
                        unit.update(card_id=known['card_id'],identity_score=round(score,3),identity_basis='current_portrait_matches_recent_reference')
            self.annotate(unit)
            def affinity(m):
                score=float(cv2.matchTemplate(patch,m['patch'],cv2.TM_CCOEFF_NORMED)[0,0])
                if unit.get('card_id') and unit.get('card_id')==m.get('card_id'):
                    score=max(score,.86-min(abs(x-m['xy'][0]),300)/2000)
                # Attack flashes can obscure the portrait, without replacing
                # the surviving follower. Keep its consumed attack and sickness.
                if (getattr(self,'last_input_name',None)=='attack' and len(units)==len(self.unit_memory)
                    and abs(x-m['xy'][0])<35 and unit.get('attack') is not None
                    and unit.get('attack')==m.get('attack') and unit.get('health')==m.get('health')
                    and not (unit.get('card_id') and m.get('card_id') and unit['card_id']!=m['card_id'])):
                    score=max(score,.8)
                return score
            match=max(unused,key=affinity,default=None)
            score=affinity(match) if match else -1
            if match is not None and score>.65:
                unused.remove(match);uid=match['uid'];face=existing or match['can_face']
                for k in ('card_id','bane','rush','aura','no_evolve','summoned_turn','storm','ignore_ward','drain','ephemeral','stealth','intimidate'):
                    if k in match:unit[k]=match[k]
                unit['evolved']=bool(unit.get('evolved') or match.get('evolved'))
                unit['super']=bool(unit.get('super') or match.get('super'))
            else:
                self.next_unit+=1;uid=self.next_unit;face=existing;unit['summoned_turn']=self.turn if not existing else 0
            unit.update(uid=uid,can_face=face);self.annotate(unit)
            combo_active=bool(getattr(self,'attacked_leader_last_turn',False))
            if (unit.get('card_id') in ('10644110','10942110')
                    or (unit.get('card_id')=='10944110' and combo_active)
                    or (unit.get('card_id')=='10941110' and combo_active and unit.get('summoned_turn')==self.turn)
                    or (unit.get('card_id')=='11044120' and unit.get('super'))
                    or unit.get('storm')):
                unit['can_face']=True;unit['storm']=True
            if unit.get('card_id')=='10644110':
                unit['bane']=True;unit['aura']=True
            if unit.get('card_id')=='10944110':
                unit['ignore_ward']=True;unit['rush']=True
            if getattr(self,'force_ready_face_this_turn',False) and unit.get('uid') not in self.attacked:
                unit['can_face']=True;unit['ready']=True;unit['rush']=True
            if self.is_nightmare and (unit.get('storm') or unit.get('card_id') in (nightmare.ANSE,nightmare.GHOST)):unit['can_face']=True
            # Known summoning sickness and printed Rush/Storm beat ambient glow.
            born=unit.get('summoned_turn')
            can_attack=(unit['can_face'] or unit.get('evolved') or unit.get('super') or unit.get('rush'))
            if born==self.turn and not can_attack:unit['ready']=False
            elif can_attack:unit['ready']=True
            if unit.get('uid') in self.attacked:unit['ready']=False
            memory.append({**unit,'patch':patch})
        self.unit_memory=memory
        return units

    def settle_action(self,card_id=None,timeout=4.5):
        """Wait for this action's battlefield or real effect picker, without clearing state."""
        deadline=time.monotonic()+timeout
        previous=None;stable=0;previous_targets=None
        while time.monotonic()<deadline:
            if self.outcome!='unknown':return False
            phase=self.v.turn(self.im)
            field=cv2.resize(cv2.cvtColor(np.asarray(self.im.crop((285,145,1060,495))),cv2.COLOR_RGB2GRAY),(155,70))
            delta=float(np.abs(field.astype(float)-previous.astype(float)).mean()) if previous is not None else 999
            stable=stable+1 if phase and delta<6.0 else 0
            if stable>=1:return True
            if card_id and not phase:
                discard_grid=False
                req=self.v.discard_requirement(card_id)
                if req:
                    cards=self.v.picker_cards(self.im)
                    discard_grid=bool(cards)
                    signature=tuple(sorted(tuple(c['xy']) for c in cards))
                    if signature and (len(signature)>=req or previous_targets):
                        return True
                    previous_targets=signature
                if not discard_grid and card_id=='10741120' and (getattr(self,'active_effect',None) or {}).get('kind') in ('evolve','super_evolve'):
                    cards=self.v.picker_cards(self.im)
                    signature=tuple(sorted(tuple(c['xy']) for c in cards))
                    if signature and (len(signature)>=1 or previous_targets):
                        return True
                    previous_targets=signature
                elif not discard_grid and card_id in ('10741120','10641310'):
                    own_targets=self.v.board(self.im,'own')
                    signature=tuple(tuple(u['xy']) for u in own_targets)
                    if signature and previous_targets and len(signature)==len(previous_targets) and all(
                            abs(x-px)<=4 and abs(y-py)<=4 for (x,y),(px,py) in zip(signature,previous_targets)):
                        return True
                    previous_targets=signature
                if not discard_grid and card_id in ('10444110','10642310','10843310'):
                    if card_id in ('10444110','10642310'):targets=self.v.highlighted_followers(self.im)
                    else:
                        targets,face=self.v.effect_targets(self.im)
                        if face:targets=targets+[{'xy':[640,80]}]
                    signature=tuple(tuple(u['xy']) for u in targets)
                    # Orange outlines breathe by 1-2 px while background effects
                    # continue. Compare local targets, not animated whole-field pixels.
                    if signature and previous_targets and len(signature)==len(previous_targets) and all(
                            abs(x-px)<=4 and abs(y-py)<=4 for (x,y),(px,py) in zip(signature,previous_targets)):
                        return True
                    previous_targets=signature
                if req or card_id in ('10741120','10641310','10444110','10642310','10843310'):
                    text=' '.join(r['text'] for r in self.texts((440,80,1240,160)))
                    if re.search('捨棄|舍弃|模式|選擇|选择',text):return True
            if not phase and self.v.scene(self.im)[0]=='RESULT':return False
            previous=field
            with self.metrics.span('animation_wait'):time.sleep(.18)
            self.observe('action_settling',_network_check=False)
        self.log('local_missing',field='action_surface',card=card_id)
        return False

    def resolve_discard(self,cid,required):
        """One effect transaction: rank legal cards, handle auto-commit or confirmation."""
        if not hasattr(self,'discard_transactions'):self.discard_transactions={}
        key=(getattr(self,'effect_serial',0),cid)
        tx=self.discard_transactions.setdefault(key,{'sent':[],'confirmed_button':False,'completed':False})
        if tx['completed']:return True
        candidates=self.v.picker_cards(self.im)
        for _ in range(2):
            if candidates:break
            self.observe('discard_candidates_reread');candidates=self.v.picker_cards(self.im)
        if not candidates:raise RuntimeError('Discard picker unreadable after local rereads')
        needed=min(required,len(candidates))
        context={**self.last_state,'discard_source':cid}
        planned=(getattr(self,'active_effect',None) or {}).get('plan',{})
        if planned.get('policy_tier')=='finish':
            context['lethal_components']=planned.get('damage_timing',{}).get('followup_ids',[])
        ranked=discard_plan(candidates,context,len(candidates),cid)
        self.log('discard_plan',card=cid,required=required,available=len(candidates),needed=needed,
                 ranking=ranked,already_sent=tx['sent'],selected_state_basis='recorded taps; completion checked separately')
        for candidate in ranked:
            if len(tx['sent'])>=needed:break
            if any(abs(candidate['xy'][0]-c['xy'][0])<30 and abs(candidate['xy'][1]-c['xy'][1])<30 for c in tx['sent']):continue
            current=self.v.picker_cards(self.im)
            legal=next((c for c in current if abs(c['xy'][0]-candidate['xy'][0])<30 and abs(c['xy'][1]-candidate['xy'][1])<30),None)
            if legal is None:continue
            tx['sent'].append(legal) # Never toggle an uncertain selection by replaying it.
            self.act('discard_select',legal['xy'],delay=.5,card=legal,required=required,
                     selected_sent=len(tx['sent']),reason='existing_discard_rank',ranking=ranked)
            if self.v.turn(self.im)=='OUR':break
        deadline=time.monotonic()+8
        absent=0;settling=False
        while time.monotonic()<deadline:
            self.stop()
            phase=self.v.turn(self.im);remaining=self.v.picker_cards(self.im) if not phase else []
            # Match the main loop's full-screen terminal-text fallback: oversized
            # WIN lettering can be missed by observe()'s narrower OCR crop.
            if self.outcome=='unknown' and not phase and not remaining:
                text=' '.join(r['text'] for r in self.texts((20,20,1250,700)))
                if re.search(r'\b(WIN|VICTORY|LOSE|DEFEAT)\b',text,re.I):
                    self.outcome='win' if re.search(r'\b(WIN|VICTORY)\b',text,re.I) else 'loss'
                    self.log('outcome',outcome=self.outcome,evidence='terminal_text',text=text,frame=self.frame)
            # Effects can end a match without ever restoring the turn button.
            # Return to the main result handler; do not infer a win from loading.
            if self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT':
                tx['stage']='terminal';self.active_effect=None
                self.log('discard_terminal',card=cid,selected=tx['sent'],
                         selection_completed=tx['completed'],frame=self.frame)
                return True
            next_effect=False
            if cid=='10642310' and not remaining and not phase:
                next_effect=bool(self.v.highlighted_followers(self.im) or
                                 self.button('^(赤流|赤色奔流)$',(15,310,275,390)))
            if phase=='OUR' or next_effect:
                tx['stage']='next_effect' if next_effect else 'resolved'
                if not tx['completed']:
                    self.confirm('discard',True,card=cid,required=required,selected=tx['sent'],
                                 resolution='confirm_button' if tx['confirmed_button'] else 'automatic',
                                 evidence='next_effect_picker' if next_effect else 'battlefield_restored',frame=self.frame)
                tx['completed']=True
                if hasattr(self,'dragon_telemetry'):
                    self.dragon_telemetry.record_discard(self.turn,cid,tx['sent'],candidates)
                self.log('discard_effect_settled',card=cid,stage=tx['stage'],frame=self.frame)
                if not next_effect:self.active_effect=None
                return True
            if settling:
                if remaining:
                    raise RuntimeError('Discard picker reappeared after submission; no duplicate selection')
            else:
                # Taps alone are not proof. Repeated picker disappearance plus
                # spent PP confirms submission, not the subsequent effect/winner.
                pp,_=self.v.pp(self.im) if not remaining else (None,None)
                before_pp=self.last_state.get('pp')
                is_evo_discard=(getattr(self,'active_effect',None) or {}).get('kind') in ('evolve','super_evolve')
                paid=(pp is not None and before_pp is not None and pp<before_pp) or is_evo_discard
                absent=absent+1 if not remaining and len(tx['sent'])>=needed and paid else 0
                if absent>=2:
                    tx['completed']=True;tx['stage']='effect_settling'
                    settling=True;deadline=time.monotonic()+30
                    self.confirm('discard',True,card=cid,required=required,selected=tx['sent'],
                                 evidence='picker_absent_twice_and_pp_spent',before_pp=before_pp,
                                 after_pp=pp,stage='effect_settling',frame=self.frame)
            if not settling and len(tx['sent'])>=needed and not tx['confirmed_button']:
                point=self.button('^(決定|决定|確定|确定|確認|确认)$',(800,260,1250,690))
                if point:
                    tx['confirmed_button']=True
                    self.act('discard_confirm',point,delay=.6,required=required,selected_sent=len(tx['sent']))
                    continue
            with self.metrics.span('animation_wait'):time.sleep(.3)
            self.observe('discard_settling')
        if settling:
            self.log('effect_settlement_timeout',card=cid,selection_completed=True,frame=self.frame)
            raise RuntimeError('Submitted discard effect did not reach a turn, target or result within 30 seconds')
        self.confirm('discard',False,card=cid,required=required,selected=tx['sent'],reason='picker_did_not_resolve',frame=self.frame)
        raise RuntimeError('Discard picker did not resolve after bounded local rereads; no duplicate selection')

    def clear_effect_on_phase_exit(self):
        """A closed prompt is no longer actionable; this does not prove damage."""
        phase=self.v.turn(self.im)
        terminal=self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT'
        if self.active_effect and (phase in ('OUR','ENEMY') or terminal):
            self.log('effect_phase_exit',effect=self.active_effect,phase=phase,
                     terminal=terminal,receipt='unconfirmed',frame=self.frame)
            self.active_effect=None;self.last_progress=time.monotonic()
        return terminal or phase in ('OUR','ENEMY')

    def resolve_hand_buff(self,cid):
        """Select a follower card in hand to receive +2/+2 from Carrier Dragon's Evolution."""
        candidates=self.v.picker_cards(self.im)
        for _ in range(2):
            if candidates or self.v.turn(self.im)=='OUR':break
            self.observe('hand_buff_candidates_reread');candidates=self.v.picker_cards(self.im)
        if not candidates:
            if self.v.turn(self.im)=='OUR':
                self.active_effect=None;return True
            return False
        ranked=hand_buff_plan(candidates,self.last_state)
        chosen=ranked[0]
        self.log('hand_buff_plan',card=cid,available=len(candidates),chosen=chosen,ranking=ranked)
        self.act('hand_buff_select',chosen['xy'],delay=.5,card=chosen)
        if self.v.turn(self.im)!='OUR':
            point=self.button('^(決定|决定|確定|确定|確認|确认)$',(800,260,1250,690))
            if point:self.act('hand_buff_confirm',point,delay=.5)
        settled=self.settle_action(timeout=6)
        if settled or self.v.turn(self.im)=='OUR':
            self.active_effect=None
            if hasattr(self,'dragon_telemetry'):
                self.dragon_telemetry.record_hand_buff(self.turn,cid,chosen,candidates)
            self.confirm('hand_buff',True,card=cid,chosen=chosen,frame=self.frame)
            return True
        return False

    def resolve_prompt(self,card=None):
        if self.is_nightmare:return nightmare_runtime.resolve_prompt(self,card)
        cid=(card or {}).get('id');state=self.last_state
        eff_kind=(getattr(self,'active_effect',None) or {}).get('kind')
        for attempt in range(4):
            self.stop()
            if self.clear_effect_on_phase_exit():return True
            # A live end-turn label means the spell has already resolved.
            if self.button('結束|结束',(1100,265,1230,380)):return
            rows=self.texts((15,100,1260,620));text=' '.join(r['text'] for r in rows)
            count=self.v.discard_requirement(cid)
            discard_key=(getattr(self,'effect_serial',0),cid)
            discard_done=getattr(self,'discard_transactions',{}).get(discard_key,{}).get('completed',False)
            # The card description still says "discard" on the next target
            # picker. A completed selection must not re-enter that stage.
            if count and not discard_done and (re.search('[捨舍][棄弃]|[丟丢][棄弃]',text) or self.v.picker_cards(self.im)):
                resolved=self.resolve_discard(cid,count)
                if self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT':return True
                if cid!='10642310' or not resolved:return resolved
                if self.v.turn(self.im)=='OUR':return True
            if cid=='10741120' and (eff_kind in ('evolve','super_evolve') or self.v.picker_cards(self.im)):
                return self.resolve_hand_buff(cid)
            if cid in ('10741120','10641310') and eff_kind=='play':
                prior_own=list(state.get('own') or [])
                if not prior_own:
                    self.active_effect=None;return True
                live_own=self.v.board(self.im,'own')
                candidates=[]
                for old_u in prior_own:
                    matched=min(live_own,key=lambda u:abs(u['xy'][0]-old_u['xy'][0]),default=None) if live_own else None
                    if matched and abs(matched['xy'][0]-old_u['xy'][0])<95:
                        merged={**old_u,'xy':matched['xy']}
                    else:
                        merged=dict(old_u)
                    candidates.append(merged)
                chosen=choose_own_board_target(candidates,cid,state)
                if chosen:
                    key=(getattr(self,'effect_serial',0),cid,'own_board')
                    if not hasattr(self,'effect_attempts'):self.effect_attempts=set()
                    if key not in self.effect_attempts:
                        self.effect_attempts.add(key)
                        self.act('choose_own_effect_target',chosen['xy'],delay=.5,card=cid,target=chosen,candidates=candidates)
                        settled=self.settle_action()
                        self.confirm('own_board_buff',bool(settled),card=cid,target=chosen,frame=self.frame)
                        if settled or self.v.turn(self.im)=='OUR':
                            self.active_effect=None
                            return True
            if re.search('模式|能力.*選|選.*能力',text):
                rows=self.texts((515,210,1020,460))
                options=[r for r in rows if re.search('抽取|回復|恢復|恢复|給予|给予|使|傷害|生命',r['text'])]
                if not options:self.log('local_missing',field='mode_choices');return
                mode=choose_mode(state,self.settings)
                chosen=min(options,key=lambda r:r['box'][1]) if mode==1 else max(options,key=lambda r:r['box'][1])
                self.act('choose_mode',self.center(chosen),delay=3,mode=mode,reason='heal_or_minus_four',board=state.get('enemy'))
                point=self.button('決定|确定|確認|确认',(420,430,1240,690))
                if point:self.act('confirm_mode',point,delay=1.2)
                return
            if cid in ('10444110','10642310','10843310') or re.search('指定|選擇.*從者|選擇.*目標',text):
                targets=self.v.highlighted_followers(self.im) if cid in ('10444110','10642310') else self.v.effect_targets(self.im)[0]
                for u in targets:
                    old=min(state.get('enemy',[]),key=lambda x:abs(x['xy'][0]-u['xy'][0]),default=None)
                    if old and abs(old['xy'][0]-u['xy'][0])<40:
                        for k in ('ward','threat_level','card_id'):u[k]=old.get(k)
                chosen=choose_effect_target(targets,cid,state)
                face=False
                if cid=='10843310':
                    _,face=self.v.effect_targets(self.im)
                    if face and (state.get('enemy_hp')==1 or (getattr(self,'active_effect',None) or {}).get('plan',{}).get('policy_tier')=='finish' or not chosen or (chosen.get('health') or 99)>1):chosen=None
                point=chosen['xy'] if chosen else [640,80] if face else None
                if point:
                    key=(getattr(self,'effect_serial',0),cid,tuple(point))
                    if any(k[:2]==key[:2] for k in getattr(self,'effect_attempts',set())):
                        # Coordinates can breathe during animation. One submitted
                        # target per effect, even if its next detected centre moves.
                        if attempt<3:
                            time.sleep(.2);self.observe('effect_target_receipt_reread');continue
                        return False
                    if not hasattr(self,'effect_attempts'):self.effect_attempts=set()
                    self.effect_attempts.add(key)
                    self.act('choose_effect_target',point,delay=.5,card=cid,legal_targets=targets,reason='legal_effect_target')
                    settled=self.settle_action()
                    if cid=='10444110':
                        after=self.v.board(self.im,'enemy')
                        nearby=next((u for u in after if abs(u['xy'][0]-chosen['xy'][0])<30),None)
                        damage=nearby and chosen['health'] is not None and nearby['health'] is not None and nearby['health']<chosen['health']
                        self.confirm('wilnas_effect',bool(settled and (len(after)<len(targets) or damage)),target=chosen,before=targets,after=after,settled=settled)
                    if cid=='10642310':
                        after=self.v.board(self.im,'enemy')
                        self.confirm('red_effect',bool(settled and len(after)<len(targets)),target=chosen,
                                     before=targets,after=after,settled=settled,evidence='enemy_count_after_target_selection')
                    if settled:self.active_effect=None
                    return settled
                if cid=='10642310' and getattr(self,'discard_transactions',{}).get(discard_key,{}).get('completed'):
                    # One local reread before cancelling a now-empty target
                    # picker. Do not guess a target or repeatedly toggle it.
                    self.observe('red_targets_reread')
                    if self.v.highlighted_followers(self.im):continue
                    self.act('cancel_no_legal_effect',[1020,405],delay=.4,card=cid)
                    if self.settle_action(timeout=3) and self.v.turn(self.im)=='OUR':
                        self.active_effect=None;self.bad_cards.add(cid)
                        self.log('effect_cancelled',card=cid,reason='no_highlighted_legal_target');return False
                    raise RuntimeError('Cannot cancel targetless Red Torrent picker; stopped without target guesses')
                if attempt<3:
                    time.sleep(.2);self.observe('effect_targets_reread');continue
                key=(getattr(self,'effect_serial',0),cid)
                if not hasattr(self,'missing_effect_targets'):self.missing_effect_targets=set()
                if key not in self.missing_effect_targets:
                    self.missing_effect_targets.add(key)
                    self.log('local_missing',field='legal_effect_target',card=cid,
                             recovery='local_rereads_exhausted_wait_for_surface',frame=self.frame)
                return False
            return

    def evolve(self,state):
        if self.evolved or not state['own']:return False
        if self.turn<4:return False
        if not state.get('ep_available') and not state.get('super_available'):return False
        hypothetical={}
        if state.get('ep_available'):hypothetical['evolve']=[0,0]
        if state.get('super_available'):hypothetical['super_evolve']=[0,0]
        for unit in evolution_order(state['own'],state,self.settings):
            if unit.get('evolved') or unit.get('super'):continue
            if unit.get('card_id') and not preferred_evolution(unit,state,hypothetical,self.settings):
                continue
            self.act('select_evolution_source',unit['xy'],delay=.2)
            detected=self.v.evolution_buttons(self.im)
            available={k:p for k,p in detected.items() if self.v.evolution_enabled(self.im,p,k)}
            for row in self.texts((20,130,438,680)):
                if re.fullmatch('(超)?[進进]化',row['text'].strip()):
                    kind='super_evolve' if '超' in row['text'] else 'evolve';point=self.center(row)
                    if self.v.evolution_enabled(self.im,point,kind):available[kind]=point
            # Read the displayed title only when portrait identification is missing.
            if not unit.get('card_id'):
                title=' '.join(r['text'] for r in self.texts((20,20,438,110)))
                for cid,meta in self.strategy['cards'].items():
                    if any(alias and alias in title for alias in meta.get('aliases',[])):unit['card_id']=cid;break
            mode=preferred_evolution(unit,state,available,self.settings)
            if not mode:self.act('close_card_panel',[1020,405],delay=.2);continue
            resource_before=self.v.evolution_resource(self.im,mode)
            self.effect_serial=getattr(self,'effect_serial',0)+1
            self.turn_summon_actions=getattr(self,'turn_summon_actions',0)+1
            self.active_effect={'id':unit.get('card_id'),'kind':mode,'source':unit}
            self.act(mode,available[mode],delay=.35,source=unit,reason='card_specific_evolution',emblem_done=self.emblem_done)
            initial_settled=self.settle_action(unit.get('card_id'))
            before_prompt=self.im
            self.resolve_prompt({'id':unit.get('card_id')})
            if self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT' or self.v.turn(self.im)=='ENEMY':
                self.active_effect=None;self.evolved=True;return True
            settled=True if (initial_settled and self.im is before_prompt and self.v.turn(self.im)=='OUR') else self.settle_action(timeout=5)
            if not settled:
                self.active_effect=None;self.evolved=True
                self.confirm(mode,False,source=unit,reason='effect_or_animation_not_resolved')
                return True
            self.active_effect=None
            expected_after=min(5,len(state['own'])+(1 if unit.get('card_id')=='10943110' else 0))
            after=self.v.board(self.im,'own')
            for _ in range(4):
                if len(after)>=expected_after:break
                if self.outcome!='unknown' or self.v.turn(self.im)=='ENEMY':break
                with self.metrics.span('animation_wait'):time.sleep(.2)
                self.observe('wait_evo_board',_network_check=False)
                after=self.v.board(self.im,'own')
            resource_after=self.v.evolution_resource(self.im,mode)
            changed=min(after,key=lambda u:abs(u['xy'][0]-unit['xy'][0]),default=None)
            gain=changed and abs(changed['xy'][0]-unit['xy'][0])<55 and any(unit.get(k) is not None and changed.get(k) is not None and changed[k]>unit[k] for k in ('attack','health'))
            used=resource_after['filled']<resource_before['filled'];confirmed=bool(used or gain)
            if hasattr(self,'dragon_telemetry'):
                self.dragon_telemetry.record_evolution(self.turn,mode,unit,confirmed)
            self.confirm(mode,confirmed,source=unit,after=after,resource_before=resource_before,resource_after=resource_after)
            self.remember_units(after)
            for m in self.unit_memory:
                if abs(m['xy'][0]-unit['xy'][0])<55:
                    for k in ('uid','can_face','card_id','bane','aura','no_evolve'):
                        if k in unit:m[k]=unit[k]
                    m['evolved']=confirmed
                    if mode=='super_evolve' and confirmed:
                        m['super']=True
                        if unit.get('card_id')=='11044120':
                            m['can_face']=True;m['storm']=True;m['ward']=True;m['ready']=True
            if not confirmed and not used:self.act('close_card_panel',[1020,405],delay=.3);continue
            self.evolved=True
            if mode=='super_evolve' and confirmed:
                self.super_unit=unit['xy']
                if unit.get('card_id')=='10744110':
                    self.emblem_done=True;self.log('nine_emblem',basis='Nine identity and verified super evolution',frame=self.frame)
            return True
        self.evolved=True
        return False

    def attack(self,state,plan):
        unit=plan['source'];target=plan['target']
        before=self.im.copy()
        self.act('attack',unit['xy'],target,delay=.35,source=unit,plan=plan,
                 ward_first=any(e['ward'] for e in state['enemy']),enemy_candidates=state['enemy'])
        if self.last_attack_denial and self.last_attack_denial['kind']=='target':
            self.rejected_targets.append((before,target))
            self.confirm('attack',False,outcome='rejected_target',target=target,source_preserved=True,
                         evidence=self.last_attack_denial)
            self.last_progress=time.monotonic();return
        if self.last_attack_denial:
            self.attacked.add(unit['uid'])
            self.confirm('attack',False,outcome='rejected_source_or_rule',source=unit,
                         evidence=self.last_attack_denial)
            self.last_progress=time.monotonic();return
        after_act=self.im.copy()
        self.settle_action(timeout=2.0)
        first=self.im.copy()
        evidence=None
        if self.im is not after_act:
            with self.metrics.span('receipt_attack'):evidence=attack_evidence(self.v,before,[after_act,self.im],target)
        if not evidence or evidence['outcome']!='applied':
            with self.metrics.span('animation_wait'):time.sleep(.25)
            self.observe('attack_settled',_network_check=False)
            with self.metrics.span('receipt_attack'):evidence=attack_evidence(self.v,before,[first,self.im],target)
        if evidence['outcome']=='unconfirmed' and self.v.turn(self.im):
            first=self.im.copy()
            with self.metrics.span('animation_wait'):time.sleep(.25)
            self.observe('attack_receipt_reread',_network_check=False)
            with self.metrics.span('receipt_attack'):evidence=attack_evidence(self.v,before,[first,self.im],target)
        if target==[640,80] and self.outcome=='win':
            evidence={'outcome':'applied','evidence':'terminal_win_after_face_attack'}
        if evidence['outcome'] in ('not_applied','unconfirmed'):
            # Two settled frames must positively retain this same ready body.
            # Unknown HP alone neither proves a hit nor spends its attack.
            # A confirmed unchanged target/body also preserves the opportunity:
            # pending target-selection uses orange, not the green ready frame.
            def still_ready(image):
                raw=self.v.board(image,'own')
                source=min(raw,key=lambda u:abs(u['xy'][0]-unit['xy'][0]),default=None)
                score,x=presence(before,image,unit['xy'])
                # A network toast can cover the portrait but not its two stat
                # badges. One unchanged body cannot be confused with a copy.
                unique=(evidence['outcome']=='not_applied' and len(raw)==1
                        and len(state.get('own') or self.v.board(before,'own'))==1)
                identity=(score>.8 and abs(x-unit['xy'][0])<35) or unique
                return bool(source and abs(source['xy'][0]-unit['xy'][0])<35 and identity
                    and (source.get('ready') or evidence['outcome']=='not_applied') and all(source.get(k) is not None and source.get(k)==unit.get(k) for k in ('attack','health')))
            if still_ready(first) and still_ready(self.im):
                self.blocked_attacks.append((unit['uid'],target))
                self.confirm('attack',False,**evidence,source_preserved=True,skipped_pair=True,
                             readiness_basis='two_stable_same_body_and_unchanged_target' if evidence['outcome']=='not_applied' else 'two_stable_same_ready_body_frames')
                self.last_progress=time.monotonic();return
        self.attacked.add(unit['uid'])
        if hasattr(self,'dragon_telemetry'):
            self.dragon_telemetry.record_attack(self.turn,unit,target)
        if target==[640,80]:
            self.attacked_leader_this_turn=True
        if unit.get('card_id')=='11042120':
            self.remember_units(self.v.board(self.im,'own'))
        self.confirm('attack',evidence['outcome']=='applied',**evidence)

    def use_extra_pp(self,state):
        phase='before_turn6' if self.turn<6 else 'turn6_onward'
        if phase in self.extra_pp_attempts:return False
        plan=bonus_pp_plan(state,self.settings)
        if not plan:return False
        self.extra_pp_attempts.add(phase)
        self.log('extra_pp_plan',phase=phase,plan=plan,button=state['extra_pp'])
        self.act('extra_pp',state['extra_pp']['xy'],delay=.5,phase=phase,plan=plan)
        self.settle_action(timeout=3)
        pp,display_max=self.v.pp(self.im)
        # Extra PP is the +1 numerator, not an increase in the printed maximum.
        # Do not subtract it from subsequent ramp observations.
        applied=pp is not None and pp==state['pp']+1
        self.confirm('extra_pp',applied,phase=phase,before_pp=state['pp'],after_pp=pp,
                     permanent_max_pp=state['max_pp'],display_max_pp=display_max,
                     temporary_max_bonus=self.temporary_max_bonus,button_after=self.v.extra_pp(self.im))
        self.expanded=False
        return True

    def battle(self):
        phase=self.v.turn(self.im)
        self.clear_effect_on_phase_exit()
        if phase=='ENEMY':
            if self.is_nightmare:nightmare_runtime.audit_enemy(self)
            if self.last_phase!='ENEMY':self.last_progress=time.monotonic()
            self.last_phase='ENEMY'
            with self.metrics.span('enemy_wait'):time.sleep(.8)
            return
        if phase!='OUR':return
        if self.last_phase!='OUR':
            self.attacked_leader_last_turn=bool(getattr(self,'attacked_leader_this_turn',False))
            self.attacked_leader_this_turn=False
            self.turn+=1;self.bad_cards.clear();self.attacked.clear();self.evolved=False;self.turn_finish_evolution=None;self.turn_summon_actions=0
            self.end_turn_cancel_count=0;self.force_ready_face_this_turn=False
            self.rejected_targets=[]
            self.blocked_attacks=[];self.temporary_max_bonus=0
            self.super_unit=None;self.expanded=False;self.combat_only_phase=False;self.last_progress=time.monotonic()
            self.turn_began=time.monotonic()
            self.remember_units(self.v.board(self.im,'own'),existing=True)
            if self.leader_refs is None:
                a=cv2.cvtColor(np.asarray(self.im),cv2.COLOR_RGB2GRAY)
                self.leader_refs={'own':a[540:620,595:685].copy(),'enemy':a[15:90,595:680].copy()}
            self.log('our_turn');self.last_phase='OUR'
        if self.is_nightmare:self.nm_step_mark=self.metrics.checkpoint()
        if not self.expanded and not getattr(self,'combat_only_phase',False):
            self.act('expand_hand',[900,684],delay=.2);self.expanded=True
        if self.v.card_panel_open(self.im):
            self.act('close_hover_panel',[1020,405],delay=.15)
        state=self.snapshot(skip_hand=getattr(self,'combat_only_phase',False));self.log('state',frame=self.frame,**state)
        if hasattr(self,'dragon_telemetry') and (not self.dragon_telemetry.turns or self.dragon_telemetry.turns[-1]['turn']!=self.turn):
            self.dragon_telemetry.observe_turn_start(self.turn,state)
        if self.is_nightmare:
            nightmare_runtime.battle_step(self,state);return
        with self.metrics.span('decision'):
            plan=plan_attacks(state,self.attacked)
            cards=ordered_cards(state['hand'],state['pp'],state['max_pp'],len(state['own']),state,self.settings)
        if plan and state['enemy_hp'] is not None and plan['projected_face_damage']>=state['enemy_hp']:
            self.log('lethal_plan',enemy_hp=state['enemy_hp'],own_hp=state['own_hp'],
                     attackers=state['own'],enemy=state['enemy'],attacked=sorted(self.attacked),plan=plan)
            self.attack(state,plan);return
        if evolve_before_play(state,cards) and self.evolve(state):
            self.combat_only_phase=False;return
        if self.use_extra_pp(state):
            self.combat_only_phase=False;return
        for card in cards:
            key=(card['id'],round(card['xy'][0]/30))
            if key in self.bad_cards:continue
            self.bad_cards.add(key)
            end=[640,355]
            self.effect_serial=getattr(self,'effect_serial',0)+1
            self.turn_summon_actions=getattr(self,'turn_summon_actions',0)+1
            self.active_effect={'id':card['id'],'kind':'play','plan':card}
            self.act('play',card['xy'],end,delay=.35,
                     card=card,pp=state['pp'],maximum=state['max_pp'])
            initial_settled=self.settle_action(card['id'])
            before_prompt=self.im
            self.resolve_prompt(card)
            if self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT' or self.v.turn(self.im)=='ENEMY':
                self.active_effect=None;return
            settled=True if (initial_settled and self.im is before_prompt and self.v.turn(self.im)=='OUR') else self.settle_action(timeout=5)
            if not settled:
                self.active_effect=None;self.log('local_missing',field='unresolved_play',card=card['id']);return
            self.active_effect=None
            pp,maximum=self.v.pp(self.im)
            played=pp is not None and state['pp'] is not None and pp<state['pp']
            ramp=maximum is not None and state['max_pp'] is not None and maximum>state['max_pp']
            self.confirm('play',played or ramp,card=card['id'],before_pp=state['pp'],after_pp=pp)
            if (played or ramp) and hasattr(self,'dragon_telemetry'):
                self.dragon_telemetry.record_play(self.turn,card,state,pp)
            if card['id'] in ('10042310','10741310','10844120','10444120'):
                self.confirm('ramp',ramp,card=card['id'],before_max=state['max_pp'],after_max=maximum)
            can_extra_pp=bool(state.get('extra_pp',{}).get('enabled'))
            if pp==0 and not can_extra_pp:
                self.combat_only_phase=True
            else:
                self.expanded=False
                self.combat_only_phase=False
            if played and card.get('policy_tier')=='finish':
                self.turn_finish_evolution=card.get('damage_timing',{}).get('evolution_card')
            if played and (card['kind']=='follower' or card['id']=='10941310'):
                before_ids={u['uid'] for u in state['own']}
                expected_own=min(5,len(state['own'])+1)
                cur_own=self.v.board(self.im,'own')
                for _ in range(4):
                    if len(cur_own)>=expected_own:break
                    if self.outcome!='unknown' or self.v.turn(self.im)=='ENEMY':break
                    with self.metrics.span('animation_wait'):time.sleep(.2)
                    self.observe('wait_follower_landing',_network_check=False)
                    cur_own=self.v.board(self.im,'own')
                self.remember_units(cur_own)
                newly=[m for m in self.unit_memory if m['uid'] not in before_ids]
                chosen=max(newly,key=lambda m: m.get('card_id')==card['id'],default=None)
                if not chosen and self.unit_memory:
                    chosen=next((m for m in self.unit_memory if m.get('card_id')==card['id']), self.unit_memory[-1])
                if chosen:
                    if card['kind']=='follower':chosen['card_id']=card['id']
                    combo_active=bool(getattr(self,'attacked_leader_last_turn',False))
                    has_storm=(card['id'] in ('10644110','10942110')
                               or (card['id'] in ('10941110','10944110') and combo_active)
                               or (card['id']=='10444120' and card.get('form')=='enhance'))
                    chosen['can_face']=has_storm
                    chosen['storm']=has_storm
                    chosen['no_evolve']=card['id']=='10444120' and card.get('form')=='enhance'
                    chosen['summoned_turn']=self.turn
                    chosen.update(self.strategy['cards'].get(card['id'],{}).get('abilities',{}))
                    chosen['rush']=card['id'] in ('10944110','90041130')
                    if card['id']=='10944110':
                        chosen['ignore_ward']=True;chosen['rush']=True
                if card['id']=='90041130':
                    ocean_summoned(self.unit_memory)
            if played or ramp:self.bad_cards.clear()
            return
        im_before_evo=self.im
        if self.evolve(state):return
        self.combat_only_phase=True
        if self.im is not im_before_evo:
            state=self.snapshot(skip_hand=True)
        with self.metrics.span('decision'):plan=plan_attacks(state,self.attacked)
        if plan:self.attack(state,plan);return
        text=' '.join(r['text'] for r in self.texts((1100,265,1230,380)))
        if not re.search('結束|结束',text):
            self.log('local_wait',field='end_turn_label',text=text);time.sleep(.4);return
        if hasattr(self,'dragon_telemetry'):
            self.dragon_telemetry.record_turn_end(state)
        self.act('end_turn',[1166,325],delay=.4)
        p=self.button('^(結束|结束|確定|確認)$',(620,515,910,590))
        if p:
            cancel_btn=self.button('^(取消)$',(300,515,520,590)) or [400,572]
            if getattr(self,'end_turn_cancel_count',0)<1 and not self.blocked_attacks:
                self.end_turn_cancel_count=getattr(self,'end_turn_cancel_count',0)+1
                self.log('end_turn_cancelled_for_unspent_attack',attempt=self.end_turn_cancel_count,frame=self.frame)
                self.act('cancel_end_turn',cancel_btn,delay=.4)
                self.force_ready_face_this_turn=True
                for _ in range(4):
                    cur_b=self.v.board(self.im,'own')
                    if cur_b and any(u.get('ready') for u in cur_b):break
                    with self.metrics.span('animation_wait'):time.sleep(.2)
                    self.observe('wait_after_cancel_end_turn',_network_check=False)
                self.combat_only_phase=True
                return
            self.act('confirm_end_turn',p,delay=.4)
        # Don't repeatedly press the button if an unhandled prompt remains.
        self.wait_end_turn()

    def wait_end_turn(self):
        for _ in range(25):
            self.observe('wait_enemy')
            terminal=self.outcome!='unknown' or self.v.scene(self.im)[0]=='RESULT'
            if terminal or self.v.turn(self.im)=='ENEMY':
                self.confirm('end_turn',True,evidence='terminal' if terminal else 'enemy_turn')
                self.last_phase='TERMINAL' if terminal else 'ENEMY'
                elapsed=round(time.monotonic()-self.turn_began,3)
                self.turn_times.append(elapsed);self.log('turn_timing',seconds=elapsed,metrics=self.metrics.report());return
            with self.metrics.span('animation_wait'):time.sleep(.5)
        raise RuntimeError('End-turn did not advance; checkpoint saved, no replay')

    def result_reached(self,scene):
        return result_screen(self.v,self.im,scene,
            getattr(self.args,'ranked',False) and self.outcome!='unknown',terminal_known=self.outcome!='unknown')

    def summary_extra(self):
        if hasattr(self,'dragon_telemetry'):
            return self.dragon_telemetry.build_summary(
                self.outcome,getattr(self,'last_state',{}),getattr(self,'opponent_class',None),getattr(self,'play_order',None)
            )
        return {}

    def match_ready(self,scene):return rematch_ready(self,scene)

    def execute(self):
        status='stopped';reason='';failure_kind=None
        try:
            if self.panel_managed:
                self.config['_panel_managed']=True
            self.t=getattr(self,'transport_type',Transport)(self.config,self.out,self.stop)
            if self.panel_managed:
                Tasker.set_log_dir('');Tasker.set_save_draw(False);Tasker.set_recording(False)
                Tasker.set_save_on_error(False);Tasker.set_stdout_level(0);Tasker.set_debug_mode(False)
            self.t.metrics=self.metrics
            self.v=Vision(self.deck_profile);self.v.metrics=self.metrics;self.observe('initial')
            self.log('start',version=self.manifest['sha256'],capture=self.t.capture_method,
                     hwnd=self.t.window.hwnd,pid=self.t.window.pid,input=getattr(self.t,'input_name','MuMu SDK'),
                     ADB=getattr(self.t,'is_adb',False),mode=getattr(self.args,'mode',None))
            if self.args.inspect:
                self.log('inspection',scene=self.v.scene(self.im),texts=self.texts());status='read_only';return
            self.t.enable_input()
            while not self.finished:
                try:
                    self.observe()
                    scene,score=self.v.scene(self.im)
                    if getattr(self,'rematch_pending',False) and not self.match_ready(scene):
                        time.sleep(.3);continue
                    if scene!='UNKNOWN' or self.v.turn(self.im):self.reset_unknown_page_recovery()
                    if self.started and (self.turn or self.outcome!='unknown' or getattr(self.args,'mode',None) in ('grand_prix','weekend_plaza')) and self.result_reached(scene):
                        if len(self.turn_times)<self.turn and self.turn_began:
                            self.turn_times.append(round(time.monotonic()-self.turn_began,3))
                        self.log('result',frame=self.frame,outcome=self.outcome);self.finished=True;status='completed';break
                    # Once the game has ended, wait only for its result page. Never
                    # re-enter active-turn/prompt handling under a fading WIN layer.
                    if self.started and self.outcome!='unknown':
                        self.active_effect=None
                        if not hasattr(self,'terminal_began'):self.terminal_began=time.monotonic()
                        # Daily mission rewards can cover RESULT after WIN. They
                        # are already received; dismiss this exact overlay once.
                        # Keep the original bounded wait if the tap does not clear it.
                        if not getattr(self,'terminal_reward_dismissed',False):
                            reward_text=' '.join(r['text'] for r in self.texts((350,20,930,700)))
                            if re.search('領取結果|领取结果',reward_text) and re.search(r'\bTAP\b',reward_text,re.I):
                                self.terminal_reward_dismissed=True
                                self.act('dismiss_terminal_reward',[640,650],delay=2)
                                self.last_progress=time.monotonic()
                                continue
                        if self.recover_unknown_page(scene,terminal=True):continue
                        if time.monotonic()-self.terminal_began>30:
                            raise RuntimeError('Terminal outcome known but RESULT did not appear within 30 seconds')
                        time.sleep(.3);continue
                    if scene=='UNKNOWN' and self.started and not self.mulligan_done and not getattr(self,'opponent_class',None):
                        opponent=self.v.opponent_class(self.im)
                        if opponent:
                            self.opponent_class=opponent['class']
                            self.log('opponent_class',frame=self.frame,**opponent)
                    if scene=='MULLIGAN':
                        if not self.started:self.started=True;self.ranked_deck_verified=True;self.ranked_queue_attempted=True
                        self.mulligan();continue
                    if not self.mulligan_done and self.navigation(scene):
                        if self.preview_ready:status='preview_ready';break
                        continue
                    if self.v.turn(self.im):
                        if not self.mulligan_done and not self.started:
                            self.started=True;self.mulligan_done=True;self.ranked_deck_verified=True;self.ranked_queue_attempted=True
                            _,max_pp=self.v.pp(self.im)
                            self.turn=max(1,(max_pp or 2)-1)
                            self.log('resumed_live_battle',turn=self.turn,frame=self.frame)
                        if not self.mulligan_done:
                            time.sleep(.5);continue # Loading can show a blue turn button before the deal.
                        self.battle()
                    else:
                        if self.started and self.active_effect:
                            if self.resolve_prompt(self.active_effect):continue
                        # Recognized buttons can recover modal UI, but never launch matching or purchases.
                        rows=self.texts((20,20,1250,700))
                        text=' '.join(r['text'] for r in rows)
                        if re.search('領取結果|领取结果',text) and re.search(r'\bTAP\b',text,re.I):
                            self.act('dismiss_received_rewards',[640,650],delay=2)
                            self.last_progress=time.monotonic();continue
                        if self.started and re.search(r'GAME.*SET',text,re.I) and self.leader_refs:
                            a=cv2.cvtColor(np.asarray(self.im),cv2.COLOR_RGB2GRAY)
                            own=float(cv2.matchTemplate(a[530:630,575:705],self.leader_refs['own'],cv2.TM_CCOEFF_NORMED).max())
                            enemy=float(cv2.matchTemplate(a[0:105,575:705],self.leader_refs['enemy'],cv2.TM_CCOEFF_NORMED).max())
                            verdict='loss' if own<.4 and enemy>.7 else 'win' if enemy<.4 and own>.7 else 'unknown'
                            if verdict!='unknown':
                                self.outcome=verdict
                                self.log('outcome',outcome=verdict,evidence='GAME SET and defeated leader disappearance',
                                         own_presence=own,enemy_presence=enemy,frame=self.frame)
                        p=next((self.center(r) for r in rows if re.search('關閉|关闭|返回主',r['text'])),None)
                        if p:self.act('recover_close',p,delay=1)
                        elif self.started and re.search(r'\b(WIN|LOSE|VICTORY|DEFEAT)\b',text,re.I):
                            self.outcome='win' if re.search(r'\b(WIN|VICTORY)\b',text,re.I) else 'loss'
                            self.log('outcome',outcome=self.outcome,frame=self.frame,text=text)
                            self.act('continue_result',[640,550],delay=2);self.last_progress=time.monotonic()
                        elif re.search('TOUCH.*START|TAP.*START|點擊.*開始|点击.*开始',text,re.I):
                            self.act('title_start',[640,540],delay=3);self.last_progress=time.monotonic()
                        else:
                            if self.recover_unknown_page(scene):continue
                            if self.seq%15==0:self.log('navigation_wait',scene=scene,text=text,frame=self.frame)
                            time.sleep(.8)
                    if time.monotonic()-self.last_progress>90:
                        self.reconnect('no_game_progress_90s')
                except (RuntimeError,TimeoutError,subprocess.SubprocessError,OSError) as exc:
                    if type(exc).__name__ in ('RecoveredToLobby','ReconnectionExhausted','NetworkStalled','RecoveryBlocked'):
                        raise
                    if str(exc).startswith(('Existing battle detected','Existing mulligan detected')):raise
                    if classify(exc,self.active_effect) not in ('navigation_stalled','end_turn_unresolved','unresolved_effect_selection','runtime_error'):
                        raise
                    self.log('recoverable_stall',reason=str(exc),frame=self.frame)
                    self.reconnect(f'{type(exc).__name__}: {exc}')
        except BaseException as exc:
            reason=f'{type(exc).__name__}: {exc}'
            traceback.print_exc(file=sys.stderr)
            failure_kind=classify(exc,self.active_effect)
            self.log('stopped',reason=reason,failure_kind=failure_kind,traceback=traceback.format_exc())
            if self.panel_managed and failure_kind!='user_stop':
                from swb.worker_diagnostics import record_failure
                record_failure(ROOT,getattr(self.args,'batch',None),exc,'combat-run')
            dump(self.out/'checkpoint.json',{'reason':reason,'traceback':traceback.format_exc(),'frame':getattr(self,'frame',None),
                                          'pending':self.pending,'turn':self.turn,'state':self.last_state,
                                          'version':self.manifest['sha256'],'resume_supported':False,
                                          'failure_kind':failure_kind,'active_effect':self.active_effect,
                                          'discard_transactions':list(getattr(self,'discard_transactions',{}).values())})
            if self.t and hasattr(self,'v') and not isinstance(exc,(InterruptedError,KeyboardInterrupt)):
                if getattr(self.args,'ranked',False):
                    # Technical exceptions stop all input in ranked, preserving
                    # the checkpoint for the operator. Never start another game.
                    self.concession={'attempted':False,'reason':'ranked technical stop; no further input'}
                elif isinstance(exc,NetworkStalled):
                    self.concession={'attempted':False,'reason':'network recovery owns next input'}
                else:self.concession=concede_failed_cpu(self.t,self.v,self.out,self.started and self.cpu_verified)
                self.log('failure_exit',**self.concession)
        finally:
            if self.t:
                try:self.t.close()
                except Exception as exc:self.log('cleanup_error',reason=str(exc))
            current=version()
            summary={'rules_sha256':self.rules_sha,'detail_logs':self.detail_logs,'pruned_frames':self.evidence.pruned,'panel_config':getattr(self.args,'panel_config',None),'status':status,'reason':reason,'failure_kind':failure_kind,'outcome':self.outcome,'reached_result':self.finished and not getattr(self,'preview_ready',False),
                     'started_cpu':self.started and not getattr(self.args,'ranked',False),
                     'started_ranked':self.started and getattr(self.args,'ranked',False) and getattr(self.args,'mode',None) in (None,'ranked'),
                     'started_grand_prix':self.started and getattr(self.args,'mode',None)=='grand_prix',
                     'started_weekend_plaza':self.started and getattr(self.args,'mode',None)=='weekend_plaza',
                     'mode':getattr(self.args,'mode',None) or ('ranked' if getattr(self.args,'ranked',False) else 'practice_cpu'),
                     'ranked_queue_attempted':getattr(self,'ranked_queue_attempted',False),'ranked_deck_verified':getattr(self,'ranked_deck_verified',False),
                     'preview_ready':getattr(self,'preview_ready',False),'turns':self.turn,'dispatched':dict(self.actions),
                     'verified':dict(self.verified),'fixed_version':self.manifest['sha256'],
                     'cpu_verified':self.cpu_verified,'cpu_label':self.config['cpu_label'],'concession':self.concession,
                     'network_incident':self.network_incident,
                     'network_recovery_receipt':getattr(self,'network_recovery_receipt',None),
                     'heartbeat_io':self.heartbeat_publisher.state() if hasattr(self,'heartbeat_publisher') else {},
                     'timing':self.metrics.report(),'turn_seconds':self.turn_times,
                     'source_unchanged':current==self.manifest,'seconds':round(time.monotonic()-self.began,1)}
            summary.update(self.summary_extra())
            if self.is_nightmare:nightmare_runtime.audit_finish(self,summary)
            dump(self.out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)
            self.lock.unlink(missing_ok=True)
        return 0 if status in ('completed','read_only','preview_ready') else 3 if failure_kind=='network_stalled' else 2

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--inspect',action='store_true')
    p.add_argument('--max-minutes',type=int,default=25)
    p.add_argument('--batch-owner',type=int)
    p.add_argument('--ranked',action='store_true',help='One ranked match, requires ranked_once.py launcher')
    p.add_argument('--ranked-preview',action='store_true',help='Navigate to existing ramp deck, never enqueue')
    p.add_argument('--windows',action='store_true');args=p.parse_args()
    if args.windows:
        for w in Toolkit.find_desktop_windows():print(w)
    else:
        if args.ranked_preview:args.ranked=True
        elif args.ranked:p.error('Use ranked_once.py to enforce the single authorized ranked match')
        raise SystemExit(Run(args).execute())
