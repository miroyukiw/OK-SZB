"""Launched only by an explicit panel Start/Resume action."""
import sys
import json
from pathlib import Path
from types import SimpleNamespace
from swb.worker_diagnostics import record_failure,install_worker_console

def main():
    root=Path(__file__).resolve().parent;batch=None
    try:
        install_worker_console(root)
        # Include request parsing, dependencies and frozen-version calculation in
        # the diagnostic boundary. Launcher capture also catches import failures.
        from run import ROOT,Run,version
        from swb.ranked_batch import execute_batch,read
        req_path=Path(sys.argv[1]);request=read(req_path);batch=request.get('batch')
        if request.get('mode') in (None,'ranked'):
            request['deck_profile']='dragon_tempo'
            if isinstance(request.get('panel_config'),dict):
                request['panel_config']['deck_profile']='dragon_tempo'
            from swb.atomic_io import write_json
            write_json(req_path,request)
        args=SimpleNamespace(**request,expect_version=version()['sha256'],inspect_plan=False,max_minutes=25,panel_managed=True)
        if request.get('mode')=='paradise':
            from swb.paradise import execute_batch as execute_paradise
            return execute_paradise(ROOT,args,version)
        if request.get('mode')=='grand_prix':
            from swb.grand_prix_batch import execute_batch as execute_grand_prix
            from swb.grand_prix import GrandPrixRun
            return execute_grand_prix(ROOT,args,version,GrandPrixRun)
        if request.get('mode')=='weekend_plaza':
            from swb.weekend_plaza_batch import execute_batch as execute_plaza
            from swb.weekend_plaza import WeekendPlazaRun
            return execute_plaza(ROOT,args,version,WeekendPlazaRun)
        if (request.get('panel_config') or {}).get('input')=='adb':
            from swb.ranked_adb import RankedAdbRun,receipt_only_recovery
            if request.get('ranked_park'):
                from swb.ranked_park import ParkRankedRun
                return execute_batch(ROOT,args,version,ParkRankedRun,recover_fn=receipt_only_recovery)
            return execute_batch(ROOT,args,version,RankedAdbRun,recover_fn=receipt_only_recovery)
        return execute_batch(ROOT,args,version,Run)
    except BaseException as exc:
        record_failure(root,batch,exc,'worker-startup')
        return 2

if __name__=='__main__':raise SystemExit(main())
