"""Per-copy hand memory; current pixels remain authoritative for costs."""
from copy import deepcopy
from . import nightmare as N

class HandMemory:
    def __init__(self):
        self.cards=[];self.serial=0;self.removed=set();self.buff_receipts=set()

    def consumed(self,iid):self.removed.add(iid)

    def buff(self,iid,receipt):
        if receipt in self.buff_receipts:return
        self.buff_receipts.add(receipt)
        for c in self.cards:
            if c['iid']==iid:c['attack_bonus']=c.get('attack_bonus',0)+3

    def observe(self,hand):
        old=[c for c in self.cards if c['iid'] not in self.removed];self.removed.clear()
        new=deepcopy(hand);used=set();same=[N.cid(c) for c in old]==[N.cid(c) for c in new]
        for i,c in enumerate(new):
            candidates=[(j,p) for j,p in enumerate(old) if j not in used and N.cid(p)==N.cid(c)]
            previous=None
            if same:used.add(i);previous=old[i]
            elif len(candidates)==1 and sum(N.cid(x)==N.cid(c) for x in new)==1:
                j,previous=candidates[0];used.add(j)
            # After an ambiguous missing copy, never transfer its +3 to another
            # copy by name. Visible attack can still establish its real value.
            if previous is None:
                self.serial+=1;c['iid']=f'h{self.serial}';c['attack_bonus']=0
            else:
                c['iid']=previous['iid'];c['attack_bonus']=previous.get('attack_bonus',0)
            base=N.STATS.get(N.cid(c),(0,0,0))[1]
            visible=c.get('visible_attack')
            c['attack']=visible if visible is not None else base+c['attack_bonus']
            c['attack_basis']='visible' if visible is not None else 'printed_plus_confirmed_instance_buff'
            # The recognition pipeline already reads each cost independently.
            # A missing Garodeth cost is not replaced with 8 or an HP guess.
            c['cost_basis']='visible' if c.get('cost') is not None else 'unread'
        self.cards=deepcopy(new);return new

    def picker_instance(self,picked,candidates):
        ident=N.cid(picked)
        matches=[c for c in self.cards if N.cid(c)==ident]
        if picked.get('cost') is not None:matches=[c for c in matches if c.get('cost')==picked['cost']]
        if picked.get('visible_attack') is not None:matches=[c for c in matches if c.get('attack')==picked['visible_attack']]
        if len(matches)==1:return matches[0]['iid']
        # An inline hand target retains its observed position; a separate grid
        # has no verified ordering contract. Do not guess a duplicate's slot.
        if 'art' in picked:
            close=[c for c in matches if c.get('xy') and abs(c['xy'][0]-picked['xy'][0])<20]
            if len(close)==1:return close[0]['iid']
        return None
