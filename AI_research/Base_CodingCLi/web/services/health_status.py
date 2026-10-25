"""Combine current schedulers without treating retired agents as live failures."""
from web.services import launchd_agents


def complete_health(result: dict, schedule: dict, state_dir: str) -> dict:
    deleted = {entry['label'] for entry in launchd_agents.list_deleted(state_dir)}
    retired = set(launchd_agents.LABELS) <= deleted and all(
        not launchd_agents.read_config(label) and not launchd_agents.is_loaded(label)
        for label in launchd_agents.LABELS)
    checks = []
    for check in result['checks']:
        if check['name'] == 'scheduler' and retired:
            check = {**check, 'status': 'ok', 'active': False, 'historical_detail': check['detail'],
                     'detail': '旧轮次调度已删除；历史心跳不表示当前故障，当前周期状态见 schedule'}
        checks.append(check)
    checks.append(schedule)
    errors = [check['name'] for check in checks if check['status'] == 'error']
    warnings = [check['name'] for check in checks if check['status'] == 'warn']
    return {**result, 'checks': checks, 'schedule': schedule, 'errors': errors, 'warnings': warnings,
            'status': 'error' if errors else 'warn' if warnings else 'ok'}
