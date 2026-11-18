from types import SimpleNamespace
import pytest


def test_recipient_must_match_a_whole_address_typed_by_user(monkeypatch):
    import web.deps as deps
    from web.services.assistant_tools import reports as tool
    monkeypatch.setattr(deps, 'require_editor', lambda request, user=None: None)
    sent=[]
    monkeypatch.setattr(tool.reports, 'email_report', lambda body, request, user=None: sent.append(body) or {'ok':True})
    request=SimpleNamespace(state=SimpleNamespace(assistant_message='把报告发给 notboss@example.com'))
    args=SimpleNamespace(action='email',path='output/test.md',recipients=['boss@example.com'],ttl_hours=24,jti='')
    with pytest.raises(ValueError):
        tool.distribute(args,request,{'role':'editor'})
    assert not sent
