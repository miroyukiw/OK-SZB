"""Ranked park entry with bounded priority quests under the ranked batch lock."""
import time
from pathlib import Path
from types import SimpleNamespace
from swb.atomic_io import write_json
from swb.paradise import Navigator,utc
from swb.paradise_quests import inspect
from swb.ranked import rematch_button
from swb.ranked_adb import RankedAdbRun
from swb.network_recovery import RecoveryBlocked


class PriorityNavigator(Navigator):
    def task_state(self,board):
        selected=super().task_state(board)
        # Weekly random battles are the real ranked slots, not extra quest
        # matches. Daily tables and the other outstanding tasks go first.
        order=('visit','table_battle','guild_hello','spectate','ous')
        selected['next']=next((key for key in order if key in selected['pending']),None)
        selected['ranked_pending']='park_battles' in selected['pending']
        self.save(quest_plan=selected)
        return selected


class ParkRankedRun(RankedAdbRun):
    """One real ranked match; canonical ranked receipts/stats stay unchanged."""
    def act(self,name,*args,**kwargs):
        if name=='start_ranked' and (Path(__file__).resolve().parents[1]/'PANEL_FINISH').exists():
            raise InterruptedError('局后停止：不启动下一局乐园排位')
        return super().act(name,*args,**kwargs)

    def navigation(self,scene):
        if self.started:return False
        context=getattr(self.args,'ranked_rematch_from',None)
        verified=getattr(self.args,'ranked_park_prepared',None)
        if not verified:
            raise RecoveryBlocked('乐园排位准备凭据或速攻鬼策略未核验')
        if context:
            if not(context.get('deck_verified') and context.get('deck_profile')==self.deck_profile):
                raise RecoveryBlocked('乐园排位再战凭据不一致')
            ready=rematch_button(self)
            if not ready:raise RecoveryBlocked('乐园排位未确认蓝色再战按钮，不返回主页重开')
            entry='park_settlement_rematch'
        else:
            state=inspect(self.v,self.im)
            if state['stage']!='HUB' or not state.get('match'):
                raise RecoveryBlocked('乐园排位入口已变化，未启动匹配')
            ready=state['match'];entry='park_battle_hub'
        self.stop()
        self.park_admission={'time':utc(),'entry':entry,'deck':'速攻速攻','count':'40/40',
            'frame':self.frame,'previous_run':context.get('previous_run') if context else None,'preparation':verified}
        self.im.save(self.out/'ranked_park_before_queue.png')
        write_json(self.out/'ranked_park_admission.json',self.park_admission)
        self.ranked_deck_verified=True;self.ranked_queue_attempted=True
        self.rematch_pending=True;self.rematch_began=time.monotonic()
        self.log('ranked_queue_commit',frame=self.frame,limit=1,deck='速攻速攻',entry=entry)
        self.act('start_ranked',ready,ready,delay=2,entry=entry)
        self.started=True;self.last_progress=time.monotonic()
        return True

    def summary_extra(self):
        return {**super().summary_extra(),'ranked_origin':'park','ranked_park_admission':getattr(self,'park_admission',None)}


