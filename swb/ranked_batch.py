"""Bounded ranked batches. Resume completed slots, never replay an uncertain match."""
import json
import hashlib
import os
import re
import itertools
import time
import traceback
from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
from swb.atomic_io import read_json as read, write_json as write, replace_required
from swb.worker_diagnostics import record_failure


def network_reset_ready(slot):
    receipt=slot/'network_reset.json';pointer=slot/'attempt/run.json';claim=slot/'attempt/ATTEMPT.json'
    if not all(p.exists() for p in (receipt,pointer,claim)):return False
    r=read(receipt);run_dir=Path(read(pointer).get('run','')).resolve()
    summary=run_dir/'summary.json'
    return bool(summary.is_file() and r.get('safe_lobby')
        and ((r.get('apps_restarted') and r.get('acceleration_verified')) or r.get('reconnect_verified'))
        and r.get('interrupted_outcome')=='unknown'
        and not r.get('replay_pending') and r.get('run')==str(run_dir)
        and r.get('attempt_sha256')==hashlib.sha256(claim.read_bytes()).hexdigest()
        and r.get('summary_sha256')==hashlib.sha256(summary.read_bytes()).hexdigest()
        and read(summary).get('failure_kind')=='network_stalled')


def archive_network_attempt(slot):
    if not network_reset_ready(slot):raise RuntimeError('Network reset was not verified')
    digest=hashlib.sha256((slot/'attempt/run.json').read_bytes()).hexdigest()
    target=slot.parent/'network_history'/(slot.name+'_'+digest)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise RuntimeError('Network archive already exists')
    replace_required(slot,target)
    return str(target)


def recover_network_attempt(root,args,folder,slot,state,worker,recover_fn):
    summary=read(worker.out/'summary.json')
    if summary.get('failure_kind')!='network_stalled' or not summary.get('source_unchanged'):
        raise RuntimeError('Network recovery requires a confirmed unchanged-source network failure')
    def stop():
        if (root/'STOP').exists():raise InterruptedError('STOP requested during network recovery')
        while (root/'PANEL_PAUSE').exists():
            if (root/'STOP').exists():raise InterruptedError('STOP requested during network recovery')
            time.sleep(.1)
    stop()
    state.update(status='recovering',recovery_stage='starting',updated_utc=now())
    write(folder/'status.json',state)
    def event(stage,status):
        state.update(recovery_stage=stage,recovery_stage_status=status,updated_utc=now())
        state.setdefault('recovery_events',[]).append({'stage':stage,'status':status,'time':now()})
        write(folder/'status.json',state)
    if recover_fn is None:
        from swb.android_recovery import recover
        recover_fn=recover
    try:
        receipt=summary.get('network_recovery_receipt')
        if not receipt:receipt=recover_fn(root,args,worker,stop,event)
        if not receipt.get('safe_lobby') or receipt.get('replay_pending'):
            raise RuntimeError('Reconnect receipt did not verify a safe lobby')
    except BaseException as exc:
        state.update(recovery_stage_status='failed',recovery_error=f'{type(exc).__name__}: {exc}',updated_utc=now())
        write(folder/'status.json',state)
        raise
    stop()
    receipt.update(run=str(worker.out.resolve()),time=now(),
        attempt_sha256=hashlib.sha256((slot/'attempt/ATTEMPT.json').read_bytes()).hexdigest(),
        summary_sha256=hashlib.sha256((worker.out/'summary.json').read_bytes()).hexdigest())
    write(slot/'network_reset.json',receipt)
    archive=archive_network_attempt(slot)
    state.setdefault('network_recoveries',[]).append({'match':state['active_match'],
        'archive':archive,'outcome':'unknown','time':now()})
    state['network_interruptions']=len(list((folder/'network_history').glob('*/network_reset.json')))
    state.update(status='running',recovery_stage='completed',current_run=None,python_attempt_pid=None)
    write(folder/'status.json',state)


