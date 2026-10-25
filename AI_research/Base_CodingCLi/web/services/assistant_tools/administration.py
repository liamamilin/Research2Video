"""Health, configuration, protected account operations and maintenance."""
from __future__ import annotations
import copy
from pathlib import Path
from typing import Literal
from pydantic import Field
from . import Arguments, Tool
from web.settings import get_settings
from web.services.assistant_safety import redact


class Overview(Arguments):
    days: int = Field(default=30, ge=0, le=3650)
    month: bool = False


def overview(a, request, user):
    from web.routes import jobs, reports, usage, deploy
    return {'jobs': [j.model_dump(mode='json') for j in jobs.list_all(user=user)],
            'reports': reports.stats(user=user), 'usage': usage.get_usage(days=a.days, month=a.month, user=user),
            'deployment': deploy.deploy_status(user=user)}


class Diagnostic(Arguments):
    action: Literal['health', 'logs', 'audit', 'index', 'test_search', 'test_embedding', 'background'] = 'health'
    task_id: str = ''
    lines: int = Field(default=80, ge=10, le=300)


def diagnose(a, request, user):
    from web.routes import config, logs
    from web.services import assistant_background
    settings = get_settings()
    if a.action == 'health':
        from web.services.assistant_management import _native
        return _native(request, 'GET', '/api/health/detailed')
    if a.action == 'logs':
        return redact(logs.get_global_logs(lines=a.lines, user=user))
    if a.action == 'audit':
        return redact(logs.get_audit_log(limit=a.lines, user=user))
    if a.action == 'test_search':
        return redact(config.test_search({}, user=user))
    if a.action == 'test_embedding':
        return redact(config.test_embedding({}, user=user))
    if a.action == 'background':
        return assistant_background.read(a.task_id, user)
    from web.indexer import vectors, vector_sync
    return {'coverage': vector_sync.coverage(settings.paths.state_dir), 'stats': vectors.index_stats()}


class ConfigQuery(Arguments):
    action: Literal['system', 'providers', 'secrets', 'gateway'] = 'system'


def query_config(a, request, user):
    from web.routes import config, gateway
    from web.services import assistant_configuration
    if a.action == 'system':
        return assistant_configuration.view(user)
    if a.action == 'providers':
        return redact(config.get_providers(user=user))
    if a.action == 'secrets':
        data = config.get_secrets(user=user)
        return {'secrets': [{k: item[k] for k in ('env', 'label', 'configured')} for item in data['secrets']]}
    from web.services import assistant_maintenance
    return assistant_maintenance.gateway_status()


class ConfigChange(Arguments):
    action: Literal['test_model', 'apply', 'rollback', 'lan_access']
    patch: dict = Field(default_factory=dict)
    expected_mtime: float | None = None
    expected_version: str = ''
    rollback_id: str = ''
    lan_access: bool | None = None
    force_save: bool = False
    provider_id: str = ''


def config_change(a, request, user):
    from web.services import assistant_configuration
    from web.routes import gateway
    if a.action == 'test_model':
        return assistant_configuration.test_candidate(a.patch, user, a.provider_id)
    if a.action == 'rollback':
        return assistant_configuration.rollback(a.rollback_id, request, user)
    if a.action == 'lan_access':
        if a.lan_access is None:
            raise ValueError('需要指定 lan_access')
        result = gateway.update_gateway(gateway.GatewayUpdate(lan_access=a.lan_access), request, user=user)
        from web.services import assistant_maintenance
        return {**result, **assistant_maintenance.gateway_status()}
    if a.expected_mtime is None:
        raise ValueError('保存前先读取配置，提交 expected_mtime')
    return assistant_configuration.apply(a.patch, a.expected_mtime, request, user, force_save=a.force_save,
                                         provider_id=a.provider_id, expected_version=a.expected_version)


class Reindex(Arguments):
    include_vectors: bool = True


def rebuild(a, request, user):
    from web.services import assistant_background
    from web.indexer import scanner, db, vector_sync
    settings = get_settings()
    def operation(progress):
        progress(stage='全文索引', completed=0)
        result = scanner.full_scan(settings.paths.output_dir)
        # get_stats uses total_reports in older stores. Query the count through
        # the stable list API so every report is visited regardless of version.
        total = db.list_reports(page=1, per_page=1)['total']
        items = db.list_reports(page=1, per_page=max(total, 1))['items']
        failed = []
        done = 0
        if a.include_vectors:
            for item in items:
                progress(stage='向量索引', completed=done, total=total, path=item['path'])
                data = vector_sync.sync_report(settings.paths.output_dir, item['path'])
                if data.get('reason'):
                    failed.append({'path': item['path'], 'error': data['reason']})
                done += 1
        return {**result, 'processed': done, 'failed': failed, 'coverage': vector_sync.coverage(settings.paths.state_dir)}
    return assistant_background.start('reindex', user, operation)


