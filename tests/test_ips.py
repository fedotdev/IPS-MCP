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


def test_search_serialization_and_validation():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=[])

    from ips_mcp.server import McpServer
    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {})
    tool = server._by_name["ips_search_objects"]["call"]
    tool({"object_type_id": 7, "attribute_ids_to_select": [10],
          "condition": {"attribute_id": 10, "value": "abc"}, "record_count": 1000})
    assert seen == [{"objectTypeId": 7, "attributeIdsToSelect": [10],
                     "recordCount": 1000,
                     "conditions": [{"attributeId": 10, "relationalOperator": "equal", "value": "abc"}]}]
    try:
        tool({"object_type_id": -1})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("all-types search must be rejected")
    try:
        tool({"object_type_id": 7, "record_count": 5000})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("record_count cap must be rejected")
    for key, value in (("object_type_id", True), ("object_type_id", "7"),
                       ("object_type_id", 1.5), ("record_count", True),
                       ("record_count", "10"), ("record_count", 1.5),
                       ("attribute_ids_to_select", [True]),
                       ("attribute_ids_to_select", ["10"]),
                       ("attribute_ids_to_select", [1.5])):
        args = {"object_type_id": 7}
        args[key] = value
        try:
            tool(args)
        except ips.IpsError:
            pass
        else:
            raise AssertionError(f"invalid search value accepted: {key}={value!r}")


