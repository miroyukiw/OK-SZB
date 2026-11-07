"""Evidence-based network recovery; no UNKNOWN-scene or elapsed-turn heuristic.

The batch owns the window for the entire transaction. A failed match is archived
only after the adapter proves that both Android apps restarted and the game is
back at a safe lobby. It never creates a win/loss for that interrupted attempt.
"""
import re
import time
from dataclasses import dataclass


class NetworkStalled(RuntimeError):
    pass


class RecoveryBlocked(RuntimeError):
    pass


def settled_match_notice(text):
    # OCR rows are ordered by x, not reading order; the second line may be first.
    return bool(re.search(r'上次.{0,8}(?:對戰|对战)',text)
                and re.search(r'勝利|胜利|結束|结束',text))


def battle_reentry(rows):
    """The explicit existing-match return dialog, never a new queue action."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not (re.search(r'是否要?重返[對对][戰战]',text)
            and re.search(r'(?:先前|之前).{0,12}[對对][戰战]',text)
            and re.search(r'[關关][閉闭].{0,6}[應应]用(?:程式|程序)',text)):
        return None
    buttons=[r for r in rows if re.fullmatch(r'重返(?:[對对][戰战])?',re.sub(r'\s+','',r['text']))
        and r.get('score',1)>=.75 and 650<=r['box'][0]+r['box'][2]/2<=1030
        and 350<=r['box'][1]+r['box'][3]/2<=620]
    return {'button':buttons[0] if len(buttons)==1 else None,'text':text}


def server_adjudication(rows):
    """Only the server's explicit already-adjudicated defeat, not an error."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not re.search(r'判定.{0,6}[為为][敗败]北',text):
        return None
    if not (re.search(r'[對对][戰战]',text) and re.search(r'[結结]束|[敗败]北|上次',text)):
        return None
    buttons=[r for r in rows if re.fullmatch(r'返回[標标][題题][畫画]面|OK|確[認认]|確[定]',re.sub(r'\s+','',r['text']),re.I)
        and r.get('score',1)>=.75 and 250<=r['box'][0]+r['box'][2]/2<=1030
        and 350<=r['box'][1]+r['box'][3]/2<=620]
    return {'outcome':'loss','button':buttons[0] if len(buttons)==1 else None,'text':text}


def park_reentry(rows):
    """Restore the previous park session, distinct from an ongoing match."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not (re.search(r'(?:遊玩|游玩).{0,16}[樂乐][園园]',text)
            and re.search(r'[關关][閉闭].{0,6}[應应]用(?:程式|程序)',text)
            and re.search(r'是否要?重返(?:遊戲|游戏)',text)):
        return None
    buttons=[r for r in rows if re.fullmatch(r'重返(?:遊戲|游戏)?',re.sub(r'\s+','',r['text']))
        and r.get('score',1)>=.75 and 650<=r['box'][0]+r['box'][2]/2<=1030
        and 350<=r['box'][1]+r['box'][3]/2<=620]
    return {'button':buttons[0] if len(buttons)==1 else None,'text':text}




def private_reentry(rows):
    """The abandoned private room prompt. Always choose 'Do not return' to reach lobby."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not (re.search(r'私人[對对][戰战]|不重返.*[主頁主页][面]',text) and re.search(r'不重返',text)):
        return None
    buttons=[r for r in rows if '不重返' in re.sub(r'\s+','',r['text'])
        and 300<=r['box'][0]+r['box'][2]/2<=650
        and 450<=r['box'][1]+r['box'][3]/2<=650]
    return {'button':buttons[0] if buttons else None,'text':text}

