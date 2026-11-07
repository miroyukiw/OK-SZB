"""Recover via visible app tabs and the deployed project's input transport.

No ADB, shell, desktop UU, emulator shutdown, or host-network control.
"""
import ctypes as C
from ctypes import wintypes as W
import re,time,json
from pathlib import Path
import cv2,numpy as np
from PIL import Image
from maa.controller import Win32Controller
from swb.transport import Transport,wait,InputRotationUnavailable
from swb.vision import Vision
from swb.network_recovery import (RecoveryBlocked,restart_sequence,network_evidence,
    reconnect_five,dialog_button,publish_reconnect,navigation_modal,confirmation_button)

ASSETS=Path(__file__).resolve().parents[1]/'assets/recovery'


class WindowApps:
    def __init__(self,config,out,stop):
        if config.get('platform')!='mumu':raise RecoveryBlocked('Android recovery requires MuMu')
        self.config=dict(config,_panel_managed=True);self.out=Path(out)
        self.out.mkdir(parents=True,exist_ok=True)
        self.stop=stop;self.t=Transport(self.config,self.out,stop)
        # Reuse the currently deployed portrait-navigation adapter, exactly as
        # recover_game.py does. No new Android transport or ADB path is added.
        self.t.enable_input()
        self.v=Vision();self.outer=None;self.n=len(list(self.out.glob('*.png')))
        self.hwnd=self.t.window.u.GetAncestor(self.t.window.hwnd,2)
        self.outer=Win32Controller(self.hwnd,16,4,4)
        self.outer.set_screenshot_use_raw_size(True);wait(self.outer.post_connection())

    def sleep(self,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:self.stop();time.sleep(.1)

    def screen(self,outer=False):
        self.stop();self.t.window.check()
        if outer:
            im=Image.fromarray(wait(self.outer.post_screencap()).get()[:,:,::-1])
        else:im=self.t.capture_raw()
        self.n+=1;im.save(self.out/f'{self.n:04d}_{"window" if outer else "android"}.png')
        return im

    def click(self,point,outer=False):
        self.stop();self.t.window.check()
        controller=self.outer if outer else self.t.controller
        def record(status,**details):
            with (self.out/'inputs.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'time':time.time(),'point':point,'outer':outer,'status':status,**details})+'\n')
        record('intent')
        if not outer:
            before=self.screen()
            size=self.t.window.check()
            if abs(size[0]/size[1]-self.t.sdk.size[0]/self.t.sdk.size[1])>.03:
                self.t.enable_input()
            try:
                previous=getattr(self.t.sdk,'rotation_refresh',None)
                self.t.sdk.input_rotation()
                if getattr(self.t.sdk,'rotation_refresh',None) is not previous:
                    after=self.screen()
                    if after.size!=before.size or np.mean(np.abs(np.asarray(after).astype(float)-np.asarray(before).astype(float)))>3:
                        record('not_sent',reason='UI changed during rotation refresh; target needs reobservation')
                        raise RecoveryBlocked('UI changed during rotation refresh; reobserve navigation target')
                self.t.tap_window(point)
            except InputRotationUnavailable as exc:
                record('not_sent',reason=str(exc),renderer_pid=self.t.window.pid)
                raise
            else:record('dispatched',method='mumu_sdk',rotation_refresh=getattr(self.t.sdk,'rotation_refresh',None))
            self.sleep(1);return
        wait(controller.post_click(*map(int,point)))
        record('dispatched',method='maa_outer_postmessage')
        self.stop();self.sleep(1)

    def navigate(self,rows,text):
        """Choose the top modal first, and wait for changed UI after a click."""
        normalized=[dict(r,box=[r['box'][0]*1280/self.t.raw_size[0],
            r['box'][1]*720/self.t.raw_size[1],r['box'][2]*1280/self.t.raw_size[0],
            r['box'][3]*720/self.t.raw_size[1]]) for r in rows]
        button=confirmation_button(normalized)
        if button:
            point=(round((button['box'][0]+button['box'][2]/2)*self.t.raw_size[0]/1280),
                   round((button['box'][1]+button['box'][3]/2)*self.t.raw_size[1]/720))
        elif navigation_modal(normalized):return False
        else:point=passive_button(rows,text)
        if point is None:return False
        # Loading animation/OCR jitter must not create another START action.
        kind='settled_match_confirmation' if button else 'title_start' if re.search(
            r'TOUCH.*START|TAP.*START',text,re.I) else 'passive_navigation'
        key=(kind,round(point[0]/20),round(point[1]/20))
        if kind=='title_start':
            # Title START is safe to retry only while the exact title button is
            # freshly visible. A cold renderer may ignore its first click.
            attempts=getattr(self,'_title_start_attempts',0)
            if attempts>=3 or time.monotonic()-getattr(self,'_title_start_at',-100)<4:return False
            self._title_start_attempts=attempts+1;self._title_start_at=time.monotonic()
        elif getattr(self,'_navigation_pending',None)==key:return False
        self.click(point);self._navigation_pending=key
        return True

    def icon(self,im,name):
        # Restrict to the application-tab strip; the host close button is never
        # eligible. Match exact observed app icons, not guessed tab indices.
        ref=np.asarray(Image.open(ASSETS/(name+'.png')).convert('RGB'))
        area=np.asarray(im.convert('RGB'))[:55,:im.width-180]
        score=cv2.matchTemplate(area,ref,cv2.TM_CCOEFF_NORMED)
        _,best,_,p=cv2.minMaxLoc(score)
        if best<.92:return None
        return (p[0]+ref.shape[1]//2,p[1]+ref.shape[0]//2)

    def tab(self,im,name):
        point=self.icon(im,name+'_tab')
        if point is not None:return point
        rows=self.v.ocr(im,(0,0,im.width-180,60))
        pattern=r'Shadowverse|影[之の][诗詩]' if name=='game' else r'UU.*加速|^UU$'
        matches=[r for r in rows if re.search(pattern,r['text'],re.I)]
        if len(matches)>1:raise RecoveryBlocked(f'Ambiguous {name} Android tabs')
        if matches:return self.center(matches[0])
        if not any('mumu' in r['text'].lower() for r in rows) and not any(
                self.icon(im,known+'_tab') is not None for known in ('home','uu','game')):
            raise RecoveryBlocked('MuMu application tab strip not identified')
        return None

    def tab_close_control(self,im,point):
        # Selected tabs expand with layout/title width. Find the tab's actual
        # background component, never a fixed offset or the host window controls.
        a=np.asarray(im.convert('RGB'));px,py=map(int,point)
        strip=a[:60,:im.width-180]
        if not(3<=px<strip.shape[1]-4 and 0<=py<strip.shape[0]):
            raise RecoveryBlocked('App tab point is outside the Android tab strip')
        bg=np.median(strip[min(54,py+18),max(0,px-3):px+4],axis=0)
        mask=(np.max(np.abs(strip.astype(float)-bg),axis=2)<10).astype('uint8')
        _,_,stats,_=cv2.connectedComponentsWithStats(mask)
        boxes=[(int(x),int(y),int(w),int(h)) for x,y,w,h,area in stats[1:]
               if x<=px<x+w and y<=py<y+h and 60<=w<im.width*.75 and h>=24 and area>500]
        if len(boxes)!=1:raise RecoveryBlocked('Selected Android tab bounds not verified')
        x,y,w,h=boxes[0];x0=max(x,px+18);x1=x+w
        ref=np.asarray(Image.open(ASSETS/'tab_close.png').convert('RGB'))
        crop=a[y:y+h,x0:x1]
        if crop.shape[0]<ref.shape[0] or crop.shape[1]<ref.shape[1]:
            raise RecoveryBlocked('Selected Android tab has no close-control area')
        scores=cv2.matchTemplate(crop,ref,cv2.TM_CCOEFF_NORMED)
        _,score,_,loc=cv2.minMaxLoc(scores)
        if score<.92:raise RecoveryBlocked('Selected Android tab close button not verified')
        cx=x0+loc[0]+ref.shape[1]//2;cy=y+loc[1]+ref.shape[0]//2
        scores[max(0,loc[1]-ref.shape[0]):loc[1]+ref.shape[0],max(0,loc[0]-ref.shape[1]):loc[0]+ref.shape[1]]=-1
        if scores.max()>=.92:raise RecoveryBlocked('Multiple close controls inside selected Android tab')
        return (cx,cy)

    def close_app(self,name):
        im=self.screen(True);p=self.tab(im,name)
        if p is None:
            with (self.out/'app_state.jsonl').open('a',encoding='utf-8') as f:
                f.write(json.dumps({'app':name,'state':'tab_already_absent','frame':self.n})+'\n')
            return
        self.click(p,True)
        im=self.screen(True);p=self.tab(im,name)
        if p is None:return
        # Look only immediately to the right of the selected app icon. Never
        # click a window close button or another tab's close control.
        close_point=self.tab_close_control(im,p)
        self.click(close_point,True)
        im=self.screen(True)
        deadline=time.monotonic()+8;retried=False;retry_at=time.monotonic()+2
        while self.tab(im,name) is not None:
            if time.monotonic()>deadline:raise RecoveryBlocked(f'{name} tab did not close')
            if not retried and time.monotonic()>retry_at:
                # A first Qt click can only focus/hover. One further click is
                # permitted after a fresh screenshot proves the SAME app and
                # SAME close control still exist; never replay an old position
                # across a tab or geometry transition.
                current=self.tab(im,name)
                if current!=p:raise RecoveryBlocked('Tab moved before close confirmation')
                if self.tab_close_control(im,current)!=close_point:raise RecoveryBlocked('Close control changed')
                self.click(close_point,True)
                retried=True
            self.sleep(.5);im=self.screen(True)

    def close_game(self):self.close_app('game')
    def close_uu(self):self.close_app('uu')

    def close(self):
        if self.outer:self.outer.__del__();self.outer._own=False;self.outer=None
        self.t.close()

    def launch_uu(self):
        im=self.screen();rows=self.v.ocr(im)
        buttons=[r for r in rows if re.fullmatch(r'UU加速器',re.sub(r'\s+','',r['text']),re.I)]
        if len(buttons)!=1:raise RecoveryBlocked('UU Android launcher icon not uniquely identified')
        x,y,w,h=buttons[0]['box'];self.click((x+w//2,y+h//2))
        self.sleep(3)

    @staticmethod
    def text(rows):return re.sub(r'\s+','',''.join(r['text'] for r in rows))

    @staticmethod
    def center(row):
        x,y,w,h=row['box'];return (x+w//2,y+h//2)

    def checked_rows(self,im):
        rows=self.v.ocr(im);text=self.text(rows)
        if re.search(r'连接请求|連線要求|connectionrequest|VPN.{0,8}(?:允许|允許|权限)|登录|登入|sign.?in|log.?in|立即支付|確認支付|立即购买|立即購買|会员(?:已)?到期|开通会员',text,re.I):
            raise RecoveryBlocked('New login, purchase, or VPN permission requires user action')
        return rows

    def game_title(self,rows):
        return any(re.search(r'影[之の][诗詩][:：]?超凡世界|Shadowverse.*Worlds',r['text'],re.I) for r in rows)

    def acceleration_active(self,rows):
        text=self.text(rows)
        legacy='停止加速' in text and any(label in text for label in ('启动游戏','啟動遊戲','啟動游戲','打开游戏','打開遊戲'))
        local=bool('正在加速' in text and re.search(r'\d{1,3}:\d{2}:\d{2}',text))
        return self.game_title(rows) and (legacy or local)

    def accelerate(self):
        deadline=time.monotonic()+45;clicked=False
        while time.monotonic()<deadline:
            im=self.screen();rows=self.checked_rows(im);text=self.text(rows)
            if self.acceleration_active(rows):return
            buttons=[r for r in rows if r['text'].strip()=='加速']
            titles=[r for r in rows if re.search(r'影[之の][诗詩]',r['text'])]
            # Only the button on the approved game's local-game card; exclude
            # the bottom navigation tab also named 加速.
            buttons=[r for r in buttons if any(abs(r['box'][1]-t['box'][1])<80 for t in titles)]
            if not clicked and self.game_title(rows) and len(buttons)==1:
                self.click(self.center(buttons[0]));clicked=True
            self.sleep(.5)
        raise RecoveryBlocked('UU did not confirm acceleration for Shadowverse')

    def launch_game(self):
        self._navigation_pending=None
        self._title_start_attempts=0;self._title_start_at=-100
        im=self.screen();rows=self.checked_rows(im)
        if not self.acceleration_active(rows):
            raise RecoveryBlocked('UU acceleration must be verified before opening Shadowverse')
        buttons=[r for r in rows if r['text'].strip() in ('启动游戏','啟動遊戲','啟動游戲','打开游戏','打開遊戲')]
        if len(buttons)>1:raise RecoveryBlocked('Open Game button is ambiguous')
        if buttons:self.click(self.center(buttons[0]));self.sleep(4);return
        # The local-games UU layout exposes a timer rather than Open Game.
        # Navigate through the observed Android launcher tab and exact app label.
        window=self.screen(True);home=self.icon(window,'home_tab')
        if home is None:raise RecoveryBlocked('Verified UU is active but Android launcher tab was not identified')
        self.click(home,True);im=self.screen();rows=self.checked_rows(im)
        titles=[r for r in rows if re.fullmatch(r'Shadowverse(?:WB|WorldsBeyond|W[.．…]{1,3})|影[之芝][诗詩][:：]?超凡世界',re.sub(r'\s+','',r['text']),re.I)]
        if len(titles)!=1:raise RecoveryBlocked('Shadowverse Android launcher icon was not uniquely identified')
        self.click(self.center(titles[0]));self.sleep(4)

    def wait_lobby(self):
        # This is a verification stage inside ONE numbered attempt. Reconnect
        # dialogs are handled only by the outer five-attempt owner.
        deadline=time.monotonic()+90;stable=0;last_action=0
        while time.monotonic()<deadline:
            try:im=self.screen()
            except RuntimeError as exc:
                if 'Blank Win32 capture persisted' not in str(exc):raise
                # Cold starts may outlast one capture's bounded wait. Stay
                # inside the original 90-second startup budget; send no input.
                with (self.out/'capture_wait.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps({'time':time.time(),'reason':str(exc),'capture':self.t.capture_method})+'\n')
                self.sleep(.5);continue
            rows=self.checked_rows(im);text=self.text(rows)
            if network_evidence(text):
                raise RecoveryBlocked('Network dialog remained after restart; numbered attempt failed')
            if abs(im.width/im.height-16/9)>.03:self.sleep(.5);continue
            normalized=[dict(r,box=[r['box'][0]*1280/im.width,r['box'][1]*720/im.height,
                r['box'][2]*1280/im.width,r['box'][3]*720/im.height]) for r in rows]
            if navigation_modal(normalized):
                stable=0;self.navigate(rows,text);self.sleep(.5);continue
            scene,_=self.v.scene(im.resize((1280,720)))
            if scene in ('HOME','RANKED_READY','RANKED_SETUP'):
                stable+=1
                if stable>=2:return
            else:stable=0
            if time.monotonic()-last_action>4:
                if self.navigate(rows,text):last_action=time.monotonic()
            self.sleep(.5)
        raise RecoveryBlocked('No verified lobby after restart; numbered attempt failed')


def passive_button(rows,text):
    """Allow observed title, received-reward and settled-result navigation only."""
    if navigation_modal(rows):return None
    if re.search(r'Now\s*Loading|正在[載载]入|[讀读]取中',text,re.I):return None
    if re.search(r'領取結果|领取结果|已領取|已领取',text) and re.search(r'TAP',text,re.I):
        taps=[row for row in rows if re.fullmatch(r'TAP',row['text'].strip(),re.I)]
        if len(taps)==1:return WindowApps.center(taps[0])
    if re.search(r'TOUCH.*START|TAP.*START|點擊.*開始|点击.*开始',text,re.I):
        targets=[row for row in rows if re.search(r'TOUCH.*START|TAP.*START|點擊.*開始|点击.*开始',row['text'],re.I)]
        if len(targets)==1:return WindowApps.center(targets[0])
    if re.search(r'RESULT|WIN|LOSE|VICTORY|DEFEAT',text,re.I):
        targets=[row for row in rows if re.fullmatch(r'返回主[頁页]面|回到主[頁页]面',row['text'].strip())]
        if len(targets)==1:return WindowApps.center(targets[0])
    return None


class ReconnectFlow:
    def __init__(self,adapter,allow_combat=False):
        self.a=adapter;self.allow_combat=allow_combat;self.events=[]

    def attempt(self,number):
        a=self.a;stable=0;clicked=False;deadline=time.monotonic()+12;initial_phase=None;observed=False
        # Fresh captures precede every decision. Unknown frames get reread;
        # no arbitrary screen-center tap is permitted.
        while time.monotonic()<deadline:
            a.stop();im=a.screen();rows=a.checked_rows(im);text=a.text(rows)
            if abs(im.width/im.height-16/9)>.03:
                if a.acceleration_active(rows):
                    # Reuse the already verified Android UU session; do not
                    # restart it merely because its display is portrait.
                    a.launch_game();a.wait_lobby()
                    return {'mode':'safe_lobby','safe_lobby':True,'apps_restarted':False,
                        'acceleration_verified':True,'interrupted_outcome':'unknown','replay_pending':False,
                        'actions':self.events+[{'attempt':number,'action':'launch_from_verified_uu'}]}
                break
            normalized=im.resize((1280,720));scene,_=a.v.scene(normalized)
            normalized_rows=[dict(row,box=[round(row['box'][0]*1280/im.width),round(row['box'][1]*720/im.height),
                round(row['box'][2]*1280/im.width),round(row['box'][3]*720/im.height)]) for row in rows]
            button=dialog_button(normalized_rows)
            if network_evidence(text):
                stable=0
                if not clicked and button is not None:
                    point=(round((button['box'][0]+button['box'][2]/2)*im.width/1280),
                           round((button['box'][1]+button['box'][3]/2)*im.height/720))
                    self.events.append({'attempt':number,'action':'dialog_button','text':button['text'],'point':point})
                    a.click(point);clicked=True
                a.sleep(.7);continue
            if navigation_modal(normalized_rows):
                stable=0;a.navigate(rows,text);a.sleep(.7);continue
            phase=a.v.turn(normalized) if self.allow_combat else None
            if not observed:initial_phase=phase;observed=True
            combat_verified=phase in ('OUR','ENEMY') and (clicked or phase!=initial_phase)
            if scene in ('HOME','RANKED_READY','RANKED_SETUP') or combat_verified:
                stable+=1
                if stable>=2:
                    mode='continue' if phase else 'safe_lobby'
                    return {'mode':mode,'safe_lobby':not bool(phase),'apps_restarted':False,
                        'acceleration_verified':False,'interrupted_outcome':'unknown','replay_pending':False,
                        'actions':self.events}
            else:stable=0
            if a.navigate(rows,text):
                self.events.append({'attempt':number,'action':'observed_navigation_button'})
            a.sleep(.7)
        # Escalation is part of this attempt, not another hidden retry loop.
        result=restart_sequence(a,a.stop,lambda stage,status:self.events.append(
            {'attempt':number,'stage':stage,'status':status}))
        return dict(result,mode='safe_lobby',actions=self.events)


def recover_in_run(root,worker,trigger):
    stop=worker.stop;batch=getattr(worker.args,'batch',None)
    return recover(root,worker.args,worker,stop,lambda *args:None,allow_combat=bool(worker.started),trigger=trigger,batch=batch)


def recover(root,args,worker,stop,event,allow_combat=False,trigger='network',batch=None):
    output=worker.out/'reconnect';output.mkdir(parents=True,exist_ok=True)
    def progress(number,status,message):
        publish_reconnect(root,output,number,status,message,batch or getattr(args,'batch',None))
        event('reconnect_'+str(number),status)
    # Initialization failures also consume exactly one numbered attempt.
    adapter=None;flow=None
    def attempt(number):
        nonlocal adapter,flow
        if number>1 and adapter:
            adapter.close();adapter=None;flow=None
        if adapter is None:
            adapter=WindowApps(worker.config,output,stop);flow=ReconnectFlow(adapter,allow_combat)
        return flow.attempt(number)
    try:
        result=reconnect_five(attempt,stop,progress);result['trigger']=trigger
        return result
    finally:
        if adapter:adapter.close()
