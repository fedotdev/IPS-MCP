import os
import sys
import threading

import httpx

sys.path.insert(0, "src")

import json

from ips_mcp import ips


def handler_with(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_auth_then_401_refresh():
    calls = []
    auth_ok = False

    def handler(request):
        if request.url.path == "/core/api/Auth/authenticate":
            return httpx.Response(200, json={"accessToken": "t1",
                                             "refreshToken": "r1"})
        calls.append(request.headers.get("authorization"))
        if not auth_ok:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": 1})

    # auth_ok поднимается изнутри после выдачи токена
    orig = handler

    def wrapped(request):
        resp = orig(request)
        if request.url.path == "/core/api/Auth/authenticate":
            nonlocal auth_ok
            auth_ok = True
        return resp

    c = ips.IpsClient("http://x", "u", "p")
    c._http = handler_with(wrapped)
    assert c.request("GET", "/core/api/objects/1") == {"ok": 1}
    assert calls == [None, "Bearer t1"], calls


def test_refresh_after_token_expired():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/core/api/Auth/refreshTokens":
            return httpx.Response(200, json={"accessToken": "t2",
                                             "refreshToken": "r2"})
        if len([p for p in calls if p == "/core/api/objects/2"]) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": 2})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "old"
    c._refresh = "refresh-old"
    c._http = handler_with(handler)
    assert c.request("GET", "/core/api/objects/2") == {"ok": 2}
    assert calls == ["/core/api/objects/2", "/core/api/Auth/refreshTokens",
                     "/core/api/objects/2"], calls


def test_404_mapping():
    def handler(request):
        return httpx.Response(404, json={"detail": "нет объекта"})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    try:
        c.request("GET", "/core/api/objects/42")
    except ips.IpsError as e:
        assert e.status == 404
        assert str(e).startswith("IPS: объект не найден")
        assert "id=42" in str(e)
    else:
        raise AssertionError("ожидали IpsError")


def test_mapper():
    gloss = {
        "object_types": {1075: "Операция"},
        "relation_types": {1002: "Технологический состав"},
        "attribute_types": {1066: "Номер объекта"},
    }
    obj = ips.decorate_object({"objectID": 5, "objectType": 1075}, gloss)
    assert obj["objectTypeName"] == "Операция"
    rel = ips.decorate_relation({"relationID": 9, "relationType": 1002}, gloss)
    assert rel["relationTypeName"] == "Технологический состав"
    attrs = ips.decorate_attrs([{"attributeId": 1066}], gloss)
    assert attrs[0]["attributeName"] == "Номер объекта"


def test_paged():
    items = list(range(103))
    p = ips.paged(items, page=1, page_size=100)
    assert p["total"] == 103 and len(p["items"]) == 100 and p["has_more"] is True
    p2 = ips.paged(items, page=2, page_size=100)
    assert len(p2["items"]) == 3 and p2["has_more"] is False
    assert ips.paged(items, page=0, page_size=0)["page"] == 1
    assert ips.paged(items, page_size=5000)["page_size"] == 1000


def test_resolve_object_links():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={"entity": {"objectID": 7,
                                                    "objectType": 1110,
                                                    "caption": "Цехозаход 1"}})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    gloss = {"object_types": {1110: "Цехозаход"}}
    attrs = [
        {"attributeId": 1, "attributeType": "ftObjectLink",
         "values": [7], "attributeName": "Ссылка"},
        {"attributeId": 2, "attributeType": "string", "values": ["x"]},
        {"attributeId": 3, "attributeType": "ftObjectLink",
         "values": [7, 8]},
    ]
    out = ips.resolve_object_links(c, gloss, attrs, cap=2)
    assert out[0]["resolvedValues"][0]["caption"] == "Цехозаход 1"
    assert out[0]["resolvedValues"][0]["objectTypeName"] == "Цехозаход"
    assert "resolvedValues" not in out[1]
    # кап 2 запроса: ссылки атрибута 3 не разворачиваются полностью
    assert len(calls) == 2, calls


def test_write_flow_once():
    from ips_mcp.server import McpServer

    state = {"value": "005 ТЕСТ"}

    def handler(request):
        path = request.url.path
        if request.method == "GET" and path.endswith("/attributesValues"):
            return httpx.Response(200, json=[
                {"attributeId": 1066, "attributeName": "Номер объекта",
                 "values": [state["value"]]}])
        if path.endswith("/attributes") and request.method == "POST":
            state["value"] = "006 НОВОЕ"
            return httpx.Response(200, json={"result": {}})
        if path.endswith("/checkOut"):
            return httpx.Response(200, json={"result": -111})
        if path.endswith("/checkIn"):
            return httpx.Response(200, json={"result": -111})
        return httpx.Response(200, json={"result": {}})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    names = [t["name"] for t in server.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
         "params": {}})["result"]["tools"]]
    assert "ips_prepare_update_attribute" in names
    assert "ips_commit_update_attribute" in names

    prep = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                          "params": {"name": "ips_prepare_update_attribute",
                                     "arguments": {"object_id": 1349039,
                                                   "attribute_id": 1066,
                                                   "value": "006 НОВОЕ"}}})
    body = json.loads(prep["result"]["content"][0]["text"])
    rid = body["request_id"]
    assert body["preview"]["attribute"]["old_value"] == ["005 ТЕСТ"]
    assert body["preview"]["operations"] == ["checkOut", "edit", "attributes",
                                             "saveChanges", "checkIn"]

    com = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                         "params": {"name": "ips_commit_update_attribute",
                                    "arguments": {"request_id": rid}}})
    out = json.loads(com["result"]["content"][0]["text"])
    assert out["status"] == "ok"
    assert out["verify"]["value"] == ["006 НОВОЕ"]
    assert out["workingCopyId"] == -111

    # once: повторный commit тем же request_id невозможен
    again = server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "ips_commit_update_attribute",
                                      "arguments": {"request_id": rid}}})
    assert "once" in again["result"]["content"][0]["text"]


