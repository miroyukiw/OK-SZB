"""Adapt the actual upstream StartTab controls to the existing MAA capture source."""
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PySide6.QtCore import QObject,Signal,Qt,QUrl
from PySide6.QtGui import QPixmap,QDesktopServices
from PySide6.QtWidgets import QListWidgetItem,QDialog,QVBoxLayout,QLabel
from qfluentwidgets import InfoBar,InfoBarPosition,CaptionLabel,PushButton,FluentIcon,isDarkTheme
from ok import og
from ok.core.events import communicate
from src import state
from swb.capture_preview import METHODS,list_mumu,make_capture_source

class CapturePage(QObject):
    completed=Signal(str,object)
    def __init__(self,window):
        super().__init__(window);self.window=window;self.tab=window.start_tab;self.panel=state.panel
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='MaaPreview');self.busy=False;self.instances=[];self.preview=None
        self.completed.connect(self.finished)
        self.folder=self.panel.data/'ok_panel/screenshots';self.folder.mkdir(parents=True,exist_ok=True)
        tab=self.tab;card=tab.start_card
        # Keep upstream MainWindow / StartTab / StartCard / Card / selection widgets unchanged.
        # Replace only their default engine callbacks with this project's MAA adapters.
        for signal in (card.capture_button.clicked,card.refresh_button.clicked,card.start_button.clicked,
                       tab.device_list.itemSelectionChanged,tab.capture_list.itemSelectionChanged,
                       tab.interaction_list.itemSelectionChanged):signal.disconnect()
        communicate.adb_devices.disconnect(tab.update_capture)
        communicate.executor_paused.disconnect(tab.update_selection)
        for signal,slot in ((communicate.executor_paused,card.update_status),(communicate.window,card.update_status),
                            (communicate.task,card.update_task),(card.hotkey_changed,card.update_status)):
            signal.disconnect(slot)
        card.status_bar.hide();card.start_button.setText('排位设置')
        card.update_status=lambda:card.start_button.setText('排位设置')
        card.start_button.clicked.connect(lambda:window.switchTo(window.onetime_tab))
        card.capture_button.clicked.connect(self.capture);card.refresh_button.clicked.connect(self.refresh)
        tab.device_list.itemSelectionChanged.connect(self.select_instance)
        tab.capture_list.itemSelectionChanged.connect(self.select_method)
        tab.device_container.titleLabel.setText('选择窗口')
        tab.capture_container.titleLabel.setText('截图方式')
        tab.interaction_container.titleLabel.setText('选择交互方式')
        if tab.device_search_box:tab.device_search_box.setPlaceholderText('搜索 MuMu 实例')
        tab.capture_list.clear()
        for key,label in METHODS:
            item=QListWidgetItem(label);item.setData(Qt.UserRole,key);tab.capture_list.addItem(item)
        tab.capture_list.blockSignals(True)
        key=self.panel.config().get('capture_method','auto')
        tab.capture_list.setCurrentRow(next((i for i,m in enumerate(METHODS) if m[0]==key),0))
        tab.capture_list.blockSignals(False)
        tab.interaction_list.clear();tab.interaction_list.addItem('MuMu SDK · 后台输入');tab.interaction_list.setCurrentRow(0)
        tab.interaction_list.setToolTip('只在排位任务运行时启用；截图不发送游戏输入')
        # Use the existing developer toolbar for relevant local actions only.
        for w in (tab.export_log_button,tab.open_log_folder_button,tab.open_logs_button,tab.ocr_button,tab.overlay_switch):w.hide()
        tab.open_install_folder_button.clicked.disconnect()
        tab.open_install_folder_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.panel.root))))
        tab.open_screenshot_folder_button.clicked.disconnect()
        tab.open_screenshot_folder_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.folder))))
        self.status=CaptionLabel('点击刷新读取 MuMu 实例；截图不发送游戏输入。')
        self.status.setWordWrap(True);tab.debug_layout.insertWidget(0,self.status,1)
        self.set_rows([{'instance':int(self.panel.config().get('instance',0)),'name':'MuMu','connected':False}],queried=False)
        og.app.app.aboutToQuit.connect(lambda:self.pool.shutdown(wait=False,cancel_futures=True))
        if not self.panel.offline:self.refresh()

    def notify(self,text,error=False):
        (InfoBar.error if error else InfoBar.success)(title='截图未完成' if error else '已完成',content=str(text),parent=self.window,position=InfoBarPosition.TOP,duration=6500)

    def submit(self,kind,fn):
        if self.busy:return
        self.busy=True;self.apply_enabled()
        def done(future):
            try:payload={'value':future.result()}
            except Exception as exc:payload={'error':str(exc)}
            self.completed.emit(kind,payload)
        self.pool.submit(fn).add_done_callback(done)

    def apply_enabled(self):
        running=self.panel.status()['running'];tab=self.tab
        tab.start_card.start_button.setText('排位设置')
        tab.device_list.setEnabled(not running and not self.busy)
        tab.capture_list.setEnabled(not running and not self.busy)
        tab.start_card.refresh_button.setEnabled(not self.busy and not self.panel.offline)
        tab.start_card.capture_button.setEnabled(not self.busy and not self.panel.offline)

    def refresh(self):
        if self.panel.offline:return
        cfg=self.panel.config();self.submit('refresh',lambda:list_mumu(cfg))

    def set_rows(self,rows,queried=True):
        self.instances=rows;listing=self.tab.device_list;listing.blockSignals(True);listing.clear()
        current=int(self.panel.config().get('instance',0))
        for row in rows:
            status=('已连接' if row['connected'] else '未运行') if queried else '待刷新'
            item=QListWidgetItem(f"MuMu {row['instance']} · {row['name']} · {status}")
            item.setData(Qt.UserRole,{'nick':row['name'],'address':str(row['instance'])})
            listing.addItem(item)
        idx=next((i for i,r in enumerate(rows) if r['instance']==current),-1)
        listing.setCurrentRow(idx);listing.blockSignals(False)
        if queried:self.status.setText('仅截图，不发送游戏输入。窗口与截图方式选择后自动保存，下次任务生效。')
        self.tab.filter_devices()

    def save_selection(self):
        row=self.tab.device_list.currentRow()
        if row<0:return
        method=self.tab.capture_list.currentItem()
        self.panel.window({'platform':'mumu','instance':self.instances[row]['instance'],
                           'capture_method':method.data(Qt.UserRole) if method else 'auto'})
        self.status.setText('已保存 MuMu 窗口与截图方式，下次任务生效。')

    def select_instance(self):
        try:self.save_selection()
        except Exception as exc:self.notify(exc,True)
    def select_method(self):self.select_instance()

    def capture_once(self,cfg):
        source=make_capture_source(cfg,self.panel.data/'ok_panel/maa_preview')
        try:
            frame=source.get_frame()
            stamp=datetime.now().strftime('%Y%m%d_%H%M%S_%f')
            path=og.ok.screenshot.generate_screen_shot(frame,{},str(self.folder),'MuMu_'+stamp,False,None)
            return {'path':path,'method':source.method,'size':(frame.shape[1],frame.shape[0])}
        finally:source.close()

    def capture(self):
        if self.panel.offline:return
        cfg=self.panel.config()
        if cfg.get('platform')!='mumu':self.notify('请先选择 MuMu 实例',True);return
        if self.preview:self.preview.close();self.preview=None
        self.status.setText('正在从当前 MuMu 窗口读取一张截图…')
        self.submit('capture',lambda:self.capture_once(cfg))

    def finished(self,kind,payload):
        self.busy=False;self.apply_enabled()
        if 'error' in payload:
            error=payload['error']
            if 'instance is not running' in error:error='MuMu 实例未运行，请打开后刷新。'
            self.status.setText(error)
            if kind=='capture':self.notify(error,True)
            return
        if kind=='refresh':self.set_rows(payload['value']);return
        result=payload['value'];self.status.setText(f"截图成功 · {result['method']} · {result['size'][0]}×{result['size'][1]}")
        dialog=QDialog(self.window);dialog.setWindowTitle('MuMu 截图 · '+Path(result['path']).name);dialog.resize(1000,660)
        dialog.setStyleSheet('QDialog { background-color: '+('#202020' if isDarkTheme() else '#f3f3f3')+'; }')
        layout=QVBoxLayout(dialog);label=QLabel();pix=QPixmap(result['path']);label.setPixmap(pix.scaled(960,580,Qt.KeepAspectRatio,Qt.SmoothTransformation));label.setAlignment(Qt.AlignCenter);layout.addWidget(label)
        location=CaptionLabel('单次截图：'+result['path']);location.setWordWrap(True);layout.addWidget(location)
        close=PushButton('关闭');close.clicked.connect(dialog.close);layout.addWidget(close)
        dialog.setAttribute(Qt.WA_DeleteOnClose);dialog.destroyed.connect(lambda:setattr(self,'preview',None));self.preview=dialog;dialog.show()
