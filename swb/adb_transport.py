"""Explicit Android-only transport. Never touches the Windows desktop or UU."""
import io
import re
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from PIL import Image

GAME='jp.co.cygames.ShadowverseWorldsBeyond'
UU='com.netease.uu'

def game_data_download(rows):
    """Only the free, pre-login game-content download prompt, never store UI."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not re.search('下[載载][資资]料',text) or not re.search('開始遊戲前|开始游戏前',text):return None
    if not re.search('須下[載载]遊戲資料|须下载游戏资料',text):return None
    size=re.search(r'(\d+(?:[.,]\d+)?)\s*(MB|GB)',text,re.I)
    candidates=[r for r in rows if re.fullmatch('OK',r['text'].strip(),re.I) and r.get('score',0)>=.8
        and 650<=r['box'][0]+r['box'][2]/2<=1000 and 450<=r['box'][1]+r['box'][3]/2<=620]
    if not size or len(candidates)!=1:return None
    x,y,w,h=candidates[0]['box']
    return {'point':[x+w//2,y+h//2],'size':size[1]+size[2].upper()}

class AdbClient:
    def __init__(self,config,stop=lambda:None):
        self.stop=stop;self.config=config
        self.binary=str(config.get('adb_path') or Path(config['cli']).with_name('adb.exe'))
        self.serial=config.get('adb_serial','127.0.0.1:16384')
        if not isinstance(self.serial,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',self.serial):
            raise ValueError('Invalid explicit ADB serial')
    def call(self,*args,empty_ok=False,timeout=25,retries=2):
        for attempt in range(retries+1):
            self.stop()
            try:
                p=subprocess.run([self.binary,'-s',self.serial,*map(str,args)],capture_output=True,timeout=timeout,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.stop()
                if p.returncode and not (empty_ok and not p.stdout.strip()):
                    raise RuntimeError('ADB command failed: '+p.stderr.decode(errors='replace')[:300])
                return p.stdout
            except subprocess.TimeoutExpired as exc:
                self.stop()
                if attempt<retries:
                    time.sleep(1)
                    continue
                raise TimeoutError(f'ADB command timed out after {timeout}s: {" ".join(map(str,args))}') from exc
    def _connect_serial(self,serial):
        subprocess.run([self.binary,'connect',serial],capture_output=True,timeout=15,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.serial=serial
        if isinstance(getattr(self,'config',None),dict):self.config['adb_serial']=serial
        return self.call('get-state').decode().strip()
    def _discover_mumu_serial(self):
        mgr=Path(self.binary).with_name('MuMuManager.exe')
        if not mgr.is_file():return None
        inst=str((getattr(self,'config',None) or {}).get('instance',0))
        p=subprocess.run([str(mgr),'info','-v',inst],capture_output=True,timeout=10,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:return None
        import json as _json
        data=_json.loads(p.stdout.decode(errors='replace'))
        host=data.get('adb_host_ip') or '127.0.0.1';port=data.get('adb_port')
        return f'{host}:{port}' if port else None
    def connect(self):
        try:state=self.call('get-state').decode().strip()
        except RuntimeError:
            try:
                if not re.fullmatch(r'127\.0\.0\.1:\d{1,5}',self.serial):raise RuntimeError('non-local')
                state=self._connect_serial(self.serial)
            except RuntimeError:
                discovered=self._discover_mumu_serial()
                if not discovered:raise
                state=self._connect_serial(discovered)
        if state!='device':raise RuntimeError('ADB device is not online')
    def pids(self,package):
        if package not in (GAME,UU,UU+':core'):raise ValueError('Package not allowed')
        raw=self.call('shell','pidof',package,empty_ok=True).decode().strip()
        if raw and not re.fullmatch(r'\d+(?:\s+\d+)*',raw):raise RuntimeError('Invalid Android process response')
        return tuple(sorted(map(int,raw.split())))
    def focus(self):
        raw=self.call('shell','dumpsys','window').decode(errors='replace')
        top=re.search(r'mTopFocusedDisplayId=(\d+)',raw)
        if not top:raise RuntimeError('Android focused display is unknown')
        display=int(top[1]);blocks=re.split(r'Display: mDisplayId=(\d+)',raw)
        block=next((blocks[i+1] for i in range(1,len(blocks)-1,2) if int(blocks[i])==display),'')
        focused=re.search(r'mCurrentFocus=Window\{[^\n]*\s([^\s/]+)/',block)
        pkg=focused[1] if focused else None
        if pkg!=GAME:
            for i in range(1,len(blocks)-1,2):
                m=re.search(r'mCurrentFocus=Window\{[^\n]*\s([^\s/]+)/',blocks[i+1])
                if m and m[1]==GAME:return int(blocks[i]),GAME
        return display,pkg
    def screen(self,display=None):
        if display is None:
            try:display,_=self.focus()
            except Exception:display=None
        cmd=('exec-out','screencap','-d',str(display),'-p') if display is not None else ('exec-out','screencap','-p')
        return Image.open(io.BytesIO(self.call(*cmd))).convert('RGB')
    def launch_game(self):
        component=self.call('shell','cmd','package','resolve-activity','--brief',GAME).decode().strip().splitlines()[-1]
        if not component.startswith(GAME+'/'):raise RuntimeError('Game activity did not resolve')
        self.call('shell','am','start','-n',component)

class AdbTransport:
    capture_method='ADB exec-out screencap'
    input_name='ADB input'
    is_adb=True
    def __init__(self,config,out,stop):
        self.stop=stop;self.out=Path(out);self.metrics=None;self.last_capture=0
        self.adb=AdbClient(config,stop);self.adb.connect()
        pids=self.adb.pids(GAME)
        if not pids:raise RuntimeError('Shadowverse is not running on the selected ADB device')
        self.game_pids=pids;self.display,package=self.adb.focus()
        if package!=GAME:raise RuntimeError('The selected Android display is not Shadowverse')
        self.window=SimpleNamespace(hwnd=None,pid=pids[0],events=[])
    def refresh_process(self,timeout=20):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            self.stop()
            pids=self.adb.pids(GAME)
            if pids:
                try:
                    display,package=self.adb.focus()
                    if package==GAME:
                        self.game_pids=pids;self.display=display
                        self.window=SimpleNamespace(hwnd=None,pid=pids[0],events=[])
                        return self.game_pids
                except Exception:pass
            time.sleep(.5)
        pids=self.adb.pids(GAME)
        if not pids:raise RuntimeError('Shadowverse is not running on the selected ADB device')
        self.game_pids=pids;self.display,package=self.adb.focus()
        if package!=GAME:raise RuntimeError('The selected Android display is not Shadowverse')
        self.window=SimpleNamespace(hwnd=None,pid=pids[0],events=[])
        return self.game_pids
    def capture_raw(self):
        self.stop();im=self.adb.screen(getattr(self,'display',None));self.raw_size=im.size;self.last_capture=time.monotonic();return im
    def enable_input(self):self.stop()
    def gesture(self,start,end=None,duration=350,trace=None):
        self.stop()
        if time.monotonic()-self.last_capture>20:raise RuntimeError('ADB requires a fresh screenshot')
        if self.adb.focus()!=(self.display,GAME) or self.adb.pids(GAME)!=self.game_pids:
            display,package=self.adb.focus();current_pids=self.adb.pids(GAME)
            if package==GAME and current_pids:
                self.display=display;self.game_pids=current_pids
                self.window=SimpleNamespace(hwnd=None,pid=current_pids[0],events=[])
            else:
                raise RuntimeError('Android game/display changed; input stopped')
        w,h=self.raw_size
        if abs(w/h-16/9)>.03:raise RuntimeError('ADB input requires the landscape game')
        def point(p):
            if len(p)!=2 or not 0<=p[0]<1280 or not 0<=p[1]<720:raise ValueError('Input is outside the game')
            return [round(p[0]*w/1280),round(p[1]*h/720)]
        a=point(start)
        for attempt in range(3):
            try:
                if end is not None:self.adb.call('shell','input','-d',self.display,'swipe',*a,*point(end),duration)
                else:self.adb.call('shell','input','-d',self.display,'tap',*a)
                break
            except RuntimeError as exc:
                if attempt<2 and 'INJECT_EVENTS' in str(exc):
                    self.stop();time.sleep(.3)
                    display,package=self.adb.focus();current_pids=self.adb.pids(GAME)
                    if package==GAME and current_pids:
                        self.display=display;self.game_pids=current_pids
                        self.window=SimpleNamespace(hwnd=None,pid=current_pids[0],events=[])
                    else:
                        raise RuntimeError('Android game/display changed; input stopped')
                    continue
                raise
        self.last_capture=0
        if trace:trace('target')
    def close(self):pass

class AdbGameLifecycle:
    """Rest boundary adapter: stop/start GAME only, preserve accelerator PIDs."""
    def __init__(self,config,out,stop):
        self.config=config;self.out=Path(out);self.out.mkdir(parents=True,exist_ok=True)
        self.stop=stop;self.adb=AdbClient(config,stop);self.adb.connect()
    def accelerator(self):
        pids=self.adb.pids(UU+':core') or self.adb.pids(UU)
        if not pids:raise RuntimeError('UU is not running; enable acceleration before continuing')
        return pids
    def close_game(self):
        from swb.atomic_io import write_json
        before=self.accelerator();self.adb.call('shell','am','force-stop',GAME)
        end=time.monotonic()+12
        while self.adb.pids(GAME):
            self.stop()
            if time.monotonic()>end:raise RuntimeError('Game did not close; rest not started')
            time.sleep(.2)
        after=self.accelerator()
        if before!=after:raise RuntimeError('Accelerator process changed during game close')
        write_json(self.out/'closed.json',{'game_closed':True,'uu_pids_before':before,'uu_pids_after':after,'accelerator_touched':False})
    def open_game(self):
        from swb.vision import Vision
        from swb.atomic_io import write_json
        from swb.paradise_quests import inspect as park_inspect
        before=self.accelerator();self.adb.launch_game();v=Vision()
        end=time.monotonic()+90;last_tap=0;stable=0;download_sent=False;download_at=None;restore_sent=False
        while time.monotonic()<end:
            self.stop();im=self.adb.screen(getattr(self,'display',None))
            if abs(im.width/im.height-16/9)>.03:time.sleep(.5);continue
            w,h=im.size;im=im.resize((1280,720));scene=v.scene(im)[0]
            if scene=='HOME' or park_inspect(v,im)['stage'] in ('WORLD','NPC_INTERACT'):
                stable+=1
                if stable>=2:break
            else:stable=0
            text=' '.join(r['text'] for r in v.ocr(im,(150,100,1130,640)))
            if re.search('系統.*維護中|系统.*维护中',text):
                im.save(self.out/'maintenance.png')
                raise RuntimeError('游戏服务器维护中：'+re.sub(r'\s+',' ',text))
            if re.search('新版本',text) and re.search('重新[啟启]動|重新启动',text):
                im.save(self.out/'update_required.png')
                raise RuntimeError('Game requires a version update/restart; screenshot retained, UU unchanged')
            from swb.network_recovery import network_evidence,navigation_modal,park_reentry
            if network_evidence(text):raise RuntimeError('Network dialog after rest; game retained for inspection')
            rows=v.ocr(im,(20,20,1250,700))
            download=game_data_download(rows)
            restore=park_reentry(rows)
            if download:
                if download_sent:
                    if time.monotonic()-download_at>20:raise RuntimeError('Game data download confirmation did not advance; not clicked twice')
                else:
                    display,package=self.adb.focus()
                    if package!=GAME:raise RuntimeError('Game is not the focused Android app')
                    download_sent=True;download_at=time.monotonic();end=max(end,download_at+15*60)
                    write_json(self.out/'data_download_commit.json',{'size':download['size'],'point':download['point'],'free_game_content':True,'accelerator_touched':False})
                    px,py=download['point'];px,py=round(px*w/1280),round(py*h/720)
                    self.adb.call('shell','input','-d',display,'swipe',px,py,px,py,200)
            elif restore and restore.get('button') and time.monotonic()-last_tap>4:
                display,package=self.adb.focus()
                if package!=GAME:raise RuntimeError('Game is not the focused Android app')
                bx,by,bw,bh=restore['button']['box']
                self.adb.call('shell','input','-d',display,'tap',round((bx+bw//2)*w/1280),round((by+bh//2)*h/720))
                restore_sent=True;last_tap=time.monotonic()
            elif not navigation_modal(rows) and re.search(r'TAP.*START|TOUCH.*START|TOSTART|點擊.*開始|点击.*开始|CRIWARE',text,re.I) and time.monotonic()-last_tap>4:
                display,package=self.adb.focus()
                if package!=GAME:raise RuntimeError('Game is not the focused Android app')
                self.adb.call('shell','input','-d',display,'tap',round(640*w/1280),round(540*h/720))
                last_tap=time.monotonic()
            time.sleep(.5)
        else:raise RuntimeError('No verified game homepage after rest')
        after=self.accelerator()
        if before!=after:raise RuntimeError('Accelerator process changed during game launch')
        write_json(self.out/'opened.json',{'safe_lobby':True,'uu_pids_before':before,'uu_pids_after':after,'accelerator_touched':False})
    def close(self):pass