def test_write_failure_rolls_back_checkout():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/checkOut"):
            return httpx.Response(200, json={"result": -111})
        if request.url.path.endswith("/attributes"):
            return httpx.Response(400, json={"detail": "invalid value"})
        if request.url.path.endswith("/cancelChanges"):
            return httpx.Response(200, json={"result": [-111]})
        return httpx.Response(200, json={"result": {}})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    try:
        ips.write_attribute(c, 1349039, 1066, "bad")
    except ips.IpsError as e:
        assert e.status == 400
    else:
        raise AssertionError("ожидали ошибку записи")
    assert calls == [
        "/core/api/objects/1349039/checkOut",
        "/core/api/objects/-111/edit",
        "/core/api/objects/-111/attributes",
        "/core/api/objects/cancelChanges",
    ], calls


def test_config_file_loading(tmpdir_factory=None):
    import tempfile
    from ips_mcp.server import load_config

    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"IPS_LOGIN": "file_user",
                                 "IPS_PASSWORD": "file_pass",
                                 "IPS_ROLE_ID": 5,
                                 "IPS_ENABLE_WRITE": True}))
        old = {k: os.environ.get(k) for k in ("IPS_LOGIN", "IPS_PASSWORD",
                                               "IPS_ROLE_ID", "IPS_ENABLE_WRITE")}
        try:
            for k in old:
                os.environ.pop(k, None)
            load_config(cfg)
            assert os.environ["IPS_LOGIN"] == "file_user"
            assert os.environ["IPS_ROLE_ID"] == "5"
            # загрузка часов: env игнорирует файл
            os.environ["IPS_LOGIN"] = "env_user"
            load_config(cfg)
            assert os.environ["IPS_LOGIN"] == "env_user"
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


def test_relative_gloss_resolves_from_config_dir():
    """Относительный IPS_GLOSS_DB разрешается от каталога config.json, а не cwd.

    Иначе конфиг, скопированный из другого проекта, молча указывал бы в никуда.
    """
    import tempfile
    from ips_mcp.server import load_config

    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "config.json")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"IPS_LOGIN": "u", "IPS_PASSWORD": "p",
                                 "IPS_GLOSS_DB": "gloss/generated/gloss.db"}))
        old = os.environ.pop("IPS_GLOSS_DB", None)
        try:
            load_config(cfg)
            expected = os.path.normpath(os.path.join(d, "gloss", "generated", "gloss.db"))
            assert os.environ["IPS_GLOSS_DB"] == expected, os.environ["IPS_GLOSS_DB"]
        finally:
            if old is None:
                os.environ.pop("IPS_GLOSS_DB", None)
            else:
                os.environ["IPS_GLOSS_DB"] = old


def test_init_writes_config_and_never_clobbers():
    """init пишет config.json из env и отказывается перезаписывать существующий."""
    import tempfile
    from ips_mcp.server import init_config

    keys = ("IPS_LOGIN", "IPS_PASSWORD", "IPS_BASE_URL", "IPS_ROLE_ID",
            "IPS_GLOSS_DB", "IPS_AUDIT_LOG", "IPS_ENABLE_WRITE")
    old = {k: os.environ.get(k) for k in keys}
    with tempfile.TemporaryDirectory() as d:
        cfg = os.path.join(d, "config.json")
        try:
            for k in keys:
                os.environ.pop(k, None)
            os.environ["IPS_LOGIN"] = "init_user"
            os.environ["IPS_PASSWORD"] = "init_pass"
            os.environ["IPS_ROLE_ID"] = "42"
            assert init_config(["--config", cfg]) == 0
            with open(cfg, encoding="utf-8") as fh:
                data = json.load(fh)
            assert data["IPS_LOGIN"] == "init_user"
            assert data["IPS_ROLE_ID"] == 42, data["IPS_ROLE_ID"]
            assert data["IPS_ENABLE_WRITE"] is False

            # повторный init без --force обязан отказаться и не тронуть файл
            os.environ["IPS_LOGIN"] = "other"
            assert init_config(["--config", cfg]) == 1
            with open(cfg, encoding="utf-8") as fh:
                assert json.load(fh)["IPS_LOGIN"] == "init_user"
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok: {t.__name__}")