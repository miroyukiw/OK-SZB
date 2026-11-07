"""Pure panel activity labels; a live PID alone is not a healthy game loop."""
def ranked_activity(running,batch,reconnect,heartbeat,now,paused=False,finish=False,error=None):
    same=reconnect.get('batch')==batch.get('batch') and bool(batch.get('batch'))
    status=reconnect.get('status') if same else None
    if not running:
        if status=='exhausted' or batch.get('stop_kind')=='network_error':return '重连失败，已停止'
        if batch.get('stop_kind') in ('user_stop','user_finish_after_match'):return '已按要求停止'
        if batch.get('status')=='interrupted' or error:return '技术中断'
        return '已停止'
    if paused:return '输入已暂停'
    if batch.get('status')=='resting':return '休息中'
    if status in ('trying','failed'):
        return f"重连中 {reconnect.get('attempt',0)}/{reconnect.get('limit',5)}"
    if status=='exhausted':return '重连失败，正在停止'
    age=max(0,now-heartbeat['time']) if isinstance(heartbeat.get('time'),(int,float)) else None
    if age is not None and age>45:return f'画面响应超时（{int(age)}秒）'
    if finish:return '本局结束后停止'
    if batch.get('ranked_park') and batch.get('park_phase') in ('quests','prepare','ready'):
        if heartbeat.get('turn'):return f"乐园优先课题 · 第 {heartbeat['turn']} 回合"
        return '乐园优先课题' if batch.get('park_phase')=='quests' else '准备乐园排位'
    if batch.get('mode')=='paradise':
        if heartbeat.get('turn'):return f"乐园对局中 · 第 {heartbeat['turn']} 回合"
        stage={'BOARD':'核对课题','HUB':'准备匹配','WORLD':'进入广场','PORTAL':'进入乐园',
            'CHAT':'公会问候','WATCHING':'观战中','WATCH_LIST':'寻找可观战对局','MATCHING':'匹配中',
            'NPC_INTERACT':'准备欧丝对战','NPC_CHOICE':'准备欧丝对战','NPC_READY':'确认欧丝对战',
            'DECK_VIEW':'核验牌组','DECK_PICK':'选择牌组','DECK_CONFIRM':'确认牌组','LOADING':'载入中'}.get(heartbeat.get('park_stage'),'匹配 / 核对中')
        return '乐园课题 · '+stage
    if heartbeat.get('started_ranked'):
        label='乐园排位' if batch.get('ranked_park') else '对局中'
        return f"{label} · 第 {heartbeat['turn']} 回合" if heartbeat.get('turn') else '匹配 / 进入对局中'
    return '正在进入排位'
