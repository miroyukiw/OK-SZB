"""ADB park quests: one visible pass, no friend/guild battles or blind replay."""
import hashlib
import json
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from swb.atomic_io import read_json,write_json
from swb.adb_transport import AdbTransport,AdbGameLifecycle,GAME,UU
from swb.paradise_quests import inspect,plan,compact,point,text
from swb.ranked_adb import RankedAdbRun
from swb.telemetry import HeartbeatPublisher
from swb.ranked import rematch_button

MODE='paradise'
def utc():return datetime.now(timezone.utc).isoformat()

class ParadiseBattleRun(RankedAdbRun):
    """Own only the readiness press admitted by this park navigator."""
    def act(self,name,*args,**kwargs):
        if name=='start_paradise' and (Path(__file__).resolve().parents[1]/'PANEL_FINISH').exists():
            raise InterruptedError('局后停止：不执行下一次匹配或再战')
        return super().act(name,*args,**kwargs)

    def navigation(self,scene):
        if self.started:return False
        state=inspect(self.v,self.im);kind=self.args.paradise_kind
        rematch=bool(getattr(self.args,'paradise_rematch',False))
        expected={'ous':'NPC_READY','park_match':'HUB','table_battle':'PLAY_ROOM'}[kind]
        from swb.network_recovery import RecoveryBlocked
        if getattr(self,'prestart_began',None) is None:self.prestart_began=time.monotonic()
        waited=time.monotonic()-self.prestart_began
        if rematch:
            if kind!='park_match' or not getattr(self.args,'paradise_previous_run',None):raise RuntimeError('再战缺少上一局真实完成回执')
            ready=rematch_button(self)
        else:
            ready=None
            if state['stage']==expected:
                ready=state.get('match') if kind=='park_match' else state.get('point')
            elif state['stage'] not in ('LOADING','UNKNOWN','DECK_CONFIRM','DECK_VIEW','DECK_PICK') or waited>=10:
                # 对局尚未开始：房间已解散/页面已离开。绝不进入重连循环，交回导航器重新处理课题。
                raise RecoveryBlocked('乐园对局未开始：准备页已变为'+state['stage'])
        if not self.args.paradise_deck_verified:raise RuntimeError('乐园牌组未核验')
        if not ready:
            # 关闭选牌页后准备按钮仍在滑入；短暂重读，不立即判定失败。
            if waited<10:time.sleep(.4);return True
            raise RecoveryBlocked('乐园对局未开始：10秒内未识别到准备按钮')
        self.stop()
        if (Path(__file__).resolve().parents[1]/'PANEL_FINISH').exists():raise InterruptedError('局后停止：未启动乐园对局')
        self.paradise_admission={'kind':kind,'time':utc(),'deck':'速攻速攻','count':'40/40','frame':self.frame,
            'rematch':rematch,'previous_run':getattr(self.args,'paradise_previous_run',None)}
        write_json(self.out/'paradise_battle_commit.json',self.paradise_admission)
        self.ranked_deck_verified=True;self.ranked_queue_attempted=True;self.started=True
        self.rematch_pending=True;self.rematch_began=time.monotonic()
        self.act('start_paradise',ready,ready,delay=2)
        return True

    def match_ready(self,scene):
        # Parent ranked readiness checks would mistake a private ready room for
        # a new lobby. Only a battle from our committed ready press is owned.
        if self.v.turn(self.im) or scene=='MULLIGAN':self.rematch_pending=False;return True
        state=inspect(self.v,self.im)
        if state['stage'] in ('HOME','PORTAL','HUB','BOARD'):raise RuntimeError('乐园准备未进入对局，结果未知，不自动重打')
        if time.monotonic()-self.rematch_began>90:raise RuntimeError('乐园准备等待超时，不自动重复准备')
        time.sleep(.3);return False

    def result_reached(self,scene):
        state=inspect(self.v,self.im)
        if state['stage']=='QUEST_RESULT':
            if self.args.paradise_kind=='ous' and not state['npc']:return False
            if self.outcome!='unknown' and self.outcome!=state['outcome']:raise RuntimeError('乐园胜负与结算页不一致')
            self.outcome=state['outcome'];return True
        if super().result_reached(scene):return True
        if self.outcome=='unknown':
            if getattr(self,'started',False) and getattr(self,'turn',0)>0 and state['stage']=='PLAY_ROOM' and getattr(self.args,'paradise_kind',None)=='table_battle':
                left_win=any(re.fullmatch(r'1\s*wins?',r['text'].strip(),re.I) for r in self.v.ocr(self.im,(20,135,145,190)))
                right_win=any(re.fullmatch(r'1\s*wins?',r['text'].strip(),re.I) for r in self.v.ocr(self.im,(1135,135,1260,190)))
                if left_win!=right_win:
                    self.outcome='win' if left_win else 'loss';return True
            if getattr(self,'started',False) and getattr(self,'turn',0)>0 and state['stage'] in ('WORLD','PLAY_ROOM'):
                seen=getattr(self,'park_exit_seen',None)
                if not seen or seen['stage']!=state['stage']:
                    self.park_exit_seen={'stage':state['stage'],'at':time.monotonic(),'frame':self.frame}
                elif seen['frame']!=self.frame and time.monotonic()-seen['at']>=2:
                    self.im.save(self.out/'unresolved_park_exit.png')
                    write_json(self.out/'unresolved_park_exit.json',{'time':utc(),'stage':state['stage'],
                        'first_frame':seen['frame'],'frame':self.frame,'turn':self.turn,'outcome':'unknown',
                        'automatic_replay':False})
                    from swb.network_recovery import RecoveryBlocked
                    raise RecoveryBlocked('乐园对局已回广场或对战室，但胜负未确认；保留未知结果，不重连或自动重打')
            else:self.park_exit_seen=None
            return False
        if self.v.turn(self.im):return False
        # NPC/private games may return directly to the public world/room.
        return inspect(self.v,self.im)['stage'] in ('WORLD','PLAY_ROOM')

    def summary_extra(self):
        return {'started_paradise':self.started,'paradise':{'kind':self.args.paradise_kind,
            'admission':getattr(self,'paradise_admission',None),'outcome':self.outcome}}

    def reset_unknown_page_recovery(self):
        self.unknown_battle_began=None
        return super().reset_unknown_page_recovery()

    def recover_unknown_page(self,scene,terminal=False):
        if inspect(self.v,self.im)['stage'] not in ('UNKNOWN',):return False
        if getattr(self,'started',False) and getattr(self,'turn',0)>0 and not terminal:
            now=time.monotonic()
            if getattr(self,'unknown_battle_began',None) is None:self.unknown_battle_began=now
            if now-self.unknown_battle_began<4.5:return False
        return super().recover_unknown_page(scene,terminal)

    def reconnect(self,trigger):
        if not getattr(self,'started',False):
            # 还没按下准备，没有任何对局可重连；重连只会把房间退掉并无限循环。
            from swb.network_recovery import RecoveryBlocked
            raise RecoveryBlocked('乐园对局未开始，不执行重连：'+str(trigger))
        try:return super().reconnect(trigger)
        except BaseException:
            if getattr(self,'started',False) and getattr(self,'turn',0)>0 and self.outcome=='unknown':
                state=inspect(self.v,self.im)
                if state['stage'] in ('WORLD','PLAY_ROOM'):
                    first_frame=self.frame;first_at=time.monotonic()
                    for _ in range(8):
                        self.stop();time.sleep(.4)
                        self.observe('park_exit_verify',_network_check=False)
                        if inspect(self.v,self.im)['stage']==state['stage'] and self.frame!=first_frame and time.monotonic()-first_at>=2:
                            self.im.save(self.out/'unresolved_park_exit.png')
                            write_json(self.out/'unresolved_park_exit.json',{'time':utc(),'stage':state['stage'],
                                'first_frame':first_frame,'frame':self.frame,'turn':self.turn,'outcome':'unknown',
                                'automatic_replay':False})
                            from swb.network_recovery import RecoveryBlocked
                            raise RecoveryBlocked('乐园对局已回广场或对战室，但胜负未确认；保留未知结果，不重连或自动重打')
            raise