def safe_unstarted(slot):
    """A durable barrier always precedes runner construction in protocol 2.

    Legacy claims need an explicitly investigated, hash-bound repair receipt.
    Absence of run.json alone is NOT evidence that an old runner never started.
    """
    claim=slot/'attempt/ATTEMPT.json'
    if not claim.exists() or any((slot/p).exists() for p in (
            'record.json','attempt/run.json','attempt/RUNNER_STARTING.json')):
        return False
    data=read(claim)
    if data.get('protocol')==2:return True
    receipt=slot/'prestart_recovery.json';interruption=slot/'interruption.json'
    if not receipt.exists() or not interruption.exists():return False
    r=read(receipt)
    return bool(r.get('kind')=='verified_before_runner_construction'
        and r.get('batch')==slot.parent.name and r.get('match')==int(slot.name[6:])
        and r.get('attempt_sha256')==hashlib.sha256(claim.read_bytes()).hexdigest()
        and r.get('interruption_sha256')==hashlib.sha256(interruption.read_bytes()).hexdigest()
        and read(interruption).get('run') is None and r.get('reviewed_source_sha256'))


def archive_unstarted(slot):
    if not safe_unstarted(slot):raise RuntimeError('Attempt is not proven unstarted')
    digest=hashlib.sha256((slot/'attempt/ATTEMPT.json').read_bytes()).hexdigest()
    target=slot.parent/'prestart_history'/(slot.name+'_'+digest)
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():raise RuntimeError('Prestart archive already exists; inspect evidence')
    replace_required(slot,target)
    return str(target)


def now():return datetime.now(timezone.utc).isoformat()


def settlement_recovered(folder,run_dir,summary):
    path=folder/'settlement_recovery.json'
    if not path.exists():return False
    recovery=read(path)
    if recovery.get('kind')=='server_adjudicated_result':
        attempt=folder/'attempt/ATTEMPT.json';evidence=recovery.get('frame',{})
        frame=(run_dir/evidence.get('file','')).resolve()
        from swb.network_recovery import server_adjudication
        decision=server_adjudication(recovery.get('dialog_rows',[]))
        return bool(decision and decision['outcome']==recovery.get('outcome')
            and summary.get('outcome') in ('unknown',recovery['outcome'])
            and recovery.get('batch')==folder.parent.name and recovery.get('match')==int(folder.name[6:])
            and Path(recovery.get('run','')).resolve()==run_dir
            and recovery.get('original_version')==summary.get('fixed_version')
            and recovery.get('original_summary_sha256')==hashlib.sha256((run_dir/'summary.json').read_bytes()).hexdigest()
            and attempt.exists() and recovery.get('attempt_sha256')==hashlib.sha256(attempt.read_bytes()).hexdigest()
            and frame.is_relative_to(run_dir) and frame.is_file()
            and evidence.get('sha256')==hashlib.sha256(frame.read_bytes()).hexdigest())
    if recovery.get('kind')=='user_confirmed':
        # Explicit user evidence closes this one real attempt. It does not
        # rewrite the original unknown summary as an automatic success.
        attempt=folder/'attempt/ATTEMPT.json';message=recovery.get('user_confirmation',{})
        return bool(Path(recovery.get('run','')).resolve()==run_dir
            and recovery.get('batch')==folder.parent.name
            and recovery.get('match')==int(folder.name[6:])
            and recovery.get('outcome') in ('win','loss')
            and summary.get('outcome') in ('unknown',recovery.get('outcome'))
            and recovery.get('original_version')==summary.get('fixed_version')
            and attempt.exists()
            and hashlib.sha256(attempt.read_bytes()).hexdigest()==recovery.get('attempt_sha256')
            and hashlib.sha256((run_dir/'summary.json').read_bytes()).hexdigest()==recovery.get('original_summary_sha256')
            and message.get('message_id') and message.get('text'))
    if (recovery.get('kind')!='saved_settlement_evidence' or Path(recovery.get('run','')).resolve()!=run_dir
        or recovery.get('outcome') not in ('win','loss') or recovery['outcome']!=summary.get('outcome')
        or recovery.get('original_version')!=summary.get('fixed_version') or not recovery.get('settlement_complete')):
        return False
    frames=recovery.get('frames',{})
    if set(frames)!={'outcome','settlement'}:return False
    for evidence in frames.values():
        frame=(run_dir/evidence.get('file','')).resolve()
        if not frame.is_relative_to(run_dir) or not frame.is_file():return False
        if hashlib.sha256(frame.read_bytes()).hexdigest()!=evidence.get('sha256'):return False
    return True


