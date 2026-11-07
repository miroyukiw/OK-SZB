"""MAA Win32 capture + explicitly selected background input, never ADB."""
import ctypes as C
from ctypes import wintypes as W
import json
import os
import re
from pathlib import Path
import subprocess
import time
from contextlib import nullcontext

import numpy as np
import cv2
from PIL import Image
from maa.controller import Win32Controller
from maa.toolkit import Toolkit


class InputRotationUnavailable(RuntimeError):
    """A size query succeeded, but no current-renderer transform was observed.

    Raised before touch-down; a missing transform is never replaced by a guess.
    """
    pass


def capture_has_content(frame):
    """Reject solid renderer placeholders even when their RGB channels differ.

    MuMu can add a thin border around the placeholder. Ignore that border and
    measure variation inside each channel, rather than between RGB values.
    """
    if frame is None or frame.ndim<2 or min(frame.shape[:2])<20:return False
    h,w=frame.shape[:2];core=frame[h//20:h-h//20,w//20:w-w//20]
    return float(cv2.meanStdDev(core)[1].max())>=3


def wait(job, timeout=12):
    deadline = time.monotonic() + timeout
    while not job.done:
        if time.monotonic() > deadline:
            raise TimeoutError('MAA job timeout')
        time.sleep(.02)
    if not job.succeeded:
        raise RuntimeError('MAA job failed')
    return job


class Window:
    def __init__(self, config):
        self.config = config
        self.u = C.WinDLL('user32', use_last_error=True)
        for name, args, result in [
            ('ShowWindow', [W.HWND,C.c_int], W.BOOL),
            ('IsWindow', [W.HWND], W.BOOL), ('IsIconic', [W.HWND], W.BOOL),
            ('GetAncestor', [W.HWND, W.UINT], W.HWND),
            ('GetWindowThreadProcessId', [W.HWND, C.POINTER(W.DWORD)], W.DWORD),
            ('GetClientRect', [W.HWND, C.POINTER(W.RECT)], W.BOOL),
            ('SetThreadDpiAwarenessContext', [C.c_void_p], C.c_void_p),
        ]:
            fn = getattr(self.u, name); fn.argtypes = args; fn.restype = result
        self.events=[]
        self.u.SetThreadDpiAwarenessContext(C.c_void_p(-4))
        if config['platform'] == 'mumu':
            p = subprocess.run([config['cli'], 'info', '--vmindex', str(config['instance'])],
                               capture_output=True, check=True, timeout=10,
                               creationflags=subprocess.CREATE_NO_WINDOW)
            info = json.loads(p.stdout)
            if info.get('error_code') != 0 or (not info.get('is_android_started') and not (info.get('is_process_started') and info.get('render_wnd'))):
                raise RuntimeError('MuMu instance is not running')
            if info.get('is_process_started') and info.get('render_wnd'):
                self.hwnd=int(info['render_wnd'],16);self.pid=int(info['pid'])
            else:
                # Some MuMu builds keep a valid Qt renderer while CLI info
                # incorrectly reports is_process_started=false. Bind only a
                # unique observed instance title and its own renderer child.
                roots=[w for w in Toolkit.find_desktop_windows() if w.window_name==info.get('name') and w.class_name.startswith('Qt')]
                if len(roots)!=1:raise RuntimeError('MuMu renderer absent or ambiguous')
                root=roots[0].hwnd;pid=W.DWORD();self.u.GetWindowThreadProcessId(root,C.byref(pid));self.pid=pid.value
                children=[];callback_type=C.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
                def inspect(hwnd,lparam):
                    title=C.create_unicode_buffer(260);owner=W.DWORD()
                    self.u.GetWindowTextW(hwnd,title,260);self.u.GetWindowThreadProcessId(hwnd,C.byref(owner))
                    if title.value=='MuMuNxDevice' and owner.value==self.pid:children.append(hwnd)
                    return True
                self.u.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,C.c_int]
                self.u.EnumChildWindows.argtypes=[W.HWND,callback_type,W.LPARAM]
                self.u.EnumChildWindows(root,callback_type(inspect),0)
                if len(children)!=1:raise RuntimeError('MuMu instance renderer child absent or ambiguous')
                self.hwnd=children[0];self.events.append({'event':'renderer_bound','basis':'unique_instance_title_and_same_pid_child','pid':self.pid,'hwnd':self.hwnd})
        else:
            candidates = [w for w in Toolkit.find_desktop_windows()
                          if w.hwnd == config.get('hwnd')]
            if len(candidates) != 1:
                raise RuntimeError('Select exactly one PC HWND with the windows command')
            self.hwnd = candidates[0].hwnd
            pid = W.DWORD(); self.u.GetWindowThreadProcessId(self.hwnd, C.byref(pid))
            self.pid = pid.value
        self.size = self.check()

    def check(self):
        if not self.u.IsWindow(self.hwnd):
            raise RuntimeError('Bound window closed')
        pid = W.DWORD(); self.u.GetWindowThreadProcessId(self.hwnd, C.byref(pid))
        if pid.value != self.pid:
            raise RuntimeError('Bound window PID changed')
        root = self.u.GetAncestor(self.hwnd, 2)
        if self.u.IsIconic(root) and self.config['platform']=='mumu':
            self.u.ShowWindow(root,4)  # SW_SHOWNOACTIVATE; never focus another app.
            deadline=time.monotonic()+2
            while self.u.IsIconic(root) and time.monotonic()<deadline:time.sleep(.05)
            if self.u.IsIconic(root):raise RuntimeError('MuMu window restore failed')
            self.last_restore=time.monotonic()
            self.events.append({'event':'window_restored','root':root,'pid':self.pid,'activation':False})
        elif self.u.IsIconic(root):raise RuntimeError('Bound PC window minimized')
        rect = W.RECT()
        if not self.u.GetClientRect(self.hwnd, C.byref(rect)):
            raise C.WinError(C.get_last_error())
        size = (rect.right, rect.bottom)
        if min(size) < 100:
            raise RuntimeError(f'Unexpected game viewport {size}')
        return size


