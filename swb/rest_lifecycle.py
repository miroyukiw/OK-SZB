"""MuMu's installed CLI controls ONLY Shadowverse; UU is checked read-only."""
import json
import re
import subprocess
import time
from pathlib import Path
from swb.atomic_io import write_json

GAME='jp.co.cygames.ShadowverseWorldsBeyond'
UU='com.netease.uu'

class MuMuGameLifecycle:
    def __init__(self,config,out,stop):
        if config.get('platform')!='mumu':raise RuntimeError('Scheduled rest requires the configured MuMu player')
        self.config=config;self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True);self.stop=stop

    def command(self,args):
        self.stop()
        p=subprocess.Popen([self.config['cli'],*args],stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            end=time.monotonic()+20
            while p.poll() is None:
                self.stop()
                if time.monotonic()>end:raise TimeoutError('MuMu app command timed out')
                time.sleep(.1)
            stdout,stderr=p.communicate();self.stop()
            text=stdout.decode('utf-8',errors='replace').strip()
            if p.returncode:raise RuntimeError('MuMu app command failed: '+stderr.decode('utf-8',errors='replace')[:500])
            if text.startswith('{'):
                result=json.loads(text)
                if result.get('error_code',result.get('errcode',0))!=0:raise RuntimeError('MuMu rejected app command: '+text[:500])
            return text
        finally:
            if p.poll() is None:p.kill();p.communicate()

    def pids(self,package):
        if package not in (GAME,UU):raise ValueError('Package not allowed')
        result=self.command(['sh','--vmindex',str(self.config['instance']),'--cmd','pidof '+package])
        if result and not re.fullmatch(r'\d+(?:\s+\d+)*',result):raise RuntimeError('Unexpected MuMu process verification response')
        return sorted(map(int,result.split()))

    def app(self,action):
        if action not in ('close','launch'):raise ValueError('App action not allowed')
        # Package cannot come from a request, screenshot, or arbitrary config.
        return self.command(['control','--vmindex',str(self.config['instance']),'app',action,'--package',GAME])

    def verify_uu(self):
        pids=self.pids(UU)
        if not pids:raise RuntimeError('UU is not running; scheduled rest will not change accelerator state')
        return pids

    def close_game(self):
        before=self.verify_uu();self.app('close')
        end=time.monotonic()+12
        while self.pids(GAME):
            self.stop()
            if time.monotonic()>end:raise RuntimeError('Shadowverse did not close; rest timer not started')
            time.sleep(.2)
        after=self.verify_uu()
        if before!=after:raise RuntimeError('UU process changed while closing game; retained for inspection')
        write_json(self.out/'closed.json',{'time':time.time(),'game_closed':True,'uu_pids_before':before,'uu_pids_after':after,'accelerator_touched':False})

    def open_game(self):
        before=self.verify_uu()
        if not self.pids(GAME):self.app('launch')
        end=time.monotonic()+15
        while not self.pids(GAME):
            self.stop()
            if time.monotonic()>end:raise RuntimeError('Shadowverse did not start')
            time.sleep(.2)
        # Reuse existing title/lobby navigation. This path never calls the
        # network restart sequence and cannot close, start, or configure UU.
        from swb.android_recovery import WindowApps
        adapter=WindowApps(self.config,self.out/'lobby',self.stop)
        try:adapter.wait_lobby()
        finally:adapter.close()
        after=self.verify_uu()
        if before!=after:raise RuntimeError('UU process changed during game launch')
        write_json(self.out/'opened.json',{'time':time.time(),'safe_lobby':True,'game_running':True,'uu_pids_before':before,'uu_pids_after':after,'accelerator_touched':False})

    def close(self):pass
