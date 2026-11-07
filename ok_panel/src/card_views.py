"""Shadowverse-specific content hosted inside upstream CustomTab/Card widgets."""
import copy,json
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QPixmap, QDesktopServices, QFont
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTableWidgetItem, QHeaderView, QAbstractItemView, QFileDialog, QDialog, QSplitter
from qfluentwidgets import (FluentIcon as FI, NavigationItemPosition,
    ScrollArea, CardWidget, ExpandSettingCard, PushButton, PrimaryPushButton,
    LineEdit, SearchLineEdit, ComboBox, SwitchButton, CheckBox, TableWidget,
    PlainTextEdit, TitleLabel, SubtitleLabel, BodyLabel, CaptionLabel,
    InfoBar, InfoBarPosition, Theme, setTheme, setThemeColor)
from panel_app import Panel, ROOT, CLASSES
from swb.user_rules import FIELDS, OPS, load

FL = {'pp':'当前 PP','max_pp':'最大 PP','own_hp':'我方血量','enemy_hp':'敌方血量','turn':'回合','enemy_count':'敌方随从','own_count':'我方随从','hand_count':'手牌数量','form':'形态','opponent_class':'对手职业'}
AL = {'set_score':'设置优先级','add_score':'增加优先级','hold':'暂缓出牌'}


def combo(values):
    w=ComboBox(); w.addItems([str(x) for x in values]); return w


def line(value='', width=None):
    w=LineEdit(); w.setText(str(value))
    if width: w.setFixedWidth(width)
    return w


def hbox(*widgets):
    w=QWidget(); layout=QHBoxLayout(w); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
    for item in widgets:
        if item is None: layout.addStretch(1)
        else: layout.addWidget(item)
    return w


def table(headers, height=240):
    w=TableWidget(); w.setColumnCount(len(headers)); w.setHorizontalHeaderLabels(headers)
    w.verticalHeader().hide(); w.setBorderVisible(False); w.setBorderRadius(8)
    w.setSelectionBehavior(QAbstractItemView.SelectRows); w.setSelectionMode(QAbstractItemView.SingleSelection)
    w.setEditTriggers(QAbstractItemView.NoEditTriggers); w.setMinimumHeight(height)
    w.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    w.horizontalHeader().setSectionResizeMode(len(headers)-1,QHeaderView.Stretch)
    return w


def fill_table(w, rows):
    w.blockSignals(True); w.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for j,value in enumerate(row): w.setItem(i,j,QTableWidgetItem(str(value)))
    w.blockSignals(False)