def test_create_and_relation_two_phase_once_without_delete():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.url.path == "/core/api/objects":
            return httpx.Response(200, json={"entity": {"objectID": 42}})
        if request.url.path.endswith("/commitCreation"):
            return httpx.Response(200, json={"result": {"objectId": 42, "relatedObjectIds": []}})
        return httpx.Response(200, json={"result": {}})

    from ips_mcp.server import McpServer
    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prep = server._by_name["ips_prepare_create_object"]["call"]({"object_type_id": 7})
    assert calls == []
    out = server._by_name["ips_commit_create_object"]["call"]({"request_id": prep["request_id"]})
    assert out["status"] == "ok"
    assert [x[1] for x in calls] == ["/core/api/objects", "/core/api/objects/42/commitCreation"]
    try:
        server._by_name["ips_commit_create_object"]["call"]({"request_id": prep["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("create confirmation must be once")
    before = len(calls)
    try:
        server._by_name["ips_prepare_create_relation"]["call"]({"relation_type_id": 1, "project_version_id": 2, "part_version_id": 3})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("relation without an active parent checkout must be rejected")
    assert len(calls) == before
    assert all(x[0] != "DELETE" for x in calls)


def test_create_object_ids_and_error_stages():
    from ips_mcp.server import McpServer

    def server_for(handler):
        c = ips.IpsClient("http://x", "u", "p")
        c._token = "t"
        c._http = handler_with(handler)
        return McpServer(c, {}, enable_write=True)

    captured = []
    def integer_response(request):
        captured.append((request.url.path, json.loads(request.content or b"{}")))
        if request.url.path == "/core/api/objects":
            return httpx.Response(200, json={"entity": {"objectID": 0}})
        return httpx.Response(200, json={"result": {"objectId": 0}})
    server = server_for(integer_response)
    prep = server._by_name["ips_prepare_create_object"]["call"]({"object_type_id": 0, "attributes": [{"attributeID": 4, "values": ["x"]}]})
    assert prep["preview"]["payload"] == {"objectType": 0, "attributes": [{"attributeID": 4, "values": ["x"]}]}
    out = server._by_name["ips_commit_create_object"]["call"]({"request_id": prep["request_id"]})
    assert out["status"] == "ok" and out["object_id"] == 0
    assert captured[1][0] == "/core/api/objects/0/commitCreation"

    for response, expected in ((httpx.Response(500), "unknown"),):
        def failed(request, response=response):
            return response
        server = server_for(failed)
        prep = server._by_name["ips_prepare_create_object"]["call"]({"object_type_id": 1})
        out = server._by_name["ips_commit_create_object"]["call"]({"request_id": prep["request_id"]})
        assert out["status"] == expected and out["stage"] == "create"

    def create_then_fail_commit(request):
        if request.url.path == "/core/api/objects":
            return httpx.Response(200, json={"entity": {"objectID": 5}})
        return httpx.Response(500)
    server = server_for(create_then_fail_commit)
    prep = server._by_name["ips_prepare_create_object"]["call"]({"object_type_id": 1})
    out = server._by_name["ips_commit_create_object"]["call"]({"request_id": prep["request_id"]})
    assert out["status"] == "partial_unknown" and out["stage"] == "commitCreation"


def test_create_by_prototype_preserves_related_working_copy_ids_once():
    from ips_mcp.server import McpServer
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path,
                      json.loads(request.content or b"{}")))
        if request.method == "GET":
            return httpx.Response(200, json={"entity": {
                "objectID": 100, "objectGUID": "prototype-guid",
                "caption": "030 Prototype"}})
        if request.url.path.endswith("CreateByPrototype"):
            return httpx.Response(200, json={"result": {
                "objectDto": {"objectID": -200}, "relatedObjectIds": [-201]}})
        if request.url.path.endswith("commitCreation"):
            return httpx.Response(200, json={"result": {
                "objectId": 200, "relatedObjectIds": [201]}})
        raise AssertionError(request.url.path)

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prepare = server._by_name["ips_prepare_create_object"]["call"]
    commit = server._by_name["ips_commit_create_object"]["call"]
    preview = prepare({"prototype_id": 100})
    assert preview["preview"]["prototype"] == "030 Prototype"
    assert len(calls) == 1  # preview только читает прототип
    result = commit({"request_id": preview["request_id"]})
    assert result["status"] == "ok" and result["object_id"] == 200
    assert calls[2] == ("POST", "/core/api/objects/CreateByPrototype",
                        {"prototypeId": 100})
    assert calls[3] == ("POST", "/core/api/objects/-200/commitCreation",
                        {"deleteOnException": False, "autoCheckout": False,
                         "relatedObjectIds": [-201]})
    before = len(calls)
    try:
        commit({"request_id": preview["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("prototype creation confirmation must be once")
    assert len(calls) == before


def test_read_sheet_streams_rows_and_keeps_report_header_skip():
    import tempfile
    from pathlib import Path
    from openpyxl import Workbook
    sys.path.insert(0, os.path.abspath("."))
    from gloss.generate_gloss import COLUMNS, read_sheet

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "objects.xlsx"
        wb = Workbook()
        ws = wb.active
        ws.append(["Report title"])
        ws.append([title for _, title, _ in COLUMNS["object_types"]])
        values = []
        for field, _, kind in COLUMNS["object_types"]:
            value = 7 if field == "id" else "Test type" if field == "name" else None
            values.append(value)
        ws.append(values)
        wb.save(path)

        rows, _ = read_sheet(path, "object_types")
        assert rows[0]["id"] == 7 and rows[0]["name"] == "Test type"


def test_relation_transport_failure_is_partial_unknown_and_once():
    calls = []
    base = {"objectID": 2, "objectGUID": "g", "objectType": 7,
            "caption": "parent", "checkoutBy": 0}
    working = {**base, "objectID": -2, "checkoutBy": 1}
    active = {"checked_out": False}

    def handler(request):
        calls.append(request.url.path)
        if request.method == "GET" and request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        if request.method == "GET":
            obj = working if request.url.path.endswith("/-2") and active["checked_out"] else base
            return httpx.Response(200, json={"entity": obj})
        if request.url.path.endswith("/checkOut"):
            active["checked_out"] = True
            return httpx.Response(200, json={"result": -2})
        if request.url.path == "/core/api/relations":
            return httpx.Response(500, text="server error")
        return httpx.Response(200, json={"result": {}})

    from ips_mcp.server import McpServer
    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    checkout = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 2})
    server._by_name["ips_commit_checkout_object"]["call"]({"request_id": checkout["request_id"]})
    prep = server._by_name["ips_prepare_create_relation"]["call"]({
        "relation_type_id": 1, "project_version_id": -2, "part_version_id": 3})
    out = server._by_name["ips_commit_create_relation"]["call"]({"request_id": prep["request_id"]})
    assert out["status"] == "partial_unknown"
    assert calls.count("/core/api/relations") == 1
    try:
        server._by_name["ips_commit_create_relation"]["call"]({"request_id": prep["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("partial relation confirmation must be once")


def test_nested_write_attributes_rejected_before_request_id():
    from ips_mcp.server import McpServer
    c = ips.IpsClient("http://x", "u", "p")
    server = McpServer(c, {}, enable_write=True)
    for name, args in (
        ("ips_prepare_create_object", {"object_type_id": 7, "attributes": [{"attributeID": True, "values": []}]}),
        ("ips_prepare_create_object", {"object_type_id": 7, "attributes": [{"attributeID": 1, "values": [], "extra": 1}]}),
        ("ips_prepare_create_relation", {"relation_type_id": 1, "project_version_id": 2, "part_version_id": 3, "attribute_values": [{"attributeID": "1", "values": []}]}),
    ):
        try:
            server._by_name[name]["call"](args)
        except ips.IpsError:
            pass
        else:
            raise AssertionError("malformed nested DTO accepted")
    try:
        server._by_name["ips_commit_create_object"]["call"]({"request_id": "missing"})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("malformed prepare must not create a request")


def test_create_context_and_relation_payload_match_swagger():
    from ips_mcp.server import McpServer
    def handler(request):
        if request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        if request.method == "GET":
            if request.url.path.endswith("/-12"):
                return httpx.Response(200, json={"entity": {"objectID": -12, "objectGUID": "g",
                    "objectType": 7, "caption": "parent", "checkoutBy": 1}})
            return httpx.Response(200, json={"entity": {"objectID": 12, "objectGUID": "g",
                "objectType": 7, "caption": "parent", "checkoutBy": 0}})
        if request.url.path.endswith("/checkOut"):
            return httpx.Response(200, json={"result": -12})
        return httpx.Response(200, json={"result": {}})
    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prep = server._by_name["ips_prepare_create_object"]["call"]({
        "object_type_id": 7, "context_rule": {"editingContextMode": "autoUpdate"},
        "current_project": {"id": 9, "mode": "currentProject"}})
    assert prep["preview"]["payload"]["contextRule"] == {"editingContextMode": "autoUpdate"}
    assert prep["preview"]["payload"]["currentProjectDto"] == {"id": 9, "mode": "currentProject"}
    checkout = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 12})
    server._by_name["ips_commit_checkout_object"]["call"]({"request_id": checkout["request_id"]})
    rel = server._by_name["ips_prepare_create_relation"]["call"]({
        "relation_type_id": 1, "project_version_id": -12, "part_version_id": 3,
        "attribute_values": [{"attributeID": 8, "values": [4]}]})
    assert rel["preview"]["payload"]["attributeValues"] == [{"attributeId": 8, "values": [4]}]
    for args in ({"context_rule": {"unknown": 1}}, {"current_project": {"mode": "bad"}}):
        args["object_type_id"] = 7
        try:
            server._by_name["ips_prepare_create_object"]["call"](args)
        except ips.IpsError:
            pass
        else:
            raise AssertionError("invalid nested context accepted")


def test_write_tools_absent_without_flag():
    from ips_mcp.server import McpServer
    c = ips.IpsClient("http://x", "u", "p")
    server = McpServer(c, {})
    assert not any("create_" in name or "update_" in name or "delete_" in name for name in server._by_name)


def test_checkout_lifecycle_once_without_retries():
    from ips_mcp.server import McpServer
    calls = []
    identity = {"objectID": 12, "objectGUID": "guid", "objectType": 7,
                "caption": "part", "checkoutBy": 0}
    working = {**identity, "objectID": -12, "checkoutBy": 1}
    state = {"checked_out": False}

    def handler(request):
        calls.append((request.method, request.url.path, json.loads(request.content or b"{}")))
        if request.method == "GET" and request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        if request.method == "GET":
            obj = dict(working if request.url.path.endswith("/-12") and state["checked_out"] else identity)
            if request.url.path.endswith("/-12") and state["checked_out"]:
                obj["checkoutBy"] = 1
            return httpx.Response(200, json={"entity": obj})
        if request.url.path.endswith("/checkOut"):
            state["checked_out"] = True
            return httpx.Response(200, json={"result": -12})
        if request.url.path.endswith("/checkIn"):
            state["checked_out"] = False
            return httpx.Response(200, json={"result": -12})
        if request.url.path.endswith("/cancelChanges"):
            state["checked_out"] = False
        return httpx.Response(200, json={"result": {}})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    readonly = McpServer(c, {})
    assert "ips_prepare_checkout_object" not in readonly._by_name
    prep = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 12})
    assert len(calls) == 1
    checked = server._by_name["ips_commit_checkout_object"]["call"]({"request_id": prep["request_id"]})
    assert checked["workingCopyId"] == -12 and checked["result"] == {"result": -12}
    assert ("POST", "/core/api/objects/12/checkOut", {}) in calls
    try:
        server._by_name["ips_commit_checkout_object"]["call"]({"request_id": prep["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("checkout request reused")

    prep = server._by_name["ips_prepare_finish_checkout"]["call"]({"working_copy_id": -12, "action": "checkin"})
    start = len(calls)
    done = server._by_name["ips_commit_finish_checkout"]["call"]({"request_id": prep["request_id"]})
    assert done["status"] == "ok"
    assert [x[1] for x in calls[start:start + 4]] == [
        "/core/api/objects/-12", "/core/api/objects/-12/saveChanges",
        "/core/api/objects/-12", "/core/api/objects/-12/checkIn"]
    before_retry = len(calls)
    try:
        server._by_name["ips_commit_finish_checkout"]["call"]({"request_id": prep["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("finish request reused")
    assert len(calls) == before_retry

    checkout_again = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 12})
    server._by_name["ips_commit_checkout_object"]["call"]({"request_id": checkout_again["request_id"]})
    prep = server._by_name["ips_prepare_finish_checkout"]["call"]({"working_copy_id": -12, "action": "cancel"})
    server._by_name["ips_commit_finish_checkout"]["call"]({"request_id": prep["request_id"]})
    assert ("POST", "/core/api/objects/cancelChanges", [-12]) in calls


def test_checkout_fail_closed_and_finish_untracked():
    from ips_mcp.server import McpServer
    current = {"objectID": 9, "objectGUID": "g", "objectType": 3,
               "caption": "x", "checkoutBy": 0}

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"entity": dict(current)})
        return httpx.Response(200, json={"result": -9})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prep_finish = server._by_name["ips_prepare_finish_checkout"]["call"]
    try:
        prep_finish({"working_copy_id": 999, "action": "checkin"})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("untracked working copy accepted")
    for malformed in ({**current, "objectGUID": ""}, {k: v for k, v in current.items() if k != "objectGUID"},
                      {**current, "objectType": True}, {k: v for k, v in current.items() if k != "checkoutBy"}):
        c._http = handler_with(lambda request, obj=malformed:
                               httpx.Response(200, json={"entity": obj}))
        try:
            server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 9})
        except ips.IpsError:
            pass
        else:
            raise AssertionError("malformed checkout identity accepted")
    c._http = handler_with(handler)
    prep = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 9})
    current["objectGUID"] = "stale"
    out = server._by_name["ips_commit_checkout_object"]["call"]({"request_id": prep["request_id"]})
    assert out["status"] == "pre_send_rejected"


