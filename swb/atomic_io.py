"""Required checkpoints: unique temporary files and bounded Windows replace retry.

No ACL changes, delete-before-replace, deferred success, or silent write failures.
"""
import json
import os
import stat
import tempfile
import time
from pathlib import Path

REPLACE_DELAYS = (0, .02, .05, .1, .2, .3, .4, .5)


def replace_required(source, target):
    source, target = Path(source), Path(target)
    for attempt, delay in enumerate(REPLACE_DELAYS, 1):
        if delay:
            time.sleep(delay)
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            readonly = (target.exists() and bool(getattr(target.stat(), 'st_file_attributes', 0)
                        & stat.FILE_ATTRIBUTE_READONLY))
            transient = getattr(exc, 'winerror', None) in (5, 32, 33) and not readonly
            if not transient or attempt == len(REPLACE_DELAYS):
                exc.add_note(f'Required replace failed: target={target}; temporary={source}; '
                             f'attempts={attempt}; winerror={getattr(exc, "winerror", None)}; '
                             'checkpoint NOT committed; temporary evidence retained')
                raise


def write_json(path, value):
    path = Path(path)
    payload = json.dumps(value, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    replace_required(name, path)


def read_json(path):
    # Close the OS handle before JSON parsing; required writes still retry if
    # another reader temporarily holds a Windows handle without delete sharing.
    payload = Path(path).read_bytes()
    return json.loads(payload.decode('utf-8-sig'))
