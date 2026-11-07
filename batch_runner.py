"""Independent local experiment: A x 7, one offline optimization, B x 7, stop.
No model/API client, no outbound callbacks, no automatic retries or third batch.
"""
import argparse,json,os,subprocess,sys,time,traceback
from pathlib import Path
from collections import Counter
from datetime import datetime,timezone
from run import version,dump
from swb.notify import emit
from swb.failures import classify
ROOT=Path(__file__).resolve().parent

def discover_run(before,runs=None):
    runs=runs or ROOT/'runs'
    fresh=[p for p in runs.iterdir() if p not in before and p.is_dir() and (p/'version.json').exists()]
    return fresh[0] if len(fresh)==1 else None

def terminate_worker(child):
    # Windows venv python.exe is a redirector; terminate its owned descendant tree too.
    if child.poll() is None:
        result=subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True,
                              timeout=15,creationflags=subprocess.CREATE_NO_WINDOW)
        child.wait(timeout=10)
        if result.returncode and child.poll() is None:raise RuntimeError('Could not stop owned worker tree')

def notify(kind,message,**details):
    row=emit(kind,message,**details)
    try:
        child=subprocess.Popen([sys.executable,'-X','utf8',str(ROOT/'notify_local.py'),message],
                         cwd=ROOT,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        dump(ROOT/'notifications'/('dispatch_'+row['id']+'.json'),{'event_id':row['id'],'pid':child.pid,'launched':True,'visible_delivery_verified':False})
    except Exception as exc:
        dump(ROOT/'notifications'/('dispatch_'+row['id']+'.json'),{'event_id':row['id'],'launched':False,'error':str(exc)})
    return row

def alive(pid):
    import ctypes as c
    k=c.WinDLL('kernel32');k.OpenProcess.restype=c.c_void_p;k.CloseHandle.argtypes=[c.c_void_p]
    h=k.OpenProcess(0x1000,False,int(pid))
    if not h:return False
    code=c.c_ulong();k.GetExitCodeProcess.argtypes=[c.c_void_p,c.POINTER(c.c_ulong)]
    ok=k.GetExitCodeProcess(h,c.byref(code));k.CloseHandle(h)
    return bool(ok and code.value==259)

def main(label,resume=False,repair_note=None):
    os.chdir(ROOT)
    if (ROOT/'STOP').exists():raise RuntimeError('STOP exists; explicitly clear it before a new batch')
    if (ROOT/'RUNNING.lock').exists():raise RuntimeError('A game run lock already exists; never start a second controller')
    lock=ROOT/'BATCH.lock'
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
    out=ROOT/'batches'/label;child=None;state=None
    try:
        manifest=version()
        if out.exists() and not resume:raise RuntimeError(f'Batch {label} already exists. No automatic repeat or extra games.')
        if resume:
            state=json.loads((out/'status.json').read_text(encoding='utf-8'))
            if state['status']!='interrupted' or len(state['games'])>=7:raise RuntimeError('Only unfinished interrupted slots can continue')
            note=Path(repair_note or '').resolve()
            if not note.is_relative_to(ROOT/'repairs') or not note.is_file():raise RuntimeError('Expected a documented local technical repair')
            old=json.loads((out/'version.json').read_text(encoding='utf-8'))
            for relative in old['files']:
                if relative.replace('\\','/') in ('swb/policy.py','assets/strategy.json') and old['files'][relative]!=manifest['files'][relative]:
                    raise RuntimeError('A technical continuation cannot change the strategy before concentrated optimization')
            if any(r['note']==str(note) for r in state.get('repairs',[])):raise RuntimeError('This repair note was already used; no repeated automatic repair attempt')
            state.setdefault('repairs',[]).append({'note':str(note),'from_version':state['version'],'to_version':manifest['sha256'],
                               'next_slot':len(state['games'])+1,'time':datetime.now(timezone.utc).isoformat()})
            state.update(status='running',pid=os.getpid(),version=manifest['sha256'],current_run=None)
            state.pop('reason',None);state.pop('traceback',None)
            dump(out/'repair_version.json',manifest);dump(out/'status.json',state)
        elif label=='B':
            previous=json.loads((ROOT/'batches/A/status.json').read_text(encoding='utf-8'))
            if previous['status'] not in ('completed','completed_with_technical') or len(previous['games'])!=7:raise RuntimeError('A must close its seven scheduled slots first')
            if previous['version']==manifest['sha256']:raise RuntimeError('B requires the one documented strategy optimization')
            if not (ROOT/'batches/OPTIMIZATION.md').exists():raise RuntimeError('Missing concentrated optimization analysis')
        if not resume:
            out.mkdir(parents=True)
            state={'batch':label,'pid':os.getpid(),'status':'running','version':manifest['sha256'],
               'started':datetime.now(timezone.utc).isoformat(),'planned_games':7,'games':[],
               'model_calls':0,'outbound_callback':False,'current_run':None}
            dump(out/'version.json',manifest);dump(out/'status.json',state)
        for index in range(len(state['games'])+1,8):
            if (ROOT/'STOP').exists():raise InterruptedError('User stop between games')
            if version()!=manifest:raise RuntimeError('Source changed inside frozen batch')
            before=set(ROOT.joinpath('runs').iterdir())
            with (out/f'game_{index:02d}.stdout.log').open('w',encoding='utf-8') as stdout, (out/f'game_{index:02d}.stderr.log').open('w',encoding='utf-8') as stderr:
                child=subprocess.Popen([sys.executable,'-X','utf8','-u','run.py','--max-minutes','20','--batch-owner',str(os.getpid())],
                    cwd=ROOT,stdin=subprocess.DEVNULL,stdout=stdout,stderr=stderr,creationflags=subprocess.CREATE_NO_WINDOW)
                state.update(game=index,child_pid=child.pid,current_run=None);dump(out/'status.json',state)
                started=time.monotonic();run_dir=None;watchdog_at=None
                while child.poll() is None:
                    if run_dir is None:
                        run_dir=discover_run(before)
                        if run_dir:state['current_run']=str(run_dir);dump(out/'status.json',state)
                    beat=run_dir/'heartbeat.json' if run_dir else None
                    stale=(time.time()-beat.stat().st_mtime>150) if beat and beat.exists() else time.monotonic()-started>150
                    if stale and watchdog_at is None:
                        watchdog_at=time.monotonic()
                        if run_dir:(run_dir/'WATCHDOG_STOP').write_text('No worker heartbeat for 150 seconds',encoding='utf-8')
                    if watchdog_at and time.monotonic()-watchdog_at>55:
                        terminate_worker(child)
                        if run_dir:dump(run_dir/'hard_watchdog.json',{'reason':'worker unresponsive','pid':child.pid,'concession':'not attempted after unresponsive native worker; no further input'})
                        raise RuntimeError('Native worker unresponsive; terminated only owned process, stopped batch')
                    time.sleep(2)
                code=child.returncode;child=None
            # A startup rejection can finish before the supervisor's first two-second poll.
            if run_dir is None:
                run_dir=discover_run(before)
                if run_dir:state['current_run']=str(run_dir);dump(out/'status.json',state)
            if run_dir is None:
                run_dir=out/'startup_failures'/f'slot_{index:02d}';run_dir.mkdir(parents=True,exist_ok=True)
            if not (run_dir/'summary.json').exists():
                dump(run_dir/'summary.json',{'status':'stopped','reason':'Worker exited without summary',
                    'failure_kind':'worker_startup_or_abrupt_exit','outcome':'unknown','reached_result':False,
                    'started_cpu':False,'cpu_verified':False,'turns':0,'source_unchanged':False,
                    'fixed_version':manifest['sha256'],'version_basis':'supervisor expected; worker not confirmed',
                    'dispatched':{},'verified':{},'seconds':round(time.monotonic()-started,3)})
            summary=json.loads((run_dir/'summary.json').read_text(encoding='utf-8'))
            events=[]
            if (run_dir/'events.jsonl').exists():
                for line in (run_dir/'events.jsonl').read_text(encoding='utf-8').splitlines():
                    try:events.append(json.loads(line))
                    except json.JSONDecodeError:pass # Preserve a truncated final line as evidence on disk.
            last_state=next((e for e in reversed(events) if e['event']=='state'),{})
            hp=last_state.get('own_hp');incoming=sum(e.get('attack') or 0 for e in last_state.get('enemy',[]))
            analysis={'evidence':{'last_state_frame':last_state.get('frame'),'last_own_hp':hp,
                       'last_enemy_visible_attack':incoming,
                       'unapplied_attacks':sum(e['event']=='receipt' and e.get('name')=='attack' and e.get('outcome')=='not_applied' for e in events),
                       'local_missing_fields':[e.get('field') for e in events if e['event']=='local_missing'],
                       'opening':[e for e in events if e['event']=='opening_hand']},
                      'inference':'Visible board could already threaten lethal at the last observed decision.' if hp is not None and incoming>=hp else 'Cause of loss is not determined from visible board alone.',
                      'limit':'Opponent hidden hand, draws and unseen last action are not inferred as facts.'}
            dump(run_dir/'analysis.json',analysis)
            if summary['outcome']!='loss':
                analysis['inference']='Technical interruption; inspect checkpoint.' if summary['status']!='completed' else 'Completed without a recorded loss; no loss attribution.'
                dump(run_dir/'analysis.json',analysis)
            technical=code!=0 or summary['status']!='completed' or not summary['source_unchanged'] or not summary.get('cpu_verified')
            row={'game':index,'run':str(run_dir),'exit_code':code,'technical_abort':technical,**summary}
            state['games'].append(row)
            count=Counter('technical_abort' if g['technical_abort'] else g['outcome'] for g in state['games'])
            completed=sum(not g['technical_abort'] for g in state['games']);known=count['win']+count['loss']
            state['totals']={**count,'attempted':len(state['games']),'completed':completed,
                'winrate_known':count['win']/known if known else None,'known_denominator':known,
                'technical_completion_rate':completed/len(state['games'])}
            grouped={}
            for game in state['games']:
                key=game['fixed_version'];g=grouped.setdefault(key,Counter())
                g['attempted']+=1;g['technical_abort' if game['technical_abort'] else game['outcome']]+=1
            state['by_version']={}
            for key,g in grouped.items():
                denominator=g['win']+g['loss'];state['by_version'][key]={**g,'known_denominator':denominator,'winrate':g['win']/denominator if denominator else None}
            if len(grouped)>1:
                state['totals']['winrate_known']=None
                state['totals']['winrate_note']='Different technical versions are reported separately; no mixed-version winrate.'
            dump(out/'status.json',state)
            emit('game_finished',f'Batch {label} game {index}: '+('technical abort' if technical else summary['outcome']),run=str(run_dir),summary=summary)
            if technical:raise RuntimeError('Technical interruption; no automatic retry, batch frozen for diagnosis')
        state['status']='completed_with_technical' if any(g['technical_abort'] for g in state['games']) else 'completed'
        state['next']='one_concentrated_optimization' if label=='A' else 'experiment_finished_no_third_batch'
        dump(out/'status.json',state)
        notify('batch_completed',f'SWB batch {label}: 7 scheduled slots closed; {completed} matches completed. Runner stopped.',status=str(out/'status.json'))
        return 0
    except BaseException as exc:
        if state:
            state.update(status='interrupted',reason=f'{type(exc).__name__}: {exc}',failure_kind=classify(exc),traceback=traceback.format_exc())
            dump(out/'status.json',state)
        notify('batch_interrupted',f'SWB batch {label} stopped; diagnosis required.',reason=str(exc),status=str(out/'status.json'))
        return 2
    finally:
        if child and child.poll() is None:
            (ROOT/'STOP').write_text('Batch supervisor stopped',encoding='utf-8')
            try:child.wait(timeout=20)
            except subprocess.TimeoutExpired:terminate_worker(child)
        lock.unlink(missing_ok=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('batch',choices=['A','B'])
    p.add_argument('--resume-after-repair',action='store_true');p.add_argument('--repair-note');args=p.parse_args()
    raise SystemExit(main(args.batch,args.resume_after_repair,args.repair_note))
