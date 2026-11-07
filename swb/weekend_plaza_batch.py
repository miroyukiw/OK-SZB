"""Exclusive one-event supervisor for 16-player weekend plaza tournaments."""
import hashlib
import os
import re
import time
from pathlib import Path
from types import SimpleNamespace
from swb.atomic_io import read_json as read,write_json as write
from swb.ranked_batch import now
from swb.worker_diagnostics import record_failure

MODE='weekend_plaza'
def completed(root,slot):
    pointer=slot/'attempt/run.json'
    if not pointer.exists():raise RuntimeError('16人赛尝试结果不明，不能自动重打')
    folder=Path(read(pointer)['run']).resolve()
    if not folder.is_relative_to((root/'runs').resolve()):raise RuntimeError('广场赛证据不在当前项目中')
    s=read(folder/'summary.json') if (folder/'summary.json').exists() else {}
    data=s.get(MODE) or {};before=data.get('before') or {};result=data.get('result') or {}
    if not(s.get('mode')==MODE and s.get('started_weekend_plaza') and s.get('source_unchanged') and s.get('reached_result')
        and s.get('status')=='completed' and s.get('outcome') in ('win','loss') and before.get('players')==16
        and before.get('round') in (1,2,3,4) and result.get('players')==16 and result.get('round')==before.get('round') and result.get('outcome')==s['outcome']):
        raise RuntimeError('16人赛本轮没有确认完成，保留现场，不自动重打')
    for proof in (before.get('evidence'),result.get('evidence')):
        if not proof:raise RuntimeError('16人赛回执缺少关键画面')
        path=(folder/proof['file']).resolve()
        if not path.is_relative_to(folder) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=proof['sha256']:
            raise RuntimeError('16人赛关键画面校验失败')
    row={'match':int(slot.name[6:]),'mode':MODE,'run':str(folder),'version':s['fixed_version'],'outcome':s['outcome'],
        'seconds':s['seconds'],'round':result['round'],'event_ended':result.get('event_ended',False),
        'deck_verified':s.get('ranked_deck_verified',False),'result':result}
    if (slot/'record.json').exists() and read(slot/'record.json')!=row:raise RuntimeError('16人赛完成记录冲突')
    return row

def prepare(root,args,manifest):
    root=Path(root)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',args.batch):raise ValueError('Invalid plaza batch name')
    if args.matches!=4 or getattr(args,'players',16)!=16 or getattr(args,'wait_open',False):raise ValueError('周末广场赛模式只允许16人、单场赛事、最多4轮，未开放即停止')
    if manifest['sha256']!=args.expect_version:raise RuntimeError('广场赛启动版本不一致')
    folder=root/'batches'/args.batch
    if args.resume:
        if not(folder/'status.json').exists():raise RuntimeError('16人赛恢复记录不存在')
        state=read(folder/'status.json')
        if state.get('mode')!=MODE or state.get('batch')!=args.batch:raise RuntimeError('该记录不是16人周末广场赛')
    else:
        if folder.exists():raise RuntimeError('16人赛批次已存在，不覆盖历史记录')
        state={'batch':args.batch,'mode':MODE,'players':16,'authorized_matches':4,'games':[],'status':'ready'}
    rows=[]
    for slot in sorted(folder.glob('match_[0-9][0-9]')):
        if int(slot.name[6:])!=len(rows)+1:raise RuntimeError('16人赛历史场次不连续')
        if (slot/'navigation_end.json').exists():
            if state.get('status') in ('completed','not_open','unavailable','preview_ready'):return folder,state,rows
            raise RuntimeError('该导航任务已结束，请新启动任务')
        rows.append(completed(root,slot))
    for previous,current in zip(rows,rows[1:]):
        if current['round']!=previous['round']+1:raise RuntimeError('16人赛轮次记录不连续')
    return folder,state,rows

