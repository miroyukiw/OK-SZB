"""Failure categories describe evidence; technical exits are never losses."""
def classify(exc,active_effect=None):
    if type(exc).__name__ in ('NetworkStalled','RecoveredToLobby'):return 'network_stalled'
    if type(exc).__name__=='ReconnectionExhausted':return 'reconnection_exhausted'
    if type(exc).__name__=='RecoveryBlocked':return 'network_recovery_blocked'
    if type(exc).__name__=='ValidationExit':return 'intentional_validation_exit'
    text=str(exc).lower()
    if isinstance(exc,(KeyboardInterrupt,InterruptedError)):return 'user_stop'
    if 'minimized' in text or 'window' in text or 'renderer' in text:return 'window_unavailable'
    if 'watchdog' in text or 'maa job timeout' in text:return 'native_timeout'
    if 'touch' in text or 'release' in text or 'sdk' in text or 'input rotation' in text:return 'input_transport'
    if isinstance(exc,OSError):
        return 'heartbeat_io_error' if 'heartbeat' in text else 'filesystem_io_error'
    import subprocess
    if isinstance(exc,(subprocess.SubprocessError,subprocess.TimeoutExpired)) or 'timed out' in text or 'adb' in text:return 'native_timeout'
    if active_effect:return 'unresolved_effect_selection'
    if 'end-turn' in text:return 'end_turn_unresolved'
    if 'progress' in text or 'navigation did not advance' in text:return 'navigation_stalled'
    if isinstance(exc,TimeoutError):return 'run_deadline'
    return 'runtime_error'
