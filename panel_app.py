# Public archive: local paths removed or made configurable.
"""Local, dependency-free management UI. No controller is created on startup."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from swb.user_rules import validate,load,FIELDS,OPS,ACTIONS
from swb.ranked_batch import read,write
from swb.worker_diagnostics import record_failure

ROOT=Path(__file__).resolve().parent
LIB=Path(os.environ.get('SWB_CARD_LIBRARY_ROOT', str(ROOT/'external/card-library')))
IMAGES=Path(os.environ.get('SWB_CARD_IMAGES_ROOT', str(ROOT/'external/card-images')))
CLASSES={0:'中立',1:'精灵',2:'皇家',3:'巫师',4:'龙族',5:'梦魇',6:'主教',7:'复仇者'}

def catalog():
    # SQLite URI mode=ro is enforced even if the process has broader write access.
    with sqlite3.connect((LIB/'cards.sqlite').as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        rows=[dict(r) for r in con.execute('select card_id,name,cost,class_id,class_key,type,common_text,evo_text,is_token,series_name,source_url from cards')]
        aliases={}
        for cid,name in con.execute('select card_id,name from aliases'):aliases.setdefault(cid,[]).append(name)
    for row in rows:
        row['class_name']=CLASSES.get(row['class_id'],str(row['class_id']))
        row['aliases']=aliases.get(row['card_id'],[])
        row['common_text']=re.sub('<[^>]+>','',row.get('common_text') or '')
        row['evo_text']=re.sub('<[^>]+>','',row.get('evo_text') or '')
        row['origin']='原始只读卡库'
    ids={r['card_id'] for r in rows}
    # The verified live deck contains Oracle, absent from the 517-card collection.
    # Supplement the view from the existing verified deck; never insert into its DB.
    for c in read(ROOT/'assets/deck.json')['cards']:
        if c['card_id'] not in ids:
            rows.append({'card_id':c['card_id'],'name':c['name'],'cost':c['cost'],'class_id':4,'class_key':'dragon',
                'class_name':'龙族','type':{1:'follower',2:'amulet',4:'spell'}[c['type']],
                'common_text':c['text'],'evo_text':'','is_token':False,'series_name':'既有实测牌组补充',
                'aliases':[],'source_url':'https://shadowverse-wb.com/chs/deck/cardslist/card/?card_id='+c['card_id'],
                'origin':'assets/deck.json 已校验牌组；图库缺卡时显示本地识别图'})
    # Official verified Dragon/Nightmare overlays; original SQLite remains read-only.
    for overlay_path, label in ((ROOT/'assets/nightmare/cards.json','官方已核验快鬼'),(ROOT/'assets/cards.json','官方已核验新龙族')):
        if not overlay_path.exists():continue
        overlay=read(overlay_path)
        by_id={r['card_id']:r for r in rows}
        for cid,c in overlay.items():
            common=c.get('common') or {}; cls=common.get('class',c.get('class_id',4))
            row={'card_id':cid,'name':c['name'],'cost':c['cost'],'class_id':cls,
                 'class_key':'neutral' if cls==0 else ('dragon' if cls==4 else 'nightmare'),'class_name':CLASSES.get(cls,str(cls)),
                 'type':c['type'],'common_text':re.sub('<[^>]+>','',common.get('skill_text',c.get('skill_text',''))),
                 'evo_text':re.sub('<[^>]+>','',(c.get('evo') or {}).get('skill_text','')),
                 'is_token':c.get('is_token',False),'series_name':label,
                 'aliases':[a['name'] for a in c.get('aliases',[])],'source_url':'https://shadowverse-wb.com/chs/deck/cardslist/card/?card_id='+cid,
                 'origin':'独立官方卡组映射；原始库只读'}
            if cid in by_id:rows[rows.index(by_id[cid])]=row
            else:rows.append(row)
    return rows

def live_pid(pid):
    if not isinstance(pid,int) or pid<=0:return False
    import ctypes
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_int,ctypes.c_ulong];k.OpenProcess.restype=ctypes.c_void_p
    k.CloseHandle.argtypes=[ctypes.c_void_p]
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return ctypes.get_last_error()==5
    code=ctypes.c_ulong();k.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
    ok=k.GetExitCodeProcess(h,ctypes.byref(code));k.CloseHandle(h)
    return not ok or code.value==259

class Panel:
    def __init__(self,root=ROOT,offline=False,runner_python=None):
        self.root=Path(root);self.data=self.root/'user_data';self.data.mkdir(exist_ok=True)
        self.offline=offline;self.mutex=threading.RLock();self.proc=None;self.runner_python=runner_python or sys.executable
        self.cards=catalog();self.by_id={r['card_id']:r for r in self.cards}
        self.image_index=read(IMAGES/'card_image_index.json')['cards']
        if not (self.data/'rules.json').exists():write(self.data/'rules.json',{'schema_version':1,'cards':{}})
        if not (self.data/'decks.json').exists():
            d=read(self.root/'assets/deck.json')
            write(self.data/'decks.json',[{'id':'dragon-tempo-default','name':'疾驰弃牌龙（ドラゴン）','class_id':4,'profile':'dragon_tempo','cards':{x['card_id']:x['count'] for x in d['cards']},'source':d.get('source','assets/deck.json')}])

    def active_strategy(self):
        from swb.deck_profile import active
        profile=active(self.root)
        return {'profile':profile,'name':'疾驰弃牌龙（ドラゴン） · 新版本指定系列'}

    def select_deck_profile(self,profile):
        if profile not in ('dragon_tempo','nightmare_aggro','needle_dragon'):raise ValueError('请选择已有适配策略')
        if self.status()['running']:raise ValueError('任务运行中，停止后再切换卡组')
        write(self.root/'assets/active_deck.json',{'profile':'dragon_tempo','available':['dragon_tempo']})
        return self.active_strategy()

    def locks(self):
        result=[]
        for name in ('RUNNING.lock','BATCH.lock'):
            p=self.root/name
            if p.exists():
                try:pid=int(p.read_text().strip())
                except ValueError:pid=None
                result.append({'file':name,'pid':pid,'alive':live_pid(pid) if pid else True})
        return result
    def status(self):
        recent=[]
        for p in sorted((self.root/'batches').glob('*/status.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
            s=read(p)
            if 'authorized_matches' not in s:continue # Old CPU batches are a different protocol.
            recent.append({k:s.get(k) for k in ('batch','mode','status','completed','wins','losses','active_match','next_match','authorized_matches','reason','technical_interruptions','current_run','reconnect','stop_kind','rest','challenge','reward_claimed','players','current_round','next_open','reward_received','quest_scope','park_match_plan','route_validation','ranked_park','park_phase','park_ranked_remaining','park_next_check','park')})
        locks=self.locks();request=read(self.data/'request.json') if (self.data/'request.json').exists() else None
        error=read(self.data/'worker_error.json') if (self.data/'worker_error.json').exists() else None
        validation=read(self.data/'cpu_validation_result.json') if (self.data/'cpu_validation_result.json').exists() else None
        if error and validation and validation.get('completed') and error.get('batch')==validation.get('batch') and validation.get('finished_utc','')>error.get('time',''):
            error=None  # Preserve original error/history; this later CPU receipt resolves its pointer in UI.
        running=bool(self.proc and self.proc.poll() is None) or any(l['alive'] for l in locks)
        reconnect_path=self.data/'reconnect_status.json'
        reconnect=read(reconnect_path) if reconnect_path.exists() else None
        active=next((b for b in recent if b.get('batch')==(request or {}).get('batch')), {})
        heartbeat={}
        if active.get('current_run'):
            path=Path(active['current_run']).resolve()/'heartbeat.json'
            if path.is_relative_to((self.root/'runs').resolve()) and path.exists():heartbeat=read(path)
        from swb.ranked_activity import ranked_activity
        activity=ranked_activity(running,active,reconnect or {},heartbeat,time.time(),
            paused=(self.root/'PANEL_PAUSE').exists(),finish=(self.root/'PANEL_FINISH').exists(),
            error=error if (error or {}).get('batch')==active.get('batch') else None)
        sched_path=self.data/'scheduled_rest.json'
        sched_data=read(sched_path) if sched_path.exists() else {}
        is_resting=bool(sched_data.get('rest'))
        rest_remaining=max(0,int((sched_data.get('rest') or {}).get('until',0)-time.time())) if is_resting else 0
        return {'cpu_validation':validation,'running':running,'locks':locks,'reconnect':reconnect,'stop':(self.root/'STOP').exists(),'paused':(self.root/'PANEL_PAUSE').exists(),
            'finish_after_match':(self.root/'PANEL_FINISH').exists(),'resting':is_resting,'rest_remaining':rest_remaining,'active_seconds':sched_data.get('active_seconds',0.),
            'batches':recent,'request':request,'error':error,'offline':self.offline,'activity':activity,'heartbeat':heartbeat}
    def bootstrap(self):
        return {'cards':self.cards,'rules':load(self.data/'rules.json'),'decks':read(self.data/'decks.json'),
            'config':self.config(),'active_strategy':self.active_strategy(),'status':self.status(),'interface':read(self.root/'panel/interface.json'),
            'fields':FIELDS,'ops':OPS,'actions':ACTIONS,'library':str(LIB/'cards.sqlite')}
    def config(self):
        return read(self.data/'window.json') if (self.data/'window.json').exists() else read(self.root/'config.json')
    def save_rules(self,rules):
        rules=validate(rules)
        if set(rules['cards'])-set(self.by_id):raise ValueError('规则包含本地库未收录的卡牌 ID')
        write(self.data/'rules.json',rules);return {'saved':True,'effective':'下一局开始；当前局使用已冻结规则'}
    def save_decks(self,decks):
        if not isinstance(decks,list):raise ValueError('牌组必须为列表')
        seen=set()
        for d in decks:
            if not isinstance(d,dict) or not isinstance(d.get('id'),str) or d['id'] in seen:raise ValueError('牌组 ID 必须唯一')
            seen.add(d['id'])
            if not isinstance(d.get('name'),str) or not d['name'].strip() or d.get('class_id') not in range(1,8):raise ValueError('牌组名称/职业无效')
            if not isinstance(d.get('cards'),dict):raise ValueError('cards 必须为对象')
            for cid,n in d['cards'].items():
                if cid not in self.by_id or type(n) is not int or n<1 or n>3:raise ValueError('每张卡数量必须为 1–3')
                c=self.by_id[cid]
                if c['is_token'] or c['class_id'] not in (0,d['class_id']):raise ValueError('牌组中含衍生卡或其他职业卡')
        write(self.data/'decks.json',decks);return {'saved':True}
    def window(self,data):
        if self.status()['running']:raise ValueError('运行中不能更换窗口；停止后保存')
        cfg=read(self.root/'config.json')
        if data.get('platform')=='mumu':
            i=data.get('instance',0)
            if type(i) is not int or i<0:raise ValueError('MuMu 实例编号无效')
            cfg.update(platform='mumu',instance=i,input='mumu-sdk')
        elif data.get('platform')=='pc':
            hwnd=data.get('hwnd')
            if type(hwnd) is not int or hwnd<=0:raise ValueError('请选择有效窗口')
            from maa.toolkit import Toolkit
            if hwnd not in [w.hwnd for w in Toolkit.find_desktop_windows()]:raise ValueError('窗口已关闭，请刷新')
            cfg.update(platform='pc',hwnd=hwnd,input='postmessage')
        else:raise ValueError('仅支持既有 MuMu / Win32 链路')
        method=data.get('capture_method','auto')
        if method not in ('auto','PrintWindow','FramePool','GDI'):raise ValueError('未知 MAA 截图方式')
        cfg['capture_method']=method
        write(self.data/'window.json',cfg);return {'saved':True}
    def start(self,data):
        with self.mutex:
            if self.offline:raise ValueError('离线验收面板禁止启动游戏')
            if self.status()['running']:raise ValueError('已有 runner 运行，禁止重复启动')
            # Dead locks may be cleared only after the OS confirms that their owners exited.
            for lock in self.locks():
                if lock['alive']:raise ValueError('控制器仍在运行')
                (self.root/lock['file']).unlink()
            resume=data.get('resume',False)
            if type(resume) is not bool:raise ValueError('resume 无效')
            mode=data.get('mode','ranked')
            if mode not in ('ranked','grand_prix','weekend_plaza','paradise'):raise ValueError('未知任务模式')
            preview=data.get('preview',False)
            if type(preview) is not bool:raise ValueError('preview 无效')
            total=data.get('matches','1' if mode=='paradise' else '5' if mode=='grand_prix' else '4' if mode=='weekend_plaza' else None)
            if mode=='paradise':
                if resume or total!='1' or data.get('paid_entry',False):raise ValueError('乐园每次扫描一轮课题，不恢复未知对局或付费')
                for key in ('park_daily','park_weekly','park_guild_hello'):
                    if type(data.get(key,True)) is not bool:raise ValueError('乐园选项无效')
                if not(data.get('park_daily',True) or data.get('park_weekly',True)):raise ValueError('请至少启用每日或每周课题')
                if type(data.get('park_max_battles',8)) is not int or not 1<=data.get('park_max_battles',8)<=8:raise ValueError('乐园单轮对局上限须为1～8')
            if mode=='grand_prix' and (total!='5' or data.get('paid_entry',False)):
                raise ValueError('大赛每次完成当前一组，最多5战；不支持自动付费入场')
            if mode=='weekend_plaza' and (total!='4' or data.get('players',16)!=16 or data.get('wait_open',False) or data.get('paid_entry',False)):
                raise ValueError('周末广场赛仅支持16人、单场最多4轮；未开放即停止，不自动支付')
            plaza_wait=data.get('plaza_wait_minutes',30)
            if mode=='weekend_plaza' and (type(plaza_wait) is not int or not 1<=plaza_wait<=60):raise ValueError('赛事内等候上限须为1～60分钟')
            if total!='infinite' and (not isinstance(total,str) or not re.fullmatch('[1-9][0-9]*',total)):
                raise ValueError('局数须为正整数或 infinite')
            matches=None if total=='infinite' else int(total)
            batch=data.get('batch') if resume else {'grand_prix':'gp_','weekend_plaza':'wp_','paradise':'park_'}.get(mode,'panel_')+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
            if not isinstance(batch,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_-]{0,79}',batch):raise ValueError('请选择恢复批次')
            detail=data.get('detail_logs',True)
            if type(detail) is not bool:raise ValueError('日志开关无效')
            marker=self.root/'STOP'
            stop_token=[marker.stat().st_mtime_ns,hashlib.sha256(marker.read_bytes()).hexdigest()] if marker.exists() else None
            profile='dragon_tempo'
            if mode=='ranked':self.select_deck_profile(profile)
            config={**self.config(),'deck_profile':profile}
            if mode in ('grand_prix','weekend_plaza','paradise') or (mode=='ranked' and config.get('platform')=='mumu'):
                serial=data.get('adb_serial',config.get('adb_serial','127.0.0.1:16384'))
                if not isinstance(serial,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}',serial):raise ValueError('ADB 地址无效')
                if config.get('platform')!='mumu':raise ValueError('大赛 ADB 模式需要配置 MuMu')
                config.update(input='adb',adb_serial=serial)
            request={'mode':mode,'deck_profile':profile,'batch':batch,'matches':matches,'resume':resume,'detail_logs':detail,'panel_config':config,'panel_stop_token':stop_token}
            if mode=='ranked':
                park=data.get('ranked_park',True)
                if type(park) is not bool:raise ValueError('乐园优先开关无效')
                latest_park=self.data/'paradise_latest.json'
                if park and not resume and latest_park.exists():
                    try:
                        p_info=load(latest_park)
                        p_time=datetime.fromisoformat(p_info['time']).timestamp()
                        p_ref=p_info.get('refresh_after_seconds') or 0
                        if (not (p_info.get('plan') or {}).get('pending')
                                and not p_info.get('claimable') and not p_info.get('daily_claimable')
                                and p_ref>300 and datetime.now(timezone.utc).timestamp()<p_time+p_ref-120):
                            park=False
                    except Exception:
                        pass
                if park and (profile not in ('nightmare_aggro','dragon_tempo') or config.get('input')!='adb'):
                    raise ValueError('乐园优先目前适配ADB排位')
                request.update(ranked_park=park,park_daily=True,park_weekly=True,park_guild_hello=True)
            if mode=='grand_prix':request['gp_preview']=preview
            if mode=='weekend_plaza':request.update(players=16,plaza_preview=preview,plaza_wait_minutes=plaza_wait,wait_open=False)
            if mode=='paradise':request.update(park_preview=preview,park_max_battles=data.get('park_max_battles',8),**{k:data.get(k,True) for k in ('park_daily','park_weekly','park_guild_hello')})
            # Read-only reconciliation before spawning; doesn't clear STOP or send game input.
            if resume:
                prior=self.root/'batches'/batch/'status.json'
                if not prior.exists() or 'authorized_matches' not in read(prior):raise ValueError('该目录不是可恢复的排位批次')
                if mode=='weekend_plaza':from swb.weekend_plaza_batch import prepare
                elif mode=='grand_prix':from swb.grand_prix_batch import prepare
                else:from swb.ranked_batch import prepare
                from run import version
                from types import SimpleNamespace
                m=version();prepare(self.root,SimpleNamespace(**request,expect_version=m['sha256']),m)
            load(self.data/'rules.json')
            for name in ('PANEL_PAUSE','PANEL_FINISH'):(self.root/name).unlink(missing_ok=True)
            (self.data/'worker_error.json').unlink(missing_ok=True)
            write(self.data/'request.json',request)
            write(self.data/'worker_launch.json',{'batch':batch,
                'requested_utc':datetime.now(timezone.utc).isoformat(),
                'console':str(self.root/'logs/worker-launch.log'),
                'bootstrap_console':str(self.root/'logs/worker-bootstrap.log')})
            try:
                (self.root/'logs').mkdir(exist_ok=True)
                # Capture interpreter / top-level import failures. The worker
                # installs its own bounded console before importing the runner.
                with (self.root/'logs/worker-bootstrap.log').open('wb') as console:
                    self.proc=subprocess.Popen([self.runner_python,'-B','-X','utf8',str(self.root/'panel_worker.py'),str(self.data/'request.json')],cwd=self.root,
                        stdout=console,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as exc:
                record_failure(self.root,batch,exc,'panel-launch')
                raise
            return {'started':True,'pid':self.proc.pid,'batch':batch,'matches':matches,'detail_logs':detail}
    def control(self,action):
        with self.mutex:
            if action=='stop':
                (self.root/'STOP').write_text('User requested immediate STOP from panel.\n',encoding='utf-8')
                (self.root/'PANEL_PAUSE').unlink(missing_ok=True)
            elif action=='finish':
                if not self.status()['running']:raise ValueError('没有正在运行的任务')
                (self.root/'PANEL_FINISH').write_text('Finish current match, then stop before next queue.\n',encoding='utf-8')
                (self.root/'PANEL_PAUSE').unlink(missing_ok=True)
            elif action=='pause':
                if not self.status()['running']:raise ValueError('没有正在运行的任务')
                (self.root/'PANEL_PAUSE').write_text('Suspend input until user resumes.\n')
            elif action=='continue':(self.root/'PANEL_PAUSE').unlink(missing_ok=True)
            elif action=='skip_rest':
                (self.root/'SKIP_REST').write_text('User requested skip scheduled rest from panel.\n',encoding='utf-8')
                sched_path=self.data/'scheduled_rest.json'
                if sched_path.exists():
                    try:
                        d=read(sched_path)
                        d['active_seconds']=0.
                        if d.get('rest'):
                            d['last_rest']={**d['rest'],'completed_at':time.time(),'skipped':True}
                            d['rest']=None
                        d['updated_at']=time.time()
                        write(sched_path,d)
                    except Exception:pass
            else:raise ValueError('未知控制动作')
            return self.status()
    def image_path(self,cid,form):
        item=self.image_index.get(cid,{}).get('forms',{}).get(form,{})
        p=(IMAGES/item.get('file','')).resolve()
        if not p.is_relative_to(IMAGES.resolve()) or not p.is_file():
            for candidate in (
                self.root/'assets/source_cards'/f'{cid}_{form}.png',
                self.root/'assets/board_art'/f'{cid}_{form}.png',
                self.root/'assets/art'/f'{cid}.png',
                self.root/'assets/nightmare/board_art'/f'{cid}_{form}.png',
                self.root/'assets/nightmare/art'/f'{cid}.png',
            ):
                if candidate.is_file():
                    p=candidate;break
            if not cid.isdigit() or not p.is_file():raise FileNotFoundError('该形态没有本地图片')
        return p

def serve(port=18765,offline=False,open_browser=True):
    panel=Panel(offline=offline);token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,value,status=200,mime='application/json; charset=utf-8'):
            raw=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(raw)
        def allowed(self):return self.headers.get('Host') in (f'127.0.0.1:{port}',f'localhost:{port}')
        def do_GET(self):
            if not self.allowed():return self.send({'error':'Invalid host'},403)
            route=urlparse(self.path);q=parse_qs(route.query)
            try:
                if route.path=='/':return self.send((ROOT/'panel/index.html').read_text(encoding='utf-8').replace('__TOKEN__',token).encode('utf-8'),mime='text/html; charset=utf-8')
                if route.path=='/api/bootstrap':return self.send(panel.bootstrap())
                if route.path=='/api/status':return self.send(panel.status())
                if route.path=='/api/rules':return self.send(load(panel.data/'rules.json'))
                if route.path=='/api/source':return self.send({'source':(ROOT/'swb/policy.py').read_text(encoding='utf-8')})
                if route.path=='/api/windows':
                    from maa.toolkit import Toolkit
                    return self.send([{'hwnd':w.hwnd,'title':w.window_name,'class_name':w.class_name} for w in Toolkit.find_desktop_windows() if w.window_name])
                if route.path=='/image':return self.send(panel.image_path(q.get('id',[''])[0],q.get('form',['common'])[0]).read_bytes(),mime='image/png')
                return self.send({'error':'Not found'},404)
            except Exception as exc:self.send({'error':str(exc)},400)
        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Panel-Token')!=token:return self.send({'error':'Local panel token required'},403)
            try:
                n=int(self.headers.get('Content-Length','0'))
                if n>2*1024*1024:raise ValueError('文件超过 2 MiB')
                data=json.loads(self.rfile.read(n));path=urlparse(self.path).path
                with panel.mutex:
                    if path=='/api/rules':result=panel.save_rules(data)
                    elif path=='/api/decks':result=panel.save_decks(data)
                    elif path=='/api/window':result=panel.window(data)
                    elif path=='/api/start':result=panel.start(data)
                    elif path=='/api/control':result=panel.control(data['action'])
                    else:return self.send({'error':'Not found'},404)
                self.send(result)
            except Exception as exc:self.send({'error':str(exc)},400)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    url=f'http://127.0.0.1:{port}/';print(url,flush=True)
    if open_browser:webbrowser.open(url)
    server.serve_forever()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=18765);p.add_argument('--offline',action='store_true');p.add_argument('--no-browser',action='store_true');a=p.parse_args()
    serve(a.port,a.offline,not a.no_browser)