def record_interruption(folder,state,exc):
    index=state.get('active_match')
    if not index:return
    slot=folder/f'match_{index:02d}'
    if not (slot/'attempt/ATTEMPT.json').exists():return
    pointer=slot/'attempt/run.json';summary={};run_path=None
    if pointer.exists():
        run_path=read(pointer).get('run')
        summary_path=Path(run_path)/'summary.json' if run_path else None
        if summary_path and summary_path.exists():summary=read(summary_path)
    user_stop=isinstance(exc,(InterruptedError,KeyboardInterrupt)) or str(summary.get('reason','')).startswith(('InterruptedError:','KeyboardInterrupt:'))
    path=slot/'interruption.json'
    entry=read(path) if path.exists() else {
        'match':index,'run':run_path,'version':summary.get('fixed_version',state.get('version')),
        'outcome':summary.get('outcome','unknown'),'reached_result_at_exit':summary.get('reached_result',False),
        'technical_interruption':not user_stop,'reason':summary.get('reason') or f'{type(exc).__name__}: {exc}',
        'time':now(),'traceback':''.join(traceback.format_exception(exc)),
        'runner_constructed':(slot/'attempt/run.json').exists(),
        'safe_unstarted':safe_unstarted(slot)}
    if not path.exists():write(path,entry)
    history=state.setdefault('interrupted_attempts',[])
    if not any(item['match']==index for item in history):history.append(entry)
    state['technical_interruptions']=sum(bool(item.get('technical_interruption')) for item in history)


def completed_record(root,folder,index):
    record=folder/'record.json';attempt=folder/'attempt/ATTEMPT.json';pointer=folder/'attempt/run.json'
    row=read(record) if record.exists() else None
    if row is None and (safe_unstarted(folder) or network_reset_ready(folder)):return None
    if row is None and not attempt.exists():
        # A crash before claiming a slot sent no input. An empty attempt directory is safe.
        entries=list(folder.iterdir()) if folder.exists() else []
        if any(x.name!='attempt' or not x.is_dir() or any(x.iterdir()) for x in entries):
            raise RuntimeError(f'Unresolved slot {index}: no completion record')
        return None
    run_path=row.get('run') if row else read(pointer).get('run') if pointer.exists() else None
    if not run_path:raise RuntimeError(f'Unresolved attempt {index}; no automatic replay')
    run_dir=Path(run_path).resolve()
    if not run_dir.is_relative_to((root/'runs').resolve()):raise RuntimeError('Run evidence is outside this project')
    summary_path=run_dir/'summary.json'
    if not summary_path.exists():raise RuntimeError(f'Unresolved attempt {index}; summary absent')
    s=read(summary_path)
    recovered=settlement_recovered(folder,run_dir,s)
    if not (s.get('source_unchanged') and s.get('started_ranked') and
            ((s.get('status')=='completed' and s.get('reached_result')) or recovered)):
        raise RuntimeError(f'Unresolved attempt {index}; previous match was not confirmed complete')
    if row:
        if row.get('match')!=index or row.get('version')!=s.get('fixed_version'):
            raise RuntimeError(f'Conflicting completion evidence in slot {index}')
        return row
    recovery=read(folder/'settlement_recovery.json') if recovered else {}
    user_confirmed=recovery.get('kind')=='user_confirmed'
    server_confirmed=recovery.get('kind')=='server_adjudicated_result'
    return {'match':index,'run':str(run_dir),'version':s['fixed_version'],
            'outcome':recovery['outcome'] if user_confirmed or server_confirmed else s.get('outcome','unknown'),
            'reached_result':bool(s.get('reached_result')) if user_confirmed else True,'source_unchanged':True,
            'completion_basis':'user_confirmed' if user_confirmed else 'server_adjudication' if server_confirmed else 'saved_settlement_evidence' if recovered else 'automatic',
            'user_confirmed':user_confirmed,'original_outcome':s.get('outcome','unknown'),
            'source_message_id':recovery.get('user_confirmation',{}).get('message_id'),
            'ranked_deck_verified':s.get('ranked_deck_verified',False),
            'turns':s.get('turns',0),'seconds':s.get('seconds'),
            'technical_interruption':recovered,'reconciled_from_completed_summary':not recovered,
            'settlement_recovered':recovered and not user_confirmed,'original_run_status':s.get('status'),
            'original_reached_result':s.get('reached_result',False)}


