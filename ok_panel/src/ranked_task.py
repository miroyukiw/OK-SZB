"""Official BaseTask/ConfigCard metadata; actions bridge to the existing MAA runner."""
from ok import BaseTask,og
from qfluentwidgets import FluentIcon
from src.state import panel

class RankedTask(BaseTask):
    runner_mode='ranked'
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.name='排位模式';self.description='速攻鬼默认乐园排位 · 每日 / 每周优先';self.icon=FluentIcon.PLAY
        self.support_schedule_task=False;self.show_create_shortcut=False
        self.default_config.update({'卡组与策略':'跟随卡牌页运行卡组','排位次数':'1','无限':False,'乐园优先':True,'记录对局日志（用于后续胜率分析）':True,'恢复批次':'不恢复'})
        self.config_description['乐园优先']='速攻鬼先补每日牌桌、观战、欧丝等缺项，再从乐园打排位。周常五局计入排位次数；连续再战，整段后核对领奖。扎针龙保留原入口。'
        batches=[b for b in panel.status()['batches'] if (b.get('mode') or 'ranked')=='ranked']
        self.config_description.update({'排位次数':'任意正整数；恢复时是含已完成场次的总目标','无限':'持续排位，直到手动停止','记录对局日志（用于后续胜率分析）':'开：轮换保留行动和截图证据；关：只保留必要状态、结果、故障信息','恢复批次':'已完成场次不重打；未完成战斗暂不支持恢复'})
        self.config_type.update({'卡组与策略':{'type':'drop_down','options':['跟随卡牌页运行卡组','速攻鬼（速攻速攻）','扎针龙（跳費龍）']},'恢复批次':{'type':'drop_down','options':['不恢复']+[b['batch'] for b in batches]},
            '任务操作':{'type':'button','buttons':[{'text':'从断点恢复','callback':self.resume},{'text':'立即停止','callback':self.disable}]}})
        self.instructions='累计运行4小时后，打完当前局，关闭游戏随机休息30～60分钟，加速器保持运行；休息结束自动启动游戏继续。休息进度跨批次保存，停止按钮始终优先。策略规则下一局生效；“打完这把停止”实时生效。关闭面板不结束独立 runner。'
        self.instructions+=' 乐园优先默认开启；好友/公会对战始终跳过，跨天登录保留真实进度；进入乐园和牌桌奖励自动领取。日常刷新或休息后重新核对，平时不逐局读课题。'

    def start_runner(self,resume=False):
        if self.paused and panel.status()['running']:return self.unpause()
        choice=self.config.get('卡组与策略','跟随卡牌页运行卡组')
        profile={'速攻鬼（速攻速攻）':'nightmare_aggro','扎针龙（跳費龍）':'needle_dragon'}.get(choice)
        use_park=bool(self.config.get('乐园优先',True)) and (profile or panel.active_strategy()['profile'])=='nightmare_aggro'
        result=panel.start({'deck_profile':profile,'matches':'infinite' if self.config.get('无限') else str(self.config.get('排位次数','1')).strip(),
            'ranked_park':use_park,
            'detail_logs':bool(self.config.get('记录对局日志（用于后续胜率分析）')),
            'resume':resume,'batch':self.config.get('恢复批次')})
        self.sync();return result

    def run(self):return self.start_runner()
    def pause(self):panel.control('pause');self.sync()
    def unpause(self):
        if panel.status()['running']:panel.control('continue')
        self.sync()
    def disable(self):panel.control('stop');self.sync()
    def finish_after_match(self):
        try:panel.control('finish');self.sync()
        except Exception as e:
            from qfluentwidgets import InfoBar,InfoBarPosition
            InfoBar.warning(title='无法设置局后停止',content=str(e),parent=og.main_window,position=InfoBarPosition.TOP,duration=5000)
    def resume(self):
        try:return self.start_runner(True)
        except Exception as e:
            from qfluentwidgets import InfoBar,InfoBarPosition
            InfoBar.error(title='无法恢复',content=str(e),parent=og.main_window,position=InfoBarPosition.TOP,duration=7000)

    def sync(self):
        s=panel.status();b=next((b for b in s['batches'] if (b.get('mode') or 'ranked')=='ranked'),{})
        running=s['running'] and ((s.get('request') or {}).get('mode') or 'ranked')=='ranked'
        self._enabled=running;self._paused=s['paused'] and running;self.running=running
        reconnect=s.get('reconnect') or {}
        if running and b.get('status')!='resting' and reconnect.get('batch')==b.get('batch') and reconnect.get('status') in ('trying','failed'):
            b=dict(b,reason=reconnect.get('message'));s=dict(s,error=None)
        
        operator_stop=b.get('stop_kind') in ('user_stop','user_finish_after_match') or str((s.get('error') or {}).get('reason','')).startswith(('InterruptedError:','KeyboardInterrupt:'))
        self.info={'状态':s['activity'],'技术中断':b.get('technical_interruptions',0),'原因':str((s['error'] or {}).get('reason') or b.get('reason') or '—'),'运行卡组':panel.active_strategy()['name'],
            '已完成':b.get('completed',0),'胜 / 负':f"{b.get('wins',0)} / {b.get('losses',0)}",
            '批次':b.get('batch','—')}
        if b.get('ranked_park'):
            board=(b.get('park') or {}).get('quests') or {};tasks=board.get('tasks',[])
            weekly=next((t for t in tasks if t['id']=='park_battles'),{})
            self.info.update({'乐园每日':f"{sum(t['complete'] for t in tasks if t['scope']=='daily')} / 2",
                '乐园周常对战':f"{weekly.get('progress','—')} / {weekly.get('total',5)}",
                '乐园本周积分':str(board.get('points','—'))+'/100'})
        validation=s.get('cpu_validation')
        if validation:self.info['最近CPU验收']=str(validation.get('outcome'))+' · '+str(validation.get('turns'))+'回合 · '+str(validation.get('seconds'))+'秒'
        from ok.core.events import communicate
        communicate.task.emit(self)
        return s

