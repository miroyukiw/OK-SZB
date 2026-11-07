"""Necessary worker fault evidence, independent of optional detailed game logs."""
import json
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from swb.atomic_io import write_json


def record_failure(root, batch, exc, stage, secondary=()):
    row = {'batch': batch, 'time': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(),
           'stage': stage, 'reason': f'{type(exc).__name__}: {exc}',
           'traceback': ''.join(traceback.format_exception(exc)), 'secondary_errors': list(secondary)}
    print(json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True)
    directory = Path(root) / 'user_data/worker_errors'
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
        # Exclusive new file needs no replacement of a possibly held old file.
        with path.open('x', encoding='utf-8') as stream:
            json.dump(row, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        row['diagnostic_file'] = str(path)
        write_json(Path(root) / 'user_data/worker_error.json', row)
        # Keep a bounded recent archive; current batch interruption remains durable.
        # Historical error evidence is retained; no automatic deletion.
    except Exception:
        print('Failed to save diagnostic (original failure above):', file=sys.stderr)
        traceback.print_exc()
    return row


class BoundedConsole:
    """Worker-owned console; closing the panel cannot break a worker pipe."""
    encoding='utf-8'
    def __init__(self,path):self.path=Path(path)
    def write(self,text):
        from swb.atomic_io import replace_required
        payload=text.encode('utf-8',errors='backslashreplace')
        for start in range(0,len(payload),8192):
            block=payload[start:start+8192]
            if self.path.exists() and self.path.stat().st_size+len(block)>1024*1024:
                replace_required(self.path,self.path.with_suffix('.previous.log'))
            with self.path.open('ab') as stream:stream.write(block)
        return len(text)
    def flush(self):pass
    def isatty(self):return False


def install_worker_console(root):
    path=Path(root)/'logs/worker-launch.log';path.parent.mkdir(parents=True,exist_ok=True)
    sys.stdout=sys.stderr=BoundedConsole(path)
