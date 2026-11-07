"""Only concede the verified CPU owned by a stopped diagnostic; never resume or launch."""
import argparse,json,os
from pathlib import Path
from datetime import datetime,timezone
from run import version,dump
from swb.transport import Transport
from swb.vision import Vision
from swb.recovery import concede_failed_cpu
ROOT=Path(__file__).resolve().parent
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('run');args=p.parse_args()
 failed=(ROOT/'runs'/args.run).resolve()
 if not failed.is_relative_to(ROOT/'runs'):raise RuntimeError('Expected local run')
 s=json.loads((failed/'summary.json').read_text(encoding='utf-8'))
 if not s.get('cpu_verified') or not s.get('started_cpu') or s.get('reached_result') or (s.get('concession') or {}).get('confirmed_result'):
  raise RuntimeError('No unresolved verified CPU owned by this diagnostic')
 if any((ROOT/x).exists() for x in ['RUNNING.lock','BATCH.lock','STOP']):raise RuntimeError('Runner or stop marker exists')
 events=[json.loads(x) for x in (failed/'events.jsonl').read_text(encoding='utf-8').splitlines()]
 start=next(e for e in events if e['event']=='start')
 out=ROOT/'runs'/('technical_exit_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'));out.mkdir()
 manifest=version();dump(out/'version.json',manifest)
 fd=os.open(ROOT/'RUNNING.lock',os.O_CREAT|os.O_EXCL|os.O_WRONLY);os.write(fd,str(os.getpid()).encode());os.close(fd)
 t=None;result={}
 try:
  t=Transport(json.loads((ROOT/'config.json').read_text(encoding='utf-8')),out)
  if t.window.hwnd!=start['hwnd'] or t.window.pid!=start['pid']:raise RuntimeError('CPU window identity changed')
  v=Vision();t.enable_input();result=concede_failed_cpu(t,v,out,True)
 except Exception as exc:result={'confirmed_result':False,'error':str(exc)}
 finally:
  if t:t.close()
  result.update(owned_run=str(failed),version=manifest['sha256'],source_unchanged=version()==manifest)
  dump(out/'summary.json',result);(ROOT/'RUNNING.lock').unlink(missing_ok=True)
 print(json.dumps({'out':str(out),**result},ensure_ascii=False))
 raise SystemExit(0 if result.get('confirmed_result') else 2)
