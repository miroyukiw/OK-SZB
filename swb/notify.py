"""Durable local notifications; no GPT, credentials, API calls or outbound network."""
import ctypes as C
from ctypes import wintypes as W
import json,os,time,uuid
from pathlib import Path
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1]

def atomic_json(path,value):
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
    temp.replace(path)
def emit(kind,message,**details):
    row={'id':uuid.uuid4().hex,'time':datetime.now(timezone.utc).isoformat(),
         'kind':kind,'message':message,'details':details,'automatic_astra_callback':False}
    out=ROOT/'notifications';out.mkdir(exist_ok=True)
    atomic_json(out/(row['id']+'.json'),row)
    atomic_json(out/'latest.json',row)
    with (out/'events.jsonl').open('a',encoding='utf-8') as f:
        f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
    return row

def balloon(message):
    # Shell accepts the balloon; Focus Assist can suppress its visible presentation.
    class NID(C.Structure):
        _fields_=[('cbSize',W.DWORD),('hWnd',W.HWND),('uID',W.UINT),('uFlags',W.UINT),
                  ('uCallbackMessage',W.UINT),('hIcon',W.HICON),('szTip',W.WCHAR*128),
                  ('dwState',W.DWORD),('dwStateMask',W.DWORD),('szInfo',W.WCHAR*256),
                  ('uTimeoutOrVersion',W.UINT),('szInfoTitle',W.WCHAR*64),('dwInfoFlags',W.DWORD),
                  ('guidItem',C.c_byte*16),('hBalloonIcon',W.HICON)]
    u=C.WinDLL('user32');shell=C.WinDLL('shell32')
    u.CreateWindowExW.argtypes=[W.DWORD,W.LPCWSTR,W.LPCWSTR,W.DWORD,C.c_int,C.c_int,C.c_int,C.c_int,W.HWND,W.HMENU,W.HINSTANCE,C.c_void_p]
    u.CreateWindowExW.restype=W.HWND
    u.LoadIconW.argtypes=[W.HINSTANCE,C.c_void_p];u.LoadIconW.restype=W.HICON
    u.DestroyWindow.argtypes=[W.HWND];shell.Shell_NotifyIconW.argtypes=[W.DWORD,C.POINTER(NID)]
    hwnd=u.CreateWindowExW(0,'STATIC','SWB batch notice',0,0,0,0,0,None,None,None,None)
    n=NID();n.cbSize=C.sizeof(n);n.hWnd=hwnd;n.uID=1;n.uFlags=2|4|16
    n.hIcon=u.LoadIconW(None,C.c_void_p(32516));n.szTip='SWB CPU runner'
    n.szInfo=message[:255];n.szInfoTitle='SWB CPU runner';n.dwInfoFlags=1;n.uTimeoutOrVersion=10000
    accepted=bool(shell.Shell_NotifyIconW(0,C.byref(n)))
    receipt={'pid':os.getpid(),'shell_accepted':accepted,'visible_delivery_verified':False,'time':time.time()}
    (ROOT/'notifications'/'balloon_receipt.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8')
    if accepted:time.sleep(8);shell.Shell_NotifyIconW(2,C.byref(n))
    u.DestroyWindow(hwnd)
    return receipt
