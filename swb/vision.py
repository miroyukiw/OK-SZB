import json
import re
import hashlib
from copy import deepcopy
from functools import wraps
from collections import OrderedDict
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from maa.resource import Resource
from maa.tasker import Tasker
from maa.pipeline import JOCR
from maa.library import Library
from swb.metrics import Metrics

ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/'assets'

def rgb(image):
    if not hasattr(image,'_swb_rgb'):image._swb_rgb=np.array(image.convert('RGB'))
    return image._swb_rgb
def gray(image):
    if not hasattr(image,'_swb_gray'):image._swb_gray=cv2.cvtColor(rgb(image),cv2.COLOR_RGB2GRAY)
    return image._swb_gray

def printed_pp(text):
    """Read the whole numerator: 6+1/8 is seven spendable PP, maximum eight."""
    value=re.search(r'(?<![\d+＋])\b(\d{1,2})(?:\s*[+＋]\s*(1))?\s*[/／]\s*(\d{1,2})\b',text)
    if not value:return None
    base,bonus,maximum=int(value[1]),int(value[2] or 0),int(value[3])
    if 1<=maximum<=10 and 0<=base<=maximum and base+bonus<=11:
        return base+bonus,maximum
    return None

class Vision:
    def __init__(self,profile=None):
        self.metrics=Metrics();self.ocr_cache=OrderedDict();self.identity_cache=OrderedDict()
        self.frame_cache=OrderedDict()
        from swb.deck_profile import active,is_nightmare
        self.profile=profile or active(ROOT)
        self.cards=json.loads((ASSETS/'cards.json').read_text(encoding='utf-8'))
        art_root=ASSETS
        board_paths=list((ASSETS/'board_art').glob('*.png'))
        if is_nightmare(self.profile):
            art_root=ASSETS/'nightmare'
            self.cards.update(json.loads((art_root/'cards.json').read_text(encoding='utf-8')))
            board_paths+=list((art_root/'board_art').glob('*.png'))
        self.turn_marker=gray(Image.open(ASSETS/'ui/pp_label.png'))
        self.health_shield=gray(Image.open(ASSETS/'ui/health_shield.png'))
        self.selection_health=[gray(Image.open(p)) for p in (ASSETS/'ui').glob('selection_health_*.png')]
        self.extra_health=[gray(Image.open(p)) for p in (ASSETS/'ui').glob('health_extra_*.png')]
        self.panel_edge=gray(Image.open(ASSETS/'ui/card_panel_edge.png'))
        self.super_crests=[gray(Image.open(p)) for p in (ASSETS/'ui').glob('super_crest_*.png')]
        self.board_super_crests=self.super_crests+[gray(Image.open(p)) for p in (ASSETS/'ui').glob('board_crest_*.png')]
        self.shield_mask=np.zeros(self.health_shield.shape,dtype=np.uint8)
        cv2.polylines(self.shield_mask,[np.array([[21,1],[40,12],[38,36],[21,53],[3,37],[1,13]])],
                      True,255,4)
        self.scenes=[]
        for name,refs in json.loads((ASSETS/'scenes.json').read_text()).items():
            for ref in refs:
                im=Image.open(ASSETS/ref['file'])
                self.scenes.append((name,[(box,gray(im.crop(box)),rgb(im.crop(box)).mean((0,1))) for box in ref['rois']]))
        if is_nightmare(self.profile):
            for name,refs in json.loads((art_root/'scenes.json').read_text(encoding='utf-8')).items():
                for ref in refs:
                    im=Image.open(art_root/ref['file'])
                    self.scenes.append((name,[(box,gray(im.crop(box)),rgb(im.crop(box)).mean((0,1))) for box in ref['rois']]))
        self.art=[]
        for p in (art_root/'art').glob('*.png'):
            im=Image.open(p)
            for size in (104,120,136,152):
                t=im.resize((size,size))
                for angle in (-8,-4,0,4,8):
                    r=t.rotate(angle,Image.Resampling.BICUBIC)
                    box=(round(size*.08),round(size*.05),round(size*.48),round(size*.52))
                    self.art.append((p.stem,size,angle,box,gray(r.crop(box))))
        self.digits=[]
        for p in (ASSETS/'numerals').glob('*.png'):
            if '.glyph' not in p.name:
                self.digits.append((int(p.stem.split('_')[0]), self.glyph(Image.open(p))))
        self.costs=[(int(p.stem),gray(Image.open(p))) for p in (ASSETS/'costs').glob('*.png') if p.stem.isdigit()]
        if is_nightmare(self.profile):
            self.costs += [(int(p.stem),gray(Image.open(p))) for p in (art_root/'costs').glob('*.png') if p.stem.isdigit()]
        self.board_refs=[];self.picker_refs=[];self.pack_refs=[];self.recent_board_patches=[]
        for p in board_paths:
            cid,form=p.stem.split('_',1)
            for size in (120,160,200,240,280,320):
                self.board_refs.append((cid,form,gray(Image.open(p).resize((size,size)))))
        self.board_refs_coarse=[
            (cid,form,idx,cv2.resize(a,(a.shape[1]//2,a.shape[0]//2)))
            for idx,(cid,form,a) in enumerate(self.board_refs)
        ]
        for p in (art_root/'art').glob('*.png'):
            for size in (105,130,155,180):
                self.picker_refs.append((p.stem,'common',gray(Image.open(p).resize((size,size)))))
        pack_art_dir=ASSETS/'pack_10010'/'art'
        if pack_art_dir.exists():
            for p in pack_art_dir.glob('*.png'):
                im=Image.open(p)
                self.pack_refs.append((p.stem,'common',gray(im)))
                for size in (105,130,155,180):
                    self.pack_refs.append((p.stem,'common',gray(im.resize((size,size)))))
        pack_board_dir=ASSETS/'pack_10010'/'board_art'
        if pack_board_dir.exists():
            for p in pack_board_dir.glob('*_evo.png'):
                cid,_=p.stem.split('_',1);im=Image.open(p)
                self.pack_refs.append((cid,'evo',gray(im)))
                for size in (105,130,155,180):
                    self.pack_refs.append((cid,'evo',gray(im.resize((size,size)))))
        self.resource=Resource()
        if not self.resource.post_bundle(str(ASSETS/'resource')).wait().succeeded:
            raise RuntimeError('Cannot load official OCR resource')
        self.tasker=Tasker()
        Library.framework().MaaTaskerBindResource(self.tasker._handle,self.resource._handle)

    def ocr(self,image,box=None):
        im=image.crop(box) if box else image
        key=(box and tuple(box),im.size,hashlib.blake2b(im.tobytes(),digest_size=12).digest())
        if key in self.ocr_cache:
            with self.metrics.span('ocr_cache_hit'):return deepcopy(self.ocr_cache[key])
        scale=2 if box else 1
        a=rgb(im.resize((im.width*scale,im.height*scale)))[:,:,::-1].copy()
        with self.metrics.span('ocr_engine'):
            detail=self.tasker.post_recognition('OCR',JOCR(expected=['.*'],threshold=.45),a).wait().get()
        if not detail or not detail.nodes:
            # Empty OCR is still a valid result for these exact pixels. Do not
            # repeatedly submit an unchanged empty label/dialog region.
            if detail is not None:
                self.ocr_cache[key]=[]
                if len(self.ocr_cache)>512:self.ocr_cache.popitem(last=False)
            return []
        rec=detail.nodes[0].recognition
        result=[r for r in rec.raw_detail.get('all',[]) if r.get('score',0)>.4] if rec else []
        for row in result: row['box']=[round(n/scale) for n in row['box']]
        if box:
            for row in result:
                row['box'][0]+=box[0]; row['box'][1]+=box[1]
        self.ocr_cache[key]=deepcopy(result)
        if len(self.ocr_cache)>512:self.ocr_cache.popitem(last=False)
        return result

    def scene(self,image):
        best=('UNKNOWN',-1.)
        for name,parts in self.scenes:
            scores=[]
            for box,t,color in parts:
                x,y,r,b=box
                a=gray(image.crop((max(0,x-6),max(0,y-10),min(1280,r+6),min(720,b+10))))
                score=float(cv2.matchTemplate(a,t,cv2.TM_CCOEFF_NORMED).max())
                # A modal dims the underlying page without changing its NCC pattern.
                if np.abs(rgb(image.crop(box)).mean((0,1))-color).mean()>38:score=0
                scores.append(score)
            score=min(scores)
            if score>best[1]: best=name,score
        return best if best[1]>.86 else ('UNKNOWN',best[1])

    def opponent_class(self,image):
        # The versus screen places our configured class at the lower left
        # and the opponent class at the lower right. Never use leader names or
        # infer an archetype. Failure is a local unknown, not a navigation gate.
        own=' '.join(r['text'] for r in self.ocr(image,(100,465,225,540)))
        expected='夜魔|夢魘|梦魇' if self.profile=='nightmare_aggro' else '[龍龙]族'
        if not re.search(expected,own):return None
        text=' '.join(r['text'] for r in self.ocr(image,(975,465,1250,545)))
        for name,pattern in (('royal','皇家'),('nightmare','夜魔|夢魘|梦魇'),
                             ('dragon','[龍龙]族'),('bishop','主教'),
                             ('rune','巫[師师]'),('forest','精[靈灵]'),
                             ('portal','[復复]仇者|超越者')):
            if re.search(pattern,text):return {'class':name,'text':text,'basis':'versus_opponent_class_text'}
        return None

    def turn(self,image):
        anchor=gray(image.crop((1105,410,1155,450)))
        if cv2.matchTemplate(anchor,self.turn_marker,cv2.TM_CCOEFF_NORMED).max()<.78 and self.pp(image)[1] is None:return None
        a=rgb(image)[285:352,1134:1196].astype(int)
        blue=((a[:,:,2]-a[:,:,0]>40)&(a[:,:,2]>100)).mean()
        red=((a[:,:,0]-a[:,:,2]>35)&(a[:,:,0]>100)).mean()
        if blue>.4:
            # Evolution/loading keeps a blue disk behind the animation, but removes its label.
            text=' '.join(r['text'] for r in self.ocr(image,(1100,265,1230,380)))
            return 'OUR' if re.search('結束|结束',text) else None
        if red>.4:
            text=' '.join(r['text'] for r in self.ocr(image,(1100,265,1230,380)))
            return 'ENEMY' if re.search('敵方|敌方|對手|对手|回合',text) else None
        return None

    def card_panel_open(self,image):
        edge=gray(image)[70:285,14:32]
        return bool(cv2.matchTemplate(edge,self.panel_edge,cv2.TM_CCOEFF_NORMED).max()>.72)

    @staticmethod
    def glyph(image):
        a=rgb(image).astype(int)
        m=(a.min(2)>145)&(a.max(2)-a.min(2)<90)
        if m.sum()<8:
            m=(a[:,:,1]>150)&(a[:,:,1]-a[:,:,0]>22)&(a[:,:,1]-a[:,:,2]>20)
        m=m.astype('uint8')*255
        n,labels,stats,_=cv2.connectedComponentsWithStats(m)
        keep=[i for i in range(1,n) if stats[i,4]>=4]
        m=np.isin(labels,keep).astype('uint8')*255
        y,x=np.nonzero(m)
        if len(x)<5:return None
        return cv2.resize(m[y.min():y.max()+1,x.min():x.max()+1],(20,28))/255

    def record_read(self,key,value,score=None,method='unread',**evidence):
        if not hasattr(self,'read_evidence'):self.read_evidence={}
        row={'value':value,'score':score,'method':method,'calibrated_probability':False,**evidence}
        self.read_evidence[key]=row
        if key.startswith('stat:'):self.last_stat_evidence=row
        return value

    def record_pp(self,result,rows,states,method):
        scores=[r.get('score') for r in rows if r.get('score') is not None]
        confidence=min(scores) if scores and method=='printed_fraction' else None
        for key,value in zip(('pp','max_pp'),result):self.record_read(key,value,confidence,method,rows=rows,pip_states=states)
        return result

    def number(self,image,box):
        g=self.glyph(image.crop(box))
        if g is None:return self.record_read("number:"+str(box),None)
        scores={}
        for n,t in self.digits:
            if t is not None:scores[n]=max(scores.get(n,0),1-float(np.abs(g-t).mean()))
        ranked=sorted(scores.items(),key=lambda x:-x[1])
        if ranked and ranked[0][1]>.8 and (len(ranked)<2 or ranked[0][1]-ranked[1][1]>.045):return self.record_read("number:"+str(box),ranked[0][0],ranked[0][1],"glyph_match",margin=ranked[0][1]-ranked[1][1] if len(ranked)>1 else None)
        crop=image.crop(box).resize(((box[2]-box[0])*3,(box[3]-box[1])*3))
        a=rgb(crop)[:,:,::-1].copy()
        detail=self.tasker.post_recognition('OCR',JOCR(expected=['^[0-9]{1,2}$'],only_rec=True,threshold=.65),a).wait().get()
        if detail and detail.nodes:
            rec=detail.nodes[0].recognition
            for row in rec.raw_detail.get('filtered',[]) if rec else []:
                if row.get('score',0)>.65 and re.fullmatch('[0-9]{1,2}',row.get('text','')):
                    return self.record_read('number:'+str(box),int(row['text']),row['score'],'ocr')
        return self.record_read('number:'+str(box),None)

    def evolution_buttons(self,image):
        a=rgb(image).astype(int)[100:680,25:430]
        r,g,b=np.moveaxis(a,2,0)
        masks={'evolve':(r>170)&(g>115)&(b<110),
               'super_evolve':(r>100)&(b>150)&(b-g>25)&(r-g>10)}
        result={}
        for name,mask in masks.items():
            # Enabled buttons have a solid bright horizontal interior; disabled countdowns do not.
            density=mask[:,10:185].mean(1) if name=='evolve' else mask[:,215:390].mean(1)
            groups=[]
            for y in np.nonzero(density>.65)[0]:
                if not groups or y>groups[-1][-1]+3:groups.append([y])
                else:groups[-1].append(y)
            groups=[g for g in groups if len(g)>10]
            if groups:
                # Pink card artwork used to impersonate Super Evolution.
                # Verify the button label inside its actual colored band.
                for group in sorted(groups,key=lambda g:g[-1],reverse=True):
                    y=100+round(sum(group)/len(group));left,right=(25,225) if name=='evolve' else (225,430)
                    labels=self.ocr(image,(left,max(100,y-28),right,min(680,y+29)))
                    valid=[]
                    for row in labels:
                        label=re.sub(r'[\s【】\[\]]','',row['text'])
                        wanted=r'(?:超(?:級|级)?)[進进]化' if name=='super_evolve' else r'[進进]化'
                        if re.fullmatch(wanted,label):valid.append(row)
                    if valid:
                        row=max(valid,key=lambda r:r.get('score',0));x,top,w,h=row['box']
                        result[name]=[round(x+w/2),round(top+h/2)];break
        return result

    def stat_number(self,image,box):
        crop=image.crop(box);a=rgb(crop).astype(int);r,g,b=np.moveaxis(a,2,0)
        colored=(g>145)&(g-r>22)&(g-b>40)
        white=(a.min(2)>175)&(a.max(2)-a.min(2)<60)
        for mask in (colored,white):
            n,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype('uint8'))
            keep=[i for i in range(1,n) if stats[i,4]>30 and stats[i,3]>15]
            if not keep:continue
            # Green glow can split a digit at its waist (12 became 1 when the
            # lower stroke of 2 was discarded). Retain nearby aligned strokes.
            if mask is colored:
                anchors=list(keep)
                for i in range(1,n):
                    x,y,w,h,area=stats[i]
                    if i in keep or area<=20 or w<=4 or h<=3:continue
                    for j in anchors:
                        ax,ay,aw,ah,_=stats[j]
                        overlap=min(x+w,ax+aw)-max(x,ax)
                        gap=max(y-(ay+ah),ay-(y+h),0)
                        if overlap>0 and gap<=4:
                            keep.append(i);break
            filtered=np.isin(labels,keep).astype('uint8')*255
            clean=Image.fromarray(filtered).convert('RGB').resize((crop.width*3,crop.height*3))
            for row in self.ocr(clean):
                if re.fullmatch(r'\d{1,2}',row['text']) and row['score']>.65:return self.record_read('stat:'+str(box),int(row['text']),row['score'],'stat_ocr')
        for row in self.ocr(crop.resize((crop.width*3,crop.height*3))):
            if re.fullmatch(r'\d{1,2}',row['text']) and row['score']>.65:return self.record_read('stat:'+str(box),int(row['text']),row['score'],'stat_ocr')
        value=self.number(image,box);evidence=self.read_evidence.get('number:'+str(box),{})
        return self.record_read('stat:'+str(box),value,evidence.get('score'),evidence.get('method','unread'))

    def evolution_enabled(self,image,point,kind):
        x,y=point
        a=rgb(image).astype(int)[max(0,y-10):y+11,max(0,x-75):x+76]
        r,g,b=np.moveaxis(a,2,0)
        m=(r>170)&(g>115)&(b<110) if kind=='evolve' else (r>100)&(b>150)&(b-g>25)&(r-g>10)
        return bool(m.mean()>.5)

    def leader_hp(self,image,side):
        # Prefer the complete normal-camera badge. A contour can split a glowing
        # two-digit value (20 -> 2) even while the whole badge is readable.
        fixed=(725,15,810,120) if side=='enemy' else (740,495,825,590)
        crop=image.crop(fixed)
        rows=[r for r in self.ocr(crop.resize((crop.width*3,crop.height*3)))
              if re.fullmatch(r'-?\d{1,2}',r['text']) and r['score']>.85]
        if rows:
            row=max(rows,key=lambda r:r['box'][2]*r['box'][3]);return self.record_read(side+'_hp',int(row['text']),row['score'],'fixed_badge_ocr')
        # Some stylized single digits (notably 7) produce no OCR row at 3x.
        # Retry the complete badge at 4x, with the same numeric/confidence rule.
        rows=[r for r in self.ocr(crop.resize((crop.width*4,crop.height*4)))
              if re.fullmatch(r'-?\d{1,2}',r['text']) and r['score']>.85]
        if rows:
            row=max(rows,key=lambda r:r['box'][2]*r['box'][3]);return self.record_read(side+'_hp',int(row['text']),row['score'],'fixed_badge_ocr_4x')
        # Locate the red badge locally: lethal-hit camera zoom moves it right/down.
        left,top,right,bottom=(720,15,865,155) if side=='enemy' else (735,485,855,620)
        a=rgb(image).astype(int)[top:bottom,left:right];r,g,b=np.moveaxis(a,2,0)
        mask=((r>95)&(r>g*1.45)&(r>b*1.25)).astype('uint8')*255
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        boxes=[]
        for c in contours:
            x,y,w,h=cv2.boundingRect(c)
            if 25<w<95 and 30<h<115 and cv2.contourArea(c)>450:
                boxes.append((x+left-5,y+top-5,x+left+w+5,y+top+h+5))
        for box in boxes:
            crop=image.crop(box)
            rows=self.ocr(crop.resize((crop.width*3,crop.height*3)))+self.ocr(image,box)
            for row in rows:
                if re.fullmatch(r'-?\d{1,2}',row['text']) and row['score']>.75:
                    return self.record_read(side+'_hp',int(row['text']),row['score'],'dynamic_badge_ocr')
        # A red animated background may merge with the badge contour. The normal
        # camera's tight badge ROI remains usable; dynamic search above handles zoom.
        fixed=(730,25,790,105) if side=='enemy' else (745,530,810,615)
        crop=image.crop(fixed)
        for row in self.ocr(crop.resize((crop.width*3,crop.height*3))):
            if re.fullmatch(r'-?\d{1,2}',row['text']) and row['score']>.90:return self.record_read(side+'_hp',int(row['text']),row['score'],'tight_badge_ocr')
        return self.record_read(side+'_hp',None)

    def enemy_leader_hp(self,image):return self.leader_hp(image,'enemy')
    def own_leader_hp(self,image):return self.leader_hp(image,'own')

    def identify_patch(self,image,box,refs):
        patch=gray(image.crop(box));key=(box[2]-box[0],hashlib.blake2b((patch//8).tobytes(),digest_size=12).digest(),id(refs))
        if key in self.identity_cache:return self.identity_cache[key]
        if refs is self.board_refs and self.recent_board_patches:
            for prev_patch,prev_ans in self.recent_board_patches:
                if prev_patch.shape==patch.shape and float(cv2.matchTemplate(patch,prev_patch,cv2.TM_CCOEFF_NORMED)[0,0])>=.92:
                    self.identity_cache[key]=prev_ans
                    return prev_ans
        scores={}
        if refs is self.board_refs and getattr(self,'board_refs_coarse',None):
            p_small=cv2.resize(patch,(max(4,patch.shape[1]//2),max(4,patch.shape[0]//2)))
            c_scores=[(float(cv2.matchTemplate(a_s,p_small,cv2.TM_CCOEFF_NORMED).max()),idx)
                      for _,_,idx,a_s in self.board_refs_coarse]
            c_scores.sort(reverse=True)
            candidate_refs=[self.board_refs[idx] for _,idx in c_scores[:24]]
        else:
            candidate_refs=refs
        for cid,form,a in candidate_refs:
            score=float(cv2.matchTemplate(a,patch,cv2.TM_CCOEFF_NORMED).max())
            if score>scores.get(cid,(-1,None))[0]:scores[cid]=(score,form)
        ranked=sorted(scores.items(),key=lambda x:-x[1][0]);answer={}
        if ranked and ranked[0][1][0]>.78 and (len(ranked)<2 or ranked[0][1][0]-ranked[1][1][0]>.04):
            cid,(score,form)=ranked[0];answer={'card_id':cid,'identity_score':round(score,3),'evolved':form=='evo'}
        self.identity_cache[key]=answer
        if len(self.identity_cache)>256:self.identity_cache.popitem(last=False)
        if refs is self.board_refs:
            self.recent_board_patches.append((patch.copy(),answer))
            if len(self.recent_board_patches)>24:self.recent_board_patches.pop(0)
        return answer

    def identify_board(self,image,unit):
        x,y=unit['xy'];return self.identify_patch(image,(x-18,y-60,x+18,y-20),self.board_refs)

    def identify_pack_card(self,image,box=None):
        if box is None:
            w,h=image.size
            box=(max(0,w//2-22),max(0,h//2-22),min(w,w//2+22),min(h,h//2+22))
        return self.identify_patch(image,box,self.pack_refs)

    def evolution_resource(self,image,kind):
        a=rgb(image).astype(int)
        xs=(548,564) if kind=='evolve' else (713,730)
        levels=[]
        for x in xs:
            p=a[529:541,x-5:x+6];r,g,b=np.moveaxis(p,2,0)
            m=(r>170)&(g>120)&(b<135) if kind=='evolve' else (r>125)&(b>155)&(b-g>22)
            levels.append(int(m.sum()))
        return {'filled':sum(n>(50 if kind=='super_evolve' else 45) for n in levels),'pixels':levels}

    def health_marks(self,image,selection=False):
        y0,y1=(239,305) if selection else (250,319)
        scores=cv2.matchTemplate(gray(image)[y0:y1,280:1080],self.health_shield,
                                cv2.TM_CCOEFF_NORMED,mask=self.shield_mask)
        scores=np.nan_to_num(scores,nan=-1,posinf=-1,neginf=-1)
        marks=[]
        for _ in range(8):
            _,score,_,(x,y)=cv2.minMaxLoc(scores)
            if score<.50:break
            hx=x+301;hy=y+y0+28
            if 320<hx<1060 and self.red_health(image,hx,hy):marks.append((hx,hy,score))
            scores[:,max(0,x-70):x+71]=-1
        if selection:
            for template in self.selection_health:
                scores=cv2.matchTemplate(gray(image)[y0:y1,280:1080],template,cv2.TM_CCOEFF_NORMED)
                for _ in range(5):
                    _,score,_,(x,y)=cv2.minMaxLoc(scores)
                    if score<.83:break
                    hx=x+280+template.shape[1]//2;hy=y+y0+template.shape[0]//2
                    if all(abs(hx-m[0])>25 for m in marks):marks.append((hx,hy,score))
                    scores[:,max(0,x-40):x+41]=-1
        return marks

    @staticmethod
    def red_health(image,x,y):
        a=rgb(image).astype(int)[y-22:y+24,x-19:x+20]
        r,g,b=np.moveaxis(a,2,0)
        # The grayscale outline resembles the attack badge; only health has a red field.
        return bool(((r>95)&(r>g*1.35)&(r>b*1.18)).sum()>180)

    def selection_outline(self,image,cx):
        """Independent legal-highlight evidence, including wide evolved frames."""
        a=rgb(image).astype(int);r,g,b=np.moveaxis(a,2,0)
        orange=(r>220)&(g>85)&(g<220)&(r-g>40)&(g-b>40)
        for half in (68,100,112):
            if not(280<=cx-half-14 and cx+half+14<=1080):continue
            left=orange[150:245,cx-half-14:cx-half+14]
            right=orange[150:245,cx+half-14:cx+half+14]
            if min(left.sum(),right.sum())<100:continue
            l=a[245:305,cx-half:cx-20];h=a[245:305,cx+20:cx+half+14]
            lr,lg,lb=np.moveaxis(l,2,0);hr,hg,hb=np.moveaxis(h,2,0)
            if ((lb>65)&(lb>lr*1.25)&(lb>lg*1.1)).sum()>=100 and ((hr>95)&(hr>hg*1.35)&(hr>hb*1.18)).sum()>=180:return True
        return False

    def highlighted_followers(self,image):
        # Each health badge stays separate even when adjacent orange outlines join.
        if self.turn(image):return []
        a=rgb(image).astype(int);targets=[]
        for hx,hy,score in self.health_marks(image,selection=True):
            cx=hx-52
            p=a[130:304,max(280,cx-80):min(1080,cx+81)];r,g,b=np.moveaxis(p,2,0)
            if ((r>220)&(g>85)&(g<220)&(r-g>40)&(g-b>40)).sum()<220:continue
            # Super frames are wider; a nearby crest supplies its actual center.
            # Purple artwork alone is not a crest: that shifts the digit crop into a neighbor.
            crest=gray(image)[110:170,hx-110:hx-15]
            sup=max(float(cv2.matchTemplate(crest,t,cv2.TM_CCOEFF_NORMED).max())
                    for t in self.super_crests)>.58
            ax=hx-125
            wide=self.stat_number(image,(ax-26,hy-28,ax+26,hy+30)) if sup else None
            sup=sup and wide is not None
            cx=hx-(64 if sup else 52)
            ax=hx-104
            attack=wide if sup else self.stat_number(image,(ax-26,hy-28,ax+26,hy+30))
            health=self.stat_number(image,(hx-26,hy-28,hx+26,hy+30))
            # A skull/ward glow can resemble the shield outline. Do not turn a
            # badge with neither number into a point 52 px left of the target.
            if attack is None and health is None and not self.selection_outline(image,cx):continue
            targets.append({'xy':[cx,215],'attack':attack,'health':health})
        # Some evolved health frames do not match the shield template. A full
        # orange portrait outline plus the two coloured stat badges independently
        # establishes a selectable follower; reading the digits is optional.
        r,g,b=np.moveaxis(a,2,0)
        orange=((r>220)&(g>85)&(g<220)&(r-g>40)&(g-b>40)).astype('uint8')
        _,_,stats,_=cv2.connectedComponentsWithStats(orange[120:310,280:1080])
        for x,y,w,h,area in stats[1:]:
            x=int(x)+280;y=int(y)+120;w=int(w);h=int(h)
            if not(115<=w<=190 and h>=100 and y<=165 and y+h>=235 and area>=700):continue
            if min(orange[155:245,x:x+14].sum(),orange[155:245,x+w-14:x+w].sum())<70:continue
            left=a[246:305,x-2:x+48];right=a[246:305,x+w-48:x+w+2]
            lr,lg,lb=np.moveaxis(left,2,0);rr,rg,rb=np.moveaxis(right,2,0)
            if ((lb>65)&(lb>lr*1.25)&(lb>lg*1.1)).sum()<100:continue
            if ((rr>95)&(rr>rg*1.35)&(rr>rb*1.18)).sum()<180:continue
            cx=x+w//2
            attack=self.stat_number(image,(x-2,246,x+48,305))
            health=self.stat_number(image,(x+w-48,246,x+w+2,305))
            targets=[u for u in targets if abs(u['xy'][0]-cx)>=45]
            targets.append({'xy':[cx,215],'attack':attack,'health':health,
                            'selection_evidence':'orange_outline_and_stat_badges'})
        return sorted(targets,key=lambda u:u['xy'][0])

    def effect_targets(self,image):
        if self.turn(image):return [],False
        a=rgb(image).astype(int)
        units=self.board(image,'enemy');targets=self.highlighted_followers(image)
        for u in units:
            x,y=u['xy'];patch=a[y-100:y+45,x-78:x+79]
            r,g,b=np.moveaxis(patch,2,0)
            yy,xx=np.mgrid[-100:45,-78:79];ring=(xx/66)**2+((yy+18)/75)**2
            orange=(r>180)&(r-g>35)&(g-b>25)&(g>70)&(ring>.8)&(ring<1.3)
            if orange.sum()>130 and all(abs(u['xy'][0]-t['xy'][0])>=45 for t in targets):targets.append(u)
        patch=a[0:150,490:790];r,g,b=np.moveaxis(patch,2,0)
        yy,xx=np.mgrid[0:150,-150:150];ring=(xx/138)**2+((yy-12)/124)**2
        orange=(r>180)&(r-g>35)&(g-b>25)&(g>70)&(ring>.82)&(ring<1.3)
        face=bool(orange.sum()>180)
        return targets,face

    def picker_cards(self,image):
        """Orange outlined cards in the game's separate effect-choice grid."""
        a=rgb(image).astype(int);r,g,b=np.moveaxis(a,2,0)
        mask=((r>200)&(g>95)&(g<215)&(r-g>35)&(g-b>35)).astype('uint8')*255
        contours,_=cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
        cards=[]
        for contour in contours:
            x,y,w,h=cv2.boundingRect(contour)
            if x<260 or y<100 or y+h>640 or not(125<=w<=205 and 170<=h<=265):continue
            cost=self.cost_at(image,(x,y,x+58,y+60))
            cx,cy=x+w//2,y+h//2
            ident=self.identify_patch(image,(cx-18,cy-16,cx+18,cy+24),self.picker_refs) or (self.identify_patch(image,(cx-18,cy-16,cx+18,cy+24),self.pack_refs) if self.pack_refs else {})
            cards.append({'xy':[cx,cy],'cost':cost,'box':[x,y,w,h],'id':ident.get('card_id')})
            if self.profile=='nightmare_aggro':
                cards[-1]['visible_attack']=self.stat_number(image,(x,y+h-44,x+48,y+h))
        return sorted(cards,key=lambda c:-(c['cost'] if c['cost'] is not None else -1))

    def discard_requirement(self,card_id):
        explicit={'10842120':1,'10641110':1,'10643110':1,'10644110':1,'10642310':1,'10844120':2}
        if str(card_id) in explicit:return explicit[str(card_id)]
        text=self.cards.get(str(card_id),{}).get('common',{}).get('skill_text','')
        text=re.sub('<[^>]+>','',text)
        match=re.search(r'[選选][擇择]自己的([1-9])[張张]手牌[，,。\s]*[捨舍][棄弃]',text)
        return int(match[1]) if match else None

    def pp(self,image):
        a=rgb(image).astype(int); states=[]
        for x in (1086,1103,1121,1138,1155,1173,1190,1207,1225,1242):
            r,g,b=a[458:465,x-3:x+4].mean((0,1))
            states.append('full' if g>150 and g-r>30 and g-b>30 else
                          'bonus' if r>210 and g>150 and r-g>20 and g-b>60 and b<150 else
                          'spent' if min(r,g,b)>90 and max(r,g,b)-min(r,g,b)<35 else 'locked')
        maximum=sum(s in ('full','spent') for s in states)
        pp=states.count('full')
        # Orange bonus pips and dim spent pips are not reliable maximum markers.
        # Prefer a complete, high-confidence printed fraction. Never match only
        # the trailing 1/8 inside 6+1/8, which used to waste the rest of the turn.
        rows=self.ocr(image,(1070,408,1260,450))
        text=' '.join(r['text'] for r in rows)
        value=printed_pp(text)
        if value and all(r.get('score',0)>=.90 for r in rows if re.search(r'\d',r['text'])):
            return self.record_pp(value,rows,states,'printed_fraction')
        # A small denominator can have low OCR confidence (or /2 becomes 12).
        # The separately read high-confidence numerator plus a matching green
        # pip count and exactly one orange bonus pip proves the spendable +1.
        # The orange pip never contributes to permanent capacity.
        if states.count('bonus')==1:
            for row in rows:
                numerator=re.fullmatch(r'(?:PP\s*)?(\d{1,2})\s*[+＋]\s*1',row['text'].strip(),re.I)
                if numerator and row.get('score',0)>=.90 and int(numerator[1])==pp and maximum:
                    return self.record_pp((pp+1,maximum),rows,states,'numerator_plus_pips')
        return self.record_pp((pp,maximum) if maximum else (None,None),rows,states,'pip_colors')

    def extra_pp(self,image):
        # The opponent's grey PP+1 at the top is deliberately outside this ROI.
        a=rgb(image).astype(int)[485:541,1083:1248];r,g,b=np.moveaxis(a,2,0)
        orange=((r>150)&(r-g>25)&(g>b+20)).mean()
        text=' '.join(x['text'] for x in self.ocr(image,(1055,481,1270,548)))
        visible=bool(re.search(r'PP\s*[+＋]\s*1',text,re.I)) or bool(orange>.38)
        enabled=bool(visible and orange>.30)
        return {'visible':visible,'available':enabled,'enabled':enabled,
                'xy':[1168,513],'orange_fraction':round(float(orange),3),'text':text}

    def intimidate_frame(self,image,xy):
        # Intimidate is a spiked/flaming outer perimeter. Ward is an inner
        # V-shaped shield; Aura is a separate untargetable-by-effect pattern.
        a=rgb(image).astype(int);x,cy=xy;counts=[]
        for l,t,r,b in [(-35,-108,36,-80),(-25,65,26,92),(-82,-55,-58,20),(58,-55,82,20)]:
            p=a[max(0,cy+t):cy+b,max(0,x+l):x+r]
            rr,g,bb=np.moveaxis(p,2,0)
            counts.append(int(((g>190)&(g-rr>-20)&(g-bb>15)).sum()))
        # The flame animation can dim one perimeter sector. Require the
        # lower plume and two other separated sectors, rather than all four.
        # Ward's inner V does not supply this three-sided outer perimeter.
        return counts[1]>150 and sum((counts[0]>65,counts[2]>150,counts[3]>150))>=2

    def hand(self,image):
        a=gray(image)[560:720,260:1210]; hits=[]
        for cid,size,angle,box,t in self.art:
            scores=cv2.matchTemplate(a,t,cv2.TM_CCOEFF_NORMED)
            for _ in range(2):
                _,score,_,xy=cv2.minMaxLoc(scores)
                if score<.79: break
                x,y=xy[0]+260-box[0],xy[1]+560-box[1]
                hits.append({'id':cid,'score':round(score,3),'size':size,'art':[x,y],
                             'xy':[round(x+size*.25),min(695,round(y+size*.35))]})
                scores[:,max(0,xy[0]-36):xy[0]+37]=0
        kept=[]
        for h in sorted(hits,key=lambda r:-r['score']):
            if all(abs(h['xy'][0]-k['xy'][0])>42 for k in kept):kept.append(h)
        pp,_=self.pp(image)
        for h in kept:
            c=self.cards.get(h['id'],{})
            cost=c.get('cost')
            h['form']='base'
            if h['id']=='10941310' and pp is not None and pp>=3:cost=3;h['form']='enhance'
            if h['id']=='10844120' and pp is not None and 3<=pp<8:cost=3;h['form']='accelerate'
            if h['id']=='10403120' and pp is not None and pp>=8:cost=8;h['form']='enhance'
            if h['id']=='10444120' and pp is not None and pp>=10:cost=10;h['form']='enhance'
            x,y=h['art']
            read=self.cost_at(image,(max(0,x-30),max(0,y-48),x+38,min(720,y+12)),compact=h.get('size',136)<110)
            if h['id']=='10941310' and h['form']=='enhance':
                effective=3
            elif h['id']=='10641310' and read==2:
                effective=2
            else:
                effective=read if read is not None else cost
            h['cost']=effective;h['cost_confidence']=self.last_cost_evidence.get('score');h['cost_evidence']=dict(self.last_cost_evidence);h['identity_score']=h['score']
            h['expected_cost']=cost
            h['kind']='spell' if h['form']=='accelerate' else c.get('type')
        return sorted(kept,key=lambda h:h['xy'][0])

    def graveyard(self,image):
        if self.profile=='nightmare_aggro':
            # Current Nightmare uses a separate red `graveyard xN` panel above
            # the blue hand/deck counters, unlike the older generic HUD.
            rows=self.ocr(image,(20,610,220,663))
            text=' '.join(r['text'] for r in rows)
            if re.search('墓場|墓场',text):
                matches=[(r,re.search(r'[xX×χ]\s*(\d{1,2})',r['text'])) for r in rows]
                for row,match in matches:
                    if match and row.get('score',0)>.65:
                        return self.record_read('graveyard',int(match[1]),row['score'],'nightmare_grave_panel',rows=rows)
                for row in rows:
                    if row['box'][0]>135 and re.fullmatch(r'\d{1,2}',row['text']) and row.get('score',0)>.85:
                        return self.record_read('graveyard',int(row['text']),row['score'],'nightmare_grave_panel',rows=rows)
        # Existing 1280x720 HUD: skull counter after hand/deck counts.
        value=self.number(image,(179,672,216,698))
        evidence=getattr(self,'read_evidence',{}).get('number:'+str((179,672,216,698)),{})
        return self.record_read('graveyard',value if value is not None and 0<=value<=99 else None,evidence.get('score'),evidence.get('method','unread'))

    def cost_at(self,image,box,compact=False):
        a=gray(image.crop(box)); candidates={}
        for n,t in self.costs:
            for scale in ((.4,.5,.6,.7,.8,1) if compact else (.8,1,1.2,1.4)):
                r=cv2.resize(t,None,fx=scale,fy=scale)
                if r.shape[0]>a.shape[0] or r.shape[1]>a.shape[1]:continue
                s=float(cv2.matchTemplate(a,r,cv2.TM_CCOEFF_NORMED).max())
                candidates[n]=max(candidates.get(n,0),s)
        ranked=sorted(candidates.items(),key=lambda t:-t[1])
        value=ranked[0][0] if ranked and ranked[0][1]>.74 and ranked[0][1]-ranked[1][1]>.035 else None
        self.last_cost_evidence={'score':ranked[0][1] if ranked else None,'margin':ranked[0][1]-ranked[1][1] if len(ranked)>1 else None,'value':value,'method':'template_match'}
        return value

    def board(self,image,side):
        a=rgb(image).astype(int); y=452 if side=='own' else 284
        strip=gray(image)[y-35:y+36,270:1060]
        scores=cv2.matchTemplate(strip,self.health_shield,cv2.TM_CCOEFF_NORMED,mask=self.shield_mask)
        scores=np.nan_to_num(scores,nan=-1,posinf=-1,neginf=-1)
        centers=[]
        for _ in range(8):
            _,score,_,(x,dy)=cv2.minMaxLoc(scores)
            if score<.50:break
            hx=x+270+21
            if self.red_health(image,hx,y):centers.append(hx)
            scores[:,max(0,x-65):x+66]=-1
        extra=[]
        for template in self.extra_health:
            scores=cv2.matchTemplate(strip,template,cv2.TM_CCOEFF_NORMED)
            for _ in range(5):
                _,score,_,(x,dy)=cv2.minMaxLoc(scores)
                if score<.88:break
                hx=x+270+template.shape[1]//2
                if self.red_health(image,hx,y) and all(abs(hx-p)>30 for p in extra):extra.append(hx)
                scores[:,max(0,x-45):x+46]=-1
        centers=[hx for hx in centers if all(abs(hx-p)>60 for p in extra)]+extra
        units=[]
        for hx in centers:
            x=hx-49; cy=400 if side=='own' else 234
            badge=a[y-23:y+25,x-70:x-25]
            br,bg,bb=np.moveaxis(badge,2,0)
            if ((bb>65)&(bb>br*1.25)&(bb>bg*1.1)).sum()<100:continue
            attack=self.stat_number(image,(x-70,y-23,x-25,y+25))
            if attack is None:
                attack=self.stat_number(image,(x-77,y-34,x-20,y+26))
            attack_confidence=getattr(self,'last_stat_evidence',{}).get('score')
            hp=self.stat_number(image,(hx-23,y-23,hx+23,y+25));health_confidence=getattr(self,'last_stat_evidence',{}).get('score')
            # Thin green/gold perimeter supplies readiness evidence independently of card names.
            patch=a[cy-100:cy+50,x-75:x+76]
            if patch.shape[:2]!=(150,151):continue
            yy,xx=np.mgrid[-100:50,-75:76]
            ring=((xx/64)**2+((yy+15)/73)**2)
            edge=(ring>.87)&(ring<1.2)
            r,gc,b=np.moveaxis(patch,2,0)
            green=(gc>140)&(gc-r>16)&(gc-b>18)
            ready=bool((green&edge).sum()>80)
            # Ward has two yellow/green V edges below the portrait, including gold-lit shields.
            sy,sx=np.mgrid[0:65,-60:61]
            edge=np.abs(np.abs(sx)-(48-sy*.73))<4
            ward=False
            for offset in (0,5,10):
                shield=a[cy+offset:cy+offset+65,x-60:x+61]
                sr,sg,sb=np.moveaxis(shield,2,0)
                hue=(sg>100)&(sr<sg*1.4)&(sb<sg*.7)
                if (hue&edge).sum()/edge.sum()>.25:ward=True;break
            units.append({'xy':[x,cy],'attack':attack,'health':hp,'ready':ready,'ward':ward,'attack_confidence':attack_confidence,'health_confidence':health_confidence})
        # Super evolution changes spacing. Do not impose a fixed equal-width slot layout.
        units=[u for u in units if 285<u['xy'][0]<1015 and
               (u['attack'] is not None or u['health'] is not None)]
        units=self.super_board(image,side,units)
        for unit in units:
            if self.intimidate_frame(image,unit['xy']):
                unit.update(intimidate=True,attackable=False,ward=False,
                            attackability_basis='intimidate_outer_frame')
        return units

    def super_board(self,image,side,units):
        # Super evolution expands the frame and moves its numbers. Its crest is stable across cards.
        cy=400 if side=='own' else 234
        a=gray(image);matches=[]
        pixels=rgb(image).astype(int)
        def wide_badge(u):
            hx=u['xy'][0]+49
            patch=pixels[cy+28:cy+81,hx-165:hx-65]
            if patch.shape[:2]!=(53,100):return False
            r,g,b=np.moveaxis(patch,2,0)
            _,_,stats,centers=cv2.connectedComponentsWithStats(((b>65)&(b>r*1.25)&(b>g*1.1)).astype('uint8'))
            # Use the main contained badge, not a clipped blue neighbour/background.
            # A larger blue animation above/right of the badge must not replace
            # a valid badge. Select by its lower, wide-frame position, not area.
            parts=[(area,px,py) for (x,y,w,h,area),(px,py) in zip(stats[1:],centers[1:])
                   if area>180 and 15<=x and x+w<100 and 16<w<60 and h>25]
            return bool(parts and (27<max(parts)[1]<55 or
                        (max(parts)[2]<18 and any(27<px<55 and py>18 for _,px,py in parts))))
        for t in self.board_super_crests:
            scores=cv2.matchTemplate(a[cy-100:cy-45,275:1050],t,cv2.TM_CCOEFF_NORMED)
            for _ in range(5):
                _,score,_,(x,y)=cv2.minMaxLoc(scores)
                if score<.68:break
                cx=x+275+t.shape[1]//2
                # Anchor the crest to a real red health badge, including shifted gold frames.
                near=min(units,key=lambda u:abs(u['xy'][0]-11-cx),default=None)
                if near and abs(near['xy'][0]-11-cx)<30 and wide_badge(near):
                    matches.append((score,near['xy'][0]-11))
                scores[:,max(0,x-75):x+76]=-1
        # Bright arenas/glow lower crest NCC. Confirm the wider attack/health
        # badge spacing before accepting a weaker crest, not the portrait color.
        pixels=rgb(image).astype(int)
        for u in units:
            hx=u['xy'][0]+49;cx=hx-60
            if not wide_badge(u):continue
            crest=a[cy-100:cy-45,cx-50:cx+50]
            score=max(float(cv2.matchTemplate(crest,t,cv2.TM_CCOEFF_NORMED).max()) for t in self.board_super_crests)
            if score>.52 and self.stat_number(image,(cx-82,cy+18,cx-32,cy+78)) is not None:
                matches.append((score,cx))
        points=[]
        for score,cx in sorted(matches,reverse=True):
            if not any(abs(cx-p)<45 for p in points):points.append(cx)
        pixels=rgb(image).astype(int)
        for x in points:
            old=next((u for u in units if abs(u['xy'][0]-x)<40),None)
            units=[u for u in units if abs(u['xy'][0]-x)>=40]
            patch=pixels[cy-100:cy+80,x-106:x+107];r,g,b=np.moveaxis(patch,2,0)
            yy,xx=np.mgrid[-100:80,-106:107]
            edge=(np.abs(xx)>82)|(yy<-75)|(yy>61)
            green=(g>140)&(g-r>16)&(g-b>18)
            gold=(r>210)&(g>150)&(b<110)&(r-g<100)
            ready=bool((green&edge).sum()>100 or (gold&edge).sum()>180)
            units.append({'xy':[x,cy],'attack':self.stat_number(image,(x-82,cy+18,x-32,cy+78)),
                          'health':self.stat_number(image,(x+32,cy+18,x+84,cy+78)),
                          'ready':ready,'ward':bool(old and old['ward']) or self.super_ward(image,x,cy),'super':True})
        return units

    def super_ward(self,image,x,cy):
        a=rgb(image).astype(int)[cy-30:cy+65,x-70:x+71]
        r,g,b=np.moveaxis(a,2,0);yy,xx=np.mgrid[-30:65,-70:71]
        edge=np.abs(np.abs(xx)-(53-(yy+15)*.64))<5
        hue=(g>120)&(r<g*1.4)&(b<g*.7)
        return bool((hue&edge).sum()/edge.sum()>.22)

def _timed_recognition(method):
    @wraps(method)
    def measured(self,*args,**kwargs):
        with self.metrics.span('recognition_'+method.__name__):return method(self,*args,**kwargs)
    return measured
for _method in ('ocr','scene','turn','pp','hand','board','picker_cards','highlighted_followers',
                'effect_targets','leader_hp','identify_board','evolution_resource','cost_at','identify_patch'):
    setattr(Vision,_method,_timed_recognition(getattr(Vision,_method)))

def _frame_cached(method,roi):
    @wraps(method)
    def cached(self,image,*args,**kwargs):
        # Exact pixels only: mutable results are copied; no stale-frame reuse.
        bounds=roi(*args,**kwargs) if callable(roi) else roi
        crop=image.crop(bounds) if bounds else image
        key=(method.__name__,args,tuple(sorted(kwargs.items())),crop.size,
             hashlib.blake2b(crop.tobytes(),digest_size=12).digest())
        if key in self.frame_cache:
            with self.metrics.span('same_frame_cache_hit'):return deepcopy(self.frame_cache[key])
        result=method(self,image,*args,**kwargs);self.frame_cache[key]=deepcopy(result)
        if len(self.frame_cache)>128:self.frame_cache.popitem(last=False)
        return result
    return cached
for _method,_roi in [('hand',(260,410,1255,720)),('board',lambda side:(160,292,1170,493) if side=='own' else (160,126,1170,327)),('highlighted_followers',(270,105,1080,320)),('turn',(1090,260,1255,470))]:
    setattr(Vision,_method,_frame_cached(getattr(Vision,_method),_roi))
