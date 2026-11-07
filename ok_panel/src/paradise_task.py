"""Native park quest card; never schedules or resumes ranked games."""
from ok import BaseTask
from qfluentwidgets import FluentIcon
from src.ranked_task import RankedTask
from src.state import panel

class ParadiseTask(RankedTask):
    runner_mode='paradise'
    def __init__(self,*args,**kwargs):
        BaseTask.__init__(self,*args,**kwargs)
        self.name='乐园每日 / 每周';self.description='排除好友及公会对战 · 按实际课题进度执行';self.icon=FluentIcon.PLAY
        self.support_schedule_task=False;self.show_create_shortcut=False
        self.default_config.update({'每日课题':True,'每周课题':True,'公会你好表情':True,'只读取课题（不执行）':False,
            '本轮对局上限':'8','ADB 地址':'127.0.0.1:16384','记录对局日志（用于后续胜率分析）':True})
        self.config_description.update({'每日课题':'进入公用乐园，按需进行公开对战桌对局',
            '每周课题':'开局核对剩余局数，从乐园匹配后用再战连续完成，整段结束再核对；每日牌桌先补缺项',
            '公会你好表情':'仅在周常未完成时，向当前公会群发送一张欧丝“你好”表情；结果不明不重复发送',
            '只读取课题（不执行）':'只导航和读取，不发消息、不领取奖励、不匹配或开赛',
            '本轮对局上限':'1～8局；达到上限后先确认课题进度，再停止，不进入下一场匹配'})
        self.config_type.update({'任务操作':{'type':'button','buttons':[{'text':'立即停止','callback':self.disable}]}})
        self.instructions='每次启动扫描一轮当前课题，按真实完成度补做并领取可领积分奖励。好友/公会成员对战始终排除，不进入公会对战区或私人广场；公开匹配的对战桌属于允许项。进入乐园3天不会通过反复进出刷次数，不等待跨天。公会群只发送一次已授权的你好表情。默认不消费宝箱钥匙、不购买道具。对局日志与排位分开，停止后不会恢复排位。未知结果保留现场，重新启动时以课题和现场核对，不盲目重打。'

    def start_runner(self,resume=False):
        if self.paused and panel.status()['running']:return self.unpause()
        limit=str(self.config.get('本轮对局上限','8')).strip()
        if not limit.isdigit() or not 1<=int(limit)<=8:raise ValueError('本轮对局上限须为1～8局')
        result=panel.start({'mode':self.runner_mode,'matches':'1','park_daily':bool(self.config.get('每日课题',True)),
            'park_weekly':bool(self.config.get('每周课题',True)),'park_guild_hello':bool(self.config.get('公会你好表情',True)),
            'preview':bool(self.config.get('只读取课题（不执行）',False)),
            'park_max_battles':int(limit),
            'adb_serial':str(self.config.get('ADB 地址','127.0.0.1:16384')).strip(),
            'detail_logs':bool(self.config.get('记录对局日志（用于后续胜率分析）',True))})
        self.sync();return result

    def sync(self):
        from swb.atomic_io import read_json
        s=panel.status();b=next((b for b in s['batches'] if b.get('mode')==self.runner_mode),{})
        running=s['running'] and (s.get('request') or {}).get('mode')==self.runner_mode
        self._enabled=running;self._paused=s['paused'] and running;self.running=running
        path=panel.root/'user_data/paradise_latest.json';latest=read_json(path) if path.exists() else {}
        tasks=latest.get('tasks',[])
        request=s.get('request') or {};scope=b.get('quest_scope') or ({'daily':request.get('park_daily'),'weekly':request.get('park_weekly')} if request.get('batch')==b.get('batch') else {})
        series=b.get('park_match_plan') or {}
        self.info={'状态':s['activity'] if running else {'completed':'已选课题本轮完成','preview_ready':'只读完成','paused':'已停止','interrupted':'技术中断'}.get(b.get('status'),'未启动'),
            '原因':b.get('reason') or '—','本周积分':str(latest.get('points','—'))+'/100',
            '最近执行范围':' + '.join(name for key,name in (('daily','每日'),('weekly','每周')) if scope.get(key)) or '—',
            '每日完成':f"{sum(t['complete'] for t in tasks if t['scope']=='daily')} / 2",
            '本段匹配':f"{series.get('completed',0)} / {series['scheduled']}" if series.get('scheduled') else '—',
            '排除项':'好友 / 公会成员对战','本轮对局':b.get('completed',0),'批次':b.get('batch','—')}
        from ok.core.events import communicate
        communicate.task.emit(self);return s
