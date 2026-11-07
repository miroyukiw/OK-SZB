"""Bounded failure exit from the CPU match owned by this run. No guessed menu actions."""
from pathlib import Path
import json,re,time
import cv2
from PIL import Image
from swb.vision import gray

def concede_failed_cpu(transport,vision,out,owned_cpu):
    report={'attempted':False,'confirmed_result':False,'actions':[]}
    deadline=time.monotonic()+45
    def guard():
        if (Path(__file__).resolve().parents[1]/'STOP').exists():raise InterruptedError('User stop')
        if time.monotonic()>deadline:raise TimeoutError('Concession deadline')
    old_stop=transport.stop;transport.stop=guard
    def observe(label):
        guard();im=transport.capture();im.save(out/('failure_'+label+'.png'));return im
    def find(im,pattern,box=None):
        rows=vision.ocr(im,box)
        matches=[r for r in rows if re.fullmatch(pattern,r['text'].strip())]
        if len(matches)!=1:return None
        x,y,w,h=matches[0]['box'];return [x+w//2,y+h//2]
    def click(name,point):
        guard();report['actions'].append({'name':name,'point':point})
        (out/'concession.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        transport.gesture(point);time.sleep(1)
    try:
        if not owned_cpu:raise RuntimeError('No verified current-run CPU ownership')
        im=observe('before')
        if vision.scene(im)[0]=='RESULT':report['confirmed_result']=True;return report
        templates=[gray(Image.open(p)) for p in (Path(__file__).resolve().parents[1]/'assets/ui').glob('battle_menu*.png')]
        icon=gray(im.crop((20,20,105,95)))
        score=max(float(cv2.matchTemplate(icon,t,cv2.TM_CCOEFF_NORMED).max()) for t in templates)
        if score<.86:raise RuntimeError('Battle menu icon not recognized')
        report.update(attempted=True,menu_score=score)
        point=None
        for attempt in range(2):
            click('battle_menu',[60,58]);im=observe('menu'+str(attempt))
            point=find(im,'放棄|放弃|投降|認輸|认输',(480,105,800,188))
            if point:break
            # In selection mode the first tap cancels selection, not opening
            # the menu. Retry only after observing a restored battlefield.
            if attempt or vision.turn(im) not in ('OUR','ENEMY'):break
            icon=gray(im.crop((20,20,105,95)))
            if max(float(cv2.matchTemplate(icon,t,cv2.TM_CCOEFF_NORMED).max()) for t in templates)<.86:break
            report['selection_cancelled_before_menu']=True
        if not point:raise RuntimeError('Concede menu control not recognized')
        click('concede',point);im=observe('confirm')
        text=' '.join(r['text'] for r in vision.ocr(im,(400,290,930,405)))
        if not re.search('放棄|放弃|投降',text):raise RuntimeError('Concede confirmation not recognized')
        point=find(im,'放棄|放弃|投降|確認|确定',(640,510,930,600))
        if not point:raise RuntimeError('Concede confirmation button absent')
        click('confirm_concede',point)
        continued=False
        while time.monotonic()<deadline:
            im=observe('result')
            if vision.scene(im)[0]=='RESULT':report['confirmed_result']=True;break
            text=' '.join(r['text'] for r in vision.ocr(im))
            if not continued and re.search(r'\b(LOSE|DEFEAT)\b',text,re.I):
                click('continue_result',[640,550]);continued=True
            else:time.sleep(1)
        if not report['confirmed_result']:report['error']='Result not observed within bounded concession'
    except Exception as exc:report['error']=f'{type(exc).__name__}: {exc}'
    finally:
        transport.stop=old_stop
        (out/'concession.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report
