"""16-player weekend plaza card; closed-entry policy is always stop."""
from ok import BaseTask
from qfluentwidgets import FluentIcon
from src.ranked_task import RankedTask
from src.state import panel

class WeekendPlazaTask(RankedTask):
    runner_mode='weekend_plaza'
    def __init__(self,*args,**kwargs):
        BaseTask.__init__(self,*args,**kwargs)
        self.name='周末广场赛（16人）';self.description='16人指定系列 · 未开放即停止 · 实测页面已接入';self.icon=FluentIcon.PLAY
        self.support_schedule_task=False;self.show_create_shortcut=False
        self.default_config.update({'ADB 地址':'127.0.0.1:16384','赛事内等待上限（分钟）':'30',
            '记录对局日志（用于后续胜率分析）':True,'恢复批次':'不恢复'})
        self.config_description.update({'ADB 地址':'MuMu 安卓设备；默认实例地址127.0.0.1:16384',
            '赛事内等待上限（分钟）':'1～60分钟；只用于已报名后的等人及轮间等待，不等待尚未开放的活动',
            '恢复批次':'只从已确认的上一轮之后继续；结果不明的对局不会重打',
            '记录对局日志（用于后续胜率分析）':'广场赛单独记录，不计入排位或大赛胜率'})
        self.config_type.update({'恢复批次':{'type':'drop_down','options':['不恢复']},
            '任务操作':{'type':'button','buttons':[{'text':'从断点恢复','callback':self.resume},{'text':'立即停止','callback':self.disable}]}})
        self.instructions='每次只参加当前一场16人周末赛，最多4轮；只报名已核对的16人指定系列，不参加8人赛或私人赛。未开放时显示下次时间并停止，不后台等开赛。比赛按赛事轮次继续，不因单局败北提前退出。局后停止只停止操作，不会自动退赛，已报名状态需人工留意。累计运行4小时后的休息安排在整场赛结束后，只关闭游戏，保留UU。报名、准备、轮间等待、弃权判胜和结束页已按真实赛事接入；完整自动连跑仍待下次名额复验。赛事奖励发送至礼物箱，结束后核对领取；任务不会批量领取其他礼物。无法确认的新页面会保存并停止。'
    def start_runner(self,resume=False):
        if self.paused and panel.status()['running']:return self.unpause()
        text=str(self.config.get('赛事内等待上限（分钟）','30')).strip()
        if not text.isdigit() or not 1<=int(text)<=60:raise ValueError('赛事内等待上限须为1～60分钟')
        result=panel.start({'mode':self.runner_mode,'players':16,'matches':'4','wait_open':False,
            'plaza_wait_minutes':int(text),'adb_serial':str(self.config.get('ADB 地址','127.0.0.1:16384')).strip(),
            'detail_logs':bool(self.config.get('记录对局日志（用于后续胜率分析）',True)),
            'resume':resume,'batch':self.config.get('恢复批次')})
        self.sync();return result
    def sync(self):
        s=panel.status();b=next((b for b in s['batches'] if b.get('mode')==self.runner_mode),{})
        running=s['running'] and (s.get('request') or {}).get('mode')==self.runner_mode
        self._enabled=running;self._paused=s['paused'] and running;self.running=running
        status='本局结束后停止' if running and s.get('finish_after_match') else '已暂停' if running and s['paused'] else '休息中' if running and b.get('status')=='resting' else '运行中' if running else {
            'not_open':'未开放，已停止','unavailable':'当前无法报名，已停止','completed':'赛事已结束','preview_ready':'预览完成',
            'paused':'已停止，保留轮次','interrupted':'已按要求停止' if b.get('stop_kind')=='user_stop' else '技术中断'}.get(b.get('status'),'未启动')
        opening=b.get('next_open')
        self.info={'状态':status,'原因':b.get('reason') or '—','下次开放':opening.replace('T',' ') if opening else '以游戏内时间为准',
            '目标赛制':'16人 · 指定系列','已完成轮次':b.get('completed',0),
            '胜 / 负':f"{b.get('wins',0)} / {b.get('losses',0)}",'批次':b.get('batch','—')}
        from ok.core.events import communicate
        communicate.task.emit(self);return s