def execute_batch(root,args,version_fn,run_factory):
    root=Path(root).resolve();manifest=version_fn();folder,state,rows=prepare(root,args,manifest)
    if state.get('status') in ('completed','not_open','unavailable','preview_ready'):return 0
    stop_path=root/'STOP'
    def token():return [stop_path.stat().st_mtime_ns,hashlib.sha256(stop_path.read_bytes()).hexdigest()] if stop_path.exists() else None
    initial=token()
    if hasattr(args,'panel_stop_token') and initial!=args.panel_stop_token:raise InterruptedError('启动后收到新的停止请求')
    if (root/'RUNNING.lock').exists():raise RuntimeError('已有对局程序运行')
    lock=root/'BATCH.lock';fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w') as f:f.write(str(os.getpid()))
    worker=None;rest=None;failure=None
    def save(**changes):
        state.update(changes,games=rows,completed=len(rows),wins=sum(r['outcome']=='win' for r in rows),
            losses=sum(r['outcome']=='loss' for r in rows),updated_utc=now())
        write(folder/'status.json',state)
    def stop():
        if stop_path.exists():raise InterruptedError('16人赛收到立即停止请求')
        while (root/'PANEL_PAUSE').exists():
            if stop_path.exists():raise InterruptedError('16人赛暂停中收到停止请求')
            time.sleep(.1)
    def context():
        if not rows:return None
        last=rows[-1]
        return {'players':16,'round':last['round'],'deck_verified':last['deck_verified'],
            'previous_kind':last['result'].get('kind','battle'),
            'event_ended':last['event_ended'],'previous_run':last['run']}
    def run_one(attempt,finish_only=False):
        nonlocal worker
        stop()
        if (root/'PANEL_FINISH').exists():raise InterruptedError('已设置局后停止，不继续广场赛')
        if version_fn()!=manifest:raise RuntimeError('16人赛运行期间程序版本发生变化')
        attempt.mkdir(parents=True,exist_ok=True)
        with (attempt/'ATTEMPT.json').open('x',encoding='utf-8') as f:
            import json;json.dump({'mode':MODE,'players':16,'version':manifest['sha256'],'time':now()},f);f.flush();os.fsync(f.fileno())
        worker=run_factory(SimpleNamespace(inspect=False,ranked=True,ranked_preview=False,mode=MODE,
            max_minutes=35+getattr(args,'plaza_wait_minutes',30),batch_owner=os.getpid(),batch=args.batch,
            panel_managed=True,detail_logs=getattr(args,'detail_logs',True),panel_config=args.panel_config,
            plaza_preview=getattr(args,'plaza_preview',False),plaza_context=context(),plaza_finish_only=finish_only,
            plaza_wait_minutes=getattr(args,'plaza_wait_minutes',30)))
        write(attempt/'run.json',{'run':str(worker.out),'version':manifest['sha256']});save(current_run=str(worker.out))
        code=worker.execute();summary=read(worker.out/'summary.json')
        if code!=0:
            if summary.get('failure_kind')=='user_stop':raise InterruptedError(summary.get('reason'))
            raise RuntimeError(summary.get('reason') or '16人赛运行中断')
        if not summary.get('source_unchanged'):raise RuntimeError('16人赛源文件校验失败')
        return summary
    def navigation_result(summary):
        result=summary[MODE];reason=result.get('end')
        if reason=='not_open':
            opening=result.get('next_open')
            message='周末广场赛未开放，已按设置停止'+('；下次开放 '+opening.replace('T',' ') if opening else '')
            save(status='not_open',reason=message,next_open=opening,active_match=None,stop_kind='not_open')
        elif reason=='event_complete':save(status='completed',reason='16人广场赛已结束',active_match=None,reward_received=result.get('reward_received'))
        elif reason=='preview_ready':save(status='preview_ready',reason='已到达16人赛准备页，没有报名或开赛',active_match=None)
        else:save(status='unavailable',reason={'no_16_player_event':'未找到唯一可报名的16人周末广场赛',
            'wrong_size':'当前不是16人赛，未操作报名','wrong_event':'当前为其他类型赛事，未操作报名',
            'size_unverified':'参赛人数识别置信度不足，未操作报名',
            'payment_blocked':'出现费用确认，已停止，不自动支付','closed_after_rounds':'赛事页面已关闭，结束状态待核对',
            'waiting_deck_verification':'已到达赛事准备页，牌组确认控件待实测，未开赛',
            'unknown_round':'未能读取当前轮次，未开赛','unverified_finish':'已停止，赛事最终结束页尚未确认',
            'already_participated':'本期16人周末广场赛已参加，未重复报名',
            'unrecognized_event_controls':'16人赛内部控件尚未核验，已保留画面'}.get(reason,reason),active_match=None)
    try:
        if (root/'RUNNING.lock').exists():raise RuntimeError('另一个对局程序取得了控制权')
        if token()!=initial:raise InterruptedError('启动过程中收到新的停止请求')
        folder.mkdir(parents=True,exist_ok=True);write(folder/'versions'/f"{manifest['sha256']}.json",manifest)
        stop_path.unlink(missing_ok=True);save(status='running',reason=None,version=manifest['sha256'],wrapper_pid=os.getpid(),active_match=None)
        from swb.adb_transport import AdbClient,AdbGameLifecycle,GAME,UU
        from swb.scheduled_rest import RestSchedule
        client=AdbClient(args.panel_config,stop);client.connect()
        if not(client.pids(UU+':core') or client.pids(UU)):raise RuntimeError('请先开启UU对游戏的加速')
        if not client.pids(GAME) or client.focus()[1]!=GAME:
            client.launch_game();deadline=time.monotonic()+20
            while not client.pids(GAME) or client.focus()[1]!=GAME:
                stop()
                if time.monotonic()>deadline:raise RuntimeError('游戏没有进入Android前台')
                time.sleep(.2)
        if not getattr(args,'plaza_preview',False):rest=RestSchedule(root,folder,state,rows,args.panel_config,adapter_factory=AdbGameLifecycle)
        for index in range(len(rows)+1,5):
            if rows and (rows[-1]['event_ended'] or rows[-1]['round']==4):break
            stop()
            if (root/'PANEL_FINISH').exists():save(status='paused',stop_kind='user_finish_after_match',active_match=None);return 0
            slot=folder/f'match_{index:02d}';save(active_match=index)
            summary=run_one(slot/'attempt');result=summary[MODE];worker=None
            if result.get('end'):
                write(slot/'navigation_end.json',{'run':state['current_run'],'end':result['end']});navigation_result(summary);return 0
            row=completed(root,slot);write(slot/'record.json',row);rows.append(row)
            save(active_match=None,current_round=row['round'])
            if rest:rest.checkpoint(rows)
            if (root/'PANEL_FINISH').exists():save(status='paused',stop_kind='user_finish_after_match');return 0
        summary=run_one(folder/'settlement'/now().replace(':','-').replace('+','_'),True);worker=None;navigation_result(summary)
        if state.get('status')=='completed' and rest:
            # A registered plaza tournament cannot safely survive closing the game
            # between rounds. Honor accumulated rest only at the event boundary.
            if not rest.before_match():save(status='paused',stop_kind='user_finish_after_match');return 0
            save(status='completed',stop_kind='weekend_plaza_event_complete')
        return 0
    except BaseException as exc:
        failure=exc;save(status='interrupted',reason=f'{type(exc).__name__}: {exc}',
            stop_kind='user_stop' if isinstance(exc,(InterruptedError,KeyboardInterrupt)) else 'technical_interruption')
        record_failure(root,args.batch,exc,'weekend-plaza-batch');return 2
    finally:
        errors=[]
        try:
            if rest:rest.checkpoint(rows+([{'run':str(worker.out)}] if worker else []))
        except BaseException as exc:errors.append(exc)
        try:stop_path.write_text('16-player weekend plaza stopped; explicit start required.\n',encoding='utf-8')
        except BaseException as exc:errors.append(exc)
        for owned in ((worker.lock if worker else None),lock):
            try:
                if owned and owned.exists() and owned.read_text().strip()==str(os.getpid()):owned.unlink()
            except BaseException as exc:errors.append(exc)
        if errors:
            record_failure(root,args.batch,failure or errors[0],'weekend-plaza-cleanup',[str(e) for e in errors])
            if failure is None:raise errors[0]