class Navigator:
    def __init__(self,root,folder,args,state,save,guard):
        from swb.vision import Vision
        self.root=Path(root);self.folder=folder;self.args=args;self.state=state;self.save=save;self.guard=guard
        self.out=self.root/'runs'/('park_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        self.out.mkdir(parents=True);self.seq=0;self.phase='scan';self.target=None;self.board=None
        self.deck_checked=False;self.deck_selecting=False;self.deck_confirming=False;self.public_owned=False
        self.receipt_allowed=False
        self.rest_checked=False
        self.series=None
        self.watching_owned=False;self.watch_at=None;self.npc_steps=0;self.claimed=set();self.claim_attempts={};self.unknown_taps=0
        self.npc_last_seen=None;self.npc_occlusion_step_used=False
        self.after_battle=False;self.last_progress=time.monotonic();self.repeats={};self.last_stage=None
        self.empty_page_since=None
        self.watch_unknown_since=None
        self.hub_condition_since=None
        self.v=Vision();self.t=AdbTransport(args.panel_config,self.out,self.guard)
        if not(self.t.adb.pids(UU+':core') or self.t.adb.pids(UU)):raise RuntimeError('UU未运行，请先加速；不会重启UU')
        self.uu=self.t.adb.pids(UU+':core') or self.t.adb.pids(UU)
        self.publisher=HeartbeatPublisher(self.out/'heartbeat.json',lambda k,**d:self.event(k,**d),guard)
        self.save(current_run=str(self.out))

    def event(self,kind,**data):
        with (self.out/'events.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps({'time':utc(),'kind':kind,**data},ensure_ascii=False)+'\n')

    def observe(self):
        self.guard();self.seq+=1;self.im=self.t.capture_raw().resize((1280,720));self.view=inspect(self.v,self.im)
        self.publisher.publish({'pid':os.getpid(),'time':time.time(),'mode':MODE,'park_stage':self.view['stage'],'park_task':self.target})
        if self.view['stage']!='UNKNOWN':self.unknown_taps=0;self.watch_unknown_since=None
        if self.view['stage']!='UNKNOWN' or self.view.get('text'):self.empty_page_since=None
        if self.view['stage']!='HUB':self.hub_condition_since=None
        if self.view['stage']!=self.last_stage:
            self.im.save(self.out/f'{self.seq:05d}_{self.view["stage"].lower()}.png')
            self.event('stage',stage=self.view['stage'],target=self.target);self.last_stage=self.view['stage'];self.repeats={}
        if self.view['stage']=='UNKNOWN':self.event('unknown_ocr',text=self.view.get('text'),frame=self.seq)
        return self.view

    def move(self,name,p,end=None,duration=200):
        if not p:raise RuntimeError('乐园控件未唯一识别：'+name)
        self.guard();self.repeats[name]=self.repeats.get(name,0)+1
        if self.repeats[name]>3:raise RuntimeError('乐园页面未推进：'+name)
        self.event('input_before',name=name,point=p,end=end,frame=self.seq)
        use_end=end if end is not None else (None if self.repeats[name]>=2 else p)
        self.t.gesture(p,use_end,duration=duration)
        self.event('input_sent',name=name);self.last_progress=time.monotonic();self.wait(2)
        return True

    def wait(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.guard();time.sleep(min(.2,end-time.monotonic()))

    def task_state(self,board):
        selected=plan(board,self.args.park_daily,self.args.park_weekly,self.args.park_guild_hello)
        self.save(quests=board,quest_plan=selected,reason='正在核对乐园每日 / 每周课题')
        write_json(self.root/'user_data/paradise_latest.json',{'time':utc(),'batch':self.args.batch,**board,'plan':selected})
        return selected

    def send_hello(self,state):
        if not self.args.park_guild_hello:raise RuntimeError('未授权公会表情')
        if not state['selected_guild']:return self.move('select_current_guild',state['guild'])
        if not state['sticker_open']:return self.move('open_stickers',[581,653])
        # The observed first tile is Ous saying 你好. Require both words inside
        # that tile, a selected guild, and this exact sticker page before input.
        tile=compact(text(self.v.ocr(self.im,(490,345,630,466))))
        if '你' not in tile or '好' not in tile:raise RuntimeError('没有确认到你好表情，不发送其他内容')
        ledger=self.root/'user_data/paradise_guild_hello.json'
        previous=read_json(ledger) if ledger.exists() else {}
        # Never resend a committed but unconfirmed social action. A completed
        # prior week may send once when the fresh board actually resets to 0/1.
        if previous.get('status')=='committed':raise RuntimeError('上次公会表情结果未确认，请人工核对，不重复发送')
        week=datetime.now(timezone(timedelta(hours=8))).strftime('%G-W%V')
        # A confirmed prior send plus a fresh 0/1 board is a new game quest
        # cycle. Calendar week boundaries are not used to infer a game reset.
        self.im.save(self.out/'guild_hello_before.png')
        write_json(ledger,{'status':'committed','week':week,'time':utc(),'batch':self.args.batch,'guild':state['guild_name'],
            'content':'歐絲 你好 sticker','evidence':str(self.out/'guild_hello_before.png'),'repeat_allowed':False})
        self.move('send_guild_hello_once',[561,413]);self.phase='return_board';return True

    def deck(self,state):
        stage=state['stage']
        if stage=='DECK_PICK':
            if not self.deck_selecting:raise RuntimeError('非本任务打开的选牌页')
            if not state['point']:return self.move('select_rotation',state['rotation'])
            self.deck_confirming=True;return self.move('select_nightmare',state['point'])
        if stage=='DECK_CONFIRM':
            if not self.deck_confirming:raise RuntimeError('牌组确认缺少本次选择记录')
            if not self.deck_checked:
                self.deck_checked=True;self.im.save(self.out/'verified_nightmare_deck.png')
            self.deck_confirming=False;return self.move('decide_deck',state['point'])
        if stage=='DECK_VIEW':
            if not self.deck_selecting or state['count']!='40/40':raise RuntimeError('乐园牌组不是速攻速攻40/40')
            self.deck_checked=True;self.im.save(self.out/'verified_nightmare_deck.png');return self.move('close_deck',state['point'])
        return False

    def battle(self,kind):
        self.guard();self.t.close()
        from run import version
        rematch=kind=='park_match' and self.phase=='match_series'
        previous=self.state.get('games',[])[-1]['run'] if rematch and self.state.get('games') else None
        worker=ParadiseBattleRun(SimpleNamespace(inspect=False,ranked=True,ranked_preview=False,mode=MODE,
            max_minutes=25,batch_owner=os.getpid(),batch=self.args.batch,panel_managed=True,
            detail_logs=self.args.detail_logs,panel_config=self.args.panel_config,paradise_kind=kind,paradise_deck_verified=True,
            paradise_rematch=rematch,paradise_previous_run=previous))
        try:
            self.save(current_run=str(worker.out),active_match=len(self.state.get('games',[]))+1,reason='乐园课题对局中')
            self.event('battle_admitted',battle_kind=kind,run=str(worker.out))
        except BaseException as exc:
            write_json(worker.out/'startup_aborted.json',{'time':utc(),'reason':str(exc),'battle_started':False})
            if worker.lock.exists() and worker.lock.read_text().strip()==str(os.getpid()):worker.lock.unlink()
            raise
        code=worker.execute();summary=read_json(worker.out/'summary.json')
        if summary.get('source_unchanged') and not summary.get('started_paradise') and summary.get('outcome','unknown')=='unknown':
            # 准备按钮从未按下：没有对局、没有胜负，按真实页面重新接管课题（有次数上限）。
            self.not_started=getattr(self,'not_started',0)+1
            self.save(active_match=None,not_started_attempts=self.state.get('not_started_attempts',[])+[
                {'run':str(worker.out),'kind':kind,'reason':summary.get('reason',''),'time':utc()}])
            if self.not_started>3:raise RuntimeError('乐园对局连续3次未能开始，保存现场后停止：'+summary.get('reason',''))
            self.t=AdbTransport(self.args.panel_config,self.out,self.guard)
            stage=self.observe()['stage']
            self.public_owned=(kind=='table_battle' and stage=='PLAY_ROOM')
            self.deck_checked=self.public_owned;self.deck_selecting=False;self.deck_confirming=False
            if kind=='park_match' and self.series and self.phase=='match_series':self.phase='task'
            self.after_battle=False;self.repeats={};self.last_progress=time.monotonic()
            self.event('battle_not_started',battle_kind=kind,stage=stage,run=str(worker.out),reason=summary.get('reason',''))
            return None
        if code or not(summary.get('source_unchanged') and summary.get('reached_result') and summary.get('started_paradise') and summary.get('outcome') in ('win','loss')):
            self.save(active_match=None,unresolved_matches=self.state.get('unresolved_matches',[])+[
                {'run':str(worker.out),'kind':kind,'outcome':summary.get('outcome','unknown'),
                    'reached_result':summary.get('reached_result',False),'reason':summary.get('reason',''),'time':utc()}])
            exit_receipt=worker.out/'unresolved_park_exit.json'
            if summary.get('source_unchanged') and summary.get('started_paradise') and summary.get('outcome')=='unknown' and exit_receipt.exists():
                receipt=read_json(exit_receipt)
                if receipt.get('outcome')=='unknown' and receipt.get('stage') in ('WORLD','PLAY_ROOM'):
                    # This is a verified safe park exit, not a verified result.
                    # Only inspect/claim actual quests, then stop. Never add a
                    # win/loss, decrement a series or launch another match.
                    self.t=AdbTransport(self.args.panel_config,self.out,self.guard)
                    self.save(unresolved_stop=True,current_run=str(self.out),reason='胜负未知，返回核对课题后停止，不重打')
                    self.phase='return_board';self.target=None;self.series=None
                    self.public_owned=kind=='table_battle';self.after_battle=False
                    self.last_progress=time.monotonic()
                    return None
            raise RuntimeError('乐园对局结果未确认，保留尝试，禁止自动重打：'+summary.get('reason',''))
        row={'run':str(worker.out),'kind':kind,'outcome':summary['outcome'],'seconds':summary['seconds'],'time':utc()}
        rows=self.state.get('games',[])+[row];self.save(games=rows,completed=len(rows),wins=sum(r['outcome']=='win' for r in rows),
            losses=sum(r['outcome']=='loss' for r in rows),active_match=None,current_run=str(self.out))
        self.t=AdbTransport(self.args.panel_config,self.out,self.guard)
        self.after_battle=True;self.phase='return_board';self.public_owned=kind=='table_battle';self.target=None
        self.last_progress=time.monotonic()
        self.deck_checked=False;self.deck_selecting=False;self.deck_confirming=False
        self.rest_checked=False
        if kind=='park_match' and self.series:
            self.series['completed']+=1;self.save(park_match_plan=self.series)
            if self.series['completed']<self.series['scheduled']:
                self.phase='match_series';self.target='park_battles';self.deck_checked=True
        return row

    def step(self):
        state=self.observe();stage=state['stage']
        if stage!='UNKNOWN':self.unknown_taps=0;self.empty_page_since=None
        elif state.get('text'):self.empty_page_since=None
        if stage=='NETWORK':
            if state.get('point') and (state.get('room_failed') or not self.public_owned):
                if state.get('room_failed'):
                    self.public_owned=False;self.phase='scan';self.target=None
                    self.deck_checked=False;self.deck_selecting=False;self.deck_confirming=False
                return self.move('recover_network_dialog',state['point'])
            raise RuntimeError('乐园出现网络/费用提示，保存现场后停止')
        if stage=='PAYMENT':raise RuntimeError('乐园出现网络/费用提示，保存现场后停止')
        if stage=='DISMISS_PRIVATE_REENTRY':
            return self.move('dismiss_private_reentry',state['point'])
        if stage=='PARK_REENTRY':
            if self.public_owned:raise RuntimeError('公开配对结果未确认，不接管乐园恢复弹窗')
            return self.move('restore_park_session',state['point'])
        if stage=='UPDATE_REQUIRED':raise RuntimeError('游戏要求版本更新或重新启动；未当作加载继续等待，UU保留')
        if stage=='PRIOR_RESULT_NOTICE':return self.move('ack_prior_result_notice',state['point'])
        if stage=='IDLE_EXIT':
            if self.public_owned or self.series and self.series.get('completed'):
                raise RuntimeError('已开始的乐园流程被闲置退出，保留计数，不自动重打')
            self.phase='scan';self.target=None;self.series=None
            self.deck_checked=False;self.deck_selecting=False;self.deck_confirming=False
            return self.move('acknowledge_idle_exit',state['point'])
        if self.phase=='match_series':
            if stage=='REWARD':return self.move('dismiss_series_reward',state['point'])
            # The preceding Run has a real completed result. Reuse the existing
            # blue-button detection; never return home or scan quests per game.
            adapter=SimpleNamespace(im=self.im,texts=lambda box:self.v.ocr(self.im,box),
                center=lambda row:[row['box'][0]+row['box'][2]//2,row['box'][1]+row['box'][3]//2])
            if rematch_button(adapter):return ('battle','park_match')
            if stage=='LOADING' or time.monotonic()-self.last_progress<20:self.wait(.5);return
            raise RuntimeError('结算后未确认蓝色再战按钮，保存剩余局数，不盲目重开')
        if stage=='LOADING':self.wait(.5);return
        if stage in ('HOME','TITLE','MAP'):return self.move('enter_'+stage.lower(),state['point'])
        if stage=='ITEM':
            return self.move('close_item',state['point'])
        if stage=='REWARD':
            return self.move('dismiss_reward',state['point'])
        if stage=='QUEST_RESULT':
            if getattr(self,'watching_owned',False) and self.target=='spectate':
                self.phase='return_board';return self.move('return_park_after_quest_battle',state['point'])
            if not self.after_battle and self.phase!='scan':raise RuntimeError('乐园旧结算页未接管，需核对课题后再启动')
            return self.move('return_park_after_quest_battle',state['point'])
        if stage=='BOARD':
            board=state['board'];self.board=board;selected=self.task_state(board)
            if self.state.get('unresolved_stop'):
                target=self.state.get('last_task');current=next((t for t in board['tasks'] if t['id']==target),None)
                self.save(unresolved_quest_check={'time':utc(),'task':target,'before':self.state.get('last_task_before'),
                    'after':current['progress'] if current else None,'outcome':'unknown','automatic_replay':False})
                self.phase='scan'
            if self.phase=='return_board':
                before=self.state.get('last_task_before');target=self.state.get('last_task')
                current=next((t for t in board['tasks'] if t['id']==target),None)
                if target=='park_battles' and self.series and self.series.get('completed',0)>0:
                    self.series['quest_progress_after']=current['progress'] if current else None
                    self.save(park_match_plan=self.series)
                    if current is None:raise RuntimeError('整段结束后课题计数未读，未自动补打')
                    credited=current['progress']-self.series['progress_before']
                    if credited<self.series['completed']:
                        note={'progress_before':self.series['progress_before'],'progress_after':current['progress'],
                            'finished_games':self.series['completed'],'credited_games':credited,
                            'uncredited_games':self.series['completed']-credited,'cause':'not inferred from per-series observations'}
                        self.event('quest_credit_difference',**note)
                        self.save(quest_credit_differences=self.state.get('quest_credit_differences',[])+[note])
                    self.series=None
                elif target=='table_battle' and self.after_battle and current is not None and before is not None and current['progress']<=before:
                    note={'task':target,'progress_before':before,'progress_after':current['progress'],
                        'finished_games':1,'credited_games':0,'uncredited_games':1,'cause':'early_concession_or_uncredited_table_match'}
                    self.event('quest_credit_difference',**note)
                    self.save(quest_credit_differences=self.state.get('quest_credit_differences',[])+[note])
                elif target and before is not None and (current is None or current['progress']<=before):
                    raise RuntimeError('乐园操作后课题进度未增加，不循环重做：'+target)
                if target=='guild_hello':
                    ledger=self.root/'user_data/paradise_guild_hello.json'
                    if ledger.exists():write_json(ledger,{**read_json(ledger),'status':'confirmed','confirmed_at':utc()})
                self.save(last_task=None,last_task_before=None);self.phase='scan';self.target=None;self.public_owned=False
                self.watching_owned=False;self.watch_at=None
            if self.args.park_preview:return 'preview_ready'
            if board.get('daily_claimable') and self.args.park_daily:
                reward=board['daily_claimable'][0];key='daily_'+reward['id']
                attempts=self.claim_attempts.get(key,0)
                if attempts>=3:raise RuntimeError('每日奖励仍显示可领，页面未推进：'+key)
                if key in self.claimed:
                    self.wait(1.5)
                self.claim_attempts[key]=attempts+1
                self.claimed.add(key);self.receipt_allowed=True
                evidence=self.out/(key+'_before_claim.png');self.im.save(evidence)
                self.save(daily_claim_attempts=self.state.get('daily_claim_attempts',[])+[
                    {'id':reward['id'],'time':utc(),'evidence':str(evidence)}])
                return self.move('claim_'+key,reward['point'])
            if board['claimable']:
                reward=board['claimable'][0];key=reward['threshold']
                attempts=self.claim_attempts.get(key,0)
                if attempts>=3:raise RuntimeError('同一积分奖励仍显示可领，页面未推进：'+str(key))
                if key in self.claimed:
                    self.wait(1.5)
                self.claim_attempts[key]=attempts+1
                self.claimed.add(key);self.receipt_allowed=True
                return self.move('claim_points_'+str(key),reward['point'])
            if self.state.get('unresolved_stop'):return 'unresolved_result'
            target=selected['next']
            if target is None:
                if self.target=='park_battles' and self.phase=='task':
                    return self.move('close_quest_board_for_ranked',[44,32])
                return 'completed'
            if target in ('ous','table_battle','park_battles'):
                if len(self.state.get('games',[]))>=self.args.park_max_battles:return 'limit_reached'
                if not self.rest_checked:return 'rest_boundary'
            if target=='visit':return self.move('visit_park',[44,32])
            self.target=target;self.phase='task'
            self.save(last_task=target,last_task_before=next(t['progress'] for t in board['tasks'] if t['id']==target),reason='正在处理乐园课题：'+target)
            if target=='park_battles':
                task=next(t for t in board['tasks'] if t['id']==target)
                self.series={'progress_before':task['progress'],'required':task['total']-task['progress'],
                    'scheduled':min(task['total']-task['progress'],self.args.park_max_battles-len(self.state.get('games',[]))),
                    'completed':0,'counted_at':utc(),'origin':'park_matching','quantity_reads':1}
                self.save(park_match_plan=self.series)
            if target=='ous':
                self.npc_steps=0;self.npc_last_seen=None;self.npc_occlusion_step_used=False;self._warp_step_used=False
                return self.move('warp_ous',[813,536])
            if target in ('table_battle','park_battles','spectate') and not next((t['complete'] for t in board['tasks'] if t['id']=='park_battles'),False) and self.repeats.get('open_battle_hub',0)<1:
                return self.move('open_battle_hub',[549,368])
            return self.move('close_quest_board',[44,32])
        if self.deck(state):return
        if stage=='PORTAL':
            # Enter the public plaza once per pass, then let the fresh board
            # report login progress. Never choose guild or private plazas.
            return self.move('enter_public_plaza',state['point'])
        if getattr(self,'watching_owned',False) and getattr(self,'watch_at',None) is not None and stage in ('WORLD','HUB','WATCH_LIST','WATCH_ROOM'):
            self.phase='return_board'
        if stage=='WORLD':
            if self.phase=='return_board' or self.target is None:return self.move('read_board',state['board'])
            if self.target=='guild_hello':return self.move('open_guild_messages',state['message'])
            if self.target=='ous':
                if state.get('challenge'):return self.move('challenge_ous',state['challenge'])
                if not state.get('npc'):
                    # A single short continuation after the observed warp and
                    # first steering step follows the tested approach path.
                    # It does not repeat when the NPC remains hidden.
                    if self.npc_steps==0 and not getattr(self,'_warp_step_used',False):
                        self._warp_step_used=True;self.npc_steps+=1
                        return self.move('ous_initial_approach',[151,520],[151,453],duration=1000)
                    if self.npc_last_seen and self.npc_steps==1 and not self.npc_occlusion_step_used:
                        self.npc_occlusion_step_used=True;self.npc_steps+=1
                        return self.move('ous_occluded_short_approach',[151,520],[151,453],duration=1000)
                    raise RuntimeError('欧丝位置未识别，停止后续移动')
                self.npc_steps+=1
                if self.npc_steps>6:raise RuntimeError('欧丝靠近失败，停止移动')
                self.npc_last_seen=state['npc'];x=state['npc'][0];dx=max(-43,min(43,round((x-640)/5)))
                return self.move('approach_ous_'+str(self.npc_steps),[151,520],[151+dx,453],duration=1000)
            if self.public_owned:raise RuntimeError('公开配对回到广场但没有对局回执，不重复匹配')
            return self.move('open_hub',state['hub'])
        if stage=='HUB':
            if self.phase=='return_board' or self.target is None:return self.move('hub_board',state['board'])
            if self.target=='spectate':return self.move('spectator_list',state['watch_tab'])
            if self.public_owned:raise RuntimeError('上次公开配对未确认，不重复匹配')
            if self.target=='park_battles':
                if not state.get('match'):raise RuntimeError('乐园内匹配入口未识别')
                if not self.deck_checked:
                    self.deck_selecting=True;return self.move('verify_park_match_deck',state['match_detail'])
                return ('battle','park_match')
            if self.target not in ('table_battle','park_battles'):raise RuntimeError('没有本次公开配对课题')
            if not(state['bo1'] and state['public']):
                # The title appears before the hub panels finish sliding in.
                # Re-read fresh captures briefly, without weakening BO1/public
                # checks or clicking any matchmaking control in transition.
                if self.hub_condition_since is None:self.hub_condition_since=time.monotonic()
                if time.monotonic()-self.hub_condition_since<8:self.wait(.5);return
                raise RuntimeError('公用广场BO1配对条件未核验')
            self.hub_condition_since=None
            self.public_owned=True;write_json(self.out/'public_matchmaking_commit.json',{'time':utc(),'kind':'public_random_table','frame':self.seq})
            return self.move('find_public_opponent',state['point'])
        if stage=='MATCHING':
            if not self.public_owned:raise RuntimeError('未接管未知来源的匹配')
            self.wait(.5);return
        if stage=='WATCH_LIST':
            if self.phase=='return_board':return self.move('watch_list_back',[44,32])
            if self.target!='spectate':raise RuntimeError('没有观战课题上下文')
            if not state['point']:raise RuntimeError('目前没有可观看的公开对战，下次启动再试')
            self.watching_owned=True;return self.move('enter_public_spectator_room',state['point'])
        if stage=='WATCH_ROOM':
            if not self.watching_owned:raise RuntimeError('非本任务进入的观战室')
            if self.phase=='return_board':
                p=point(self.v.ocr(self.im,(850,640,1270,715)),r'樂園|乐园') or [1008,682]
                return self.move('exit_watched_room',p)
            return self.move('ready_to_watch',state['point'])
        if stage=='WATCHING':
            if not self.watching_owned:raise RuntimeError('不操作未知来源的观战')
            if self.watch_at is None:self.watch_at=time.monotonic()
            if time.monotonic()-self.watch_at<20:self.wait(.5);return
            return self.move('spectator_menu',state['point'])
        if stage in ('WATCH_MENU','WATCH_EXIT_CONFIRM'):
            if not self.watching_owned:raise RuntimeError('未知观战退出请求')
            if stage=='WATCH_EXIT_CONFIRM':self.phase='return_board'
            return self.move(stage.lower(),state['point'])
        if stage=='ROOM_EXIT_CONFIRM':
            if not(self.watching_owned or self.public_owned):raise RuntimeError('未拥有此对战室')
            self.public_owned=False;self.watching_owned=False
            return self.move('exit_room_confirm',state['point'])
        if stage=='CHAT':
            if self.target!='guild_hello':raise RuntimeError('不操作无任务上下文的消息页')
            if self.phase=='return_board':return self.move('leave_guild_chat',[44,32])
            return self.send_hello(state)
        if stage in ('NPC_INTERACT','NPC_CHOICE','NPC_DIALOG'):
            if stage=='NPC_INTERACT':
                if self.phase=='return_board' or self.target is None:return self.move('read_board_from_npc',[1133,72])
                if self.target=='guild_hello':return self.move('open_guild_messages',[1028,72])
                if self.target in ('park_battles','table_battle','spectate'):
                    if self.public_owned:raise RuntimeError('公开配对回到广场但没有对局回执，不重复匹配')
                    return self.move('open_hub',[1167,173])
            if self.target!='ous':raise RuntimeError('欧丝对话不属于当前课题')
            p=[640,620] if stage=='NPC_DIALOG' and self.repeats.get('npc_dialog',0)>=1 else state['point']
            return self.move(stage.lower(),p)
        if stage in ('NPC_READY','PLAY_ROOM'):
            if stage=='NPC_READY' and self.target!='ous':raise RuntimeError('欧丝准备页缺少本次课题上下文')
            if stage=='PLAY_ROOM' and not self.public_owned:raise RuntimeError('不接管好友、公会或未知来源的对战室')
            if self.phase=='return_board':
                r_cnt=self.repeats.get('leave_finished_room',0)
                p=(point(self.v.ocr(self.im,(500,640,800,715)),r'主頁面|主页面') or [640,682]) if r_cnt>=2 else (point(self.v.ocr(self.im,(850,640,1270,715)),r'樂園|乐园') or [1008,682])
                return self.move('leave_finished_room',p)
            if not self.deck_checked:
                self.deck_selecting=True;return self.move('choose_quest_deck',state['deck'])
            return ('battle','ous' if stage=='NPC_READY' else 'table_battle')
        # Confirmed battle results can be left only after their receipt was saved.
        if self.after_battle:
            p=point(self.v.ocr(self.im,(620,580,1270,710)),r'返回廣場|返回對戰室|返回主頁面|返回[樂乐][園园國国圓圆圈]')
            if p:return self.move('leave_confirmed_result',p)
        # Three neutral dismissal attempts, each preceded by a new screenshot;
        # never use these on a field, a modal, private room or other known page.
        scene=self.v.scene(self.im)[0]
        if not getattr(self,'watching_owned',False) and (self.v.turn(self.im) or scene=='MULLIGAN'):raise RuntimeError('发现不属于本任务的对局，停止输入')
        if scene.startswith(('RANKED','PRACTICE')):raise RuntimeError('当前为其他对战模式，不自动操作')
        if scene=='RESULT' and self.phase=='scan':
            p=point(self.v.ocr(self.im,(630,585,1270,710)),r'返回主頁面')
            if p:return self.move('leave_previous_settled_result',p)
        from swb.network_recovery import navigation_modal
        if navigation_modal(self.v.ocr(self.im,(0,0,1280,720))):raise RuntimeError('未知确认弹窗，未点击背景')
        if getattr(self,'watching_owned',False) and getattr(self,'watch_at',None) is not None and time.monotonic()-self.watch_at<20:
            self.unknown_taps=0;self.wait(.5);return
        if getattr(self,'watching_owned',False):
            if getattr(self,'watch_unknown_since',None) is None:self.watch_unknown_since=time.monotonic()
            if time.monotonic()-self.watch_unknown_since<8:self.wait(.5);return
        if not state.get('text'):
            if self.empty_page_since is None:self.empty_page_since=time.monotonic()
            if time.monotonic()-self.empty_page_since<8:self.wait(.5);return
        if self.unknown_taps<3:
            self.unknown_taps+=1;return self.move('unknown_neutral_'+str(self.unknown_taps),[12,705])
        raise RuntimeError('乐园页面未识别，三次重新观察后停止')

def execute_batch(root,args,version_fn):
    root=Path(root);folder=root/'batches'/args.batch;lock=root/'BATCH.lock';nav=None;rest=None
    if args.resume:raise ValueError('乐园按最新课题重新扫描，不恢复未知对局，请新启动任务')
    if folder.exists():raise RuntimeError('乐园批次已存在，不覆盖历史')
    if (root/'RUNNING.lock').exists():raise RuntimeError('已有对局控制器')
    stop=root/'STOP'
    def token():return [stop.stat().st_mtime_ns,hashlib.sha256(stop.read_bytes()).hexdigest()] if stop.exists() else None
    initial=token()
    if initial!=args.panel_stop_token:raise InterruptedError('启动前收到新停止请求')
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    state={'mode':MODE,'batch':args.batch,'authorized_matches':args.park_max_battles,'games':[],'completed':0,'wins':0,'losses':0,
        'quest_scope':{'daily':args.park_daily,'weekly':args.park_weekly,'guild_hello':args.park_guild_hello}}
    def save(**changes):state.update(changes,updated_utc=utc());write_json(folder/'status.json',state)
    def guard():
        if stop.exists() or (root/'PANEL_FINISH').exists():raise InterruptedError('已按要求停止乐园课题')
        while (root/'PANEL_PAUSE').exists():
            if stop.exists() or (root/'PANEL_FINISH').exists():raise InterruptedError('暂停时收到停止请求')
            time.sleep(.1)
    try:
        if token()!=initial or (root/'RUNNING.lock').exists():raise InterruptedError('启动过程中控制状态变化')
        folder.mkdir(parents=True);manifest=version_fn()
        if manifest['sha256']!=args.expect_version:raise RuntimeError('乐园版本不一致')
        write_json(folder/'version.json',manifest);stop.unlink(missing_ok=True);save(status='running',wrapper_pid=os.getpid(),version=manifest['sha256'])
        from swb.scheduled_rest import RestSchedule
        if not args.park_preview:rest=RestSchedule(root,folder,state,[],args.panel_config,adapter_factory=AdbGameLifecycle)
        def rest_boundary():
            if not rest:return
            if not rest.before_match():raise InterruptedError('局后停止')
            if nav:
                pids=nav.t.adb.pids(GAME)
                if pids!=nav.t.game_pids:
                    nav.t.close();nav.t=AdbTransport(args.panel_config,nav.out,guard)
                    nav.public_owned=False;nav.deck_checked=False;nav.deck_selecting=False;nav.deck_confirming=False
            save(status='running',reason='休息边界已处理，继续乐园课题',rest=None)
        # A stopped task can leave GAME closed while its persisted rest is
        # pending. Resume that exact deadline before creating a game transport.
        if rest and rest.data.get('rest'):rest_boundary()
        nav=Navigator(root,folder,args,state,save,guard)
        if rest:
            from swb.scheduled_rest import WORK_SECONDS
            if rest.data.get('rest') or rest.data['active_seconds']>=WORK_SECONDS:
                initial_view=nav.observe()
                if initial_view['stage'] not in ('HOME','PORTAL','WORLD','BOARD','TITLE','IDLE_EXIT','UPDATE_REQUIRED'):
                    raise RuntimeError('已到休息边界，但当前不是已确认的安全页面，未关闭游戏')
                rest_boundary()
        deadline=time.monotonic()+3*3600
        while time.monotonic()<deadline:
            guard()
            if version_fn()!=manifest:raise RuntimeError('乐园运行期间源码变化，停止')
            result=nav.step()
            if result=='rest_boundary':
                rest_boundary();nav.rest_checked=True;continue
            if result=='limit_reached':
                save(status='paused',reason='已到本轮对局上限，课题进度已保存；下次启动继续核对缺项',stop_kind='quest_limit',active_match=None);return 0
            if result=='unresolved_result':
                save(status='paused',reason='已回乐园并核对真实课题、领取可领奖励；本局胜负未知，停止且不自动重打',
                    stop_kind='unknown_result_quest_checked',active_match=None);return 0
            if result in ('completed','preview_ready'):
                save(status=result,reason='已选范围内本轮可执行乐园课题已处理；跨天项保留真实进度' if result=='completed' else '课题已读取，未发消息、匹配或开赛',active_match=None)
                return 0
            if isinstance(result,tuple) and result[0]=='battle':
                if len(state['games'])>=args.park_max_battles:raise RuntimeError('乐园本轮对局上限已到，进度待核对')
                if rest and nav.phase=='match_series':
                    from swb.scheduled_rest import WORK_SECONDS
                    rest.checkpoint(state['games'])
                    if rest.data.get('rest') or rest.data['active_seconds']>=WORK_SECONDS:
                        rest_boundary();nav.phase='match_series_resume';nav.deck_checked=False;continue
                nav.battle(result[1])
                if rest:rest.checkpoint(state['games'])
                continue
            if time.monotonic()-nav.last_progress>180:raise RuntimeError('乐园等待超过3分钟，停止并保留现场')
        raise RuntimeError('乐园本轮运行期限已到')
    except (InterruptedError,KeyboardInterrupt) as exc:
        save(status='paused',reason=str(exc),stop_kind='user_finish_after_match' if (root/'PANEL_FINISH').exists() else 'user_stop',active_match=None);return 0
    except BaseException as exc:
        if nav:
            try:nav.t.adb.screen().save(nav.out/'failure.png')
            except Exception:pass
        save(status='interrupted',reason=str(exc),stop_kind='technical',technical_interruptions=1)
        from swb.worker_diagnostics import record_failure
        record_failure(root,args.batch,exc,'paradise-quests');return 2
    finally:
        if rest:rest.checkpoint(state['games'])
        if nav:nav.t.close()
        if lock.exists() and lock.read_text().strip()==str(os.getpid()):lock.unlink()