class EditorController:
    def __init__(self,panel):
        self.panel=panel;self.data=panel.bootstrap();self.rules=copy.deepcopy(self.data['rules']);self.decks=copy.deepcopy(self.data['decks'])
        self.deck_index=next((i for i,d in enumerate(self.decks) if d.get('profile')==self.data['active_strategy']['profile']),0);self.selected=next(iter(self.decks[self.deck_index]['cards']));self.rule_index=None;self.conditions=[];self.pages={};self.windows=[];self.window=None
    def initialize(self,window):
        self.window=window;self.refresh_deck();self.refresh_cards();self.show_card()
    def card(self,page,title):
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        page.add_card(title,body);return layout
    def notify(self,text,error=False):
        fn=InfoBar.error if error else InfoBar.success
        fn(title='操作未完成' if error else '已完成',content=str(text),parent=self.window,position=InfoBarPosition.BOTTOM_RIGHT,duration=6500 if error else 4000)

    def guard(self,fn):
        def wrapped(*_):
            try: return fn()
            except Exception as e: self.notify(str(e),True)
        return wrapped

    def button(self,text,fn,primary=False,icon=None):
        cls=PrimaryPushButton if primary else PushButton
        w=cls(icon,text) if icon else cls(text); w.clicked.connect(self.guard(fn)); return w

    def row(self,title,control,description=''):
        w=QWidget(); b=QHBoxLayout(w); b.setContentsMargins(14,10,14,10); b.setSpacing(16); w.setMinimumHeight(56)
        text=QVBoxLayout(); text.setSpacing(3); text.addWidget(BodyLabel(title))
        if description:
            sub=CaptionLabel(description); sub.setWordWrap(True); text.addWidget(sub)
        b.addLayout(text,1); b.addWidget(control); return w

    def build_settings(self):
        p=self.pages['settings']; p.heading('窗口与设置','沿用现有 MAA 截图与输入方式')
        b=self.card(p,'游戏窗口'); cfg=self.data['config']; self.platform=combo(['mumu','pc']); self.platform.setCurrentText(cfg['platform']); self.instance=line(cfg.get('instance',0),180)
        b.addWidget(self.row('控制方式',self.platform,'mumu：MAA 截图 + MuMu SDK 输入；pc：Win32 PostMessage'))
        b.addWidget(self.row('MuMu 实例编号',self.instance))
        self.window_select=combo([]); self.window_select.setMinimumWidth(300)
        b.addWidget(self.row('PC 窗口',hbox(self.window_select,self.button('刷新',self.refresh_windows,icon=FI.SYNC))))
        self.save_window_btn=self.button('保存窗口设置',self.save_window,True,FI.SAVE); b.addWidget(hbox(None,self.save_window_btn))
        b.addWidget(CaptionLabel('保存后在下次启动任务时生效。刷新只枚举窗口，不发送游戏输入。'))
        b=self.card(p,'外观'); self.theme=combo(['深色','浅色']); self.theme.currentIndexChanged.connect(lambda i:setTheme(Theme.DARK if i==0 else Theme.LIGHT)); b.addWidget(self.row('主题',self.theme,'与本机 ok-ww 使用相同的 PySide6 / Fluent 原生控件'))
        b=self.card(p,'数据位置'); t=PlainTextEdit(); t.setReadOnly(True); t.setMaximumHeight(140); t.setPlainText('原始卡牌库（只读）\n'+self.data['library']+'\n\n面板规则 / 卡组草稿\n'+str(self.panel.data)); b.addWidget(t)

    def refresh_windows(self):
        from maa.toolkit import Toolkit
        self.windows=[w for w in Toolkit.find_desktop_windows() if w.window_name]; self.window_select.clear(); self.window_select.addItems([f'{w.window_name} · {w.hwnd}' for w in self.windows]); self.notify(f'已读取 {len(self.windows)} 个窗口')

    def save_window(self):
        i=self.window_select.currentIndex(); self.panel.window({'platform':self.platform.currentText(),'instance':int(self.instance.text()),'hwnd':self.windows[i].hwnd if i>=0 else None}); self.notify('窗口设置已保存，下次启动生效')

    def build_cards(self):
        p=self.pages['cards']; p.heading('卡牌与卡组','已核验：速攻鬼（速攻速攻）40张 / 扎针龙 · 右侧选择运行卡组')
        self.query=SearchLineEdit(); self.query.setPlaceholderText('搜索牌名、文本或卡牌 ID'); self.query.textChanged.connect(self.refresh_cards)
        self.filter_class=combo(['全部职业']+list(CLASSES.values())); self.filter_cost=combo(['全部费用']+[str(i) for i in range(21)])
        self.only_deck=CheckBox('仅当前卡组'); self.only_deck.setChecked(True)
        for w in (self.filter_class,self.filter_cost):w.currentIndexChanged.connect(self.refresh_cards)
        self.only_deck.toggled.connect(self.refresh_cards)
        p.box.addWidget(hbox(self.query,self.filter_class,self.filter_cost,self.only_deck))
        split=QSplitter(Qt.Horizontal); split.setChildrenCollapsible(False); p.box.addWidget(split)
        left=CardWidget(); lb=QVBoxLayout(left); lb.setContentsMargins(12,12,12,12); split.addWidget(left)
        self.card_tree=table(['费用','职业','牌名'],450); lb.addWidget(self.card_tree); self.card_tree.itemSelectionChanged.connect(self.select_card_event); self.card_count=CaptionLabel(); lb.addWidget(self.card_count)
        middle=CardWidget(); mb=QVBoxLayout(middle); mb.setContentsMargins(16,16,16,16); mb.setSpacing(10); split.addWidget(middle)
        self.card_image=QLabel(); self.card_image.setFixedSize(120,156); self.card_name=SubtitleLabel(); self.card_name.setWordWrap(True); self.card_info=CaptionLabel(); self.card_info.setWordWrap(True)
        names=QWidget(); nb=QVBoxLayout(names); nb.setContentsMargins(0,0,0,0); nb.addWidget(self.card_name); nb.addWidget(self.card_info); nb.addStretch(); mb.addWidget(hbox(self.card_image,names))
        self.card_text=PlainTextEdit(); self.card_text.setReadOnly(True); self.card_text.setFixedHeight(105); mb.addWidget(self.card_text)
        mb.addWidget(hbox(self.button('加入卡组',lambda:self.change_count(self.selected,1),icon=FI.ADD),self.button('进化形态',self.show_evo)))
        mb.addWidget(SubtitleLabel('出牌逻辑'))
        self.rule_select=combo([]); self.rule_select.currentIndexChanged.connect(self.select_rule); mb.addWidget(self.rule_select)
        self.enabled=SwitchButton(); self.enabled.setOnText('启用'); self.enabled.setOffText('停用'); self.enabled.setChecked(True)
        mb.addWidget(hbox(self.button('添加',self.add_rule,icon=FI.ADD),self.button('删除',self.remove_rule,icon=FI.DELETE),None,self.enabled))
        self.action=combo(list(AL.values())); self.score=line('0',100); mb.addWidget(hbox(self.action,self.score))
        self.save_rule_btn=self.button('保存该卡规则',self.save_card_rule,True,FI.SAVE); mb.addWidget(self.save_rule_btn)
        self.cond_tree=table(['字段','比较','值'],100); self.cond_tree.setFixedHeight(125); mb.addWidget(self.cond_tree)
        self.field=combo(list(FL.values())); self.field.setCurrentText(FL['max_pp']); self.op=combo(OPS); self.op.setCurrentText('lt'); self.cond_value=line('7',60)
        mb.addWidget(hbox(self.field,self.op,self.cond_value)); mb.addWidget(hbox(self.button('添加条件',self.add_condition),self.button('移除条件',self.remove_condition)))
        self.note=line(); self.note.setPlaceholderText('规则备注'); mb.addWidget(self.note)
        sub=CaptionLabel('全部条件满足才应用。保存后下一局生效；当前局继续使用冻结规则。默认使用所选卡组的独立策略。快鬼使用行动路线比较；单牌分数规则仅用于旧龙族。'); sub.setWordWrap(True); mb.addWidget(sub)
        right=CardWidget(); rb=QVBoxLayout(right); rb.setContentsMargins(12,12,12,12); split.addWidget(right)
        rb.addWidget(SubtitleLabel('我的卡组')); self.deck_select=combo([]); self.deck_select.currentIndexChanged.connect(self.select_deck); rb.addWidget(self.deck_select)
        self.deck_name=line(); rb.addWidget(self.deck_name); self.deck_class=combo(list(CLASSES.values())[1:]); rb.addWidget(self.deck_class)
        rb.addWidget(hbox(self.button('新建',self.new_deck),self.button('保存',self.save_deck,True)))
        self.deck_tree=table(['张数','牌名'],250); rb.addWidget(self.deck_tree); self.deck_tree.itemSelectionChanged.connect(self.select_deck_card)
        rb.addWidget(hbox(self.button('＋',lambda:self.deck_delta(1)),self.button('－',lambda:self.deck_delta(-1)),self.button('导出清单',self.export_deck)))
        self.active_strategy=CaptionLabel();self.active_strategy.setWordWrap(True);rb.addWidget(self.active_strategy)
        rb.addWidget(self.button('设为运行卡组',self.use_deck,True))
        self.deck_count=CaptionLabel(); rb.addWidget(self.deck_count); rb.addWidget(self.button('官方牌组码向导',self.deck_code_help,icon=FI.LINK))
        note=CaptionLabel('选择已核验卡组后点『设为运行卡组』。\n再到『排位模式』点开始；这里只保存选择，不启动对战。'); note.setWordWrap(True); rb.addWidget(note)
        split.setSizes([340,420,290])

    def use_deck(self):
        d=self.deck();profile=d.get('profile') or ('needle_dragon' if d['id']=='ramp-dragon' else None)
        if not profile:raise ValueError('此草稿没有对应的自动对战策略；请选择已核验的速攻鬼或扎针龙')
        expected=json.loads((ROOT/('assets/nightmare/deck.json' if profile=='nightmare_aggro' else 'assets/deck.json')).read_text(encoding='utf-8'))
        if d['cards']!={c['card_id']:c['count'] for c in expected['cards']}:raise ValueError('本地清单与已核验40张不同，请选择原已核验卡组')
        result=self.panel.select_deck_profile(profile);self.data['active_strategy']=result
        self.active_strategy.setText('当前运行：'+result['name']);self.notify('运行卡组已保存；在排位模式点击开始才会对战')

    def deck(self):return self.decks[self.deck_index]

    def refresh_cards(self,*_):
        if not hasattr(self,'card_tree'):return
        q=self.query.text().lower(); deck=self.deck()['cards']; self.visible_cards=[]
        for c in sorted(self.data['cards'],key=lambda c:(c['cost'],c['card_id'])):
            if self.only_deck.isChecked() and c['card_id'] not in deck:continue
            if self.filter_class.currentText()!='全部职业' and c['class_name']!=self.filter_class.currentText():continue
            if self.filter_cost.currentText()!='全部费用' and str(c['cost'])!=self.filter_cost.currentText():continue
            if q and q not in ' '.join([c['name'],c['card_id'],c['common_text'],*c['aliases']]).lower():continue
            self.visible_cards.append(c)
        fill_table(self.card_tree,[(c['cost'],c['class_name'],c['name']) for c in self.visible_cards]); self.card_count.setText(f'{len(self.visible_cards)} 张 · 总计 {len(self.data["cards"])} 张')

    def select_card_event(self):
        i=self.card_tree.currentRow()
        if i>=0:self.selected=self.visible_cards[i]['card_id'];self.show_card()

    def show_card(self):
        c=self.panel.by_id[self.selected]; self.card_name.setText(c['name']);self.card_info.setText(f"{c['cost']} 费 · {c['class_name']}\n{c['card_id']}")
        self.show_image('common');self.card_text.setPlainText(c['common_text']);self.refresh_rule_list()

    def show_image(self,form):
        try:
            pix=QPixmap(str(self.panel.image_path(self.selected,form))); self.card_image.setPixmap(pix.scaled(120,156,Qt.KeepAspectRatio,Qt.SmoothTransformation))
        except FileNotFoundError:self.card_image.clear();self.card_image.setText('暂无此形态图片')

    def show_evo(self):self.show_image('evo');self.card_text.setPlainText(self.panel.by_id[self.selected]['evo_text'] or '无独立进化文本')

    def refresh_rule_list(self):
        rs=self.rules['cards'].get(self.selected,[]); self.rule_select.blockSignals(True);self.rule_select.clear()
        self.rule_select.addItems([f'{i+1}. {AL[r["action"]]} · {r.get("note","")}' for i,r in enumerate(rs)] or ['默认：快鬼行动路线策略' if self.deck().get('profile')=='nightmare_aggro' else '默认：扎针龙策略'])
        self.rule_select.blockSignals(False)
        if rs:self.select_rule(0)
        else:self.rule_index=None;self.conditions=[];self.refresh_conditions();self.action.setCurrentText(AL['add_score']);self.score.setText('0');self.note.clear();self.enabled.setChecked(True)

    def select_rule(self,i):
        rs=self.rules['cards'].get(self.selected,[])
        if i<0 or i>=len(rs):return
        self.rule_index=i;r=rs[i];self.enabled.setChecked(r.get('enabled',True));self.action.setCurrentText(AL[r['action']]);self.score.setText(str(r.get('value',0)));self.note.setText(r.get('note',''));self.conditions=copy.deepcopy(r.get('when',[]));self.refresh_conditions()

    def add_rule(self):
        rs=self.rules['cards'].setdefault(self.selected,[]);rs.append({'enabled':True,'when':[],'action':'add_score','value':0,'note':''});self.refresh_rule_list();self.rule_select.setCurrentIndex(len(rs)-1);self.select_rule(len(rs)-1)

    def remove_rule(self):
        if self.rule_index is not None:self.rules['cards'][self.selected].pop(self.rule_index);self.refresh_rule_list()

    def refresh_conditions(self):fill_table(self.cond_tree,[(FL[c['field']],c['op'],c['value']) for c in self.conditions])

    def add_condition(self):
        f=next(k for k,v in FL.items() if v==self.field.currentText());value=self.cond_value.text()
        if f not in ('form','opponent_class'):value=float(value)
        self.conditions.append({'field':f,'op':self.op.currentText(),'value':value});self.refresh_conditions()

    def remove_condition(self):
        i=self.cond_tree.currentRow()
        if i>=0:self.conditions.pop(i);self.refresh_conditions()

    def save_card_rule(self):
        proposed=copy.deepcopy(self.rules)
        if self.rule_index is not None:proposed['cards'][self.selected][self.rule_index]={'enabled':self.enabled.isChecked(),'when':copy.deepcopy(self.conditions),'action':next(k for k,v in AL.items() if v==self.action.currentText()),'value':float(self.score.text()),'note':self.note.text()}
        self.panel.save_rules(proposed);self.rules=proposed;self.refresh_rule_list();self.source.setPlainText(json.dumps(self.rules,ensure_ascii=False,indent=2));self.notify('规则已保存，下一局生效')

    def refresh_deck(self):
        self.active_strategy.setText('当前运行：'+self.panel.active_strategy()['name'])
        d=self.deck();self.deck_select.blockSignals(True);self.deck_select.clear();self.deck_select.addItems([x['name'] for x in self.decks]);self.deck_select.setCurrentIndex(self.deck_index);self.deck_select.blockSignals(False)
        self.deck_name.setText(d['name']);self.deck_class.setCurrentText(CLASSES[d['class_id']]);self.deck_ids=list(d['cards']);fill_table(self.deck_tree,[(d['cards'][cid],self.panel.by_id[cid]['name']) for cid in self.deck_ids]);self.deck_count.setText(f'{sum(d["cards"].values())} / 40 张')

    def select_deck(self,i):
        if i>=0:
            self.deck_index=i;self.filter_class.setCurrentText('全部职业');self.selected=next(iter(self.deck()['cards']),self.selected);self.refresh_deck();self.refresh_cards();self.show_card()

    def select_deck_card(self):
        i=self.deck_tree.currentRow()
        if i>=0:self.selected=self.deck_ids[i];self.show_card()

    def change_count(self,cid,delta):
        d=self.deck();c=self.panel.by_id[cid]
        if c['is_token'] or c['class_id'] not in (0,d['class_id']):raise ValueError('衍生物或其他职业牌不能加入本卡组')
        n=d['cards'].get(cid,0)+delta
        if n>3:raise ValueError('同名卡最多 3 张')
        if n<=0:d['cards'].pop(cid,None)
        else:d['cards'][cid]=n
        self.refresh_deck();self.refresh_cards()

    def deck_delta(self,delta):
        i=self.deck_tree.currentRow()
        if i>=0:self.change_count(self.deck_ids[i],delta)

    def new_deck(self):
        import time
        self.decks.append({'id':'draft-'+str(time.time_ns()),'name':'未命名卡组','class_id':4,'cards':{}});self.deck_index=len(self.decks)-1;self.only_deck.setChecked(False);self.filter_class.setCurrentText(CLASSES[4]);self.refresh_deck();self.refresh_cards()

    def save_deck(self):
        d=self.deck();d['name']=self.deck_name.text();d['class_id']=next(k for k,v in CLASSES.items() if v==self.deck_class.currentText());self.panel.save_decks(self.decks);self.refresh_deck();self.notify('卡组草稿已保存')

    def export(self,filename,value):
        path,_=QFileDialog.getSaveFileName(self.window,'导出',filename,'JSON (*.json);;所有文件 (*)')
        if path:Path(path).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');self.notify('已导出 '+path)

    def export_deck(self):self.export(self.deck()['name']+'-卡牌清单.json',self.deck())

    def deck_code_help(self):
        d=QDialog(self.window);d.setWindowTitle('官方牌组码向导');d.resize(740,660);d.setStyleSheet('QDialog{background:#202020;color:white;}');b=QVBoxLayout(d);b.setContentsMargins(24,24,24,24);b.setSpacing(12)
        b.addWidget(SubtitleLabel('在官方 Deck Portal 生成牌组码'))
        text=BodyLabel('1. 打开官方组卡页，登录你自己的账号。\n2. 按下方清单选择卡牌，组成 40 张牌并正式保存（可设为私密）。\n3. 在官方牌组详情页点击 Generate Deck Code / QR。\n4. 游戏内：卡牌 → 卡组 → 新建卡组 → 输入牌组码。');text.setWordWrap(True);b.addWidget(text)
        listing='\n'.join(f'{n} × {self.panel.by_id[cid]["name"]}   [{cid}]' for cid,n in self.deck()['cards'].items())
        t=PlainTextEdit();t.setReadOnly(True);t.setPlainText(listing);b.addWidget(t)
        b.addWidget(hbox(self.button('复制卡牌清单',lambda:QApplication.clipboard().setText(listing)),self.button('打开官方组卡页',lambda:QDesktopServices.openUrl(QUrl('https://shadowverse-wb.com/en/deck/build/')),True)))
        sub=CaptionLabel('当前没有接入官方账号会话与生成接口，无法从此按钮直接返回可导入码。面板仅打开官网、提供清单；不会上传卡组或读取登录凭据。本地导出 JSON 是编辑清单，不是游戏牌组码。临时牌组不能生成码。');sub.setWordWrap(True);b.addWidget(sub)
        b.addWidget(self.button('官方生成说明',lambda:QDesktopServices.openUrl(QUrl('https://shadowverse-wb.com/en/deck/beginner/code/'))));d.exec()

    def build_source(self):
        p=self.pages['source'];p.heading('策略编辑','快鬼：独立行动路线策略；扎针龙：原策略及下方单牌规则。右侧卡组页可切换运行策略。')
        p.box.addWidget(hbox(self.button('重新读取',self.reload_rules,icon=FI.SYNC),self.button('校验并保存',self.save_source,True,FI.SAVE),self.button('导入',self.import_rules),self.button('导出',lambda:self.export('shadowverse-wb-rules.json',json.loads(self.source.toPlainText())))))
        self.source=PlainTextEdit();self.source.setFont(QFont('Consolas',11));self.source.setMinimumHeight(440);self.source.setPlainText(json.dumps(self.rules,ensure_ascii=False,indent=2));p.box.addWidget(self.source)
        p.box.addWidget(hbox(self.button('查看策略 Python 源码',self.view_baseline),self.button('导出 policy.py',lambda:self.export('strategy.py',self.baseline_path().read_text(encoding='utf-8'))),None))
        p.box.addWidget(CaptionLabel('结构化 JSON 规则驱动 runner，不执行任意代码。原始卡牌数据库只读。'))

    def reload_rules(self):self.rules=load(self.panel.data/'rules.json');self.source.setPlainText(json.dumps(self.rules,ensure_ascii=False,indent=2));self.refresh_rule_list()

    def save_source(self):
        value=json.loads(self.source.toPlainText());self.panel.save_rules(value);self.rules=value;self.refresh_rule_list();self.notify('源码已校验保存，下一局生效')

    def import_rules(self):
        p,_=QFileDialog.getOpenFileName(self.window,'导入规则','','JSON (*.json)')
        if p:self.panel.save_rules(json.loads(Path(p).read_text(encoding='utf-8-sig')));self.reload_rules();self.notify('规则已导入，下一局生效')

    def baseline_path(self):return ROOT/('swb/nightmare.py' if self.panel.active_strategy()['profile']=='nightmare_aggro' else 'swb/policy.py')

    def view_baseline(self):
        d=QDialog(self.window);d.setWindowTitle('当前策略 · '+self.baseline_path().name);d.resize(1000,740);b=QVBoxLayout(d);t=PlainTextEdit();t.setReadOnly(True);t.setFont(QFont('Consolas',11));t.setPlainText(self.baseline_path().read_text(encoding='utf-8'));b.addWidget(t);d.exec()