class Notify(Arguments):
    channels: list[Literal['webhook', 'wecom', 'feishu', 'pushplus', 'serverchan', 'email']] = Field(min_length=1)


def notify(a, request, user):
    from core import notify as notifier
    from core.config import load_system_config
    from web import audit
    config = load_system_config(get_settings().paths.config_dir)
    available = notifier._channels(notifier._config(config))
    if set(a.channels) - set(available):
        raise ValueError('所选通知渠道尚未配置或未启用，请先查询配置')
    cfg = copy.deepcopy(config)
    section = cfg.setdefault('notifications', {})
    for name in ('wecom', 'feishu', 'pushplus', 'serverchan', 'email'):
        section.setdefault(name, {})['enabled'] = name in a.channels
    if 'webhook' not in a.channels:
        section['webhook_url'] = ''
        section['webhook_url_env'] = ''
    result = notifier.send_test(cfg)
    audit.log('assistant_notify_test', user=user['username'], details={'channels': a.channels})
    return {**result, 'ok': bool(result['results']) and all(result['results'].values())}


class Users(Arguments):
    action: Literal['list', 'update', 'delete'] = 'list'
    user_id: int | None = None
    role: Literal['viewer', 'editor', 'admin'] | None = None
    disabled: bool | None = None
    revoke_sessions: bool = False


def users(a, request, user):
    from web.routes import users as routes
    if a.action == 'list':
        return {'users': [u.model_dump(mode='json') for u in routes.list_users(user=user)]}
    if a.user_id is None:
        raise ValueError('需要明确的 user_id')
    if a.action == 'delete':
        return routes.delete_user(a.user_id, request, user=user)
    result = routes.update_user(a.user_id, a.model_dump(include={'role', 'disabled', 'revoke_sessions'}, exclude_none=True), request, user=user)
    return result.model_dump(mode='json')


class Account(Arguments):
    action: Literal['sessions', 'tokens', 'revoke_session', 'revoke_token']
    session_id: str = ''
    token_id: int | None = None


def account(a, request, user):
    from web.auth import routes
    from fastapi import Response
    if a.action == 'sessions':
        return routes.my_sessions(request)
    if a.action == 'tokens':
        return routes.list_tokens(request)
    if a.action == 'revoke_session':
        return routes.revoke_session(a.session_id, request, Response())
    if a.token_id is None:
        raise ValueError('需要指定 token_id')
    return routes.revoke_token(a.token_id, request)


class Protected(Arguments):
    kind: Literal['set_secret', 'set_headers', 'create_user', 'reset_password', 'change_password', 'create_token']
    env: str = ''
    username: str = ''
    role: Literal['viewer', 'editor', 'admin'] = 'viewer'
    user_id: int | None = None
    token_name: str = 'api-token'
    expires_days: int = Field(default=30, ge=1, le=3650)


