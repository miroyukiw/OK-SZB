"""Bound detailed panel evidence without touching pre-panel historical runs."""
import json
from pathlib import Path

class Evidence:
    def __init__(self,out,enabled=True,limit_mb=256):
        self.out=Path(out);self.enabled=enabled;self.limit=int(limit_mb)*1024*1024
        self.images=[];self.bytes=0;self.pruned=0
    def image(self,image,name):
        if not self.enabled:return
        p=self.out/name;image.save(p,compress_level=1)
        size=p.stat().st_size;self.images.append((p,size));self.bytes+=size
        while self.bytes>self.limit and len(self.images)>1:
            old,n=self.images.pop(0);old.unlink(missing_ok=True);self.bytes-=n;self.pruned+=1
    def event(self,row):
        if not self.enabled:return
        p=self.out/'events.jsonl'
        if p.exists() and p.stat().st_size>16*1024*1024:
            p.replace(self.out/'events.previous.jsonl')
        with p.open('a',encoding='utf-8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')

def prune(root,budget_mb=2048):
    """Only detailed files of completed panel-owned runs, never receipts/status."""
    from .deck_profile import active
    if active(root)=='nightmare_aggro':return 0  # Preserve original logs; independent audit never prunes.
    files=[]
    for marker in (Path(root)/'runs').glob('*/panel_evidence.json'):
        if not (marker.parent/'summary.json').exists():continue
        files.extend(p for p in marker.parent.iterdir() if p.suffix=='.png' or p.name in ('events.jsonl','events.previous.jsonl'))
    files.sort(key=lambda p:p.stat().st_mtime)
    total=sum(p.stat().st_size for p in files);deleted=0
    for p in files:
        if total<=budget_mb*1024*1024:break
        total-=p.stat().st_size;p.unlink();deleted+=1
    return deleted
