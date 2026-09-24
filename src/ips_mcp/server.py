"""IPS-MCP: read-only MCP-сервер над IPS Web API.

Транспорт — stdio (JSON-RPC 2.0 построчно). SDK не используется:
протокол MCP для фиксированного набора read-only tools закрывается stdlib.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

from . import ips

KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")


def _audit_line(path, rec):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def _err(resp):
    try:
        return resp.json()
    except Exception:
        return {"detail": resp.text[:200]}


def build_tools(client, gloss):
    def make(spec):
        def handler(args):
            return spec["call"](client, gloss, args)
        return {"name": spec["name"], "description": spec["description"],
                "inputSchema": spec["schema"], "call": handler}

    get_obj = lambda path: client.request(
        "GET", path).get("entity") or {}

    tools = [
        {
            "name": "ips_get_current_user",
            "description": "Показать пользователя, роль и уровень доступа текущей сессии IPS, без выдачи токенов.",
            "schema": {"type": "object", "properties": {}, "required": []},
            "call": lambda c, g, a: c.request("GET", "/core/api/currentUsers/userInfo"),
        },
        {
            "name": "ips_get_object",
            "description": "Получить карточку объекта IPS по objectID (идентификатор версии). Возвращает ObjectDto.",
            "schema": {"type": "object", "properties": {"object_id": {"type": "integer"}},
                       "required": ["object_id"]},
            "call": lambda c, g, a: ips.decorate_object(
                get_obj(f"/core/api/objects/{a['object_id']}"), g),
        },
        {
            "name": "ips_get_object_attributes",
            "description": "Получить значения атрибутов объекта IPS. Поддерживает пагинацию: page, page_size. Массив атрибутов.",
            "schema": {"type": "object",
                       "properties": {"object_id": {"type": "integer"},
                                      "page": {"type": "integer"},
                                      "page_size": {"type": "integer"}},
                       "required": ["object_id"]},
            "call": lambda c, g, a: ips.paged(
                ips.resolve_object_links(
                    c, g, ips.decorate_attrs(
                        c.request("GET", f"/core/api/objects/{a['object_id']}/attributesValues"), g)),
                a.get("page"), a.get("page_size")),
        },
        {
            "name": "ips_get_composition",
            "description": "Получить состав объекта: пары object+relation. Поддерживает пагинацию: page, page_size.",
            "schema": {"type": "object",
                       "properties": {"project_version_id": {"type": "integer"},
                                      "page": {"type": "integer"},
                                      "page_size": {"type": "integer"}},
                       "required": ["project_version_id"]},
            "call": lambda c, g, a: ips.paged(
                ips.decorate_composition(
                    c.request("POST", f"/core/api/objects/{a['project_version_id']}/composition",
                              json={}), g),
                a.get("page"), a.get("page_size")),
        },
        {
            "name": "ips_get_composition_filtered",
            "description": "Получить состав объекта с фильтром по типу связи и типам частей. Поддерживает пагинацию: page, page_size.",
            "schema": {"type": "object",
                       "properties": {"project_version_id": {"type": "integer"},
                                      "relation_type_id": {"type": "integer"},
                                      "part_type_ids": {"type": "array", "items": {"type": "integer"}},
                                      "page": {"type": "integer"},
                                      "page_size": {"type": "integer"}},
                       "required": ["project_version_id"]},
            "call": lambda c, g, a: ips.paged(
                ips.decorate_composition(
                    c.request("POST", f"/core/api/objects/{a['project_version_id']}/compositionWithParams",
                              json={"relationTypeId": a.get("relation_type_id"),
                                    "partTypeIds": a.get("part_type_ids")}), g),
                a.get("page"), a.get("page_size")),
        },
        {
            "name": "ips_get_relation",
            "description": "Получить связь IPS по relationID.",
            "schema": {"type": "object", "properties": {"relation_id": {"type": "integer"}},
                       "required": ["relation_id"]},
            "call": lambda c, g, a: ips.decorate_relation(
                get_obj(f"/core/api/relations/{a['relation_id']}"), g),
        },
        {
            "name": "ips_get_relation_attributes",
            "description": "Получить значения атрибутов связи IPS по relationID. Поддерживает пагинацию: page, page_size.",
            "schema": {"type": "object",
                       "properties": {"relation_id": {"type": "integer"},
                                      "page": {"type": "integer"},
                                      "page_size": {"type": "integer"}},
                       "required": ["relation_id"]},
            "call": lambda c, g, a: ips.paged(
                ips.resolve_object_links(
                    c, g, ips.decorate_attrs(
                        c.request("GET", f"/core/api/relations/{a['relation_id']}/attributesValues"), g)),
                a.get("page"), a.get("page_size")),
        },
        {
            "name": "ips_get_object_type",
            "description": "Получить метаданные типа объекта IPS по числовому ID.",
            "schema": {"type": "object", "properties": {"type_id": {"type": "integer"}},
                       "required": ["type_id"]},
            "call": lambda c, g, a: c.request("GET", f"/core/api/metadata/objectTypes/{a['type_id']}"),
        },
        {
            "name": "ips_get_attribute_type",
            "description": "Получить метаданные атрибута IPS по числовому ID.",
            "schema": {"type": "object", "properties": {"attribute_id": {"type": "integer"}},
                       "required": ["attribute_id"]},
            "call": lambda c, g, a: c.request("GET", f"/core/api/metadata/attributeTypes/{a['attribute_id']}"),
        },
        {
            "name": "ips_get_relation_type",
            "description": "Получить метаданные типа связи IPS по числовому ID.",
            "schema": {"type": "object", "properties": {"relation_type_id": {"type": "integer"}},
                       "required": ["relation_type_id"]},
            "call": lambda c, g, a: c.request("GET", f"/core/api/metadata/relationTypes/{a['relation_type_id']}"),
        },
        {
            "name": "ips_get_lifecycle",
            "description": "Получить список схем жизненного цикла IPS.",
            "schema": {"type": "object", "properties": {}, "required": []},
            "call": lambda c, g, a: c.request("GET", "/core/api/metadata/lifeCycleSchemes"),
        },
        {
            "name": "ips_get_composition_tree",
            "description": "Получить дерево состава рекурсивно с ограничением глубины (max_depth, default 3) и числа узлов (max_nodes, default 500).",
            "schema": {"type": "object",
                       "properties": {"project_version_id": {"type": "integer"},
                                      "max_depth": {"type": "integer", "default": 3},
                                      "max_nodes": {"type": "integer", "default": 500}},
                       "required": ["project_version_id"]},
            "call": _composition_tree,
        },
    ]
    return [make(t) for t in tools]


def _composition_tree(client, gloss, args):
    root = args["project_version_id"]
    max_depth = int(args.get("max_depth") or 3)
    max_nodes = int(args.get("max_nodes") or 500)

    def walk(version_id, depth):
        if len(out) >= max_nodes or depth > max_depth:
            return
        pairs = client.request(
            "POST", f"/core/api/objects/{version_id}/composition", json={})
        for pair in pairs:
            if len(out) >= max_nodes:
                return
            obj = ips.decorate_object(pair.get("object", {}), gloss)
            rel = ips.decorate_relation(pair.get("relation", {}), gloss)
            key = obj.get("objectID")
            if key in seen:
                continue
            seen.add(key)
            out.append({"object": obj, "relation": rel, "depth": depth})
            walk(key, depth + 1)

    out = []
    seen = set()
    walk(root, 1)
    return {"root": root, "max_depth": max_depth, "max_nodes": max_nodes,
            "count": len(out), "items": out}


class McpServer:
    def __init__(self, client, gloss, audit_path=None):
        self._tools = build_tools(client, gloss)
        self._by_name = {t["name"]: t for t in self._tools}
        self._audit_path = audit_path

    def _result(self, msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _tool_error(self, msg_id, text):
        return {"jsonrpc": "2.0", "id": msg_id, "isError": True,
                "result": {"content": [{"type": "text", "text": text}]}}

    def handle(self, msg):
        if not isinstance(msg, dict) or "id" not in msg:
            return None  # уведомление
        method = msg.get("method")
        rid = msg["id"]
        params = msg.get("params") or {}

        if method == "initialize":
            offer = next((p for p in params.get("supportedProtocolVersions", [])
                          if p in KNOWN_PROTOCOLS), KNOWN_PROTOCOLS[1])
            return self._result(rid, {
                "protocolVersion": offer,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ips-mcp", "version": "0.1.0"},
            })
        if method == "ping":
            return self._result(rid, {})
        if method == "tools/list":
            return self._result(rid, {"tools": [
                {"name": t["name"], "description": t["description"],
                 "inputSchema": t["inputSchema"]} for t in self._tools]})
        if method == "tools/call":
            name = params.get("name")
            args = params.get("arguments") or {}
            tool = self._by_name.get(name)
            if tool is None:
                return self._tool_error(rid, f"неизвестный инструмент: {name}")
            t0 = time.monotonic()
            status = "ok"
            try:
                out = tool["call"](args)
                text = json.dumps(out, ensure_ascii=False, indent=2)
                result = {
                    "jsonrpc": "2.0", "id": rid,
                    "result": {"content": [{"type": "text", "text": text}]}}
            except ips.IpsError as e:
                status = "error"
                result = self._tool_error(rid, str(e) + f"\nIPS detail: {e.detail}")
            except Exception as e:  # never show tokens/creds to client
                status = "error"
                result = self._tool_error(rid, f"внутренняя ошибка: {type(e).__name__}")
            _audit_line(self._audit_path, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "tool": name,
                "inputs": args,
                "duration_ms": round((time.monotonic() - t0) * 1000),
                "status": status,
            })
            return result

        return self._tool_error(rid, f"метод не поддерживается: {method}")


def run():
    base = os.environ.get("IPS_BASE_URL", "http://192.168.80.70:8080")
    login = os.environ.get("IPS_LOGIN")
    password = os.environ.get("IPS_PASSWORD")
    role_id = int(os.environ.get("IPS_ROLE_ID", "0"))
    gloss_db = os.environ.get("IPS_GLOSS_DB",
                              os.path.join(os.path.dirname(__file__),
                                           "..", "..", "gloss", "generated", "gloss.db"))
    audit_path = os.environ.get("IPS_AUDIT_LOG")
    if not login or not password:
        sys.stderr.write("IPS_LOGIN и IPS_PASSWORD обязательны\n")
        sys.exit(2)

    client = ips.IpsClient(base, login, password, role_id=role_id)
    gloss = ips.load_gloss(gloss_db) if os.path.exists(gloss_db) else {}
    server = McpServer(client, gloss, audit_path=audit_path)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run()