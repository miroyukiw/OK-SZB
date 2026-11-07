"""Per-phase timing. Nested spans are exclusive; detail rows retain inclusive totals."""
from contextlib import contextmanager
from collections import defaultdict
from time import perf_counter
import numpy as np

class Metrics:
    def __init__(self):
        self.values=defaultdict(float);self.counts=defaultdict(int);self.stack=[]
        self.samples=defaultdict(list);self.inclusive=defaultdict(list)
    @contextmanager
    def span(self,name):
        start=perf_counter();item=[name,start,0.];self.stack.append(item)
        try:yield
        finally:
            elapsed=perf_counter()-start;self.stack.pop()
            self.values[name]+=elapsed-item[2];self.counts[name]+=1
            self.samples[name].append(elapsed-item[2]);self.inclusive[name].append(elapsed)
            if self.stack:self.stack[-1][2]+=elapsed
    def checkpoint(self):
        return (perf_counter(),dict(self.values),dict(self.counts))
    def since(self,mark):
        started,values,counts=mark
        delta={k:max(0,v-values.get(k,0)) for k,v in self.values.items()}
        groups={'recognition':lambda k:k.startswith('recognition_') or k=='ocr_engine',
                'planning':lambda k:k=='decision','input':lambda k:k=='input',
                'animation_confirmation':lambda k:k=='animation_wait' or k=='receipt_attack',
                'logging':lambda k:k in ('log_io','audit_io','audit_image_io','evidence_png'),
                'capture':lambda k:k in ('capture','maa_capture_wait','maa_transfer','capture_color_convert','frame_normalize')}
        phases={name:round(sum(v for k,v in delta.items() if predicate(k)),6) for name,predicate in groups.items()}
        wall=perf_counter()-started;phases['other']=round(max(0,wall-sum(phases.values())),6)
        return {'seconds':round(wall,6),'phases':phases,'calls':{k:self.counts[k]-counts.get(k,0) for k in self.counts if self.counts[k]!=counts.get(k,0)}}
    def report(self):
        return {k:{'seconds':round(v,3),'calls':self.counts[k],
                   'median_ms':round(float(np.median(self.samples[k]))*1000,3),
                   'p95_ms':round(float(np.percentile(self.samples[k],95))*1000,3),
                   'inclusive_seconds':round(sum(self.inclusive[k]),3)} for k,v in self.values.items()}
