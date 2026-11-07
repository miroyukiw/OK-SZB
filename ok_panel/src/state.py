import os
from pathlib import Path
from panel_app import Panel,ROOT

DATA_ROOT=Path(os.environ.get('SWB_PANEL_DATA_ROOT',str(ROOT)))
panel=Panel(DATA_ROOT,offline=os.environ.get('SWB_PANEL_OFFLINE')=='1',runner_python=str(ROOT/'.venv/Scripts/python.exe'))
controller=None