def test_checkout_register_existing_negative_working_copy_without_mutation():
    from ips_mcp.server import McpServer
    calls = []
    base = {"objectID": 12, "objectGUID": "g", "objectType": 3,
            "caption": "part", "checkoutBy": 1, "readOnly": True}
    working = {**base, "objectID": -12, "readOnly": False}

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        obj = working if request.url.path.endswith("/-12") else base
        return httpx.Response(200, json={"entity": obj})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prep = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": -12})
    assert not any(method == "POST" and path.endswith("/checkOut") for method, path in calls)
    result = server._by_name["ips_commit_checkout_object"]["call"]({"request_id": prep["request_id"]})
    assert result["status"] == "ok" and result["workingCopyId"] == -12
    assert not any(method == "POST" and path.endswith("/checkOut") for method, path in calls)


def test_finish_revalidates_after_save_before_checkin():
    from ips_mcp.server import McpServer
    calls = []
    reads = 0
    obj = {"objectID": 12, "objectGUID": "g", "objectType": 7,
           "caption": "part", "checkoutBy": 0}

    def handler(request):
        nonlocal reads
        calls.append(request.url.path)
        if request.method == "GET" and request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        if request.method == "GET":
            reads += 1
            out = dict(obj)
            if request.url.path.endswith("/-12"):
                out["objectID"] = -12
                out["checkoutBy"] = 1
                if reads >= 6:
                    out["objectID"] = True
            return httpx.Response(200, json={"entity": out})
        return httpx.Response(200, json={"result": -12})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prep = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 12})
    server._by_name["ips_commit_checkout_object"]["call"]({"request_id": prep["request_id"]})
    finish = server._by_name["ips_prepare_finish_checkout"]["call"]({"working_copy_id": -12, "action": "checkin"})
    out = server._by_name["ips_commit_finish_checkout"]["call"]({"request_id": finish["request_id"]})
    assert out["status"] == "partial_unknown" and out["stage"] == "checkIn_preflight"
    assert "/core/api/objects/-12/saveChanges" in calls
    assert "/core/api/objects/-12/checkIn" not in calls