def prepare(a, request, user):
    from web.services.assistant_store import AssistantStore
    from web.deps import require_admin
    fields = []
    params = a.model_dump(exclude={'kind'})
    if a.kind in ('set_secret', 'set_headers', 'create_user', 'reset_password'):
        require_admin(request, user=user)
    if a.kind == 'set_secret':
        from web.routes.config import _secret_env_map
        if a.env not in _secret_env_map():
            raise ValueError('环境变量未在配置中引用，请先设置正确的变量名')
        fields = [{'name': 'value', 'label': f'{a.env} 新值', 'type': 'password'}]
        title = '更新密钥：' + a.env
    elif a.kind == 'set_headers':
        from web.routes.config import get_system_config
        params['expected_mtime'] = get_system_config(user=user)['mtime']
        fields = [{'name': 'value', 'label': '请求头 JSON 对象', 'type': 'password'}]
        title = '更新模型请求头（先验证流式与工具调用）'
    elif a.kind == 'create_user':
        if len(a.username) < 2:
            raise ValueError('用户名至少两个字符')
        fields = [{'name': 'password', 'label': '新用户密码', 'type': 'password'}]
        title = f'创建用户 {a.username}（{a.role}）'
    elif a.kind == 'reset_password':
        from web.auth import db
        target = db.get_user_by_id(a.user_id) if a.user_id is not None else None
        if not target:
            raise ValueError('目标用户不存在')
        fields = [{'name': 'password', 'label': '新密码', 'type': 'password'}]
        title = f'重置 {target["username"]} 的密码并撤销其会话'
    elif a.kind == 'change_password':
        fields = [{'name': 'current_password', 'label': '当前密码', 'type': 'password'},
                  {'name': 'new_password', 'label': '新密码', 'type': 'password'}]
        title = '修改自己的密码（保留当前会话）'
    else:
        params['token_name'] = a.token_name.strip()[:60] or 'api-token'
        title = f'创建自己的 API Token：{params["token_name"]}，有效 {a.expires_days} 天'
        from web.auth import db
        params['existing_token_ids'] = [token['id'] for token in db.list_api_tokens(user['id'])]
    key = AssistantStore().prepare(user, a.kind, params)
    return {'input_card': {'id': key, 'title': title, 'fields': fields,
                           'description': '输入直接提交给应用；秘密不会发送给模型或写入聊天记录。'}}


class Maintenance(Arguments):
    action: Literal['status', 'restart', 'shutdown', 'rebind_gateway', 'receipt'] = 'status'
    active_runs: Literal['refuse', 'wait', 'cancel'] = 'refuse'
    maintenance_id: str = ''


def maintenance(a, request, user):
    from web.services import assistant_maintenance
    if a.action == 'status':
        return assistant_maintenance.status()
    if a.action == 'receipt':
        return assistant_maintenance.receipt(a.maintenance_id, user)
    return assistant_maintenance.start(a.action, a.active_runs, request, user)


TOOLS = {
    'application_overview': Tool(Overview, '汇总真实Jobs/报告/用量/预算/版本；价格未知时费用保持未知。', overview, label='读取应用总览'),
    'diagnose_application': Tool(Diagnostic, '管理员查询健康/脱敏日志/审计/索引覆盖，测试搜索或Embedding，查background task_id进度。', diagnose, 'admin', label='诊断应用'),
    'query_configuration': Tool(ConfigQuery, '读取脱敏系统配置及expected_mtime、provider预设、密钥配置状态、网关已保存与实际监听。', query_config, 'admin', label='读取系统配置'),
    'manage_configuration': Tool(ConfigChange, 'test_model验证普通+流式工具+流式文本，空patch测试当前配置；apply用patch和expected_mtime保存，模型切换先测，失败保留配置，仅明确强制保存时force_save=true。切换预设用provider_id，后台同时更新URL/密钥变量/必要请求头；未指定model时选择预设兼容候选并测试。rollback恢复；lan_access仅保存配置。禁止凭据字段。', config_change, 'admin', True, '管理系统配置'),
    'rebuild_index': Tool(Reindex, '后台重建全文索引及可选向量索引，返回task_id，需查询进度后才可称完成。', rebuild, 'editor', True, '提交索引重建'),
    'test_notification': Tool(Notify, '仅向用户明确选择且已配置的channels发送测试通知。先明确渠道；分析和诊断不隐含发送。', notify, 'admin', True, '测试通知'),
    'manage_users': Tool(Users, '管理员列出用户、按真实user_id修改角色/禁用/强制下线/删除；保留防止自我锁出的限制。创建和重置密码用受保护输入卡片。', users, 'admin', True, '管理用户'),
    'manage_own_account': Tool(Account, '查询本人会话/Token元信息，撤销本人指定会话/Token。不能指定他人账号。当前Token无细粒度业务scope。', account, label='管理本人账号'),
    'request_protected_input': Tool(Protected, '为密钥、创建用户、重置密码、本人改密、本人新Token生成受保护卡片；参数不含秘密。卡片绑定目标，用户在卡片中输入或直接提交。', prepare, write=True, label='准备受保护操作'),
    'maintain_service': Tool(Maintenance, '查询实际监听与手动运行；明确要求时重启/关停应用或重绑定网关。先查影响，active_runs=wait等待或cancel取消，默认拒绝中断运行。操作持久回执，查询后判断真正结果。', maintenance, 'admin', True, '维护服务'),
}
