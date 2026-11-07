"""Adapted from official ok-script-app/src/config.py; native Qt profile only."""
from pathlib import Path
from panel_app import ROOT
from src.state import DATA_ROOT

config={
    'gui':{'type':'qt','window_size':{'width':1200,'height':800,'min_width':1000,'min_height':700}},
    'gui_title':'OK-SZB',
    'gui_icon':str(Path(__file__).resolve().parents[1]/'icons/ok-szb.png'),
    'version':'v0.1.0', 'debug':False, 'check_mutex':False,'disable_file_log':True,
    'config_folder':str(DATA_ROOT/'user_data/ok_panel/configs'),
    'screenshots_folder':str(DATA_ROOT/'user_data/ok_panel/screenshots'),
    'logs_folder':str(DATA_ROOT/'user_data/ok_panel/logs'),
    'custom_tasks':False, 'trigger_tasks':[],
    'onetime_tasks':[['src.ranked_task','RankedTask'],['src.grand_prix_task','GrandPrixTask'],['src.weekend_plaza_task','WeekendPlazaTask'],['src.paradise_task','ParadiseTask']],
    'custom_tabs':[['src.integration','CardsTab'],['src.integration','SourceTab']],
    'my_app':['src.integration','Globals'],
    # The upstream capture UI is replaced through the my_app hook by the existing MAA window page.
    # No alternate capture/input engine is started; all task actions delegate to panel_app.
    'windows':None,
    'global_configs':[],
    'links':{'default':{'github':'https://github.com/ok-oldking/ok-script-app','faq':'https://ok-script.com/'}},
    'about':'基于官方 ok-script-app 原生桌面模板。影之诗对局仍由现有 MAA runner 执行。',
}