def test_relation_requires_active_matching_checkout():
    from ips_mcp.server import McpServer
    calls = []
    relation_requests = []
    obj = {"objectID": 12, "objectGUID": "g", "objectType": 7,
           "caption": "part", "checkoutBy": 0}
    state = {"active": False}

    def handler(request):
        calls.append(request.url.path)
        if request.method == "GET" and request.url.path.endswith("/currentUsers/userInfo"):
            return httpx.Response(200, json={"userVersionId": 1})
        if request.method == "GET":
            out = dict(obj)
            if request.url.path.endswith("/-12") and state["active"]:
                out["objectID"] = -12
                out["checkoutBy"] = 1
            return httpx.Response(200, json={"entity": out})
        if request.url.path == "/core/api/relations":
            relation_requests.append(request)
            return httpx.Response(200, json={"result": {}})
        if request.url.path.endswith("/checkOut"):
            state["active"] = True
            return httpx.Response(200, json={"result": -12})
        return httpx.Response(200, json={"result": {}})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    schema = server._by_name["ips_prepare_create_relation"]["inputSchema"]["properties"]
    assert "minimum" not in schema["project_version_id"]
    assert schema["relation_type_id"]["minimum"] == 0
    assert schema["part_version_id"]["minimum"] == 0
    args = {"relation_type_id": 1, "project_version_id": -12, "part_version_id": 3}
    before = len(calls)
    try:
        server._by_name["ips_prepare_create_relation"]["call"](args)
    except ips.IpsError:
        pass
    else:
        raise AssertionError("relation allowed with no checkout")
    assert len(calls) == before
    checkout = server._by_name["ips_prepare_checkout_object"]["call"]({"object_id": 12})
    server._by_name["ips_commit_checkout_object"]["call"]({"request_id": checkout["request_id"]})
    relation = server._by_name["ips_prepare_create_relation"]["call"](args)
    server._by_name["ips_commit_create_relation"]["call"]({"request_id": relation["request_id"]})
    request = relation_requests[-1]
    assert request.url.path == "/core/api/relations"
    assert request.url.params["isNeedToLogModificationHistory"] == "true"
    assert json.loads(request.content) == {"relationType": 1, "projVersionId": -12, "partVersionId": 3}

    relation = server._by_name["ips_prepare_create_relation"]["call"]({
        **args, "attribute_values": [{"attributeID": 8, "values": [4]}]})
    server._by_name["ips_commit_create_relation"]["call"]({"request_id": relation["request_id"]})
    assert json.loads(relation_requests[-1].content)["attributeValues"] == [{"attributeId": 8, "values": [4]}]


