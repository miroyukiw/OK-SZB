"""One Grand Prix challenge per explicit start; uncertain battles never replay."""
import hashlib
import os
import re
from pathlib import Path
from types import SimpleNamespace
from swb.atomic_io import read_json as read,write_json as write
from swb.ranked_batch import now
from swb.worker_diagnostics import record_failure

def completed(root,slot):
    pointer=slot/'attempt/run.json'
    if not pointer.exists():raise RuntimeError('Unresolved Grand Prix attempt; no automatic replay')
    folder=Path(read(pointer)['run']).resolve()
    if not folder.is_relative_to((root/'runs').resolve()):raise RuntimeError('Grand Prix evidence is outside this project')
    s=read(folder/'summary.json') if (folder/'summary.json').exists() else {}
    gp=s.get('grand_prix') or {};before=gp.get('before') or {};after=gp.get('result') or {}
    if not(s.get('mode')=='grand_prix' and s.get('source_unchanged') and s.get('started_grand_prix')
        and s.get('status')=='completed' and s.get('reached_result') and s.get('outcome') in ('win','loss')
        and after.get('outcome')==s['outcome'] and after.get('played')==before.get('next')):
        raise RuntimeError('Grand Prix battle was not confirmed complete; no automatic replay')
    for proof in (before.get('evidence'),after.get('evidence')):
        if not proof:raise RuntimeError('Grand Prix result evidence is missing')
        file=(folder/proof['file']).resolve()
        if not file.is_relative_to(folder) or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest()!=proof['sha256']:
            raise RuntimeError('Grand Prix result evidence changed')
    row={'match':int(slot.name[6:]),'run':str(folder),'version':s['fixed_version'],'mode':'grand_prix',
        'outcome':s['outcome'],'seconds':s['seconds'],'turns':s['turns'],'before':before,'result':after}
    if (slot/'record.json').exists() and read(slot/'record.json')!=row:raise RuntimeError('Grand Prix completion record conflicts')
    return row

def prepare(root,args,manifest):
    root=Path(root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.batch):raise ValueError('Invalid Grand Prix batch')
    if args.matches!=5:raise ValueError('Grand Prix runs one challenge, at most five battles')
    if args.expect_version!=manifest['sha256']:raise RuntimeError('Frozen Grand Prix version mismatch')
    folder=root/'batches'/args.batch
    if args.resume:
        if not (folder/'status.json').exists():raise RuntimeError('Grand Prix resume status is missing')
        state=read(folder/'status.json')
        if state.get('mode')!='grand_prix' or state.get('batch')!=args.batch:raise RuntimeError('This is not a Grand Prix batch')
    else:
        if folder.exists():raise RuntimeError('Grand Prix batch exists; records cannot be overwritten')
        state={'batch':args.batch,'mode':'grand_prix','games':[],'authorized_matches':5,'status':'ready'}
    rows=[]
    for slot in sorted(folder.glob('match_[0-9][0-9]')):
        if int(slot.name[6:])!=len(rows)+1:raise RuntimeError('Grand Prix history has a gap')
        if (slot/'navigation_end.json').exists():
            if state.get('status')=='completed':return folder,state,rows
            raise RuntimeError('This navigation run has ended; start a new task after manual admission')
        rows.append(completed(root,slot))
    return folder,state,rows

