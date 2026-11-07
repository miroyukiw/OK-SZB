"""Heartbeat-only publication. Transaction/checkpoint writes remain fail-fast."""
import json
import os
import stat
import tempfile
import time
from pathlib import Path


class HeartbeatPublisher:
    def __init__(self,path,report,guard=lambda:None,max_defer_seconds=3.0):
        self.path=Path(path);self.report=report;self.guard=guard
        self.max_defer_seconds=max_defer_seconds;self.pending=None
        self.first_failure=None;self.deferred=0;self.recovered=0;self.last_error=None
        self.total_deferred=0

    def state(self):
        return {'deferred_observations':self.total_deferred,'recovered_incidents':self.recovered,
                'unpublished_latest':str(self.pending) if self.pending else None,
                'last_error':self.last_error}

    def details(self,exc,data,attempts):
        return {'target':str(self.path),'temporary':str(self.pending),'sample_time':data.get('time'),
                'attempts':attempts,'winerror':getattr(exc,'winerror',None),'errno':exc.errno,
                'error':str(exc),'stage':'replace','deferred_observations':self.deferred}

    def publish(self,data):
        self.guard()
        # Each publisher owns a unique temporary path. Never overwrite another
        # writer's fixed .tmp file or change any ACL/attribute/share settings.
        if self.pending is None:
            fd,name=tempfile.mkstemp(prefix='heartbeat.',suffix='.tmp',dir=self.path.parent)
            self.pending=Path(name)
            with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,indent=2)
        else:
            self.pending.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        # Failure to create/write telemetry is not an ambiguous rename conflict:
        # it propagates immediately. Only replace-time 5/32/33 may be transient.
        for attempt,delay in enumerate((0,.02,.05),1):
            self.guard()
            if delay:time.sleep(delay);self.guard()
            try:os.replace(self.pending,self.path)
            except OSError as exc:
                code=getattr(exc,'winerror',None)
                readonly=bool(getattr(self.path.stat(),'st_file_attributes',0)&stat.FILE_ATTRIBUTE_READONLY) if self.path.exists() else False
                self.last_error=self.details(exc,data,attempt)
                if code not in (5,32,33) or readonly:
                    self.report('heartbeat_io_failed',**self.last_error,will_retry=False)
                    raise
                if self.first_failure is None:self.first_failure=time.monotonic()
                if time.monotonic()-self.first_failure>=self.max_defer_seconds:
                    self.report('heartbeat_io_failed',**self.last_error,will_retry=False,
                                reason='bounded publish retry exhausted; persistent access error requires diagnosis')
                    raise
                if attempt<3:continue
                self.deferred+=1;self.total_deferred+=1
                # The journal is required: if recording this failure also fails,
                # propagate it instead of continuing without an audit trail.
                self.report('heartbeat_io_deferred',**self.details(exc,data,attempt),
                            will_retry=True,max_defer_seconds=self.max_defer_seconds)
                return False
            else:
                self.pending=None
                if self.first_failure is not None:
                    self.recovered+=1
                    self.report('heartbeat_io_recovered',target=str(self.path),sample_time=data.get('time'),
                                deferred_observations=self.deferred,last_error=self.last_error)
                    self.first_failure=None;self.deferred=0
                return True
