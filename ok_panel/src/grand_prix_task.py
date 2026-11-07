"""Grand Prix task card; shared stop/pause controls, separate challenge history."""
from ok import BaseTask
from qfluentwidgets import FluentIcon
from src.ranked_task import RankedTask
from src.state import panel

class GrandPrixTask(RankedTask):
    runner_mode='grand_prix'
    def __init__(self,*args,**kwargs):
        BaseTask.__init__(self,*args,**kwargs)
        self.name='大赛模式';self.description='指定系列 · 疾驰弃牌龙 · 完成当前一组';self.icon=FluentIcon.PLAY
        self.support_schedule_task=False;self.show_create_shortcut=False
        self.default_config.update({'ADB 地址':'127.0.0.1:16384','记录对局日志（用于后续胜率分析）':True,'恢复批次':'不恢复'})
        self.config_description.update({'ADB 地址':'MuMu 安卓设备地址；默认实例为 127.0.0.1:16384',
            '记录对局日志（用于后续胜率分析）':'单独记录大赛对局；不会混入排位统计','恢复批次':'只跳过已确认完成的对局；不重打结果不明的场次'})
        self.config_type.update({'恢复批次':{'type':'drop_down','options':['不恢复']},
            '任务操作':{'type':'button','buttons':[{'text':'从断点恢复','callback':self.resume},{'text':'立即停止','callback':self.disable}]}})
        self.instructions='先在 UU 开启游戏加速。每次只打当前一组，最多5战，结算后领奖并停止。支持首次免费报名或继续已报名组；收费入口会停止，请手动报名后再启动。当前适配指定系列轮次赛与“速攻速攻”，决赛尚未实测。累计运行4小时后，局间关闭游戏休息30～60分钟，保留加速器。'
    def start_runner(self,resume=False):
        if self.paused and panel.status()['running']:return self.unpause()
        result=panel.start({'mode':'grand_prix','matches':'5','adb_serial':str(self.config.get('ADB 地址','127.0.0.1:16384')).strip(),
            'detail_logs':bool(self.config.get('记录对局日志（用于后续胜率分析）',True)),
            'resume':resume,'batch':self.config.get('恢复批次')})
        self.sync();return result
    def sync(self):
        s=panel.status();b=next((b for b in s['batches'] if b.get('mode')=='grand_prix'),{})
        running=s['running'] and (s.get('request') or {}).get('mode')=='grand_prix'
        self._enabled=running;self._paused=s['paused'] and running;self.running=running
        challenge=b.get('challenge') or {};played=challenge.get('played',0);wins=challenge.get('wins',0)
        status='本局结束后停止' if running and s.get('finish_after_match') else '已暂停' if running and s['paused'] else '休息中' if running and b.get('status')=='resting' else '运行中' if running else {
            'completed':'本组已结束','waiting_entry':'等待手动入场','preview_ready':'预览完成','paused':'已停止，保留进度','interrupted':'已停止' if b.get('stop_kind')=='user_stop' else '技术中断'}.get(b.get('status'),'未启动')
        self.info={'状态':status,'原因':b.get('reason') or '—','运行卡组':'疾驰弃牌龙（ドラゴン）',
            '本组胜 / 负':f'{wins} / {max(0,played-wins)}','本次完成':b.get('completed',0),
            '奖励':'已领取' if b.get('reward_claimed') else '—','批次':b.get('batch','—')}
        from ok.core.events import communicate
        communicate.task.emit(self);return s
