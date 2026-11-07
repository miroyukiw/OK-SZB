# Public archive: local paths removed or made configurable.
"""Load the official template with an isolated config folder; no game starts here."""
import os,sys,runpy,ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parent
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('OK-SZB.Desktop')
sys.path.insert(0,str(ROOT/'ok_panel/vendor'))
sys.path.insert(0,str(ROOT/'ok_panel'))
sys.path.insert(0,str(ROOT))
# MAA's Python bindings are pure ctypes; existing runner dependencies stay in its venv.
sys.path.append(str(ROOT/'.venv/Lib/site-packages'))
offline='--offline' in sys.argv
os.environ['SWB_PANEL_OFFLINE']='1' if offline else '0'
data=Path(os.environ.get('SWB_PANEL_DATA_ROOT',str(ROOT)))
state=data/'user_data/ok_panel';state.mkdir(parents=True,exist_ok=True)
os.chdir(state)
cfg=state/'configs';cfg.mkdir(exist_ok=True)
if not (cfg/'ui_config.json').exists():
    (cfg/'ui_config.json').write_text('{"QFluentWidgets":{"ThemeMode":"Dark","ThemeColor":"#ff009faa"},"MainWindow":{"Language":"zh_CN","MicaEnabled":true},"Update":{"CheckUpdateAtStartUp":false}}',encoding='utf-8')
k=ctypes.WinDLL('kernel32',use_last_error=True);k.CreateMutexW.restype=ctypes.c_void_p;k.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
handle=k.CreateMutexW(None,False,'Local\\ShadowverseWBDesktopPanel'+('Offline' if offline else ''))
if ctypes.get_last_error()==183:sys.exit(0)
sys.argv=[str(ROOT/'ok_panel/main.py')]
runpy.run_path(str(ROOT/'ok_panel/main.py'),run_name='__main__')
