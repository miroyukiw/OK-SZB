"""User-authorized intervention: resolve only the stopped CPU's discard, then stop."""
import argparse,json,time,traceback
from pathlib import Path
from types import SimpleNamespace
from run import Run,ROOT,dump,version
from swb.transport import Transport
from swb.vision import Vision
from batch_runner import alive
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('failed_run');args=p.parse_args()
 failed=(ROOT/'runs'/args.failed_run).resolve()
 if not failed.is_relative_to(ROOT/'runs'):raise RuntimeError('Expected a local stopped run')
 s=json.loads((failed/'summary.json').read_text(encoding='utf-8'));checkpoint=json.loads((failed/'checkpoint.json').read_text(encoding='utf-8'))
 if not s.get('cpu_verified') or s.get('reached_result'):raise RuntimeError('Not an unresolved owned CPU')
 batch=json.loads((ROOT/'batches/stability/status.json').read_text(encoding='utf-8'))
 if batch['status']!='interrupted' or alive(batch['pid']):raise RuntimeError('Batch has not stopped')
 if any((ROOT/x).exists() for x in ['BATCH.lock','RUNNING.lock']):raise RuntimeError('Existing controller lock')
 events=[json.loads(x) for x in (failed/'events.jsonl').read_text(encoding='utf-8').splitlines()];start=next(e for e in events if e['event']=='start')
 marker=ROOT/'STOP';saved_stop=marker.read_bytes() if marker.exists() else b'Discard diagnostic finished; no batch continuation'
 r=None;t=None;report={'assisted_discard_only':True,'counted_in_stability':False,'source_run':str(failed),'success':False}
 try:
  marker.unlink(missing_ok=True)
  r=Run(SimpleNamespace(max_minutes=3,inspect=False,batch_owner=None));t=Transport(r.config,r.out,r.stop);r.t=t;t.metrics=r.metrics
  if t.window.hwnd!=start['hwnd'] or t.window.pid!=start['pid']:raise RuntimeError('Window identity changed')
  r.v=Vision();r.v.metrics=r.metrics;r.last_state=checkpoint['state'];r.active_effect=checkpoint['active_effect'];r.effect_serial=1
  r.observe('paused_discard');cid=r.active_effect['id'];required=r.v.discard_requirement(cid)
  candidates=r.v.picker_cards(r.im)
  if not required or not candidates or r.v.turn(r.im):raise RuntimeError('Expected discard grid is no longer present')
  before_pp=r.v.pp(r.im);report.update(fixed_version=r.manifest['sha256'],required=required,before_candidates=candidates,before_pp=before_pp)
  t.enable_input();r.log('assisted_resume',source_run=str(failed),scope='discard only; no battle loop, no new match')
  r.resolve_prompt({'id':cid});r.settle_action()
  after_pp=r.v.pp(r.im);board=r.v.board(r.im,'own')
  if r.v.turn(r.im)=='OUR':r.act('inspect_hand_after_discard',[900,684],delay=.5)
  hand=r.v.hand(r.im)
  report.update(after_pp=after_pp,after_board=board,after_hand=hand,verified=dict(r.verified),actions=dict(r.actions),final_frame=r.frame,
                success=bool(r.verified.get('discard') and before_pp[0] is not None and after_pp[0] is not None and after_pp[0]<before_pp[0]))
 except Exception as exc:report.update(error=str(exc),traceback=traceback.format_exc())
 finally:
  if t:t.close()
  if r:
   report.update(source_unchanged=version()==r.manifest,seconds=round(time.monotonic()-r.began,2))
   dump(r.out/'discard_validation.json',report);r.lock.unlink(missing_ok=True)
  marker.write_bytes(saved_stop)
 print(json.dumps({'out':str(r.out) if r else None,**report},ensure_ascii=False))
 raise SystemExit(0 if report['success'] else 2)