class RankedPark:
    """Navigation only between ranked slots; never creates a second batch lock."""
    def __init__(self,root,folder,args,state,persist,manifest,version_fn,rest):
        self.root=Path(root);self.folder=Path(folder);self.args=args;self.state=state
        self.persist=persist;self.manifest=manifest;self.version_fn=version_fn;self.rest=rest
        self.nav=None;self.needs_scan=True;self.next_scan=0.;self.since_scan=0;self.weekly_remaining=0
        self.rest_cycle=rest.data['cycles'] if rest else 0
        previous=state.get('park') or {}
        # A resumed unresolved priority battle is not silently replayed.
        if previous.get('active_match') is not None or previous.get('unresolved_stop'):
            raise RecoveryBlocked('旧乐园任务有未核对对局，保留记录，不在恢复排位时重打')
        self.park={**previous,'batch':args.batch,'mode':'paradise','games':previous.get('games',[])}
        self.qargs=SimpleNamespace(batch=args.batch,panel_config=args.panel_config,detail_logs=args.detail_logs,
            park_daily=getattr(args,'park_daily',True),park_weekly=getattr(args,'park_weekly',True),
            park_guild_hello=getattr(args,'park_guild_hello',True),park_max_battles=8,park_preview=False)
        self.state.update(mode='ranked',ranked_park=True,park_phase='quests')
        self.persist()

    def guard(self):
        if (self.root/'STOP').exists() or (self.root/'PANEL_FINISH').exists():
            raise InterruptedError('已按要求停止乐园排位')
        paused=time.monotonic()
        while (self.root/'PANEL_PAUSE').exists():
            if (self.root/'STOP').exists() or (self.root/'PANEL_FINISH').exists():
                raise InterruptedError('暂停期间收到停止请求')
            time.sleep(.1)
        if self.nav:
            delta=time.monotonic()-paused
            self.nav.last_progress+=delta

    def save(self,**changes):
        self.park.update(changes,updated_utc=utc());self.state['park']=self.park
        if 'current_run' in changes:self.state['current_run']=changes['current_run']
        if 'reason' in changes:self.state['reason']=changes['reason']
        self.persist()

    def open_nav(self,returning=False):
        self.close()
        self.nav=PriorityNavigator(self.root,self.folder,self.qargs,self.park,self.save,self.guard)
        self.nav.after_battle=returning;self.nav.phase='return_board' if returning else 'scan'
        self.state['park_phase']='quests';self.persist()

    def rest_boundary(self):
        if self.rest and not self.rest.before_match():raise InterruptedError('局后停止')
        if self.nav and self.nav.t.adb.pids('jp.co.cygames.ShadowverseWorldsBeyond')!=self.nav.t.game_pids:
            from swb.adb_transport import AdbTransport
            self.nav.t.close();self.nav.t=AdbTransport(self.args.panel_config,self.nav.out,self.guard)
            self.nav.public_owned=False;self.nav.deck_checked=False
            self.nav.deck_selecting=False;self.nav.deck_confirming=False
        self.state.update(status='running',rest=None);self.persist()

    def loop(self,prepare=False):
        deadline=time.monotonic()+3*3600
        while time.monotonic()<deadline:
            self.guard()
            if self.version_fn()!=self.manifest:raise RuntimeError('乐园排位期间源码发生变化')
            result=self.nav.step()
            if result=='rest_boundary':
                self.rest_boundary();self.nav.rest_checked=True;continue
            if result in ('limit_reached','unresolved_result'):
                raise RecoveryBlocked('乐园优先任务未确认完成；已保存进度，未继续排位')
            if isinstance(result,tuple) and result[0]=='battle':
                if result[1]=='park_match':
                    if not prepare:raise RuntimeError('周常对局必须交由正式排位计数')
                    evidence=self.nav.out/'verified_nightmare_deck.png'
                    if not self.nav.deck_checked or not evidence.exists():raise RecoveryBlocked('乐园排位牌组未核验')
                    return {'deck':'速攻速攻','count':'40/40','evidence':str(evidence)}
                if prepare:raise RuntimeError('排位准备阶段不启动其他对局')
                self.nav.battle(result[1])
                if self.rest:self.rest.checkpoint(self.park.get('games',[]))
                continue
            if result=='completed':return
            if time.monotonic()-self.nav.last_progress>180:raise RecoveryBlocked('乐园导航超过3分钟未推进')
        raise RecoveryBlocked('乐园优先任务超过本轮期限')

    def scan(self,returning=False):
        self.qargs.park_max_battles=len(self.park.get('games',[]))+8
        self.open_nav(returning);self.loop()
        board=self.nav.board
        if not board:raise RecoveryBlocked('未取得本轮真实乐园课题')
        weekly=next(t for t in board['tasks'] if t['id']=='park_battles')
        self.weekly_remaining=weekly['total']-weekly['progress'] if self.qargs.park_weekly else 0
        self.since_scan=0;self.needs_scan=False
        # Use the visible countdown, never infer completion from a date.
        self.next_scan=time.time()+(board.get('refresh_after_seconds') or 3600)
        self.state.update(park_phase='ready',park_ranked_remaining=self.weekly_remaining,
            park_next_check=self.next_scan,reason='乐园优先课题已核对，准备从乐园打排位')
        self.persist()

    def prepare(self,rematch=None):
        self.guard()
        if self.rest and self.rest.data['cycles']!=self.rest_cycle:
            self.rest_cycle=self.rest.data['cycles'];self.needs_scan=True
        if self.needs_scan or time.time()>=self.next_scan:
            self.scan(returning=bool(rematch));rematch=None
        if rematch:
            if self.nav is None:self.open_nav(returning=True)
            # A newly settled result can still be sliding in. Never decide
            # that rematch disappeared from one capture. This context comes
            # only from the preceding completed ranked slot.
            self.nav.after_battle=True
            for attempt in range(1,5):
                self.nav.guard();self.nav.im=self.nav.t.capture_raw().resize((1280,720))
                adapter=SimpleNamespace(im=self.nav.im,texts=lambda box:self.nav.v.ocr(self.nav.im,box),
                    center=lambda r:[r['box'][0]+r['box'][2]//2,r['box'][1]+r['box'][3]//2])
                ready=rematch_button(adapter)
                self.nav.event('ranked_rematch_probe',attempt=attempt,ready=bool(ready),previous_run=rematch['previous_run'])
                if ready:
                    self.close();self.state['park_phase']='ranked';self.persist()
                    return {'deck':'速攻速攻','count':'40/40','previous_run':rematch['previous_run']},rematch
                self.nav.im.save(self.nav.out/f'rematch_probe_{attempt:02d}_unread.png')
                if attempt<4:self.nav.wait(.5)
            # Keep ownership of that real completed result on the fallback
            # navigator too, so it can use Return to Park instead of neutral
            # dismissal taps. No new match is queued until hub verification.
            self.nav.after_battle=True
        # A result vanished, a rest returned HOME, or quests moved the UI.
        # Re-enter the park hub and verify its deck; never queue from HOME.
        if self.nav is None:self.open_nav(returning=bool(rematch))
        fresh=self.nav.observe()
        if fresh['stage']=='BOARD':self.nav.move('close_quest_board_for_ranked',[44,32])
        self.nav.target='park_battles';self.nav.phase='task';self.nav.deck_checked=False
        self.nav.deck_selecting=False;self.nav.deck_confirming=False
        self.state['park_phase']='prepare';self.persist()
        prepared=self.loop(prepare=True)
        if not prepared:raise RecoveryBlocked('乐园排位准备未完成')
        self.close();self.state['park_phase']='ranked';self.persist()
        return prepared,None

    def after_match(self):
        self.since_scan+=1
        if self.weekly_remaining and self.since_scan>=self.weekly_remaining:self.needs_scan=True

    def finish(self):
        self.scan(returning=True)
        self.state.update(park_phase='finished',reason='本轮排位完成，乐园课题及可领奖励已核对')
        self.persist();self.close()

    def close(self):
        if self.nav:
            self.nav.t.close();self.nav=None