def navigation_modal(rows):
    """A visible central dialog owns navigation even when START is behind it."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if battle_reentry(rows) or park_reentry(rows) or private_reentry(rows) or server_adjudication(rows) or network_evidence(text):return True
    headings=[r for r in rows if re.fullmatch(r'確認|确认|確定|确定|錯誤|错误|通知|重返[對对][戰战]|Confirm|Error',
        re.sub(r'\s+','',r['text']),re.I)
        and 250<=r['box'][0]+r['box'][2]/2<=1030
        and 80<=r['box'][1]+r['box'][3]/2<=300]
    return bool(headings or settled_match_notice(text))


def confirmation_button(rows):
    """Dismiss an observed settled-match notice, never an arbitrary permission."""
    text=''.join(re.sub(r'\s+','',r['text']) for r in rows)
    if not settled_match_notice(text):return None
    candidates=[r for r in rows if re.fullmatch(r'OK|確認|确认|確定|确定|重返(?:對戰|对战|遊戲|游戏)?',
        re.sub(r'\s+','',r['text']),re.I)
        and 250<=r['box'][0]+r['box'][2]/2<=1030
        and 350<=r['box'][1]+r['box'][3]/2<=620]
    return candidates[0] if len(candidates)==1 else None


class ReconnectionExhausted(RecoveryBlocked):
    pass


class RecoveredToLobby(NetworkStalled):
    """Recovery succeeded, but the old combat stack must not be replayed."""


def reconnect_five(attempt,stop,progress):
    """The only numbered retry owner; adapters perform one attempt, never recurse."""
    failures=[]
    for number in range(1,6):
        stop();progress(number,'trying',f'正在尝试重新连接 {number}/5')
        try:
            result=attempt(number);stop()
            if not result or result.get('mode') not in ('continue','safe_lobby'):
                raise RecoveryBlocked('Fresh observations did not verify recovery')
        except (InterruptedError,KeyboardInterrupt):raise
        except Exception as exc:
            failures.append(f'{type(exc).__name__}: {exc}')
            progress(number,'failed',failures[-1])
        else:
            result.update(attempts=number,reconnect_verified=True)
            progress(number,'recovered',f'重新连接成功 {number}/5')
            return result
    message='重新连接失败 5/5; '+' | '.join(f'{i+1}/5 {reason}' for i,reason in enumerate(failures))
    progress(5,'exhausted',message)
    raise ReconnectionExhausted(message)


def dialog_button(rows):
    """Only a network/error dialog with a unique, centrally located known action."""
    text=' '.join(row['text'] for row in rows)
    if not network_evidence(text):return None
    priorities=(r'重[試试連连]|重返(?:對戰|对战|遊戲|游戏)?|重新[連连][線线接]|[再重]連接|再接続|Retry|Reconnect',
                r'確[認认定]|确认|确定|OK',r'(?:返回|回到)[標标][題题](?:[畫画]面)?')
    for pattern in priorities:
        candidates=[]
        for row in rows:
            x,y,w,h=row['box']
            if 250<=x+w/2<=1030 and 200<=y+h/2<=620 and re.fullmatch(pattern,re.sub(r'\s+','',row['text']),re.I):
                candidates.append(row)
        if len(candidates)==1:return candidates[0]
        if len(candidates)>1:return None
    return None


def publish_reconnect(root,out,number,status,message,batch=None):
    from pathlib import Path
    from datetime import datetime,timezone
    from swb.atomic_io import write_json,read_json
    root=Path(root);out=Path(out)
    row={'time':datetime.now(timezone.utc).isoformat(),'attempt':number,'limit':5,
         'status':status,'message':message,'batch':batch,'run':str(out)}
    write_json(out/'reconnect_status.json',row)
    with (out/'reconnect_events.jsonl').open('a',encoding='utf-8') as stream:
        import json
        stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    write_json(root/'user_data/reconnect_status.json',row)
    if batch:
        path=root/'batches'/batch/'status.json'
        if path.exists():
            state=read_json(path)
            state.setdefault('reconnect_prior_status',state.get('status','running'))
            state.update(reconnect=row,reason=message,status='recovering' if status in ('trying','failed') else 'interrupted' if status=='exhausted' else state['reconnect_prior_status'])
            write_json(path,state)
    latest=root/'user_data/worker_error.json';previous=out/'reconnect_previous_worker_error.json'
    if latest.exists() and not previous.exists():write_json(previous,read_json(latest))
    # Compatibility for the already-open panel's latest-status reader.
    write_json(latest,{'stage':'reconnect','batch':batch,'reason':message,'is_error':status=='exhausted','reconnect':row})


def network_evidence(text):
    text=re.sub(r'\s+', '', text).lower()
    context=[{'text':text,'box':[0,0,0,0]}]
    if battle_reentry(context) or server_adjudication(context):return 'match_recovery_dialog'
    error=re.search(r'(?:伺服器|服务器|網路|网络|通信|連線|连线|連接|连接|接続|connection|network|server).{0,24}(?:錯誤|错误|失敗|失败|中斷|中断|異常|异常|逾時|逾时|超時|超时|timeout|timedout|error|failed|lost|切断)',text)
    recovery=re.search(r'重試|重试|重返|再試|再试|retry|返回.{0,6}[標标题題]|重新連|重新连|reconnect',text)
    # Require both the explicit failure and its recovery UI. Card text, an
    # unfamiliar scene, an opponent thinking, or plain loading are insufficient.
    error=error or re.search(r'(?:发生|發生|通信|接続|サーバー).{0,12}(?:错误|錯誤|エラー)',text)
    recovery=recovery or re.search(r'重新[連连]|再接続|確認|确定|確認|ok',text)
    return 'network_error_dialog' if error and recovery else None


@dataclass
class NetworkWatch:
    grace_seconds: float=30.0
    minimum_samples: int=3
    since: float | None=None
    samples: int=0
    last_sample: float | None=None

    def reset(self):
        self.since=None;self.samples=0;self.last_sample=None

    def sample(self,text,now):
        evidence=network_evidence(text)
        if not evidence:
            self.reset();return 'clear'
        # A paused process or long gap between observations isn't evidence of
        # continuous failure. Require a new consecutive observation window.
        if self.last_sample is not None and now-self.last_sample>5:
            self.reset()
        if self.since is None:self.since=now
        self.last_sample=now;self.samples+=1
        if self.samples>=self.minimum_samples and now-self.since>=self.grace_seconds:
            return 'stalled'
        return 'suspect'


def restart_sequence(adapter,stop,event):
    """Adapter implements only the two allowlisted Android apps and window UI.

    Stop is checked around every side effect, including after an OS command
    returns. Never clear STOP here, and never run concurrently with a match.
    """
    for stage,action in (
        ('close_game',adapter.close_game),
        ('close_android_uu',adapter.close_uu),
        ('launch_android_uu',adapter.launch_uu),
        ('accelerate_shadowverse',adapter.accelerate),
        ('launch_game',adapter.launch_game),
        ('verify_safe_lobby',adapter.wait_lobby),
    ):
        stop();event(stage,'starting')
        try:action();stop()
        except BaseException:
            event(stage,'failed');raise
        event(stage,'completed')
    return {'apps_restarted':True,'acceleration_verified':True,'safe_lobby':True,
            'interrupted_outcome':'unknown','replay_pending':False}
