"""Single authorized ranked match. Creates a durable, non-replayable attempt."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from run import ROOT,Run,version,dump

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--expect-version',required=True)
    p.add_argument('--max-minutes',type=int,default=25)
    p.add_argument('--batch',help='Named ranked batch, stored under batches/')
    p.add_argument('--matches',type=int,help='Total matches, including already completed matches')
    p.add_argument('--resume',action='store_true')
    p.add_argument('--inspect-plan',action='store_true',help='Read-only resume plan; no locks, writes or inputs')
    args=p.parse_args()
    if args.batch or args.matches is not None or args.resume or args.inspect_plan:
        if not args.batch or args.matches is None:p.error('--batch and --matches are required together')
        from swb.ranked_batch import execute_batch
        return execute_batch(ROOT,args,version,Run)
    if version()['sha256']!=args.expect_version:raise RuntimeError('Frozen version mismatch')
    if any((ROOT/f).exists() for f in ('RUNNING.lock','BATCH.lock')):raise RuntimeError('Another runner owns the window')
    folder=ROOT/'batches/ranked_once_20261012';folder.mkdir(exist_ok=True)
    claim=folder/'ATTEMPT.json'
    fd=os.open(claim,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump({'pid':os.getpid(),'version':args.expect_version,'limit':1},f)
    marker=ROOT/'STOP'
    run=None
    try:
        marker.unlink(missing_ok=True)
        run=Run(SimpleNamespace(inspect=False,ranked=True,ranked_preview=False,
                    max_minutes=args.max_minutes,batch_owner=None))
        dump(folder/'run.json',{'run':str(run.out),'version':args.expect_version,'limit':1})
        return run.execute()
    finally:
        marker.write_text('Single ranked attempt ended; no automatic replay.\n',encoding='utf-8')

if __name__=='__main__':raise SystemExit(main())
