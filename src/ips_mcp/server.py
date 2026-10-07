"""IPS-MCP: read-only MCP-сервер над IPS Web API.

Транспорт — stdio (JSON-RPC 2.0 построчно). SDK не используется:
протокол MCP для фиксированного набора read-only tools закрывается stdlib.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from uuid import uuid4

from . import ips

KNOWN_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

# Корень проекта: src/ips_mcp/server.py -> src/ips_mcp -> src -> <root>.
# Считаемся от файла, а не от cwd: конфиг и справочник должны находиться
# при запуске из любого каталога (opencode, CI, IDE).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULTS = {
    "IPS_BASE_URL": "http://192.168.80.70:8080",
    "IPS_ROLE_ID": 0,
    "IPS_GLOSS_DB": "gloss/generated/gloss.db",
    "IPS_AUDIT_LOG": "",
    "IPS_ENABLE_WRITE": False,
}

PATH_FIELDS = ("IPS_GLOSS_DB", "IPS_AUDIT_LOG")


def _audit_line(path, rec):
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class WriteStore:
    """Хранилище подготовленных операций. Подтверждение разовое (Once):
    после commit запрос удаляется, повторный вызов невозможен."""

    def __init__(self):
        self._pending = {}
        self._active_checkouts = {}

    def put(self, record):
        rid = uuid4().hex
        self._pending[rid] = record
        return rid

    def take(self, request_id):
        return self._pending.pop(request_id, None)

    def checkout(self, record):
        self._active_checkouts[record["working_copy_id"]] = record

    def active_checkout(self, working_copy_id):
        return self._active_checkouts.get(working_copy_id)

    def remove_checkout(self, working_copy_id):
        self._active_checkouts.pop(working_copy_id, None)


def build_tools(client, gloss):
    def make(spec):
        def handler(args):
            return spec["call"](client, gloss, args)
        return {"name": spec["name"], "description": spec["description"],
                "inputSchema": spec["schema"], "call": handler}

    get_obj = lambda path: client.request(
        "GET", path).get("entity") or {}

    def search(c, g, a):
        object_type = a["object_type_id"]
        attrs = a.get("attribute_ids_to_select", [])
        record_count = a.get("record_count", 50)
        if (isinstance(object_type, bool) or not isinstance(object_type, int) or object_type < 0 or
                not isinstance(attrs, list) or len(attrs) > 100 or
                any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in attrs) or
                isinstance(record_count, bool) or not isinstance(record_count, int) or
                not 1 <= record_count <= 1000):
            raise ips.IpsError(400, "некорректные параметры поиска", "/core/api/objects/select")
        body = {"objectTypeId": object_type, "attributeIdsToSelect": attrs,
                "recordCount": record_count}
        condition = a.get("condition")
        if condition is not None:
            if (not isinstance(condition, dict) or set(condition) != {"attribute_id", "value"} or
                    isinstance(condition["attribute_id"], bool) or not isinstance(condition["attribute_id"], int) or condition["attribute_id"] < 0 or
                    not isinstance(condition["value"], (str, int)) or isinstance(condition["value"], bool)):
                raise ips.IpsError(400, "condition: только attribute_id и строковое/числовое value",
                                   "/core/api/objects/select")
            body["conditions"] = [{"attributeId": condition["attribute_id"],
                                    "relationalOperator": "equal", "value": condition["value"]}]
        return c.request("POST", "/core/api/objects/select", json=body)

    tools = [
        {"name": "ips_search_objects",
         "description": "Read-only поиск объектов одного типа по безопасному равенству.",
         "schema": {"type": "object", "additionalProperties": False,
                    "properties": {"object_type_id": {"type": "integer", "minimum": 0},
                                   "attribute_ids_to_select": {"type": "array", "items": {"type": "integer", "minimum": 0}, "maxItems": 100},
                                   "condition": {"type": "object", "additionalProperties": False,
                                                 "properties": {"attribute_id": {"type": "integer", "minimum": 0}, "value": {"type": ["string", "integer"]}},
                                                 "required": ["attribute_id", "value"]},
                                   "record_count": {"type": "integer", "minimum": 1, "maximum": 1000}},
                    "required": ["object_type_id"]}, "call": search},
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


def build_write_tools(client, gloss, store):
    """Двухфазные write-операции: preview → одноразовое подтверждение → commit.

    Не регистрируются без IPS_ENABLE_WRITE=1.
    # ponytail: при сбое после checkout рабочая копия не освобождается;
    # добавить release/checkin после фиксации надёжного отката в IPS Web API
    """

    def prepare(a):
        object_id = a["object_id"]
        attribute_id = a["attribute_id"]
        cur = ips.snapshot_attr(client, object_id, attribute_id)
        if cur is None:
            raise ips.IpsError(404, f"атрибут {attribute_id} не найден",
                               f"/core/api/objects/{object_id}/attributesValues")
        old = cur.get("values")
        new = a.get("value")
        if old == [new]:
            raise ips.IpsError(409, "новое значение совпадает с текущим, запись не требуется",
                               f"/core/api/objects/{object_id}/attributes")
        rid = store.put({
            "object_id": object_id,
            "attribute_id": attribute_id,
            "value": new,
            "old_value": old,
        })
        return {
            "request_id": rid,
            "preview": {
                "action": "update_attribute",
                "objectId": object_id,
                "attribute": {
                    "id": attribute_id,
                    "name": cur.get("attributeName"),
                    "old_value": old,
                    "new_value": new,
                },
                "operations": ["checkOut", "edit", "attributes", "saveChanges", "checkIn"],
                "note": "Операция НЕ выполнена. Подтверждение разовое: вызовите "
                        "ips_commit_update_attribute с request_id.",
            },
        }

    def commit(a):
        request_id = a["request_id"]
        rec = store.take(request_id)
        if rec is None:
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)",
                               "/write/commit")
        current = ips.snapshot_attr(client, rec["object_id"], rec["attribute_id"])
        if current is None or current.get("values") != rec["old_value"]:
            raise ips.IpsError(409, "значение атрибута изменилось после preview; подготовьте операцию заново",
                               f"/core/api/objects/{rec['object_id']}/attributesValues")
        result = ips.write_attribute(client, rec["object_id"],
                                     rec["attribute_id"], rec["value"])
        verified = ips.read_attr_value(client, rec["object_id"], rec["attribute_id"])
        return {
            "operation": "update_attribute",
            "status": "ok",
            **result,
            "verify": {"attributeId": rec["attribute_id"], "value": verified},
        }

    def make(spec):
        def handler(args):
            return spec["call"](args)
        return {"name": spec["name"], "description": spec["description"],
                "inputSchema": spec["schema"], "call": handler}

    def create_prepare(a):
        typ, attrs, prototype_id = a.get("object_type_id"), a.get("attributes", []), a.get("prototype_id")
        if prototype_id is not None:
            if isinstance(prototype_id, bool) or not isinstance(prototype_id, int) or prototype_id < 0 or typ is not None or attrs:
                raise ips.IpsError(400, "prototype_id нельзя сочетать с object_type_id/attributes", "/write/objects")
            prototype = client.request("GET", f"/core/api/objects/{prototype_id}").get("entity") or {}
            if prototype.get("objectID") != prototype_id:
                raise ips.IpsError(404, "прототип не найден", f"/core/api/objects/{prototype_id}")
            rid = store.put({"kind": "create", "prototype_id": prototype_id,
                             "prototype_guid": prototype.get("objectGUID")})
            return {"request_id": rid, "preview": {
                "action": "create_object", "prototypeId": prototype_id,
                "prototype": prototype.get("caption"), "operations": [
                    "POST /core/api/objects/CreateByPrototype",
                    "POST /core/api/objects/{workingCopyId}/commitCreation"]}}
        if isinstance(typ, bool) or not isinstance(typ, int) or typ < 0 or not isinstance(attrs, list) or len(attrs) > 100:
            raise ips.IpsError(400, "некорректные параметры создания объекта", "/write/objects")
        if any(not isinstance(x, dict) or set(x) != {"attributeID", "values"} or
               isinstance(x["attributeID"], bool) or not isinstance(x["attributeID"], int) or
               x["attributeID"] < 0 or not isinstance(x["values"], list) or
               len(x["values"]) > 100 for x in attrs):
            raise ips.IpsError(400, "attributes допускает только attributeID и values", "/write/objects")
        payload = {"objectType": typ, "attributes": attrs}
        context = a.get("context_rule")
        if context is not None:
            if (not isinstance(context, dict) or
                    set(context) - {"versionRuleObjectId", "editingContextId", "editingContextMode"} or
                    any(isinstance(context.get(k), bool) or not isinstance(context.get(k), int) or context[k] < 0
                        for k in ("versionRuleObjectId", "editingContextId") if k in context) or
                    ("editingContextMode" in context and context["editingContextMode"] not in {"default", "autoUpdate"})):
                raise ips.IpsError(400, "context_rule не соответствует ContextRuleDto", "/write/objects")
            payload["contextRule"] = context
        project = a.get("current_project")
        if project is not None:
            if (not isinstance(project, dict) or set(project) - {"id", "mode"} or
                    ("id" in project and (isinstance(project["id"], bool) or not isinstance(project["id"], int) or project["id"] < 0)) or
                    ("mode" in project and project["mode"] not in {"none", "currentProject", "userProjects", "onlyCurrentProject"})):
                raise ips.IpsError(400, "current_project не соответствует CurrentProjectDto", "/write/objects")
            payload["currentProjectDto"] = project
        rid = store.put({"kind": "create", "payload": payload})
        return {"request_id": rid, "preview": {"action": "create_object", "payload": payload, "operations": ["POST /core/api/objects", "POST /core/api/objects/{id}/commitCreation"]}}

    def create_commit(a):
        rec = store.take(a["request_id"])
        if not rec or rec["kind"] != "create":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        if "prototype_id" in rec:
            prototype = client.request("GET", f"/core/api/objects/{rec['prototype_id']}").get("entity") or {}
            if (prototype.get("objectID") != rec["prototype_id"] or
                    prototype.get("objectGUID") != rec["prototype_guid"]):
                return {"operation": "create_by_prototype", "status": "pre_send_rejected"}
            try:
                made = client.mutation("POST", "/core/api/objects/CreateByPrototype",
                                      params={"isNeedToLogModificationHistory": True},
                                      json={"prototypeId": rec["prototype_id"]})
            except Exception as e:
                return {"operation": "create_by_prototype", "status": "unknown",
                        "stage": "create", "detail": str(e),
                        "guidance": "Сверьте IPS read-only; не повторяйте вслепую."}
            result = made.get("result") if isinstance(made, dict) else None
            dto = result.get("objectDto") if isinstance(result, dict) else None
            wc = dto.get("objectID") if isinstance(dto, dict) else None
            related = result.get("relatedObjectIds", []) if isinstance(result, dict) else None
            if (isinstance(wc, bool) or not isinstance(wc, int) or wc >= 0 or
                    not isinstance(related, list) or
                    any(isinstance(x, bool) or not isinstance(x, int) for x in related)):
                return {"operation": "create_by_prototype", "status": "unknown",
                        "stage": "create_response", "result": made}
            try:
                committed = client.mutation(
                    "POST", f"/core/api/objects/{wc}/commitCreation",
                    params={"isNeedToLogModificationHistory": True},
                    json={"deleteOnException": False, "autoCheckout": False,
                          "relatedObjectIds": related})
                data = committed.get("result") if isinstance(committed, dict) else None
                object_id = data.get("objectId") if isinstance(data, dict) else None
                if isinstance(object_id, bool) or not isinstance(object_id, int):
                    raise ips.IpsError(502, "commitCreation не вернул result.objectId",
                                       f"/core/api/objects/{wc}/commitCreation")
                return {"operation": "create_by_prototype", "status": "ok",
                        "prototype_id": rec["prototype_id"], "working_copy_id": wc,
                        "related_object_ids": related, "object_id": object_id,
                        "result": committed}
            except Exception as e:
                return {"operation": "create_by_prototype", "status": "partial_unknown",
                        "stage": "commitCreation", "prototype_id": rec["prototype_id"],
                        "working_copy_id": wc, "related_object_ids": related,
                        "detail": str(e),
                        "guidance": "Проверьте рабочую копию; не повторяйте commit вслепую."}
        try:
            made = client.mutation("POST", "/core/api/objects", json=rec["payload"])
        except Exception as e:
            if isinstance(e, ValueError):
                return {"operation": "create_object", "status": "pre_send_rejected", "stage": "create_validation", "detail": str(e), "guidance": "Запрос не отправлен; исправьте локальные данные перед новой попыткой."}
            if isinstance(e, ips.IpsError) and e.path != "/core/api/objects":
                return {"operation": "create_object", "status": "pre_send_rejected", "stage": "create_authentication", "detail": str(e), "guidance": "Создание не отправлено; устраните ошибку перед новой попыткой."}
            return {"operation": "create_object", "status": "unknown", "stage": "create", "detail": str(e), "guidance": "Не повторяйте вслепую; сверьте объекты в IPS по типу, атрибутам и времени запроса."}
        entity = made.get("entity") or made.get("result") or made if isinstance(made, dict) else None
        object_id = (entity.get("objectID") if "objectID" in entity else entity.get("objectId")) if isinstance(entity, dict) else None
        if isinstance(object_id, bool) or not isinstance(object_id, int):
            return {"operation": "create_object", "status": "unknown", "stage": "create_response", "detail": "create не вернул integer objectID", "guidance": "Создание могло пройти; сверьте объекты в IPS, не повторяйте вслепую."}
        try:
            result = client.mutation("POST", f"/core/api/objects/{object_id}/commitCreation", json={"deleteOnException": False, "autoCheckout": False})
            committed = result.get("result") if isinstance(result, dict) else None
            if not isinstance(committed, dict) or isinstance(committed.get("objectId"), bool) or not isinstance(committed.get("objectId"), int):
                raise ips.IpsError(502, "commitCreation не вернул result.objectId", f"/core/api/objects/{object_id}/commitCreation")
            return {"operation": "create_object", "status": "ok", "object_id": object_id, "result": result}
        except Exception as e:
            return {"operation": "create_object", "status": "partial_unknown", "stage": "commitCreation", "object_id": object_id, "detail": str(e), "guidance": "Объект уже создан; проверьте его состояние в IPS и не повторяйте commit вслепую."}

    def create_by_prototype_prepare(a):
        prototype_id = a.get("prototype_id")
        if isinstance(prototype_id, bool) or not isinstance(prototype_id, int) or prototype_id < 0:
            raise ips.IpsError(400, "prototype_id должен быть неотрицательным integer", "/write/create-by-prototype")
        prototype = client.request("GET", f"/core/api/objects/{prototype_id}").get("entity") or {}
        if prototype.get("objectID") != prototype_id:
            raise ips.IpsError(404, "прототип не найден", f"/core/api/objects/{prototype_id}")
        rid = store.put({"kind": "create_by_prototype", "prototype_id": prototype_id,
                         "prototype_guid": prototype.get("objectGUID")})
        return {"request_id": rid, "preview": {
            "action": "create_by_prototype", "prototypeId": prototype_id,
            "prototype": prototype.get("caption"), "operations": [
                "POST /core/api/objects/CreateByPrototype",
                "POST /core/api/objects/{workingCopyId}/commitCreation"],
            "note": "Создание не выполнено. Подтвердите отдельным вызовом commit."}}

    def create_by_prototype_commit(a):
        rec = store.take(a.get("request_id"))
        if not rec or rec.get("kind") != "create_by_prototype":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        prototype = client.request("GET", f"/core/api/objects/{rec['prototype_id']}").get("entity") or {}
        if (prototype.get("objectID") != rec["prototype_id"] or
                prototype.get("objectGUID") != rec["prototype_guid"]):
            return {"operation": "create_by_prototype", "status": "pre_send_rejected",
                    "guidance": "Прототип изменился или не найден; подготовьте preview заново."}
        try:
            made = client.mutation("POST", "/core/api/objects/CreateByPrototype",
                                  params={"isNeedToLogModificationHistory": True},
                                  json={"prototypeId": rec["prototype_id"]})
        except Exception as e:
            return {"operation": "create_by_prototype", "status": "unknown",
                    "stage": "create", "detail": str(e),
                    "guidance": "Создание могло пройти; сверьте IPS read-only и не повторяйте вслепую."}
        result = made.get("result") if isinstance(made, dict) else None
        dto = result.get("objectDto") if isinstance(result, dict) else None
        wc = dto.get("objectID") if isinstance(dto, dict) else None
        related = result.get("relatedObjectIds", []) if isinstance(result, dict) else None
        if (isinstance(wc, bool) or not isinstance(wc, int) or wc >= 0 or
                not isinstance(related, list) or any(isinstance(x, bool) or not isinstance(x, int) for x in related)):
            return {"operation": "create_by_prototype", "status": "unknown",
                    "stage": "create_response", "result": made,
                    "guidance": "Создание могло пройти; сверьте IPS read-only и не повторяйте вслепую."}
        try:
            committed = client.mutation(
                "POST", f"/core/api/objects/{wc}/commitCreation",
                params={"isNeedToLogModificationHistory": True},
                json={"deleteOnException": False, "autoCheckout": False,
                      "relatedObjectIds": related})
            committed_result = committed.get("result") if isinstance(committed, dict) else None
            object_id = committed_result.get("objectId") if isinstance(committed_result, dict) else None
            if isinstance(object_id, bool) or not isinstance(object_id, int):
                raise ips.IpsError(502, "commitCreation не вернул result.objectId",
                                   f"/core/api/objects/{wc}/commitCreation")
            return {"operation": "create_by_prototype", "status": "ok",
                    "prototype_id": rec["prototype_id"], "working_copy_id": wc,
                    "related_object_ids": related, "object_id": object_id,
                    "result": committed}
        except Exception as e:
            return {"operation": "create_by_prototype", "status": "partial_unknown",
                    "stage": "commitCreation", "prototype_id": rec["prototype_id"],
                    "working_copy_id": wc, "related_object_ids": related,
                    "detail": str(e),
                    "guidance": "Проверьте рабочую копию и связанные объекты; не повторяйте commit вслепую."}

    def relation_prepare(a):
        keys = ("relation_type_id", "project_version_id", "part_version_id")
        if (isinstance(a.get("relation_type_id"), bool) or
                not isinstance(a.get("relation_type_id"), int) or a["relation_type_id"] < 0 or
                isinstance(a.get("project_version_id"), bool) or
                not isinstance(a.get("project_version_id"), int) or
                isinstance(a.get("part_version_id"), bool) or
                not isinstance(a.get("part_version_id"), int) or a["part_version_id"] < 0):
            raise ips.IpsError(400, "ID связи и объектов должны быть integer", "/write/relations")
        values = a.get("attribute_values", [])
        if (not isinstance(values, list) or len(values) > 100 or any(
                not isinstance(x, dict) or set(x) != {"attributeID", "values"} or
                isinstance(x["attributeID"], bool) or not isinstance(x["attributeID"], int) or
                x["attributeID"] < 0 or not isinstance(x["values"], list) or
                len(x["values"]) > 100 for x in values)):
            raise ips.IpsError(400, "некорректный attribute_values", "/write/relations")
        values = [{"attributeId": x["attributeID"], "values": x["values"]} for x in values]
        payload = {"relationType": a["relation_type_id"], "projVersionId": a["project_version_id"], "partVersionId": a["part_version_id"]}
        if values:
            payload["attributeValues"] = values
        active = store.active_checkout(a["project_version_id"])
        if not active:
            raise ips.IpsError(409, "родитель не был checked out этим MCP-сервером", "/write/relations")
        validate_checkout(a["project_version_id"], active)
        rid = store.put({"kind": "relation", "payload": payload})
        return {"request_id": rid, "preview": {"action": "create_relation", "payload": payload, "risk": "Используйте checked-out parent workingCopyId; после создания завершите родителя ips_prepare_finish_checkout / ips_commit_finish_checkout."}}

    def relation_commit(a):
        rec = store.take(a["request_id"])
        if not rec or rec["kind"] != "relation":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        try:
            active = store.active_checkout(rec["payload"]["projVersionId"])
            if not active:
                return {"operation": "create_relation", "status": "pre_send_rejected"}
            validate_checkout(rec["payload"]["projVersionId"], active)
            result = client.mutation("POST", "/core/api/relations",
                                     params={"isNeedToLogModificationHistory": True},
                                     json=rec["payload"])
        except ips.IpsError as e:
            if e.path == "/core/api/relations":
                return {"operation": "create_relation", "status": "partial_unknown", "detail": str(e)}
            raise
        except ValueError as e:
            return {"operation": "create_relation", "status": "partial_unknown", "detail": str(e)}
        return {"operation": "create_relation", "status": "ok", "result": result}

    def delete_prepare(a):
        object_id, guid = a.get("object_id"), a.get("object_guid")
        if (isinstance(object_id, bool) or not isinstance(object_id, int) or
                not isinstance(guid, str) or object_id != 1406301 or
                guid != "25fe60ea-a218-4278-90f0-542128d7ef03"):
            raise ips.IpsError(400, "удаление разрешено только для заданной пары objectID/objectGUID", "/write/delete")
        obj = client.request("GET", f"/core/api/objects/{object_id}").get("entity") or {}
        path_id = obj.get("objectID")
        if (isinstance(path_id, bool) or not isinstance(path_id, int) or
                path_id != object_id or obj.get("objectGUID") != guid):
            raise ips.IpsError(409, "objectID/objectGUID не совпадают", f"/core/api/objects/{object_id}")
        rid = store.put({"kind": "delete", "delete_path_id": path_id, "object_guid": guid})
        return {"request_id": rid, "preview": {"action": "delete_object", "objectID": object_id,
                "objectGUID": guid, "irreversible": True, "operations": ["POST delete"],
                "note": "Не выполнено. Cascade semantics не проверена."}}

    def delete_commit(a):
        rec = store.take(a["request_id"])
        if not rec or rec.get("kind") != "delete":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        path = f"/core/api/objects/{rec['delete_path_id']}/delete"
        try:
            obj = client.request("GET", f"/core/api/objects/{rec['delete_path_id']}").get("entity") or {}
            current_path_id = obj.get("objectID")
            if (isinstance(current_path_id, bool) or not isinstance(current_path_id, int) or
                    current_path_id != rec["delete_path_id"] or obj.get("objectGUID") != rec["object_guid"]):
                return {"operation": "delete_object", "status": "pre_send_rejected"}
            result = client.mutation("POST", path, params={"deleteMode": 0, "isNeedToLogModificationHistory": True})
            return {"operation": "delete_object", "status": "ok", "result": result}
        except Exception as e:
            return {"operation": "delete_object", "status": "unknown", "detail": str(e),
                    "guidance": "Сверьте состояние через ips_get_object; не повторяйте вслепую."}

    def checkout_prepare(a):
        object_id = a.get("object_id")
        if isinstance(object_id, bool) or not isinstance(object_id, int):
            raise ips.IpsError(400, "object_id должен быть integer", "/write/checkout")
        base_id = abs(object_id)
        obj = client.request("GET", f"/core/api/objects/{base_id}").get("entity") or {}
        identity = checkout_identity(obj, base_id)
        if object_id < 0 or identity["checkoutBy"] != 0:
            user_info = client.request("GET", "/core/api/currentUsers/userInfo")
            nested = user_info.get("userInfo") if isinstance(user_info, dict) else None
            user_id = user_info.get("userVersionId") if isinstance(user_info, dict) else None
            if user_id is None and isinstance(nested, dict):
                user_id = nested.get("userVersionId")
            wc = object_id if object_id < 0 else -base_id
            working = client.request("GET", f"/core/api/objects/{wc}").get("entity") or {}
            if (isinstance(user_id, bool) or not isinstance(user_id, int) or
                    identity["checkoutBy"] != user_id or
                    not identity_matches(working, {**identity, "objectID": wc, "checkoutBy": user_id}) or
                    working.get("readOnly") is not False):
                raise ips.IpsError(409, "рабочая копия отсутствует, не принадлежит текущему пользователю или имеет некорректную identity", f"/core/api/objects/{wc}")
            rid = store.put({"kind": "checkout", "identity": identity,
                             "existing_working_copy_id": wc})
            return {"request_id": rid, "preview": {"action": "checkout", **identity,
                    "workingCopyId": wc}}
        rid = store.put({"kind": "checkout", "identity": identity})
        return {"request_id": rid, "preview": {"action": "checkout", **identity}}

    def checkout_commit(a):
        rec = store.take(a["request_id"])
        if not rec or rec.get("kind") != "checkout":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        identity = rec["identity"]
        try:
            if "existing_working_copy_id" in rec:
                wc = rec["existing_working_copy_id"]
                checked = client.request("GET", f"/core/api/objects/{wc}").get("entity") or {}
                user_info = client.request("GET", "/core/api/currentUsers/userInfo")
                nested = user_info.get("userInfo") if isinstance(user_info, dict) else None
                user_id = user_info.get("userVersionId") if isinstance(user_info, dict) else None
                if user_id is None and isinstance(nested, dict):
                    user_id = nested.get("userVersionId")
                if (not identity_matches(checked, {**identity, "objectID": wc, "checkoutBy": user_id}) or
                        isinstance(user_id, bool) or not isinstance(user_id, int) or
                        checked.get("readOnly") is not False):
                    return {"operation": "checkout", "status": "pre_send_rejected"}
                active = {"working_copy_id": wc, "base_identity": identity,
                          "working_identity": {k: checked.get(k) for k in
                                               ("objectID", "objectGUID", "objectType", "checkoutBy")}}
                store.checkout(active)
                return {"operation": "checkout", "status": "ok", "objectId": identity["objectID"],
                        "workingCopyId": wc, "verify": checked,
                        "guidance": "Используйте workingCopyId без изменений."}
            obj = client.request("GET", f"/core/api/objects/{identity['objectID']}").get("entity") or {}
            if not identity_matches(obj, identity) or obj.get("checkoutBy") != 0:
                return {"operation": "checkout", "status": "pre_send_rejected"}
            result = client.mutation("POST", f"/core/api/objects/{identity['objectID']}/checkOut",
                                    params={"isNeedToLogModificationHistory": True}, json={})
            wc = result.get("result") if isinstance(result, dict) else None
            if isinstance(wc, bool) or not isinstance(wc, int):
                return {"operation": "checkout", "status": "unknown", "result": result,
                        "detail": "checkOut не вернул integer workingCopyId"}
            checked = client.request("GET", f"/core/api/objects/{wc}").get("entity") or {}
            if (not isinstance(checked, dict) or isinstance(checked.get("objectID"), bool) or
                    not isinstance(checked.get("objectID"), int) or checked["objectID"] != wc or
                    not isinstance(checked.get("objectGUID"), str) or checked["objectGUID"] != identity["objectGUID"] or
                    isinstance(checked.get("objectType"), bool) or not isinstance(checked.get("objectType"), int) or
                    checked["objectType"] != identity["objectType"] or "checkoutBy" not in checked or
                    isinstance(checked["checkoutBy"], bool) or not isinstance(checked["checkoutBy"], int) or
                    checked["checkoutBy"] == 0):
                return {"operation": "checkout", "status": "partial_unknown", "result": result,
                        "workingCopyId": wc, "detail": "рабочая копия не прошла проверку identity"}
            user_info = client.request("GET", "/core/api/currentUsers/userInfo")
            if isinstance(user_info, dict):
                nested = user_info.get("userInfo")
                user_id = user_info.get("userVersionId")
                if user_id is None and isinstance(nested, dict):
                    user_id = nested.get("userVersionId")
            else:
                user_id = None
            if (isinstance(user_id, bool) or not isinstance(user_id, int) or
                    user_id != checked["checkoutBy"]):
                return {"operation": "checkout", "status": "partial_unknown", "result": result,
                        "workingCopyId": wc, "verify": checked,
                        "detail": "владелец рабочей копии не совпадает с текущим пользователем"}
            active = {"working_copy_id": wc, "base_identity": identity,
                      "working_identity": {k: checked.get(k) for k in
                                           ("objectID", "objectGUID", "objectType", "checkoutBy")}}
            store.checkout(active)
            return {"operation": "checkout", "status": "ok", "objectId": identity["objectID"],
                    "workingCopyId": wc, "result": result, "verify": checked,
                    "guidance": "Используйте workingCopyId без изменений."}
        except Exception as e:
            return {"operation": "checkout", "status": "unknown", "detail": str(e), "guidance": "Не повторяйте вслепую."}

    def finish_prepare(a):
        wc, action = a.get("working_copy_id"), a.get("action")
        if isinstance(wc, bool) or not isinstance(wc, int) or action not in {"checkin", "cancel"}:
            raise ips.IpsError(400, "нужен integer working_copy_id и action checkin/cancel", "/write/finish")
        active = store.active_checkout(wc)
        if not active:
            raise ips.IpsError(409, "workingCopyId не записан успешным checkout этого MCP-сервера", "/write/finish")
        validate_checkout(wc, active)
        rec = {"kind": "finish", "working_copy_id": wc, "action": action,
               "base_identity": active["base_identity"], "working_identity": active["working_identity"]}
        rid = store.put(rec)
        return {"request_id": rid, "preview": {"action": action, "workingCopyId": wc,
                "objectID": rec["base_identity"]["objectID"], "checkoutBy": rec["working_identity"]["checkoutBy"]}}

    def finish_commit(a):
        rec = store.take(a["request_id"])
        if not rec or rec.get("kind") != "finish":
            raise ips.IpsError(409, "подтверждение отсутствует или уже использовано (once)", "/write/commit")
        wc, action = rec["working_copy_id"], rec["action"]
        try:
            validate_checkout(wc, rec)
        except Exception as e:
            return {"operation": "finish_checkout", "status": "pre_send_rejected", "detail": str(e)}
        if action == "cancel":
            try:
                result = client.mutation("POST", "/core/api/objects/cancelChanges",
                                         params={"isNeedToLogModificationHistory": True}, json=[wc])
                after = client.request("GET", f"/core/api/objects/{rec['base_identity']['objectID']}").get("entity") or {}
                if (isinstance(after.get("objectID"), bool) or not isinstance(after.get("objectID"), int) or
                        not isinstance(after.get("objectGUID"), str) or not after.get("objectGUID") or
                        not isinstance(after.get("objectType"), int) or isinstance(after.get("objectType"), bool) or
                        after.get("objectGUID") != rec["base_identity"]["objectGUID"] or
                        after.get("objectID") != rec["base_identity"]["objectID"] or
                        after.get("objectType") != rec["base_identity"]["objectType"] or
                        isinstance(after.get("checkoutBy"), bool) or
                        not isinstance(after.get("checkoutBy"), int) or after.get("checkoutBy") != 0):
                    return {"operation": "finish_checkout", "status": "partial_unknown", "action": action, "result": result}
                store.remove_checkout(wc)
                return {"operation": "finish_checkout", "status": "ok", "action": action, "result": result, "verify": after}
            except Exception as e:
                return {"operation": "finish_checkout", "status": "unknown", "action": action, "detail": str(e)}
        try:
            saved = client.mutation("POST", f"/core/api/objects/{wc}/saveChanges",
                                    params={"isNeedToLogModificationHistory": True}, json={})
        except Exception as e:
            return {"operation": "finish_checkout", "status": "unknown", "stage": "saveChanges", "detail": str(e)}
        try:
            validate_checkout(wc, rec)
        except Exception as e:
            return {"operation": "finish_checkout", "status": "partial_unknown", "stage": "checkIn_preflight", "saveChanges": saved, "detail": str(e)}
        try:
            checked = client.mutation("POST", f"/core/api/objects/{wc}/checkIn",
                                      params={"isNeedToLogModificationHistory": True}, json={})
        except Exception as e:
            return {"operation": "finish_checkout", "status": "partial_unknown", "stage": "checkIn", "saveChanges": saved, "detail": str(e)}
        try:
            verified = client.request("GET", f"/core/api/objects/{rec['base_identity']['objectID']}").get("entity") or {}
            base = rec["base_identity"]
            if (isinstance(verified.get("objectID"), bool) or not isinstance(verified.get("objectID"), int) or
                    isinstance(verified.get("objectType"), bool) or not isinstance(verified.get("objectType"), int) or
                    any(verified.get(k) != base[k] for k in ("objectID", "objectGUID", "objectType")) or
                    isinstance(verified.get("checkoutBy"), bool) or
                    not isinstance(verified.get("checkoutBy"), int) or verified.get("checkoutBy") != 0):
                return {"operation": "finish_checkout", "status": "partial_unknown", "stage": "verify", "saveChanges": saved, "checkIn": checked, "verify": verified}
            store.remove_checkout(wc)
            return {"operation": "finish_checkout", "status": "ok", "action": action, "saveChanges": saved, "checkIn": checked, "verify": verified}
        except Exception as e:
            return {"operation": "finish_checkout", "status": "partial_unknown", "stage": "verify", "detail": str(e)}

    def checkout_identity(obj, object_id):
        if (not isinstance(obj, dict) or isinstance(obj.get("objectID"), bool) or
                not isinstance(obj.get("objectID"), int) or obj.get("objectID") != object_id or
                not isinstance(obj.get("objectGUID"), str) or
                not obj["objectGUID"] or isinstance(obj.get("objectType"), bool) or
                not isinstance(obj.get("objectType"), int) or "checkoutBy" not in obj or
                isinstance(obj.get("checkoutBy"), bool) or not isinstance(obj.get("checkoutBy"), int)):
            raise ips.IpsError(409, "неполная или некорректная identity объекта", f"/core/api/objects/{object_id}")
        return {k: obj.get(k) for k in ("objectID", "objectGUID", "objectType", "caption", "checkoutBy")}

    def identity_matches(obj, identity):
        return (isinstance(obj, dict) and
                isinstance(obj.get("objectID"), int) and not isinstance(obj.get("objectID"), bool) and
                isinstance(obj.get("objectGUID"), str) and bool(obj["objectGUID"]) and
                isinstance(obj.get("objectType"), int) and not isinstance(obj.get("objectType"), bool) and
                isinstance(obj.get("checkoutBy"), int) and not isinstance(obj.get("checkoutBy"), bool) and
                all(k in obj and obj[k] == identity[k]
                    for k in ("objectID", "objectGUID", "objectType", "caption", "checkoutBy")))

    def validate_checkout(working_copy_id, record):
        if isinstance(working_copy_id, bool) or not isinstance(working_copy_id, int):
            raise ips.IpsError(409, "invalid workingCopyId", "/write/finish")
        response = client.request("GET", f"/core/api/objects/{working_copy_id}")
        obj = response.get("entity") if isinstance(response, dict) else None
        base = record["base_identity"]
        working = record["working_identity"]
        owner = obj.get("checkoutBy") if isinstance(obj, dict) else None
        if (not isinstance(obj, dict) or isinstance(obj.get("objectID"), bool) or
                not isinstance(obj.get("objectID"), int) or obj["objectID"] != working_copy_id or
                obj["objectID"] != working["objectID"] or
                not isinstance(obj.get("objectGUID"), str) or not obj["objectGUID"] or
                obj["objectGUID"] != base["objectGUID"] or obj["objectGUID"] != working["objectGUID"] or
                isinstance(obj.get("objectType"), bool) or not isinstance(obj.get("objectType"), int) or
                obj["objectType"] != working["objectType"] or "checkoutBy" not in obj or
                isinstance(owner, bool) or not isinstance(owner, int) or owner != working["checkoutBy"]):
            raise ips.IpsError(409, "working copy identity или checkoutBy изменились", f"/core/api/objects/{working_copy_id}")

    return [
        make({"name": "ips_prepare_checkout_object", "description": "Preview standalone checkout без записи.", "schema": {"type": "object", "additionalProperties": False, "properties": {"object_id": {"type": "integer"}}, "required": ["object_id"]}, "call": checkout_prepare}),
        make({"name": "ips_commit_checkout_object", "description": "Однократный checkout; неизвестный исход не повторять, workingCopyId использовать без преобразования.", "schema": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1}}, "required": ["request_id"]}, "call": checkout_commit}),
        make({"name": "ips_prepare_finish_checkout", "description": "Preview checkin или cancel checkout.", "schema": {"type": "object", "additionalProperties": False, "properties": {"working_copy_id": {"type": "integer"}, "action": {"type": "string", "enum": ["checkin", "cancel"]}}, "required": ["working_copy_id", "action"]}, "call": finish_prepare}),
        make({"name": "ips_commit_finish_checkout", "description": "Однократно завершить checkout; partial/unknown не повторять вслепую.", "schema": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1}}, "required": ["request_id"]}, "call": finish_commit}),
        make({
            "name": "ips_prepare_update_attribute",
            "description": "Сформировать preview изменения атрибута объекта IPS. Запись НЕ выполняется, возвращается request_id для подтверждения.",
            "schema": {"type": "object",
                       "properties": {"object_id": {"type": "integer"},
                                      "attribute_id": {"type": "integer"},
                                      "value": {}},
                       "required": ["object_id", "attribute_id", "value"]},
            "call": prepare,
        }),
        make({
            "name": "ips_commit_update_attribute",
            "description": "Выполнить подтверждённое изменение атрибута; автоматический checkout/edit/save/checkin. Разовый (once).",
            "schema": {"type": "object",
                       "properties": {"request_id": {"type": "string"}},
                       "required": ["request_id"]},
            "call": commit,
        }),
        make({
            "name": "ips_prepare_create_object",
            "description": "Preview создания объекта по типу/атрибутам или отдельной копии по prototype_id.",
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "object_type_id": {"type": "integer", "minimum": 0},
                    "prototype_id": {"type": "integer", "minimum": 0},
                    "attributes": {
                        "type": "array", "maxItems": 100,
                        "items": {
                            "type": "object", "additionalProperties": False,
                            "properties": {
                                "attributeID": {"type": "integer", "minimum": 0},
                                "values": {"type": "array", "maxItems": 100},
                            },
                            "required": ["attributeID", "values"],
                        },
                    },
                    "context_rule": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "versionRuleObjectId": {"type": "integer", "minimum": 0},
                            "editingContextId": {"type": "integer", "minimum": 0},
                            "editingContextMode": {
                                "type": "string", "enum": ["default", "autoUpdate"],
                            },
                        },
                    },
                    "current_project": {
                        "type": "object", "additionalProperties": False,
                        "properties": {
                            "id": {"type": "integer", "minimum": 0},
                            "mode": {
                                "type": "string",
                                "enum": ["none", "currentProject", "userProjects", "onlyCurrentProject"],
                            },
                        },
                    },
                },
                "oneOf": [{"required": ["object_type_id"]}, {"required": ["prototype_id"]}],
            },
            "call": create_prepare,
        }),
        make({"name": "ips_commit_create_object", "description": "Одноразово создать объект и commitCreation.",
              "schema": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1}}, "required": ["request_id"]}, "call": create_commit}),
        make({"name": "ips_prepare_create_relation", "description": "Preview создания связи без записи.",
              "schema": {"type": "object", "additionalProperties": False,
                           "properties": {"relation_type_id": {"type": "integer", "minimum": 0}, "project_version_id": {"type": "integer"}, "part_version_id": {"type": "integer", "minimum": 0}, "attribute_values": {"type": "array", "maxItems": 100, "items": {"type": "object", "additionalProperties": False, "properties": {"attributeID": {"type": "integer", "minimum": 0}, "values": {"type": "array", "maxItems": 100}}, "required": ["attributeID", "values"]}}},
                         "required": ["relation_type_id", "project_version_id", "part_version_id"]}, "call": relation_prepare}),
        make({"name": "ips_commit_create_relation", "description": "Одноразово создать связь.",
              "schema": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1}}, "required": ["request_id"]}, "call": relation_commit}),
        make({"name": "ips_prepare_delete_object", "description": "Необратимый preview удаления только allowlisted identity.",
              "schema": {"type": "object", "additionalProperties": False, "properties": {"object_id": {"type": "integer"}, "object_guid": {"type": "string"}}, "required": ["object_id", "object_guid"]}, "call": delete_prepare}),
        make({"name": "ips_commit_delete_object", "description": "Однократное необратимое удаление; unknown сверяйте read-only, не повторять.",
              "schema": {"type": "object", "additionalProperties": False, "properties": {"request_id": {"type": "string", "minLength": 1}}, "required": ["request_id"]}, "call": delete_commit}),
    ]


class McpServer:
    def __init__(self, client, gloss, audit_path=None, enable_write=False):
        self._tools = build_tools(client, gloss)
        self._by_name = {t["name"]: t for t in self._tools}
        self._audit_path = audit_path
        if enable_write:
            store = WriteStore()
            self._tools += build_write_tools(client, gloss, store)
            self._by_name = {t["name"]: t for t in self._tools}

    def _result(self, msg_id, result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _tool_error(self, msg_id, text):
        # MCP: isError живёт внутри result. Верхнеуровневый isError оставлен
        # для обратной совместимости с существующими тестами.
        return {"jsonrpc": "2.0", "id": msg_id, "isError": True,
                "result": {"isError": True,
                           "content": [{"type": "text", "text": text}]}}

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
                # commit-инструменты возвращают исход в out["status"]:
                # unknown / partial_unknown / pre_send_rejected не должны
                # попадать в аудит как «ok».
                if (name.startswith("ips_commit_") and isinstance(out, dict)
                        and out.get("status") not in (None, "ok")):
                    status = str(out["status"])
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


CONFIG_ENV_KEYS = ("IPS_LOGIN", "IPS_PASSWORD", "IPS_BASE_URL", "IPS_ROLE_ID",
                   "IPS_GLOSS_DB", "IPS_AUDIT_LOG", "IPS_ENABLE_WRITE")


def default_config_path():
    return os.environ.get("IPS_CONFIG") or os.path.join(PROJECT_ROOT, "config.json")


def read_config(path):
    """Читает config.json. Возвращает {} если файла нет, None если он битый."""
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        sys.stderr.write(f"IPS-MCP: предупреждение: не удалось прочитать {path}: {e}\n")
        return None
    return data if isinstance(data, dict) else None


def load_config(path):
    """Загружает config.json, заполняя окружение. Приоритет: явные env > файл.

    Относительные IPS_GLOSS_DB / IPS_AUDIT_LOG разрешаются от каталога конфига,
    иначе файл, скопированный из другого проекта, молча указывал бы в никуда.
    """
    data = read_config(path)
    if data is None:
        return
    base = os.path.dirname(os.path.abspath(path)) if path else PROJECT_ROOT
    for key in CONFIG_ENV_KEYS:
        value = data.get(key)
        if value is None or os.environ.get(key):
            continue
        value = str(value)
        if key in PATH_FIELDS and value and not os.path.isabs(value):
            value = os.path.normpath(os.path.join(base, value))
        os.environ[key] = value


def run():
    # MCP stdio uses UTF-8; Windows console defaults (e.g. cp1251) cannot encode tool descriptions.
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    load_config(default_config_path())
    base = os.environ.get("IPS_BASE_URL", DEFAULTS["IPS_BASE_URL"])
    login = os.environ.get("IPS_LOGIN")
    password = os.environ.get("IPS_PASSWORD")
    role_id = int(os.environ.get("IPS_ROLE_ID", DEFAULTS["IPS_ROLE_ID"]))
    gloss_db = os.environ.get("IPS_GLOSS_DB",
                              os.path.join(PROJECT_ROOT, *DEFAULTS["IPS_GLOSS_DB"].split("/")))
    audit_path = os.environ.get("IPS_AUDIT_LOG")
    enable_write = os.environ.get("IPS_ENABLE_WRITE", "").strip().lower() in {"1", "true", "yes", "on"}
    if not login or not password:
        sys.stderr.write(
            "IPS_LOGIN и IPS_PASSWORD обязательны\n"
            "Задайте их в переменных окружения, например (Windows):\n"
            "  setx IPS_LOGIN \"user\"\n"
            "  setx IPS_PASSWORD \"password\"\n"
            "setx применяется только к НОВЫМ процессам: откройте новый терминал"
            " или перезапустите opencode/IDE и повторите запуск.\n")
        sys.exit(2)

    client = ips.IpsClient(base, login, password, role_id=role_id)
    gloss = ips.load_gloss(gloss_db) if os.path.exists(gloss_db) else {}
    server = McpServer(client, gloss, audit_path=audit_path,
                       enable_write=enable_write)

    sys.stderr.write(
        f"IPS-MCP: вход выполнен как {login} "
        f"(роль {role_id}, базовый URL {base})\n"
        "IPS-MCP: ожидание MCP-запросов из stdin; "
        "stdout зарезервирован под протокол.\n")

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


def _ask(prompt, secret=False):
    """Вопрос пользователю. None — stdin не интерактивен (pipe, CI, перенаправление)."""
    import getpass
    try:
        if secret:
            return getpass.getpass(f"{prompt}: ") or None
        return input(f"{prompt}: ").strip() or None
    except (EOFError, OSError):
        return None


def init_config(argv=None):
    """Создаёт/дополняет config.json. Значения берутся из env, иначе — с терминала.

    Не перезаписывает существующий config.json без --force: в нём пароль.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="ips-mcp init", add_help=True)
    ap.add_argument("--config", default=None, help="путь к config.json")
    ap.add_argument("--force", action="store_true", help="перезаписать существующий")
    args = ap.parse_args(argv)

    path = args.config or default_config_path()
    existing = read_config(path)
    if existing is None:
        sys.stderr.write(f"IPS-MCP: {path} не является JSON-объектом, исправьте или удалите\n")
        return 2
    if existing and not args.force:
        sys.stderr.write(
            f"IPS-MCP: {path} уже существует.\n"
            "  Перезаписать:  ips-mcp init --force\n"
            "  Дописать недостающие поля можно вручную — сервер берёт их из env.\n")
        return 1

    cfg = {
        "IPS_LOGIN": os.environ.get("IPS_LOGIN", ""),
        "IPS_PASSWORD": os.environ.get("IPS_PASSWORD", ""),
        "IPS_BASE_URL": os.environ.get("IPS_BASE_URL", DEFAULTS["IPS_BASE_URL"]),
        "IPS_ROLE_ID": int(os.environ.get("IPS_ROLE_ID", DEFAULTS["IPS_ROLE_ID"])),
        "IPS_GLOSS_DB": os.environ.get("IPS_GLOSS_DB", DEFAULTS["IPS_GLOSS_DB"]),
        "IPS_AUDIT_LOG": os.environ.get("IPS_AUDIT_LOG", DEFAULTS["IPS_AUDIT_LOG"]),
        "IPS_ENABLE_WRITE": os.environ.get("IPS_ENABLE_WRITE", "").lower() in {"1", "true", "yes", "on"},
    }

    for key, secret in (("IPS_LOGIN", False), ("IPS_PASSWORD", True)):
        if cfg[key]:
            continue
        value = _ask(key, secret=secret)
        if value:
            cfg[key] = value
        else:
            missing = "IPS_LOGIN" if key == "IPS_LOGIN" else "IPS_PASSWORD"
            sys.stderr.write(
                f"IPS-MCP: не задан {missing}.\n"
                "Задайте его в окружении либо запустите init в интерактивном терминале:\n"
                f'  setx {missing} "..."\n'
                "setx действует только на НОВЫЕ процессы — перезапустите терминал.\n")
            return 2

    try:
        int(cfg["IPS_ROLE_ID"])
    except (TypeError, ValueError):
        sys.stderr.write(f"IPS-MCP: IPS_ROLE_ID должен быть числом, получено {cfg['IPS_ROLE_ID']!r}\n")
        return 2

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    gloss = cfg["IPS_GLOSS_DB"]
    if gloss and not os.path.isabs(gloss):
        gloss = os.path.join(os.path.dirname(os.path.abspath(path)), gloss)
    note = "" if gloss and os.path.exists(gloss) else "  (справочник не найден — сервер стартует без имён)"
    sys.stderr.write(
        f"IPS-MCP: записан {path}\n"
        f"  роль {cfg['IPS_ROLE_ID']}, {cfg['IPS_BASE_URL']}{note}\n"
        "  Файл в .gitignore. Далее: ips-mcp  или подключите в opencode.json "
        "(type=local, command=[\"python\",\"-m\",\"ips_mcp.server\"]).\n")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        return init_config(sys.argv[2:])
    run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