def prepare(root,args,manifest):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.batch):raise ValueError('Invalid batch name')
    if args.matches is not None and args.matches<1:raise ValueError('Total matches must be positive')
    if manifest['sha256']!=args.expect_version:raise RuntimeError('Frozen version mismatch')
    folder=root/'batches'/args.batch
    if args.resume:
        if not (folder/'status.json').exists():raise RuntimeError('Resume requires an existing batch status')
        state=read(folder/'status.json')
        if state.get('mode','ranked')!='ranked':raise RuntimeError('This is not a ranked batch')
        if state.get('batch')!=args.batch:raise RuntimeError('Batch identity mismatch')
    else:
        if folder.exists():raise RuntimeError('Batch already exists; use --resume, never overwrite records')
        state={'batch':args.batch,'games':[],'version':manifest['sha256']}
    indexes=sorted(int(p.name[6:]) for p in folder.glob('match_[0-9]*') if p.is_dir() and p.name[6:].isdigit())
    if indexes and args.matches is not None and max(indexes)>args.matches:raise RuntimeError('Requested total is below existing attempt history')
    rows=[];gap=False
    for index in range(1,max(indexes,default=0)+1):
        row=completed_record(root,folder/f'match_{index:02d}',index)
        if row is None:gap=True
        elif gap:raise RuntimeError('Completion history has a gap')
        else:rows.append(row)
    if len(state.get('games',[]))>len(rows) or state.get('completed',0)>len(rows):
        raise RuntimeError('Status claims more completions than preserved evidence')
    if (state.get('active_match') and state['active_match']>len(rows)
            and not (safe_unstarted(folder/f"match_{state['active_match']:02d}")
                     or network_reset_ready(folder/f"match_{state['active_match']:02d}"))):
        raise RuntimeError('Unresolved active match; no automatic replay')
    plan={'batch':args.batch,'authorized_matches':args.matches,'completed':len(rows),
          'remaining':None if args.matches is None else args.matches-len(rows),'next_match':len(rows)+1 if args.matches is None or len(rows)<args.matches else None,
          'version':manifest['sha256'],'prior_version':state.get('version'),
          'game_input_calls':0}
    return folder,state,rows,plan


def update_counts(state,rows,total):
    state.update(games=rows,completed=len(rows),attempted=len(rows),authorized_matches=total,
                 next_match=len(rows)+1 if total is None or len(rows)<total else None,active_match=None,
                 current_run=None,python_attempt_pid=None,
                 wins=sum(r.get('outcome')=='win' for r in rows),losses=sum(r.get('outcome')=='loss' for r in rows),
                 technical_interruptions=state.get('network_interruptions',0) + len({r['match'] for r in rows if r.get('technical_interruption')} |
                    {r['match'] for r in state.get('interrupted_attempts',[]) if r.get('technical_interruption')}),updated_utc=now())
    grouped={}
    for row in rows:
        group=grouped.setdefault(row['version'],{'completed':0,'wins':0,'losses':0})
        group['completed']+=1;group['wins']+=row.get('outcome')=='win';group['losses']+=row.get('outcome')=='loss'
    state['by_version']=grouped


def rematch_context(rows,args):
    """Only a completed ranked attempt with the unchanged deck may continue."""
    if not rows:return None
    previous=rows[-1]
    if not previous.get('ranked_deck_verified'):return None
    profile=(getattr(args,'panel_config',None) or {}).get('deck_profile')
    if not profile:return None
    path=Path(previous['run'])/'summary.json'
    if not path.is_file():return None
    summary=read(path)
    if (not summary.get('source_unchanged') or not summary.get('started_ranked')
            or (summary.get('panel_config') or {}).get('deck_profile')!=profile):return None
    return {'previous_run':previous['run'],'deck_profile':profile,'deck_verified':True}