class Transport:
    def __init__(self, config, log_dir, stop=lambda: None):
        self.config, self.stop = config, stop
        Toolkit.init_option(str(log_dir),{'logging':False,'save_draw':False,'save_on_error':False,'stdout_level':0} if config.get('_panel_managed') else None)
        self.window = Window(config)
        self.controller = None
        errors = []
        methods = [(16, 'PrintWindow'), (2, 'FramePool'), (1, 'GDI')] if config['platform']=='mumu' else [(2, 'FramePool'), (16, 'PrintWindow'), (1, 'GDI')]
        selected = config.get('capture_method', 'auto')
        if selected != 'auto':
            methods = [m for m in methods if m[1] == selected]
            if not methods:raise ValueError('Unknown MAA capture method')
        for method, label in methods:
            c = Win32Controller(self.window.hwnd, method, 4, 4)
            c.set_screenshot_use_raw_size(True)
            try:
                wait(c.post_connection())
                self.controller, self.capture_method = c, label
                break
            except (RuntimeError, TimeoutError) as exc:
                errors.append(f'{label}: {exc}')
                c.__del__(); c._own = False
        if self.controller is None:
            raise RuntimeError('; '.join(errors))
        self.sdk = None
        self.last_capture = 0
        self.metrics=None

    def capture(self):
        image=self.capture_raw()
        if abs(image.width/image.height-16/9)>.03:
            raise RuntimeError(f'Game is not landscape: {image.size}')
        return image.resize((1280,720))

    def capture_raw(self):
        self.stop()
        self.window.check()
        deadline=time.monotonic()+8
        retried=-100.
        while True:
            with self.metrics.span('maa_capture_wait') if self.metrics else nullcontext():
                try:job=wait(self.controller.post_screencap())
                except RuntimeError:
                    self.stop();self.window.check()
                    if time.monotonic()-getattr(self.window,'last_restore',-100)<2 and time.monotonic()<deadline:
                        time.sleep(.15);wait(self.controller.post_connection());continue
                    raise
            with self.metrics.span('maa_transfer') if self.metrics else nullcontext():
                a=job.get()
            if capture_has_content(a):break
            if self.config.get('capture_method','auto')=='auto' and time.monotonic()-retried>=1:
                retried=time.monotonic()
                repaired=self.recover_capture()
                if repaired is not None:a=repaired;break
            if time.monotonic()>deadline:raise RuntimeError('Blank Win32 capture persisted for 8 seconds')
            self.stop();self.window.check();time.sleep(.4)
        self.last_capture = time.monotonic()
        self.raw_size = (a.shape[1], a.shape[0])
        with self.metrics.span('capture_color_convert') if self.metrics else nullcontext():
            return Image.fromarray(a[:, :, ::-1])

    def recover_capture(self):
        """Reprobe automatic backends after cold-start renderer placeholders."""
        methods=[(16,'PrintWindow'),(2,'FramePool'),(1,'GDI')] if self.config['platform']=='mumu' else [(2,'FramePool'),(16,'PrintWindow'),(1,'GDI')]
        for method,label in methods:
            self.stop();self.window.check()
            candidate=Win32Controller(self.window.hwnd,method,4,4);accepted=False
            candidate.set_screenshot_use_raw_size(True)
            try:
                wait(candidate.post_connection());self.stop()
                frame=wait(candidate.post_screencap()).get();self.stop()
                if not capture_has_content(frame):continue
                old=self.controller;previous=self.capture_method
                self.controller=candidate;self.capture_method=label;accepted=True
                old.__del__();old._own=False
                self.window.events.append({'event':'capture_backend_recovered','previous':previous,'current':label,
                                           'reason':'solid_renderer_placeholder'})
                return frame
            except (RuntimeError,TimeoutError):pass
            finally:
                if not accepted:candidate.__del__();candidate._own=False
        return None

    def enable_input(self):
        if self.config['platform'] == 'mumu':
            if self.sdk:self.sdk.close()
            self.sdk = MuMuInput(self.config)
            self.sdk.renderer_pid=self.window.pid
            self.sdk.check_renderer=self.window.check
            self.sdk.stop=self.stop
            self.sdk.refresh_rotation=self.refresh_input_rotation
            kernel=C.WinDLL('kernel32',use_last_error=True)
            kernel.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];kernel.OpenProcess.restype=W.HANDLE
            kernel.GetProcessTimes.argtypes=[W.HANDLE]+[C.POINTER(W.FILETIME)]*4
            kernel.CloseHandle.argtypes=[W.HANDLE]
            handle=kernel.OpenProcess(0x1000,False,self.window.pid)
            if handle:
                created,ended,user,kern=(W.FILETIME() for _ in range(4))
                try:
                    if kernel.GetProcessTimes(handle,C.byref(created),C.byref(ended),C.byref(kern),C.byref(user)):
                        self.sdk.renderer_started_ns=((created.dwHighDateTime<<32)+created.dwLowDateTime-116444736000000000)*100
                finally:kernel.CloseHandle(handle)
        elif self.config.get('input') != 'postmessage':
            raise RuntimeError('PC background input adapter not configured')

    def refresh_input_rotation(self):
        """Reopen MuMu's public log source and request a fresh canvas transform.

        The renderer can stop publishing readable records after logging is off
        or its file rolls away. No game input, emulator restart or angle guess.
        """
        self.stop();self.window.check()
        result=subprocess.run([self.config['cli'],'log','on'],capture_output=True,
            timeout=5,creationflags=subprocess.CREATE_NO_WINDOW)
        self.stop()
        if result.returncode:raise RuntimeError('MuMu log-on command failed')
        if result.stdout.strip():
            try:reply=json.loads(result.stdout)
            except (ValueError,UnicodeError):raise RuntimeError('MuMu log-on returned unrecognized response')
            if reply.get('errcode',0):raise RuntimeError(f'MuMu log-on error {reply["errcode"]}')
        u=self.window.u;root=u.GetAncestor(self.window.hwnd,2);rect=W.RECT()
        u.GetWindowRect.argtypes=[W.HWND,C.POINTER(W.RECT)]
        u.SetWindowPos.argtypes=[W.HWND,W.HWND,C.c_int,C.c_int,C.c_int,C.c_int,W.UINT]
        if not u.GetWindowRect(root,C.byref(rect)):raise C.WinError(C.get_last_error())
        width,height=rect.right-rect.left,rect.bottom-rect.top
        flags=0x4016  # ASYNCWINDOWPOS | NOACTIVATE | NOZORDER | NOMOVE
        def pause():
            end=time.monotonic()+.25
            while time.monotonic()<end:self.stop();time.sleep(.025)
        try:
            self.stop();self.window.check()
            if not u.SetWindowPos(root,None,0,0,width+2,height+2,flags):raise C.WinError(C.get_last_error())
            pause()
        finally:
            # Restoration is required even when STOP arrives during the refresh.
            u.SetWindowPos(root,None,0,0,width,height,flags)
        pause();restore_deadline=time.monotonic()+2
        while True:
            self.stop();current=W.RECT();u.GetWindowRect(root,C.byref(current))
            if (current.right-current.left,current.bottom-current.top)==(width,height):break
            if time.monotonic()>=restore_deadline:raise RuntimeError('MuMu rotation refresh window restoration incomplete; no input sent')
            time.sleep(.05)
        self.window.check()
        return {'logging':'enabled','renderer_pid':self.window.pid,'window_size_restored':[width,height]}

    def gesture(self, start, end=None, duration=350, trace=None):
        self.stop()
        size = self.window.check()
        if abs(size[0]/size[1]-16/9)>.03:
            raise RuntimeError('Landscape coordinates cannot be sent to a portrait window')
        if time.monotonic() - self.last_capture > 20:
            raise RuntimeError('Reobserve before input')
        if any(not (0 <= x < 1280 and 0 <= y < 720) for x, y in [start] + ([end] if end else [])):
            raise ValueError('Input outside game viewport')
        if self.sdk:
            self.sdk.gesture(start, end, duration, self.stop,trace=trace)
        elif self.config['platform'] == 'pc':
            def scale(p): return (round(p[0]*size[0]/1280), round(p[1]*size[1]/720))
            a = scale(start)
            wait(self.controller.post_swipe(*a, *scale(end), duration) if end else self.controller.post_click(*a))
        else:
            raise RuntimeError('Input is disabled')
        self.last_capture = 0

    def close(self):
        if self.sdk: self.sdk.close()
        if self.controller:
            self.controller.__del__(); self.controller._own = False
            self.controller = None

    def tap_window(self,point):
        """Explicit window-coordinate navigation outside landscape combat."""
        self.stop();size=self.window.check()
        if self.sdk is None or time.monotonic()-self.last_capture>20:
            raise RuntimeError('Fresh capture and explicit SDK input required')
        if not(0<=point[0]<size[0] and 0<=point[1]<size[1]):
            raise ValueError('Point outside window')
        if abs(size[0]/size[1]-self.sdk.size[0]/self.sdk.size[1])>.03:
            raise RuntimeError('Window and SDK display geometry differ')
        self.sdk.gesture(point,None,100,self.stop,canvas=size)
        self.last_capture=0


