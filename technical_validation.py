"""One bounded CPU diagnostic, frozen before launch; never an A/B batch."""
import argparse,json,time
from pathlib import Path
from run import Run,dump,version
from batch_runner import notify
ROOT=Path(__file__).parent
class ValidationExit(RuntimeError):pass
class ValidationRun(Run):
    def battle(self):
        super().battle()
        if self.v.turn(self.im)=='OUR':
            if self.args.exit_early and self.turn>=1:raise ValidationExit('First CPU turn reached; exercising safe concession')
            if all(self.verified.get(k,0) for k in ('super_evolve','evolve','attack')):
                raise ValidationExit('Normal/super evolution and applied attack observed; exercising safe concession')
            if self.turn>=10:raise ValidationExit('Bounded CPU diagnostic reached turn 10; no extra match')
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--exit-early',action='store_true');args=p.parse_args()
    args.max_minutes=12;args.inspect=False;args.batch_owner=None
    if any((ROOT/x).exists() for x in ['RUNNING.lock','BATCH.lock','STOP']):raise RuntimeError('A lock or STOP exists; no validation launched')
    runner=ValidationRun(args)
    status={'status':'running','pid':__import__('os').getpid(),'version':runner.manifest['sha256'],'run':str(runner.out),'scope':'one CPU diagnostic; no strategy change or repeated batch'}
    dump(ROOT/'reports/technical/live_status.json',status)
    code=runner.execute()
    summary=json.loads((runner.out/'summary.json').read_text(encoding='utf-8'))
    valid_exit=summary.get('failure_kind')=='intentional_validation_exit' and bool((summary.get('concession') or {}).get('confirmed_result'))
    status.update(status='completed' if valid_exit or summary['status']=='completed' else 'failed',summary=summary,concession_verified=valid_exit,worker_exit_code=code,finished=time.time(),validation_source_unchanged=version()==runner.manifest)
    dump(ROOT/'reports/technical/live_status.json',status)
    dump(runner.out/'technical_validation.json',status)
    notify('technical_validation_finished','SWB targeted CPU validation stopped: '+status['status'],report=str(runner.out/'technical_validation.json'))
    raise SystemExit(0 if status['status']=='completed' else 2)