def execute_batch(root,args,version_fn,run_factory,recover_fn=None):
    root=Path(root).resolve();manifest=version_fn()
    marker=root/'STOP'
    def stop_token():return (marker.stat().st_mtime_ns,marker.read_bytes()) if marker.exists() else None
    initial_stop=stop_token()
    if hasattr(args,'panel_stop_token'):
        signature=[initial_stop[0],hashlib.sha256(initial_stop[1]).hexdigest()] if initial_stop else None
        if signature!=args.panel_stop_token:raise InterruptedError('New STOP request after panel launch; start cancelled')
    folder,state,rows,plan=prepare(root,args,manifest)
    if args.inspect_plan:
        print(json.dumps(plan,ensure_ascii=False));return 0
    park_cleanup=(getattr(args,'ranked_park',False) and state.get('ranked_park')
        and state.get('park_phase')!='finished')
    if plan['remaining']==0 and not park_cleanup:
        print(json.dumps({**plan,'status':'already_completed'},ensure_ascii=False));return 0
    if (root/'RUNNING.lock').exists():raise RuntimeError('Another runner owns the window')
    lock=root/'BATCH.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    marker=root/'STOP';worker=None;failure=None;rest_schedule=None;park=None
    try:
        if (root/'RUNNING.lock').exists():raise RuntimeError('Another runner acquired the window')
        # Re-read after obtaining exclusive ownership; inspect-plan never commits.
        folder,state,rows,plan=prepare(root,args,version_fn())
        folder.mkdir(parents=True,exist_ok=True)
        state['network_interruptions']=len(list((folder/'network_history').glob('*/network_reset.json')))
        prior=state.get('version');state.setdefault('original_version',prior)
        state.setdefault('technical_history',[]).append({'time':now(),'from_version':prior,
            'to_version':manifest['sha256'],'resume_from':len(rows)+1,'total':args.matches,
            'change':'ranked batch/resume entry; combat strategy unchanged'})
        state.update(version=manifest['sha256'],status='running',wrapper_pid=os.getpid())
        update_counts(state,rows,args.matches)
        write(folder/'versions'/f"{manifest['sha256']}.json",manifest)
        for row in rows:
            record=folder/f"match_{row['match']:02d}"/'record.json'
            if not record.exists():write(record,row)
        write(folder/'status.json',state)
        pending=folder/f'match_{len(rows)+1:02d}'
        if network_reset_ready(pending):
            archive_network_attempt(pending)
            state['network_interruptions']=len(list((folder/'network_history').glob('*/network_reset.json')))
        if safe_unstarted(pending):
            archive=archive_unstarted(pending)
            state.setdefault('prestart_recoveries',[]).append({'time':now(),'match':len(rows)+1,
                'archive':archive,'game_started':False})
            write(folder/'status.json',state)
        # An explicit launch clears the pre-existing pause once. Later STOP is never cleared.
        if stop_token()!=initial_stop:raise InterruptedError('New STOP request during startup')
        marker.unlink(missing_ok=True)
        if getattr(args,'panel_managed',False):
            from swb.scheduled_rest import RestSchedule
            config=getattr(args,'panel_config',None) or read(root/'config.json')
            from swb.adb_transport import AdbGameLifecycle
            rest_schedule=RestSchedule(root,folder,state,rows,config,
                adapter_factory=AdbGameLifecycle if config.get('input')=='adb' else None)
        if getattr(args,'ranked_park',False):
            from swb.ranked_park import RankedPark
            park=RankedPark(root,folder,args,state,lambda:write(folder/'status.json',state),
                manifest,version_fn,rest_schedule)
        for index in (itertools.count(len(rows)+1) if args.matches is None else range(len(rows)+1,args.matches+1)):
            if marker.exists():raise InterruptedError('STOP requested between matches')
            if getattr(args,'panel_managed',False) and (root/'PANEL_FINISH').exists():
                state.update(status='paused',stop_kind='user_finish_after_match',active_match=None,next_match=index,updated_utc=now())
                write(folder/'status.json',state);return 0
            if version_fn()['sha256']!=manifest['sha256']:raise RuntimeError('Source changed inside frozen continuation')
            if rest_schedule and not rest_schedule.before_match():
                state.update(status='paused',stop_kind='user_finish_after_match',active_match=None,next_match=index,updated_utc=now())
                write(folder/'status.json',state);return 0
            while True:
                if marker.exists():raise InterruptedError('STOP before match attempt')
                if version_fn()['sha256']!=manifest['sha256']:raise RuntimeError('Source changed during network recovery')
                context=rematch_context(rows,args);prepared=None
                if park:prepared,context=park.prepare(context)
                slot=folder/f'match_{index:02d}';attempt=slot/'attempt';attempt.mkdir(parents=True,exist_ok=True)
                claim=attempt/'ATTEMPT.json'
                fd=os.open(claim,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
                with os.fdopen(fd,'w',encoding='utf-8') as f:
                    json.dump({'pid':os.getpid(),'version':manifest['sha256'],'batch':args.batch,'match':index,'limit':1,'protocol':2},f)
                    f.flush();os.fsync(f.fileno())
                state.update(active_match=index,attempted=index,next_match=index,updated_utc=now())
                write(folder/'status.json',state)
                # Commit this barrier before even constructing a runner. Its presence
                # intentionally blocks automatic replay after an ambiguous crash.
                write(attempt/'RUNNER_STARTING.json',{'time':now(),'pid':os.getpid(),'match':index})
                worker=run_factory(SimpleNamespace(inspect=False,ranked=True,ranked_preview=False,
                        max_minutes=args.max_minutes,batch_owner=os.getpid(),
                        panel_managed=getattr(args,'panel_managed',False),detail_logs=getattr(args,'detail_logs',True),
                        panel_config=getattr(args,'panel_config',None),batch=args.batch,
                        ranked_rematch_from=context,ranked_park_prepared=prepared))
                write(attempt/'run.json',{'run':str(worker.out),'version':manifest['sha256'],'match':index,'limit':1})
                state.update(current_run=str(worker.out),python_attempt_pid=os.getpid());write(folder/'status.json',state)
                code=worker.execute()
                if code==3:
                    recover_network_attempt(root,args,folder,slot,state,worker,recover_fn)
                    worker=None
                    if marker.exists():raise InterruptedError('STOP after network recovery')
                    continue
                if code!=0:
                    stopped=read(worker.out/'summary.json')
                    detail=stopped.get('reason') or f'worker exit {code}'
                    if stopped.get('failure_kind')=='user_stop':raise InterruptedError(detail)
                    raise RuntimeError(f'Match {index} stopped ({stopped.get("failure_kind") or "technical_interruption"}): {detail}; checkpoint retained, no automatic retry')
                row=completed_record(root,slot,index)
                if row is None:raise RuntimeError('Worker returned without a completed record')
                write(slot/'record.json',row);rows.append(row)
                update_counts(state,rows,args.matches);write(folder/'status.json',state)
                if rest_schedule:rest_schedule.checkpoint(rows)
                if park:park.after_match()
                worker=None
                break
            if getattr(args,'panel_managed',False) and (root/'PANEL_FINISH').exists():
                state.update(status='paused',stop_kind='user_finish_after_match',active_match=None,updated_utc=now())
                write(folder/'status.json',state);return 0
            if getattr(args,'panel_managed',False):
                from swb.panel_evidence import prune
                prune(root)
        if park:park.finish()
        state.update(status='completed',stop_kind='specified_count_complete',active_match=None,next_match=None,updated_utc=now())
        write(folder/'status.json',state);return 0
    except BaseException as exc:
        failure=exc;secondary=[]
        try:record_interruption(folder,state,exc)
        except BaseException:secondary.append(traceback.format_exc())
        summary={}
        try:
            if worker is not None and (worker.out/'summary.json').exists():summary=read(worker.out/'summary.json')
        except BaseException:secondary.append(traceback.format_exc())
        stop_kind=('user_stop' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else
                   'network_error' if summary.get('failure_kind') in ('network_stalled','network_recovery_blocked','reconnection_exhausted') else 'technical_interruption')
        state.update(status='interrupted',stop_kind=stop_kind,reason=f'{type(exc).__name__}: {exc}',updated_utc=now())
        try:write(folder/'status.json',state)
        except BaseException:secondary.append(traceback.format_exc())
        record_failure(root,args.batch,exc,'ranked-batch',secondary)
        print(json.dumps({'status':'interrupted','reason':state['reason']},ensure_ascii=False))
        return 2
    finally:
        cleanup=[]
        if park:
            try:park.close()
            except BaseException as exc:cleanup.append(exc)
        if rest_schedule:
            try:rest_schedule.checkpoint(rows+([{'run':str(worker.out)}] if worker is not None else []))
            except BaseException as exc:cleanup.append(exc)
        try:marker.write_text('Ranked batch ended; explicit start required.\n',encoding='utf-8')
        except BaseException as exc:cleanup.append(exc)
        # If construction succeeded but a required checkpoint failed before
        # execute(), release only our own runner lock; never resume that attempt.
        if worker is not None:
            owned=getattr(worker,'lock',None)
            try:
                if owned and owned.exists() and owned.read_text().strip()==str(os.getpid()):owned.unlink()
            except BaseException as exc:cleanup.append(exc)
        try:
            if lock.exists() and lock.read_text().strip()==str(os.getpid()):lock.unlink()
        except BaseException as exc:cleanup.append(exc)
        if cleanup:
            record_failure(root,args.batch,failure or cleanup[0],'batch-cleanup',
                           [''.join(traceback.format_exception(e)) for e in cleanup])
            if failure is None:raise cleanup[0]
