"""Official my_app + CustomTab extension points; upstream main window is unmodified."""
from PySide6.QtCore import QObject,QTimer
from PySide6.QtWidgets import QWidget
from qfluentwidgets import FluentIcon,TitleLabel,CaptionLabel,InfoBar,InfoBarPosition,PushButton
from ok import og
from ok.ui.qt.widget.CustomTab import CustomTab
from src import state

class BusinessTab(CustomTab):
    key='';label='';icon=FluentIcon.INFO
    def __init__(self):
        super().__init__();self.box=self.vBoxLayout
        if state.controller is None:
            from src.card_views import EditorController
            state.controller=EditorController(state.panel)
        state.controller.pages[self.key]=self
        getattr(state.controller,'build_'+self.key)()
    @property
    def name(self):return self.label
    def heading(self,title,subtitle):
        self.box.addWidget(TitleLabel(title));sub=CaptionLabel(subtitle);sub.setWordWrap(True);self.box.addWidget(sub)

class CardsTab(BusinessTab):key='cards';label='卡牌与卡组';icon=FluentIcon.BOOK_SHELF
class SourceTab(BusinessTab):key='source';label='策略编辑';icon=FluentIcon.EDIT
class WindowTab(BusinessTab):key='settings';label='MAA 窗口';icon=FluentIcon.APPLICATION

class ExternalStartController:
    """Delegate starts to the existing runner instead of upstream input/capture engines."""
    starting=False
    def start(self,task=None,exit_after=False):
        if isinstance(task,int):task=og.executor.onetime_tasks[task]
        if task is None:return  # Only the visible task Start button launches this runner.
        try:task.start_runner()
        except Exception as e:InfoBar.error(title='无法开始',content=str(e),parent=og.main_window,position=InfoBarPosition.TOP,duration=7000)

class Globals(QObject):
    def __init__(self,exit_event):
        super().__init__();self.exit_event=exit_event
        from ok.util.GlobalConfig import basic_options
        options=og.global_config.get_config(basic_options)
        options['Start/Stop']='None';options['Auto Start Game When App Starts']=False
    def on_show_main_window(self,window):
        # my_app is an upstream documented callback. Keep its real MainWindow/TaskCard/Settings.
        state.controller.initialize(window)
        from src.capture_page import CapturePage
        self.capture_page=CapturePage(window)
        og.app.start_controller=ExternalStartController()
        # This project uses an external MAA runner. Do not start ok-script's device discovery/runtime.
        def ui_runtime_only():
            from ok.core.events import communicate
            communicate.start_success.emit();return True
        og.ok.start_runtime=ui_runtime_only
        task=og.executor.onetime_tasks[0];self.task=task
        tab=window.onetime_tab
        original_update=tab.update_task_info
        def update_task_info(task):
            original_update(task)
            tab.task_info_container.titleLabel.setText('运行状态')
        tab.update_task_info=update_task_info
        window.switchTo(window.start_tab)
        self.timer=QTimer(self);self.timer.timeout.connect(self.refresh);self.timer.start(1000);self.refresh()
    def refresh(self):
        try:
            tasks=og.executor.onetime_tasks;s=state.panel.status()
            mode=(s.get('request') or {}).get('mode') if s['running'] else (s['batches'][0].get('mode') if s['batches'] else None)
            self.task=next((t for t in tasks if getattr(t,'runner_mode','ranked')==(mode or 'ranked')),tasks[0])
            for t in tasks:t.sync()
            w=og.main_window
            tab=w.onetime_tab
            tab.last_task=self.task;tab.current_info_run=(id(self.task),'MAA');tab.update_info_table()
            tab.task_info_table.setFixedHeight(340)
            error=s.get('error') or {}
            error_key=(error.get('pid'),error.get('time'))
            if error and error.get('is_error',True) and error.get('batch')==(s['batches'][0].get('batch') if s['batches'] else None) and error_key!=getattr(self,'last_error_key',None):
                self.last_error_key=error_key
                stopped=str(error.get('reason','')).startswith(('InterruptedError:','KeyboardInterrupt:'))
                fn=InfoBar.info if stopped else InfoBar.error
                fn(title='已按要求停止' if stopped else '对局技术中断',content=error.get('reason','工作进程异常退出'),parent=w,position=InfoBarPosition.TOP,duration=5000 if stopped else 12000)
            for card in tab.card_widgets:
                card.start_button.setEnabled(not s['offline'] and (not s['running'] or card.task is self.task))
                if not hasattr(card,'finish_after_match_button'):
                    card.finish_after_match_button=PushButton('打完这把停止',card)
                    card.finish_after_match_button.clicked.connect(card.task.finish_after_match)
                    card.all_buttons.insert(card.all_buttons.index(card.stop_button),card.finish_after_match_button)
                    card.finish_after_match_button.show()
                    card.update_buttons(card.task)
                button=card.finish_after_match_button
                button.setEnabled(s['running'] and card.task is self.task and not s.get('finish_after_match'))
                button.setToolTip('已设置，结算后停止匹配' if s.get('finish_after_match') else '立即设置，本局继续完成，之后停止匹配')
                dropdown=card.config_widget_by_key.get('恢复批次')
                card_mode=getattr(card.task,'runner_mode','ranked')
                options=['不恢复']+[b['batch'] for b in s['batches'] if (b.get('mode') or 'ranked')==card_mode and
                    (card_mode=='ranked' or b.get('status') in ('paused','interrupted'))]
                if dropdown and dropdown.tr_options!=options:
                    current=card.task.config.get('恢复批次','不恢复')
                    dropdown.combo_box.blockSignals(True)
                    dropdown.tr_options=options;dropdown.tr_dict={v:v for v in options}
                    dropdown.combo_box.clear();dropdown.combo_box.addItems(options)
                    dropdown.combo_box.setCurrentText(current if current in options else '不恢复')
                    dropdown.combo_box.blockSignals(False)
            self.capture_page.apply_enabled()
        except Exception as e:
            self.task.info['面板状态错误']=str(e)