def test_mutation_401_is_not_retried():
    calls = []
    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(401)
    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    try:
        ips.write_attribute(c, 1, 2, "x")
    except ips.IpsError:
        pass
    else:
        raise AssertionError("401 mutation must fail")
    assert calls == ["/core/api/objects/1/checkOut"], calls


def test_create_object_tool_schema_fields_are_siblings():
    from ips_mcp.server import McpServer
    server = McpServer(ips.IpsClient("http://x", "u", "p"), {}, enable_write=True)
    schema = server._by_name["ips_prepare_create_object"]["inputSchema"]
    props = schema["properties"]
    assert "context_rule" in props and "current_project" in props
    assert "current_project" not in props["context_rule"]["properties"]


def test_delete_is_allowlisted_once_and_never_retries():
    from ips_mcp.server import McpServer
    identity = {"objectID": 1406301, "objectGUID": "25fe60ea-a218-4278-90f0-542128d7ef03"}
    calls = []
    responses = []

    def handler(request):
        calls.append((request.method, request.url.path, dict(request.url.params)))
        if request.url.path.endswith("/delete"):
            return responses.pop(0)
        obj = dict(identity)
        if identity.get("race"):
            obj["objectGUID"] = "different"
        return httpx.Response(200, json={"entity": obj})

    c = ips.IpsClient("http://x", "u", "p")
    c._token = "t"
    c._http = handler_with(handler)
    server = McpServer(c, {}, enable_write=True)
    prepare = server._by_name["ips_prepare_delete_object"]["call"]
    commit = server._by_name["ips_commit_delete_object"]["call"]
    args = {"object_id": identity["objectID"], "object_guid": identity["objectGUID"]}
    preview = prepare(args)
    assert len(calls) == 1 and not any(p.endswith("/delete") for _, p, _ in calls)
    for bad in ({**args, "object_id": True}, {**args, "object_guid": 1406301},
                {**args, "object_id": 1406302}, {**args, "object_guid": "wrong"}):
        try:
            prepare(bad)
        except ips.IpsError:
            pass
        else:
            raise AssertionError("unapproved delete identity accepted")
    for response_entity in (
            {"id": 1406301, "versionID": 1406301, "objectGUID": args["object_guid"]},
            {"objectID": 1406302, "id": 1406301, "versionID": 1406301,
             "objectGUID": args["object_guid"]}):
        c._http = handler_with(lambda request, entity=response_entity:
                               httpx.Response(200, json={"entity": entity}))
        try:
            prepare(args)
        except ips.IpsError:
            pass
        else:
            raise AssertionError("missing or mismatched delete path objectID accepted")
    c._http = handler_with(handler)
    responses.append(httpx.Response(200, json={"result": {}}))
    out = commit({"request_id": preview["request_id"]})
    assert out["status"] == "ok"
    assert calls[-1] == ("POST", "/core/api/objects/1406301/delete",
                         {"deleteMode": "0", "isNeedToLogModificationHistory": "true"})
    before = len(calls)
    try:
        commit({"request_id": preview["request_id"]})
    except ips.IpsError:
        pass
    else:
        raise AssertionError("delete request was reusable")
    assert len(calls) == before

    for response in (httpx.Response(401), httpx.Response(500)):
        pending = prepare(args)
        responses.append(response)
        before = len(calls)
        out = commit({"request_id": pending["request_id"]})
        assert out["status"] == "unknown"
        assert len(calls) == before + 2
        assert calls[-1][1].endswith("/delete")

    pending = prepare(args)
    def broken_transport(request):
        if request.url.path.endswith("/delete"):
            raise httpx.ConnectError("lost", request=request)
        return httpx.Response(200, json={"entity": dict(identity)})
    c._http = handler_with(broken_transport)
    out = commit({"request_id": pending["request_id"]})
    assert out["status"] == "unknown"

    pending = prepare(args)
    identity["race"] = True
    c._http = handler_with(handler)
    before = len(calls)
    out = commit({"request_id": pending["request_id"]})
    assert out["status"] == "pre_send_rejected"
    assert not any(p.endswith("/delete") for _, p, _ in calls[before:])

    identity["race"] = False
    pending = prepare(args)
    def mismatched_path_id(request):
        return httpx.Response(200, json={"entity": {"objectID": 1406302,
                                                       "id": 1406301,
                                                       "versionID": 1406301,
                                                       "objectGUID": args["object_guid"]}})
    c._http = handler_with(mismatched_path_id)
    before = len(calls)
    out = commit({"request_id": pending["request_id"]})
    assert out["status"] == "pre_send_rejected"
    assert not any(p.endswith("/delete") for _, p, _ in calls[before:])


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
