"""Persistent four-hour play / random 30-60 minute rest, at match boundaries."""
import math
import random
import time
from datetime import datetime,timezone
from pathlib import Path
from swb.atomic_io import read_json,write_json

WORK_SECONDS=4*60*60
MIN_REST_SECONDS=30*60
MAX_REST_SECONDS=60*60

class FinishRequested(InterruptedError):pass

class RestSchedule:
    def __init__(self,root,folder,state,rows,config,adapter_factory=None,clock=time):
        self.root=Path(root);self.folder=Path(folder);self.batch=state;self.config=config;self.clock=clock
        self.path=self.root/'user_data/scheduled_rest.json'
        self.factory=adapter_factory
        existed=self.path.exists()
        self.data=read_json(self.path) if existed else {'schema':1,'created_at':clock.time(),'active_seconds':0.,'cycles':0,'accounted_runs':[],'rest':None}
        if self.data.get('schema')!=1 or not isinstance(self.data.get('active_seconds'),(float,int)) or self.data['active_seconds']<0:
            raise RuntimeError('Invalid scheduled rest checkpoint; retained for inspection')
        rest=self.data.get('rest')
        if rest is not None and (rest.get('phase') not in ('closing','resting','opening')
                or not MIN_REST_SECONDS<=rest.get('duration_seconds',0)<=MAX_REST_SECONDS
                or (rest.get('phase')=='resting' and not isinstance(rest.get('until'),(float,int)))):
            raise RuntimeError('Invalid scheduled rest deadline; retained for inspection')
        known=set(self.data['accounted_runs'])
        # Import the currently running batch once. Later starts do not charge
        # historical pre-feature batches, or count the same match twice.
        for row in rows:
            name=str(row['run'])
            if name not in known:
                summary=Path(name)/'summary.json'
                if not existed or (summary.exists() and summary.stat().st_mtime>=self.data['created_at']):
                    self.data['active_seconds']+=max(0,float(row.get('seconds') or 0))
                known.add(name)
        self.data['accounted_runs']=sorted(known)
        self.anchor=None if self.data.get('rest') else clock.monotonic()
        self.persist()

    def persist(self):
        self.data['updated_at']=self.clock.time();write_json(self.path,self.data)

    def checkpoint(self,rows=()):
        now=self.clock.monotonic()
        if self.anchor is not None:
            self.data['active_seconds']+=max(0,now-self.anchor);self.anchor=now
        self.data['accounted_runs']=sorted(set(self.data['accounted_runs'])|{str(r['run']) for r in rows})
        self.persist()

    def check_skip(self):
        if (self.root/'SKIP_REST').exists():
            (self.root/'SKIP_REST').unlink(missing_ok=True)
            return True
        return False

    def control(self):
        if (self.root/'STOP').exists():raise InterruptedError('STOP requested during scheduled rest')
        if (self.root/'PANEL_FINISH').exists():raise FinishRequested('Finish-after-match requested during scheduled rest')

    def unpaused(self):
        self.control()
        began=self.clock.monotonic()
        while (self.root/'PANEL_PAUSE').exists():
            self.control();self.clock.sleep(.2)
        if self.anchor is not None:self.anchor+=self.clock.monotonic()-began
        self.control()

    def wait(self,seconds):
        end=self.clock.monotonic()+seconds
        while self.clock.monotonic()<end:
            self.control();self.clock.sleep(min(.2,end-self.clock.monotonic()))

    def publish(self,phase):
        rest=self.data.get('rest') or {}
        remaining=max(0,math.ceil(rest.get('until',self.clock.time())-self.clock.time()))
        message=(f'休息中，剩余约 {math.ceil(remaining/60)} 分钟；游戏已关闭，加速器保持运行。'
                 if phase=='resting' else '休息结束，正在启动游戏并确认主页。' if phase=='opening'
                 else '已运行4小时，正在关闭游戏进入休息；加速器保持运行。')
        self.batch.update(status='resting',reason=message,rest={**rest,'phase':phase,'remaining_seconds':remaining},updated_utc=datetime.now(timezone.utc).isoformat())
        write_json(self.folder/'status.json',self.batch)

    def before_match(self):
        try:return self._before_match()
        except FinishRequested:return False

    def _before_match(self):
        if self.check_skip():
            self.data['active_seconds']=0.;self.data['rest']=None;self.persist()
            return True
        self.unpaused();self.checkpoint()
        if self.check_skip():
            self.data['active_seconds']=0.;self.data['rest']=None;self.persist()
            return True
        rest=self.data.get('rest')
        if rest is None and self.data['active_seconds']<WORK_SECONDS:return True
        if self.batch.get('active_match') is not None or (self.root/'RUNNING.lock').exists():
            raise RuntimeError('Scheduled rest requires a completed match and no combat runner')
        if rest is None:
            rest={'phase':'closing','duration_seconds':random.SystemRandom().randint(MIN_REST_SECONDS,MAX_REST_SECONDS),'planned_at':self.clock.time()}
            self.data['rest']=rest;self.persist()
        self.anchor=None
        if self.factory is None:
            from swb.rest_lifecycle import MuMuGameLifecycle
            factory=MuMuGameLifecycle
        else:factory=self.factory
        out=self.folder/'rest_events'/str(self.data['cycles']+1)
        adapter=factory(self.config,out,self.unpaused)
        try:
            if rest['phase']=='closing':
                self.publish('closing');adapter.close_game();self.control()
                # The rest starts only after closure is verified. Persist the
                # selected deadline before sleeping so restart cannot reroll it.
                rest.update(phase='resting',closed_at=self.clock.time(),until=self.clock.time()+rest['duration_seconds'])
                self.persist()
            while rest['phase']=='resting' and self.clock.time()<rest['until']:
                if self.check_skip():
                    break
                self.control();self.publish('resting')
                self.wait(min(5,rest['until']-self.clock.time()))
            self.unpaused()
            rest['phase']='opening';self.persist();self.publish('opening')
            adapter.open_game();self.control()
            self.data['last_rest']={**rest,'completed_at':self.clock.time()}
            self.data['cycles']+=1;self.data['active_seconds']=0.;self.data['rest']=None
            self.persist();self.anchor=self.clock.monotonic()
            self.batch.update(status='running',reason='休息结束，游戏主页已确认，继续排位。',rest=None)
            write_json(self.folder/'status.json',self.batch)
            return True
        finally:adapter.close()
