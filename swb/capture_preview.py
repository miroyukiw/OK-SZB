"""Read-only capture interface. MuMu is exposed; PC remains an internal extension."""
import json,subprocess
from pathlib import Path
from typing import Protocol

METHODS=(('auto','自动 · 现有 MAA 顺序'),('PrintWindow','PrintWindow · MuMu 首选'),
         ('FramePool','WGC / FramePool · 待验证'),('GDI','GDI · 待验证'))

class CaptureSource(Protocol):
    def get_frame(self): ...
    def close(self): ...

def list_mumu(config):
    p=subprocess.run([config['cli'],'info','--vmindex','all'],capture_output=True,timeout=12,
                     creationflags=subprocess.CREATE_NO_WINDOW)
    if p.returncode:raise RuntimeError('MuMu 实例查询失败')
    raw=p.stdout.decode('utf-8-sig',errors='replace').strip()
    if not raw:raise RuntimeError('MuMu 未返回实例信息，请检查运行权限')
    data=json.loads(raw)
    rows=([data] if 'index' in data else list(data.values())) if isinstance(data,dict) else data
    result=[]
    for row in rows:
        if not isinstance(row,dict) or 'index' not in row:continue
        connected=bool(row.get('is_process_started') and row.get('is_android_started') and row.get('render_wnd'))
        if not connected and row.get('is_android_started'):
            # Match the transport's observed-window fallback; enumeration only,
            # no window restoration and no input from this UI inventory.
            from maa.toolkit import Toolkit
            roots=[w for w in Toolkit.find_desktop_windows() if w.window_name==row.get('name') and w.class_name.startswith('Qt')]
            same=[x for x in rows if isinstance(x,dict) and x.get('name')==row.get('name') and x.get('is_android_started')]
            connected=len(roots)==1 and len(same)==1
        result.append({'instance':int(row['index']),'name':row.get('name') or 'MuMu','connected':connected})
    return result

class MaaCaptureSource:
    """Uses the runner's existing transport without enabling any input adapter."""
    def __init__(self,config,log_dir):
        from swb.transport import Transport
        self.transport=Transport(dict(config,_panel_managed=True),Path(log_dir))
    def get_frame(self):
        import numpy as np
        return np.asarray(self.transport.capture_raw())[:,:,::-1].copy()
    @property
    def method(self):return self.transport.capture_method
    def close(self):self.transport.close()

def make_capture_source(config,log_dir):
    if config.get('platform') not in ('mumu','pc'):raise ValueError('Unsupported capture source')
    return MaaCaptureSource(config,log_dir)
