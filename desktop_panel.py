"""Native Windows desktop panel. No HTTP server or embedded browser is used."""
import argparse
import copy
import json
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog,messagebox
import webbrowser
from PIL import Image,ImageTk
from panel_app import Panel,ROOT,CLASSES
from swb.user_rules import FIELDS,OPS,ACTIONS,load

BG='#111824';SURFACE='#1b2636';FG='#e4edf8';MUTED='#9cacbf';ACCENT='#81dcca'
FL={'pp':'当前 PP','max_pp':'最大 PP','own_hp':'己方血量','enemy_hp':'敌方血量','turn':'回合','enemy_count':'敌方数量','own_count':'己方数量','hand_count':'手牌数量','form':'形态','opponent_class':'对手职业'}
AL={'set_score':'设置优先级','add_score':'增减优先级','hold':'暂缓出牌'}

class Desktop(ttk.Frame):
    def __init__(self,master,panel=None):
        super().__init__(master);self.pack(fill='both',expand=True)
        self.panel=panel or Panel();self.data=self.panel.bootstrap();self.rules=copy.deepcopy(self.data['rules']);self.decks=copy.deepcopy(self.data['decks'])
        self.selected='10444120';self.deck_index=0;self.rule_index=None;self.photos={};self.dirty=False;self.page_size=100
        master.title('影之诗超凡世界 · 管理面板');master.geometry('1450x950');master.minsize(1150,780);master.configure(bg=BG)
        style=ttk.Style();style.theme_use('clam')
        style.configure('.',background=SURFACE,foreground=FG,font=('Microsoft YaHei UI',10),bordercolor='#33435a',lightcolor=SURFACE,darkcolor=SURFACE)
        style.configure('TFrame',background=BG);style.configure('Card.TFrame',background=SURFACE)
        style.configure('TLabel',background=BG);style.configure('Title.TLabel',font=('Microsoft YaHei UI',22,'bold'))
        style.configure('Muted.TLabel',foreground=MUTED);style.configure('TLabelframe',background=BG);style.configure('TLabelframe.Label',background=BG,foreground=ACCENT,font=('Microsoft YaHei UI',11,'bold'))
        style.configure('TButton',padding=(9,5));style.map('TButton',background=[('active','#2f465e')])
        style.configure('Accent.TButton',background=ACCENT,foreground='#15352f');style.map('Accent.TButton',background=[('active','#a5f5df')],foreground=[('disabled','#426157')])
        style.configure('Danger.TButton',background='#893c4e',foreground='white')
        style.configure('TEntry',fieldbackground='#101a29',foreground=FG,insertcolor=FG)
        style.configure('TCombobox',fieldbackground='#101a29',foreground=FG);style.map('TCombobox',fieldbackground=[('readonly','#182538')],foreground=[('readonly',FG)])
        style.configure('Treeview',background='#162131',fieldbackground='#162131',foreground=FG,rowheight=31,borderwidth=0)
        style.configure('Treeview.Heading',background='#26364c',foreground=FG,padding=6)
        style.map('Treeview',background=[('selected','#30574f')]);style.configure('TCheckbutton',background=BG)
        sidebar=ttk.Frame(self,width=180);sidebar.pack(side='left',fill='y',padx=(18,15),pady=24);sidebar.pack_propagate(False)
        ttk.Label(sidebar,text='◈ 影之诗',font=('Microsoft YaHei UI',20,'bold'),foreground=ACCENT).pack(anchor='w')
        ttk.Label(sidebar,text='WORLDS BEYOND\n桌面管理面板',style='Muted.TLabel').pack(anchor='w',pady=(8,30))
        self.content=ttk.Frame(self);self.content.pack(side='right',fill='both',expand=True,padx=(0,22),pady=22)
        self.pages={};self.nav={}
        for name,title in [('cards','卡牌与策略'),('ranked','排位控制'),('source','规则与源码')]:
            self.nav[name]=self.button(sidebar,title,lambda n=name:self.show(n));self.nav[name].pack(fill='x',pady=5)
            self.pages[name]=ttk.Frame(self.content)
        ttk.Label(sidebar,text='MAA Win32 截图\n既有 MuMu SDK 输入\n\n规则与牌组独立保存\n原始卡牌库只读',style='Muted.TLabel').pack(side='bottom',anchor='w')
        self.message=tk.StringVar(value='已停止 · 点击开始才会运行');ttk.Label(self.content,textvariable=self.message,foreground=ACCENT,wraplength=1150).pack(side='bottom',fill='x',pady=(8,0))
        self.build_cards();self.build_ranked();self.build_source();self.show('cards');self.refresh_cards();self.refresh_deck();self.show_card();self.refresh_status()
        master.protocol('WM_DELETE_WINDOW',self.close)
    def guard(self,fn):
        def wrapped(*args):
            try:return fn(*args)
            except Exception as e:self.message.set(str(e));messagebox.showerror('操作未完成',str(e),parent=self.winfo_toplevel())
        return wrapped
    def button(self,parent,text,fn,style='TButton'):
        return ttk.Button(parent,text=text,command=self.guard(fn),style=style)
    def show(self,name):
        for key,p in self.pages.items():p.pack_forget();self.nav[key].configure(style='Accent.TButton' if key==name else 'TButton')
        self.pages[name].pack(fill='both',expand=True)
        if name=='source':self.set_text(self.source,json.dumps(self.rules,ensure_ascii=False,indent=2))
    def title(self,page,text,sub):
        ttk.Label(page,text=text,style='Title.TLabel').pack(anchor='w')
        ttk.Label(page,text=sub,style='Muted.TLabel',wraplength=1140).pack(anchor='w',pady=(5,18))
    def text(self,parent,height=8):
        return tk.Text(parent,height=height,wrap='word',background='#101a29',foreground=FG,insertbackground=FG,relief='flat',font=('Microsoft YaHei UI',10),padx=10,pady=8)
    def set_text(self,widget,value,readonly=False):
        widget.configure(state='normal');widget.delete('1.0','end');widget.insert('1.0',value)
        if readonly:widget.configure(state='disabled')
    def tree(self,parent,columns,widths):
        tree=ttk.Treeview(parent,columns=columns,show='headings',selectmode='browse')
        for c,w in zip(columns,widths):tree.heading(c,text=c);tree.column(c,width=w,minwidth=30,stretch=c==columns[-1])
        scroll=ttk.Scrollbar(parent,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');tree.pack(fill='both',expand=True);return tree
    def build_cards(self):
        p=self.pages['cards'];self.title(p,'卡牌与策略','518 条卡牌：517 条原库 + 已校验牌组的龙之启迪。选择卡牌查看图片、卡文与真实出牌规则。')
        panes=ttk.Panedwindow(p,orient='horizontal');panes.pack(fill='both',expand=True)
        left=ttk.Frame(panes);middle=ttk.Frame(panes);right=ttk.Frame(panes);panes.add(left,weight=4);panes.add(middle,weight=5);panes.add(right,weight=3)
        self.query=tk.StringVar();entry=ttk.Entry(left,textvariable=self.query);entry.pack(fill='x',pady=(0,8));entry.bind('<KeyRelease>',lambda e:self.refresh_cards())
        filters=ttk.Frame(left);filters.pack(fill='x');self.filter_class=tk.StringVar(value='全部职业');self.filter_cost=tk.StringVar(value='全部费用')
        for variable,values,width in [(self.filter_class,['全部职业']+list(CLASSES.values()),11),(self.filter_cost,['全部费用']+[str(i) for i in range(0,21)],10)]:
            c=ttk.Combobox(filters,textvariable=variable,values=values,state='readonly',width=width);c.pack(side='left',padx=(0,6));c.bind('<<ComboboxSelected>>',lambda e:self.refresh_cards())
        self.only_deck=tk.BooleanVar(value=True);ttk.Checkbutton(left,text='仅当前牌组',variable=self.only_deck,command=self.refresh_cards).pack(anchor='w',pady=8)
        frame=ttk.Frame(left);frame.pack(fill='both',expand=True);self.card_tree=self.tree(frame,['费用','职业','卡牌'],[45,55,240]);self.card_tree.bind('<<TreeviewSelect>>',self.select_card_event)
        self.card_count=ttk.Label(left,style='Muted.TLabel');self.card_count.pack(anchor='w',pady=7)
        top=ttk.Frame(middle);top.pack(fill='x',padx=12);self.card_image=ttk.Label(top);self.card_image.pack(side='left',anchor='n')
        self.card_name=ttk.Label(top,font=('Microsoft YaHei UI',13,'bold'),wraplength=240);self.card_name.pack(anchor='w',padx=12)
        self.card_info=ttk.Label(top,style='Muted.TLabel',wraplength=220);self.card_info.pack(anchor='w',padx=12,pady=10)
        self.card_text=self.text(middle,4);self.card_text.pack(fill='x',padx=12,pady=8)
        tools=ttk.Frame(middle);tools.pack(fill='x',padx=12);self.button(tools,'＋ 加入牌组',lambda:self.change_count(self.selected,1)).pack(side='left');self.button(tools,'查看进化图',self.show_evo).pack(side='left',padx=6)
        editor=ttk.LabelFrame(middle,text='出牌逻辑 · 下一局生效',padding=12);editor.pack(fill='both',expand=True,padx=12,pady=(14,0))
        self.rule_select=ttk.Combobox(editor,state='readonly');self.rule_select.pack(fill='x');self.rule_select.bind('<<ComboboxSelected>>',lambda e:self.select_rule(self.rule_select.current()))
        actions=ttk.Frame(editor);actions.pack(fill='x',pady=6)
        self.button(actions,'添加规则',self.add_rule).pack(side='left');self.button(actions,'删除规则',self.remove_rule).pack(side='left',padx=5)
        self.enabled=tk.BooleanVar(value=True);ttk.Checkbutton(actions,text='启用',variable=self.enabled).pack(side='left')
        self.save_rule_btn=self.button(editor,'保存该卡规则',self.save_card_rule,'Accent.TButton');self.save_rule_btn.pack(fill='x',pady=4)
        row=ttk.Frame(editor);row.pack(fill='x',pady=4);self.action=tk.StringVar(value=AL['add_score']);self.score=tk.StringVar(value='0')
        ttk.Combobox(row,textvariable=self.action,values=list(AL.values()),state='readonly',width=14).pack(side='left');ttk.Entry(row,textvariable=self.score,width=10).pack(side='left',padx=6)
        ttk.Label(editor,text='AND 条件（无条件时总是应用）',style='Muted.TLabel').pack(anchor='w',pady=(8,2))
        cf=ttk.Frame(editor,height=85);cf.pack(fill='both',expand=True);self.cond_tree=self.tree(cf,['字段','比较','值'],[115,55,120]);self.cond_tree.configure(height=2)
        row=ttk.Frame(editor);row.pack(fill='x',pady=6);self.field=tk.StringVar(value='最大 PP');self.op=tk.StringVar(value='lt');self.cond_value=tk.StringVar(value='7')
        ttk.Combobox(row,textvariable=self.field,values=list(FL.values()),state='readonly',width=10).pack(side='left');ttk.Combobox(row,textvariable=self.op,values=OPS,state='readonly',width=4).pack(side='left',padx=4);ttk.Entry(row,textvariable=self.cond_value,width=8).pack(side='left')
        row=ttk.Frame(editor);row.pack(fill='x');self.button(row,'＋ 条件',self.add_condition).pack(side='left');self.button(row,'移除条件',self.remove_condition).pack(side='left',padx=5)
        self.note=tk.StringVar();ttk.Entry(editor,textvariable=self.note).pack(fill='x',pady=7)
        ttk.Label(editor,text='编辑后先保存再切换卡牌/规则。保留费用、目标与特殊卡约束。\n形态：base / accelerate / enhance；职业：royal / dragon 等。',style='Muted.TLabel',wraplength=440).pack(anchor='w',pady=(6,0))
        ttk.Label(right,text='我的牌组',font=('Microsoft YaHei UI',14,'bold')).pack(anchor='w')
        self.deck_select=ttk.Combobox(right,state='readonly');self.deck_select.pack(fill='x',pady=8);self.deck_select.bind('<<ComboboxSelected>>',self.select_deck)
        self.deck_name=tk.StringVar();ttk.Entry(right,textvariable=self.deck_name).pack(fill='x');self.deck_class=tk.StringVar(value='龙族');ttk.Combobox(right,textvariable=self.deck_class,values=list(CLASSES.values())[1:],state='readonly').pack(fill='x',pady=5)
        row=ttk.Frame(right);row.pack(fill='x');self.button(row,'新建草稿',self.new_deck).pack(side='left');self.button(row,'保存',self.save_deck,'Accent.TButton').pack(side='left',padx=5)
        df=ttk.Frame(right);df.pack(fill='both',expand=True,pady=8);self.deck_tree=self.tree(df,['数量','牌名'],[45,200]);self.deck_tree.bind('<<TreeviewSelect>>',self.select_deck_card)
        row=ttk.Frame(right);row.pack(fill='x');plus=self.button(row,'＋',lambda:self.deck_delta(1));plus.configure(width=3);plus.pack(side='left');minus=self.button(row,'−',lambda:self.deck_delta(-1));minus.configure(width=3);minus.pack(side='left',padx=5);self.button(row,'导出清单',self.export_deck).pack(side='left')
        self.deck_count=ttk.Label(right,foreground=ACCENT,wraplength=240);self.deck_count.pack(anchor='w',pady=10)
        self.button(right,'官方牌组码说明',self.deck_code_help).pack(fill='x')
        ttk.Label(right,text='本地草稿不自动替换游戏内卡组。\n排位仍选择已校验跳费龙。',style='Muted.TLabel',wraplength=240).pack(anchor='w',pady=10)
    def deck(self):return self.decks[self.deck_index]
    def refresh_cards(self):
        self.card_tree.delete(*self.card_tree.get_children());q=self.query.get().lower();d=self.deck()['cards'];n=0
        for c in sorted(self.data['cards'],key=lambda c:(c['cost'],c['card_id'])):
            if self.only_deck.get() and c['card_id'] not in d:continue
            if self.filter_class.get()!='全部职业' and c['class_name']!=self.filter_class.get():continue
            if self.filter_cost.get()!='全部费用' and str(c['cost'])!=self.filter_cost.get():continue
            if q and q not in ' '.join([c['name'],c['card_id'],c['common_text'],*c['aliases']]).lower():continue
            self.card_tree.insert('',tk.END,iid=c['card_id'],values=(c['cost'],c['class_name'],c['name']));n+=1
        self.card_count.configure(text=f'{n} 张 · 搜索牌名 / 卡文 / ID')
    def select_card_event(self,event=None):
        sel=self.card_tree.selection()
        if sel:self.selected=sel[0];self.show_card()
    def show_card(self):
        c=self.panel.by_id[self.selected];self.card_name.configure(text=c['name']);self.card_info.configure(text=f"{c['cost']} 费 · {c['class_name']}\n{c['card_id']}\n{c['origin']}")
        self.show_image('common');self.set_text(self.card_text,c['common_text'],True);self.refresh_rule_list()
    def show_image(self,form):
        try:
            key=(self.selected,form)
            if key not in self.photos:
                im=Image.open(self.panel.image_path(*key));im.thumbnail((110,143));self.photos[key]=ImageTk.PhotoImage(im)
            self.card_image.configure(image=self.photos[key])
        except FileNotFoundError:self.message.set('该卡没有此形态本地图片')
    def show_evo(self):
        self.show_image('evo');self.set_text(self.card_text,self.panel.by_id[self.selected]['evo_text'] or '没有额外进化卡文',True)
    def refresh_rule_list(self):
        rules=self.rules['cards'].get(self.selected,[]);self.rule_select['values']=[f"{i+1}. {AL[r['action']]} · {r.get('note','')}" for i,r in enumerate(rules)]
        if rules:self.rule_select.current(0);self.select_rule(0)
        else:self.rule_select.set('默认：既有跳费龙策略');self.rule_index=None;self.conditions=[];self.refresh_conditions();self.action.set(AL['add_score']);self.score.set('0');self.note.set('');self.enabled.set(True)
    def select_rule(self,i):
        self.rule_index=i;r=self.rules['cards'][self.selected][i];self.enabled.set(r.get('enabled',True));self.action.set(AL[r['action']]);self.score.set(str(r.get('value',0)));self.note.set(r.get('note',''));self.conditions=copy.deepcopy(r.get('when',[]));self.refresh_conditions()
    def add_rule(self):
        rs=self.rules['cards'].setdefault(self.selected,[]);rs.append({'enabled':True,'when':[],'action':'add_score','value':0,'note':''});self.refresh_rule_list();self.rule_select.current(len(rs)-1);self.select_rule(len(rs)-1);self.dirty=True
    def remove_rule(self):
        if self.rule_index is not None:self.rules['cards'][self.selected].pop(self.rule_index);self.refresh_rule_list();self.dirty=True
    def refresh_conditions(self):
        self.cond_tree.delete(*self.cond_tree.get_children())
        for i,c in enumerate(self.conditions):self.cond_tree.insert('',tk.END,iid=str(i),values=(FL[c['field']],c['op'],c['value']))
    def add_condition(self):
        f=next(k for k,v in FL.items() if v==self.field.get());value=self.cond_value.get()
        if f not in ('form','opponent_class'):value=float(value)
        self.conditions.append({'field':f,'op':self.op.get(),'value':value});self.refresh_conditions();self.dirty=True
    def remove_condition(self):
        sel=self.cond_tree.selection()
        if sel:self.conditions.pop(int(sel[0]));self.refresh_conditions();self.dirty=True
    def save_card_rule(self):
        proposed=copy.deepcopy(self.rules)
        if self.rule_index is not None:
            proposed['cards'][self.selected][self.rule_index]={'enabled':self.enabled.get(),'when':self.conditions,'action':next(k for k,v in AL.items() if v==self.action.get()),'value':float(self.score.get()),'note':self.note.get()}
        self.panel.save_rules(proposed);self.rules=copy.deepcopy(proposed);self.dirty=False;self.message.set('规则已保存；下一局开始生效，当前局使用冻结快照。');self.refresh_rule_list()
    def refresh_deck(self):
        d=self.deck();self.deck_select['values']=[x['name'] for x in self.decks];self.deck_select.current(self.deck_index);self.deck_name.set(d['name']);self.deck_class.set(CLASSES[d['class_id']]);self.deck_tree.delete(*self.deck_tree.get_children())
        for cid,n in d['cards'].items():self.deck_tree.insert('',tk.END,iid=cid,values=(n,self.panel.by_id[cid]['name']))
        n=sum(d['cards'].values());self.deck_count.configure(text=f'{n} / 40 张'+(' · 数量满足正式牌组' if n==40 else ' · 可保存为本地草稿'))
    def select_deck(self,event=None):self.deck_index=self.deck_select.current();self.refresh_deck();self.refresh_cards()
    def select_deck_card(self,event=None):
        sel=self.deck_tree.selection()
        if sel:self.selected=sel[0];self.show_card()
    def change_count(self,cid,delta):
        d=self.deck();c=self.panel.by_id[cid]
        if c['is_token'] or c['class_id'] not in (0,d['class_id']):raise ValueError('衍生卡或其他职业卡不能加入此牌组')
        n=d['cards'].get(cid,0)+delta
        if n>3:raise ValueError('同卡最多 3 张')
        if n<=0:d['cards'].pop(cid,None)
        else:d['cards'][cid]=n
        self.dirty=True;self.refresh_deck();self.refresh_cards()
    def deck_delta(self,delta):
        sel=self.deck_tree.selection()
        if sel:self.change_count(sel[0],delta)
    def new_deck(self):
        import time
        self.decks.append({'id':'draft-'+str(time.time_ns()),'name':'未命名牌组','class_id':4,'cards':{}});self.deck_index=len(self.decks)-1;self.only_deck.set(False);self.filter_class.set('龙族');self.refresh_deck();self.refresh_cards();self.dirty=True
    def save_deck(self):
        d=self.deck();d['name']=self.deck_name.get();d['class_id']=next(k for k,v in CLASSES.items() if v==self.deck_class.get());self.panel.save_decks(self.decks);self.refresh_deck();self.dirty=False;self.message.set('牌组已保存到 user_data/decks.json；原库未修改。')
    def export(self,filename,value):
        p=filedialog.asksaveasfilename(parent=self.winfo_toplevel(),initialfile=filename,defaultextension='.json',filetypes=[('JSON','*.json'),('所有文件','*.*')])
        if p:Path(p).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');self.message.set('已导出 '+p)
    def export_deck(self):self.export(self.deck()['name']+'-本地清单.json',self.deck())
    def deck_code_help(self):
        w=tk.Toplevel(self);w.title('超凡世界官方牌组码');w.geometry('660x390');w.configure(bg=BG)
        ttk.Label(w,text='本地 JSON 清单不是可导入牌组码',font=('Microsoft YaHei UI',16,'bold')).pack(padx=24,pady=24)
        ttk.Label(w,text='官方流程：登录 Deck Portal，按清单组好 40 张牌并正式保存，在牌组详情页生成牌组码。临时保存不能生成码。也可以在游戏内组好并导出。\n\n本面板暂未接入官方账号/生成服务，不读取凭据。只有点击下方官方链接才会打开浏览器；桌面管理本身不使用浏览器。',wraplength=600).pack(padx=24,pady=12)
        self.button(w,'打开官方牌组码说明 ↗',lambda:webbrowser.open('https://shadowverse-wb.com/en/deck/beginner/code/')).pack(pady=15)
    def build_ranked(self):
        p=self.pages['ranked'];self.title(p,'排位控制','只有点击开始或恢复才运行。沿用既有 MAA 截图与输入方式；不会切换到 ADB。')
        self.status_label=ttk.Label(p,font=('Microsoft YaHei UI',18,'bold'),foreground=ACCENT);self.status_label.pack(anchor='w',pady=(0,20))
        columns=ttk.Frame(p);columns.pack(fill='both',expand=True);left=ttk.Frame(columns);left.pack(side='left',fill='both',expand=True,padx=(0,20));right=ttk.Frame(columns);right.pack(side='right',fill='both',expand=True)
        group=ttk.LabelFrame(left,text='排位任务 · 游戏内现有跳费龙',padding=18);group.pack(fill='x')
        ttk.Label(group,text='局数（恢复时为含已完成场次的总目标）').pack(anchor='w');row=ttk.Frame(group);row.pack(fill='x',pady=10)
        self.matches=tk.StringVar(value='1');self.match_entry=ttk.Entry(row,textvariable=self.matches,width=18);self.match_entry.pack(side='left');self.infinite=tk.BooleanVar();ttk.Checkbutton(row,text='无限',variable=self.infinite,command=lambda:self.match_entry.configure(state='disabled' if self.infinite.get() else 'normal')).pack(side='left',padx=12)
        self.logs=tk.BooleanVar(value=True);ttk.Checkbutton(group,text='记录对局日志（用于后续胜率分析）',variable=self.logs).pack(anchor='w',pady=10)
        ttk.Label(group,text='开：状态、动作、决策、截图；每局截图约 256 MiB，事件轮换，面板详细证据总预算 2 GiB。\n关：仅留结果、规则快照、心跳与故障恢复状态。容量不限制对局次数，原历史日志不清理。',style='Muted.TLabel',wraplength=530).pack(anchor='w')
        row=ttk.Frame(group);row.pack(fill='x',pady=18);self.start_btn=self.button(row,'▶ 开始排位',lambda:self.launch(False),'Accent.TButton');self.start_btn.pack(side='left');self.button(row,'■ 立即停止',lambda:self.control('stop'),'Danger.TButton').pack(side='left',padx=10)
        row=ttk.Frame(group);row.pack(fill='x');self.pause_btn=self.button(row,'暂停输入',lambda:self.control('pause'));self.pause_btn.pack(side='left',padx=8);self.continue_btn=self.button(row,'继续输入',lambda:self.control('continue'));self.continue_btn.pack(side='left');self.skip_rest_btn=self.button(row,'⏭ 跳过休息',lambda:self.skip_rest());self.skip_rest_btn.pack(side='left',padx=8)
        ttk.Label(group,text='暂停输入保留进程内状态；跳过休息立即清零工时并恢复排位。立即停止后，未结算战斗不自动重放。',wraplength=530,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
        group=ttk.LabelFrame(left,text='批次断点恢复',padding=18);group.pack(fill='x',pady=18);self.batch_select=ttk.Combobox(group,state='readonly');self.batch_select.pack(fill='x');self.batch_select.bind('<<ComboboxSelected>>',self.choose_batch)
        self.resume_btn=self.button(group,'从已完成断点续跑',lambda:self.launch(True));self.resume_btn.pack(anchor='w',pady=12)
        ttk.Label(group,text='已完成场次不重打。支持结束本局后的恢复；退出/崩溃后未结算战斗动作暂不支持恢复，保留具体故障。',wraplength=530,style='Muted.TLabel').pack(anchor='w')
        cfg=ttk.LabelFrame(right,text='MAA 窗口设置',padding=18);cfg.pack(fill='x');self.platform=tk.StringVar(value=self.data['config']['platform']);ttk.Combobox(cfg,textvariable=self.platform,values=['mumu','pc'],state='readonly').pack(fill='x')
        ttk.Label(cfg,text='mumu：MAA 截图 + MuMu SDK 输入；pc：Win32 PostMessage',wraplength=510,style='Muted.TLabel').pack(anchor='w',pady=8)
        row=ttk.Frame(cfg);row.pack(fill='x');ttk.Label(row,text='MuMu 实例编号 ').pack(side='left');self.instance=tk.StringVar(value=str(self.data['config'].get('instance',0)));ttk.Entry(row,textvariable=self.instance,width=10).pack(side='left')
        self.windows=[];self.window_select=ttk.Combobox(cfg,state='readonly');self.window_select.pack(fill='x',pady=10)
        row=ttk.Frame(cfg);row.pack(fill='x');self.button(row,'刷新 PC 窗口',self.refresh_windows).pack(side='left');self.save_window_btn=self.button(row,'保存窗口设置',self.save_window);self.save_window_btn.pack(side='left',padx=8)
        ttk.Label(cfg,text='刷新只枚举窗口，无截图/输入。设置在下一次启动任务生效。',wraplength=510,style='Muted.TLabel').pack(anchor='w',pady=(12,0))
        ttk.Label(right,text='运行状态与故障',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w',pady=(22,8));self.status_text=self.text(right,15);self.status_text.pack(fill='both',expand=True)
    def choose_batch(self,event=None):
        i=self.batch_select.current()
        if i>=0:
            total=self.batches[i]['authorized_matches'];self.infinite.set(total is None);self.matches.set(str(total or 1));self.match_entry.configure(state='disabled' if total is None else 'normal')
    def launch(self,resume):
        index=self.batch_select.current();batch=self.batches[index]['batch'] if index>=0 else None
        result=self.panel.start({'matches':'infinite' if self.infinite.get() else self.matches.get().strip(),'detail_logs':self.logs.get(),'resume':resume,'batch':batch})
        self.message.set('已启动 '+result['batch']);self.update_status()
    def skip_rest(self):
        self.panel.control('skip_rest')
        self.message.set('已跳过防沉迷休息，工时已清零；若任务运行中将立即启动游戏。')
        self.update_status()
    def control(self,action):self.panel.control(action);self.message.set('控制请求已发送：'+action);self.update_status()
    def refresh_windows(self):
        from maa.toolkit import Toolkit
        self.windows=[w for w in Toolkit.find_desktop_windows() if w.window_name];self.window_select['values']=[f'{w.window_name} · {w.hwnd}' for w in self.windows]
        if self.windows:self.window_select.current(0)
        self.message.set(f'读取 {len(self.windows)} 个窗口；没有发送游戏输入。')
    def save_window(self):
        i=self.window_select.current();self.panel.window({'platform':self.platform.get(),'instance':int(self.instance.get()),'hwnd':self.windows[i].hwnd if i>=0 else None});self.message.set('窗口配置已保存，下次启动任务生效。')
    def update_status(self):
        s=self.panel.status();self.batches=s['batches'];b=self.batches[0] if self.batches else {}
        rem=max(1,(s.get('rest_remaining',0)+59)//60) if s.get('resting') else 0
        state=f"休息中（剩余约 {rem} 分钟）" if s.get('resting') else '输入暂停' if s['paused'] else '运行中' if s['running'] else '已停止'
        self.status_label.configure(text=f"{state}   ·   {b.get('completed',0)} / {'—' if not b else b.get('authorized_matches') if b.get('authorized_matches') is not None else '∞'} 局   ·   {b.get('wins',0)} 胜 {b.get('losses',0)} 负   ·   {b.get('technical_interruptions',0)} 次技术中断"+('   [离线验收]' if s['offline'] else ''))
        old=self.batch_select.current();self.batch_select['values']=[f"{b['batch']} · {b['completed']} / {b['authorized_matches'] if b['authorized_matches'] is not None else '∞'}" for b in self.batches]
        if self.batches:self.batch_select.current(max(0,min(old,len(self.batches)-1)))
        for button,enabled in [(self.start_btn,not s['running'] and not s['offline']),(self.resume_btn,not s['running'] and not s['offline'] and bool(self.batches)),(self.pause_btn,s['running'] and not s['paused']),(self.continue_btn,s['running'] and s['paused']),(self.skip_rest_btn,s.get('resting') or s.get('active_seconds',0)>0),(self.save_window_btn,not s['running'])]:button.configure(state='normal' if enabled else 'disabled')
        self.set_text(self.status_text,json.dumps({'batch':b.get('batch'),'status':b.get('status'),'active_match':b.get('active_match'),'next_match':b.get('next_match'),'STOP':s['stop'],'locks':s['locks'],'reason':s['error'] or b.get('reason'),'current_run':b.get('current_run')},ensure_ascii=False,indent=2),True)
    def refresh_status(self):
        try:self.update_status()
        except Exception as e:self.message.set('状态读取失败：'+str(e))
        self.after(2000,self.refresh_status)
    def build_source(self):
        p=self.pages['source'];self.title(p,'规则与源码','JSON 规则与可视化编辑共用 user_data/rules.json。当前局使用已冻结快照，下一局加载保存的新规则。')
        row=ttk.Frame(p);row.pack(fill='x',pady=(0,10));self.button(row,'重新读取',self.reload_rules).pack(side='left');self.button(row,'校验并保存源码',self.save_source,'Accent.TButton').pack(side='left',padx=8);self.button(row,'导出规则',lambda:self.export('shadowverse-wb-rules.json',self.rules)).pack(side='left');self.button(row,'导入规则',self.import_rules).pack(side='left',padx=8)
        self.source=self.text(p,18);self.source.configure(font=('Consolas',11));self.source.pack(fill='both',expand=True)
        ttk.Label(p,text='仅解析声明式规则，不执行任意代码。无规则时继续使用原有跳费龙逻辑。',style='Muted.TLabel').pack(anchor='w',pady=9)
        row=ttk.Frame(p);row.pack(fill='x');self.button(row,'查看基准 Python 源码',self.view_baseline).pack(side='left');self.button(row,'导出 policy.py',lambda:self.export('policy.py',(ROOT/'swb/policy.py').read_text(encoding='utf-8'))).pack(side='left',padx=8)
        ttk.Label(p,text='MAA 模板适配：复用 MaaPracticeBoilerplate 控制器/资源配置，沿用现有 Python runner；桌面卡牌和规则页为本项目新增。\n原库只读：'+self.data['library'],style='Muted.TLabel',wraplength=1120).pack(anchor='w',pady=20)
    def reload_rules(self):self.rules=load(self.panel.data/'rules.json');self.set_text(self.source,json.dumps(self.rules,ensure_ascii=False,indent=2));self.refresh_rule_list();self.message.set('已重新读取磁盘规则。')
    def save_source(self):
        value=json.loads(self.source.get('1.0','end'));self.panel.save_rules(value);self.rules=value;self.refresh_rule_list();self.message.set('源码已校验保存；下一局生效。')
    def import_rules(self):
        p=filedialog.askopenfilename(parent=self.winfo_toplevel(),filetypes=[('JSON','*.json')])
        if p:self.panel.save_rules(json.loads(Path(p).read_text(encoding='utf-8-sig')));self.reload_rules();self.message.set('规则已导入保存；下一局生效。')
    def view_baseline(self):
        w=tk.Toplevel(self);w.title('基准策略 · policy.py');w.geometry('1000x760');t=self.text(w,30);t.configure(font=('Consolas',11));t.pack(fill='both',expand=True);self.set_text(t,(ROOT/'swb/policy.py').read_text(encoding='utf-8'),True)
    def close(self):
        # Closing the UI does not stop an explicitly started independent worker.
        self.winfo_toplevel().destroy()

def main():
    p=argparse.ArgumentParser();p.add_argument('--offline',action='store_true');a=p.parse_args()
    import ctypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True);kernel.CreateMutexW.restype=ctypes.c_void_p;kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    handle=kernel.CreateMutexW(None,False,'Local\\ShadowverseWBDesktopPanel'+('Offline' if a.offline else ''))
    if ctypes.get_last_error()==183:
        user=ctypes.WinDLL('user32');user.FindWindowW.restype=ctypes.c_void_p;user.FindWindowW.argtypes=[ctypes.c_wchar_p,ctypes.c_wchar_p]
        hwnd=user.FindWindowW(None,'影之诗超凡世界 · 管理面板')
        if hwnd:
            user.ShowWindow.argtypes=[ctypes.c_void_p,ctypes.c_int];user.SetForegroundWindow.argtypes=[ctypes.c_void_p]
            user.ShowWindow(hwnd,9);user.SetForegroundWindow(hwnd)
        return
    try:
        window=tk.Tk();Desktop(window,Panel(offline=a.offline));window.mainloop()
    finally:
        if handle:kernel.CloseHandle.argtypes=[ctypes.c_void_p];kernel.CloseHandle(handle)

if __name__=='__main__':main()