class MuMuInput:
    """Public MuMu ABI, same down/move/up convention as official MAA Extras."""
    def __init__(self, config):
        self.config = config
        self.dll = C.WinDLL(config['sdk'])
        I = C.c_int
        for name, args, result in [
            ('nemu_connect', [C.c_wchar_p,I], I), ('nemu_disconnect',[I],None),
            ('nemu_get_display_id',[I,C.c_char_p,I],I),
            ('nemu_capture_display',[I,I,I,C.POINTER(I),C.POINTER(I),C.c_void_p],I),
            ('nemu_input_event_finger_touch_down',[I,I,I,I,I],I),
            ('nemu_input_event_finger_touch_up',[I,I,I],I),
        ]:
            fn=getattr(self.dll,name); fn.argtypes=args; fn.restype=result
        self.handle=self.dll.nemu_connect(config['install_root'],config['instance'])
        if self.handle <= 0: raise RuntimeError('MuMu SDK connection failed')
        self.display=self.dll.nemu_get_display_id(self.handle,b'default',0)
        w,h=I(),I()
        # Size-only query: no SDK screenshot pixels, no ADB.
        rc=self.dll.nemu_capture_display(self.handle,self.display,0,C.byref(w),C.byref(h),None)
        if rc or self.display < 0 or min(w.value,h.value)<100:
            self.close(); raise RuntimeError('MuMu display-size query failed')
        self.size=w.value,h.value

    def input_rotation(self):
        """Bounded reread, with current HWND/PID validation before every scan."""
        deadline=time.monotonic()+2
        scans=0;refreshed=False;refresh_error=None
        while True:
            getattr(self,'stop',lambda:None)()
            check=getattr(self,'check_renderer',None)
            if check:check()  # verifies the bound HWND still belongs to this PID
            scans+=1
            try:return self._read_input_rotation()
            except InputRotationUnavailable:
                refresh=getattr(self,'refresh_rotation',None)
                if refresh and not refreshed:
                    refreshed=True
                    try:self.rotation_refresh=refresh()
                    except (InterruptedError,KeyboardInterrupt):raise
                    except Exception as exc:refresh_error=f'{type(exc).__name__}: {exc}'
                    deadline=time.monotonic()+2
                    continue
                if time.monotonic()>=deadline:
                    raise InputRotationUnavailable(
                        f'MuMu input rotation unavailable after {scans} fresh scans; '
                        f'renderer_pid={getattr(self,"renderer_pid",None)}, '
                        f'display={getattr(self,"display",None)}, size={getattr(self,"size",None)}, '
                        f'logs={getattr(self,"_rotation_file_count",0)}, '
                        f'refresh={refresh_error or ("completed" if refreshed else "not configured")}; '
                        'no SDK input sent') from None
                time.sleep(.2)

    def _read_input_rotation(self):
        """Read the active renderer transform; SDK touches use natural axes.

        The size-only SDK query reports the rotated image, not touch axes.
        MuMu publishes the current canvas rotation on every resize/tab change.
        Read backwards to the last record on cold start, then only appended
        bytes. A quiet resize record must survive arbitrarily busy input logs.
        Never invent zero degrees when the authoritative record is unavailable.
        """
        folder = Path(self.config['install_root'])/'vms'/f"MuMuPlayer-12.0-{self.config['instance']}"/'logs'
        cache = getattr(self, '_rotation_logs', None)
        if cache is None:
            self._rotation_logs = cache = {}
        pattern = (rb'RendererPresenter::resizeCanvas[^\r\n]*\bw:\s*(\d+),\s*h:\s*(\d+),'
                   rb'\s*rotation:\s*(0|90|180|270)\b')
        # Log rollover can remove a name between enumeration and stat/open.
        # Keep only readable snapshots; one disappearing file is not fatal.
        names=[]
        for path in folder.glob('shell*.log*'):
            try:
                stamp=path.stat().st_mtime_ns
                if stamp>=getattr(self,'renderer_started_ns',0):names.append((stamp,path))
            except OSError:continue
        names=[path for _,path in sorted(names,key=lambda item:item[0],reverse=True)]
        self._rotation_file_count=len(names)
        owner=(getattr(self,'renderer_pid',None),getattr(self,'display',None),getattr(self,'size',None))
        if getattr(self,'_rotation_owner',None)!=owner:
            cache.clear();self._rotation_owner=owner
        if getattr(self,'renderer_pid',None):
            pattern=rb'[^\r\n]*\b'+str(self.renderer_pid).encode()+rb'/[^\r\n]*'+pattern
        def rotations(data):
            size=getattr(self,'size',None)
            return [int(angle) for width,height,angle in re.findall(pattern,data)
                    if int(height)>0 and (size is None or abs(int(width)/int(height)-size[0]/size[1])<.03)]
        for log_path in names:
            name=log_path.name
            try:
                with (folder/name).open('rb') as f:
                    stat = os.fstat(f.fileno())
                    identity = (stat.st_dev, stat.st_ino, stat.st_ctime_ns)
                    prior = cache.get(name)
                    size = stat.st_size
                    if (prior and prior['identity'] == identity and size >= prior['size']
                            and (size != prior['size'] or stat.st_mtime_ns == prior['mtime'])):
                        rotation = prior['rotation']
                        if size > prior['size']:
                            # Overlap covers an append split through a resize record.
                            f.seek(max(0, prior['size']-512))
                            matches = rotations(f.read(size-f.tell()))
                            if matches:rotation = int(matches[-1])
                    else:
                        rotation = None
                        end = size
                        while end:
                            start = max(0, end-65536)
                            f.seek(start)
                            matches = rotations(f.read(min(size-start,end-start+512)))
                            if matches:
                                rotation = int(matches[-1]);break
                            end = start
                    cache[name] = {'identity':identity, 'size':size,
                                   'mtime':stat.st_mtime_ns, 'rotation':rotation}
            except OSError:
                continue
            if rotation is not None:
                self.rotation_source={'file':name,'pid':getattr(self,'renderer_pid',None),
                    'display':getattr(self,'display',None),'size':getattr(self,'size',None),'rotation':rotation}
                return rotation
        raise InputRotationUnavailable('MuMu current-renderer rotation record absent; no SDK input sent')

    @staticmethod
    def native_point(point, size, rotation):
        x,y=point;w,h=size
        if rotation == 90:return (h-1-y,x)
        if rotation == 180:return (w-1-x,h-1-y)
        if rotation == 270:return (y,w-1-x)
        return (x,y)

    def gesture(self,start,end,duration,stop,canvas=(1280,720),trace=None):
        # 'default' follows the visible tab. Do not retain an old display after
        # Android recovery closes/reopens the game or switches to portrait UU.
        display=self.dll.nemu_get_display_id(self.handle,b'default',0)
        if display < 0:raise RuntimeError('MuMu active display query failed')
        self.display=display
        w,h=C.c_int(),C.c_int()
        if self.dll.nemu_capture_display(self.handle,self.display,0,C.byref(w),C.byref(h),None):
            raise RuntimeError('MuMu display-size query failed before input')
        self.size=w.value,h.value
        if min(self.size)<100 or abs(self.size[0]/self.size[1]-canvas[0]/canvas[1])>.03:
            raise RuntimeError('SDK display changed orientation before input')
        rotation=self.input_rotation()
        def point(p):
            scaled=(min(self.size[0]-1,round(p[0]*self.size[0]/canvas[0])),
                    min(self.size[1]-1,round(p[1]*self.size[1]/canvas[1])))
            return self.native_point(scaled,self.size,rotation)
        a=point(start); b=point(end) if end else a
        try:
            n=max(1,round(duration/20)) if end else 1
            for i in range(n+1):
                stop()
                p=tuple(round(x+(y-x)*i/n) for x,y in zip(a,b))
                if self.dll.nemu_input_event_finger_touch_down(self.handle,self.display,1,*p):
                    raise RuntimeError('MuMu touch failed; no input replay')
                if trace and i==0:
                    # Let the game's touch-down select the source before motion;
                    # retain evidence of what the game accepted, without replay.
                    time.sleep(.12);trace('pressed')
                if trace and i==n:
                    time.sleep(.06);trace('target')
                time.sleep(duration/n/1000 if end else .04)
        finally:
            if self.dll.nemu_input_event_finger_touch_up(self.handle,self.display,1):
                raise RuntimeError('MuMu release failed; outcome unknown')

    def close(self):
        if self.handle:
            self.dll.nemu_disconnect(self.handle); self.handle=0