def execute_batch(root,args,version_fn,run_factory):
    root=Path(root).resolve();manifest=version_fn();folder,state,rows=prepare(root,args,manifest)
    if state.get('status')=='completed':return 0
    marker=root/'STOP'
    def token():return [marker.stat().st_mtime_ns,hashlib.sha256(marker.read_bytes()).hexdigest()] if marker.exists() else None
    initial=token()
    if hasattr(args,'panel_stop_token') and initial!=args.panel_stop_token:raise InterruptedError('New STOP request after launch')
    if (root/'RUNNING.lock').exists():raise RuntimeError('Another controller owns the game')
    lock=root/'BATCH.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    worker=None;rest=None;failure=None
    def save(**updates):
        state.update(updates,updated_utc=now(),games=rows,completed=len(rows),
            wins=sum(r['outcome']=='win' for r in rows),losses=sum(r['outcome']=='loss' for r in rows))
        write(folder/'status.json',state)
    def control():
        if marker.exists():raise InterruptedError('STOP requested during Grand Prix')
        while (root/'PANEL_PAUSE').exists():
            if marker.exists():raise InterruptedError('STOP requested during Grand Prix pause')
            import time;time.sleep(.1)
    def run_one(attempt,finish_only=False):
        nonlocal worker
        control()
        if (root/'PANEL_FINISH').exists():raise InterruptedError('Finish-after-match requested before Grand Prix input')
        if version_fn()!=manifest:raise RuntimeError('Source changed during frozen Grand Prix challenge')
        attempt.mkdir(parents=True,exist_ok=True)
        with (attempt/'ATTEMPT.json').open('x',encoding='utf-8') as f:
            import json;json.dump({'mode':'grand_prix','version':manifest['sha256'],'time':now()},f);f.flush();os.fsync(f.fileno())
        worker=run_factory(SimpleNamespace(inspect=False,ranked=True,ranked_preview=False,mode='grand_prix',
            max_minutes=3 if finish_only or getattr(args,'gp_preview',False) else 25,batch_owner=os.getpid(),batch=args.batch,
            panel_managed=True,detail_logs=getattr(args,'detail_logs',True),panel_config=args.panel_config,
            gp_finish_only=finish_only,gp_preview=getattr(args,'gp_preview',False)))
        write(attempt/'run.json',{'run':str(worker.out),'version':manifest['sha256']})
        save(current_run=str(worker.out))
        code=worker.execute();s=read(worker.out/'summary.json')
        if code!=0:
            if s.get('failure_kind')=='user_stop':raise InterruptedError(s.get('reason'))
            raise RuntimeError(s.get('reason') or 'Grand Prix worker stopped')
        if not s.get('source_unchanged'):raise RuntimeError('Grand Prix source changed')
        return s
    try:
        if (root/'RUNNING.lock').exists():raise RuntimeError('Another runner acquired the game')
        if token()!=initial:raise InterruptedError('New STOP request during Grand Prix startup')
        folder.mkdir(parents=True,exist_ok=True);write(folder/'versions'/f"{manifest['sha256']}.json",manifest)
        marker.unlink(missing_ok=True)
        save(status='running',version=manifest['sha256'],wrapper_pid=os.getpid(),active_match=None,reason=None)
        from swb.scheduled_rest import RestSchedule
        from swb.adb_transport import AdbGameLifecycle,AdbClient,GAME
        if not getattr(args,'gp_preview',False):rest=RestSchedule(root,folder,state,rows,args.panel_config,adapter_factory=AdbGameLifecycle)
        client=AdbClient(args.panel_config,control);client.connect()
        if not(client.pids('com.netease.uu:core') or client.pids('com.netease.uu')):
            raise RuntimeError('请先在 UU 开启游戏加速，再启动大赛任务')
        if not client.pids(GAME) or client.focus()[1]!=GAME:
            import time
            client.launch_game();deadline=time.monotonic()+20
            while not client.pids(GAME) or client.focus()[1]!=GAME:
                control()
                if time.monotonic()>deadline:raise RuntimeError('Game did not become the focused Android app')
                time.sleep(.2)
        for index in range(len(rows)+1,6):
            if rows and rows[-1]['result']['ended']:break
            control()
            if (root/'PANEL_FINISH').exists() or (rest and not rest.before_match()):
                save(status='paused',stop_kind='user_finish_after_match',active_match=None);return 0
            slot=folder/f'match_{index:02d}';save(active_match=index)
            s=run_one(slot/'attempt');gp=s['grand_prix'];worker=None
            if gp.get('end'):
                write(slot/'navigation_end.json',{'run':state['current_run'],'end':gp['end']})
                save(status='waiting_entry' if gp['end']=='waiting_manual_entry' else 'preview_ready' if 'preview' in gp['end'] or gp['end']=='free_entry_available' else 'completed',
                    active_match=None,reason='需要手动入场；任务不会消耗金币、钻石或门票' if gp['end']=='waiting_manual_entry' else gp['end'],
                    reward_claimed=gp.get('reward_claimed'),challenge=gp.get('challenge'))
                return 0
            row=completed(root,slot);write(slot/'record.json',row);rows.append(row)
            save(active_match=None,challenge={'wins':row['result']['wins'],'played':row['result']['played']})
            if rest:rest.checkpoint(rows)
            if (root/'PANEL_FINISH').exists():
                save(status='paused',stop_kind='user_finish_after_match');return 0
        if not rows or not rows[-1]['result']['ended']:raise RuntimeError('Grand Prix did not finish within five new battles')
        s=run_one(folder/'settlement'/now().replace(':','-').replace('+','_'),True);gp=s['grand_prix'];worker=None
        if gp.get('end') not in ('challenge_complete','reward_already_closed'):raise RuntimeError('Grand Prix reward settlement not verified')
        save(status='completed',active_match=None,reward_claimed=gp.get('reward_claimed'),stop_kind='grand_prix_challenge_complete')
        return 0
    except BaseException as exc:
        failure=exc
        save(status='interrupted',reason=f'{type(exc).__name__}: {exc}',
            stop_kind='user_stop' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'technical_interruption')
        record_failure(root,args.batch,exc,'grand-prix-batch');return 2
    finally:
        errors=[]
        try:
            if rest:rest.checkpoint(rows+([{'run':str(worker.out)}] if worker else []))
        except BaseException as exc:errors.append(exc)
        try:marker.write_text('Grand Prix ended; explicit start required.\n',encoding='utf-8')
        except BaseException as exc:errors.append(exc)
        for owned in ((worker.lock if worker else None),lock):
            try:
                if owned and owned.exists() and owned.read_text().strip()==str(os.getpid()):owned.unlink()
            except BaseException as exc:errors.append(exc)
        if errors:
            record_failure(root,args.batch,failure or errors[0],'grand-prix-cleanup',[str(e) for e in errors])
            if failure is None:raise errors[0]
