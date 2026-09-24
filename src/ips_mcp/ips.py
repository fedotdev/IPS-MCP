"""HTTP-клиент к IPS Web API + справочник gloss + нормализация ответов."""

import logging
import sqlite3
import threading
import time

import httpx

log = logging.getLogger("ips")

ERROR_TEXTS = {
    401: "IPS: требуется повторная аутентификация",
    403: "IPS: у сервисного аккаунта нет прав на объект",
    404: "IPS: объект не найден",
    409: "IPS: объект заблокирован другим пользователем",
    500: "IPS: внутренняя ошибка сервера, повторите позже",
}


class IpsError(Exception):
    def __init__(self, status, detail, path):
        self.status = status
        self.detail = detail
        self.path = path
        msg = ERROR_TEXTS.get(
            status, f"IPS: ошибка HTTP {status}")
        segments = [s for s in path.split("/") if s.lstrip("-").isdigit()]
        if segments:
            msg += f" (id={segments[-1]})"
        super().__init__(msg)


class IpsClient:
    def __init__(self, base_url, login, password, role_id=0, access_level_id=0):
        self._base = base_url.rstrip("/")
        self._login = login
        self._password = password
        self._role_id = role_id
        self._access_level_id = access_level_id
        self._http = httpx.Client(timeout=30.0)
        self._lock = threading.Lock()
        self._token = None
        self._refresh = None

    def request(self, method, path, **kw):
        with self._lock:
            return self._request_locked(method, path, **kw)

    def _request_locked(self, method, path, **kw):
        retry = kw.pop("_retry", True)
        for attempt in range(2):
            headers = dict(kw.pop("headers", {}))
            if self._token:
                headers["Authorization"] = "Bearer " + self._token
            try:
                resp = self._http.request(method, self._base + path,
                                          headers=headers, **kw)
            except httpx.HTTPError as e:
                if attempt == 0 and retry:
                    time.sleep(0.3)
                    continue
                raise IpsError(0, f"сетевая ошибка: {type(e).__name__}", path)
            if resp.status_code == 401 and attempt == 0:
                if self._refresh:
                    self._refresh_tokens()
                else:
                    self._authenticate()
                continue
            if resp.status_code >= 500 and attempt == 0 and retry:
                time.sleep(0.3)
                continue
            if resp.status_code >= 400:
                raise IpsError(resp.status_code, resp.text[:500], path)
            return resp.json()
        raise IpsError(0, "запрос не удался после повторов", path)

    def _authenticate(self):
        resp = self._http.post(self._base + "/core/api/Auth/authenticate", json={
            "loginName": self._login,
            "password": self._password,
            "passwordType": "plainText",
            "roleID": self._role_id,
            "accessLevelID": self._access_level_id,
        })
        resp.raise_for_status()
        d = resp.json()
        self._token = d["accessToken"]
        self._refresh = d["refreshToken"]

    def _refresh_tokens(self):
        resp = self._http.post(self._base + "/core/api/Auth/refreshTokens", json={
            "accessToken": self._token,
            "refreshToken": self._refresh,
        })
        if resp.status_code >= 400:
            raise IpsError(resp.status_code, resp.text[:300],
                           "/core/api/Auth/refreshTokens")
        d = resp.json()
        self._token = d["accessToken"]
        self._refresh = d["refreshToken"]
        log.info("token refreshed")


def load_gloss(db_path):
    conn = sqlite3.connect(db_path)
    out = {
        table: dict(conn.execute(f"SELECT id, name FROM {table}"))
        for table in ("object_types", "attribute_types", "relation_types")
    }
    conn.close()
    return out


def decorate_object(d, gloss):
    d = dict(d)
    name = gloss["object_types"].get(d.get("objectType"))
    if name:
        d["objectTypeName"] = name
    return d


def decorate_relation(d, gloss):
    d = dict(d)
    name = gloss["relation_types"].get(d.get("relationType"))
    if name:
        d["relationTypeName"] = name
    return d


def decorate_attrs(items, gloss):
    out = []
    for it in items:
        it = dict(it)
        if not it.get("attributeName"):
            it["attributeName"] = gloss["attribute_types"].get(it.get("attributeId"))
        out.append(it)
    return out


def decorate_composition(items, gloss):
    return [
        {
            "object": decorate_object(p.get("object", {}), gloss),
            "relation": decorate_relation(p.get("relation", {}), gloss),
        }
        for p in items
    ]


def paged(items, page=1, page_size=50):
    total = len(items)
    page_size = max(1, min(page_size or 50, 1000))
    page = max(1, page or 1)
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": start + page_size < total,
    }


def resolve_object_links(client, gloss, attrs, cap=10):
    """Разворачивает ftObjectLink-атрибуты: числовой ID -> {id, objectType, caption}.

    Ограничение |cap| запросов к IPS за один вызов инструмента,
    чтобы не превращать чтение в веер HTTP-запросов.
    """
    out, resolved = [], 0
    for it in attrs:
        it = dict(it)
        values = it.get("values") or []
        if it.get("attributeType") == "ftObjectLink" and values and resolved < cap:
            links = []
            for v in values:
                if v is None:
                    links.append(None)
                    continue
                try:
                    obj = client.request("GET", f"/core/api/objects/{v}").get("entity") or {}
                except IpsError:
                    obj = {}
                links.append({
                    "id": v,
                    "objectType": obj.get("objectType"),
                    "objectTypeName": gloss["object_types"].get(obj.get("objectType")),
                    "caption": obj.get("caption"),
                })
                resolved += 1
                if resolved >= cap:
                    break
            it["resolvedValues"] = links
        out.append(it)
    return out


def snapshot_attr(client, object_id, attribute_id):
    """Текущее значение атрибута для preview."""
    for a in client.request("GET", f"/core/api/objects/{object_id}/attributesValues"):
        if a.get("attributeId") == attribute_id:
            return a
    return None


def write_attribute(client, object_id, attribute_id, value):
    """Выполняет checkout → edit → set attributes → saveChanges → checkIn.

    При ошибке после checkout отменяет рабочую копию через cancelChanges.
    """
    wc = None
    checked_in = False
    try:
        wc = client.request(
            "POST", f"/core/api/objects/{object_id}/checkOut", _retry=False,
            params={"isNeedToLogModificationHistory": True}, json={}).get("result")
        if not isinstance(wc, int) or wc >= 0:
            raise IpsError(500, f"checkOut вернул неверный workingCopyId: {wc!r}",
                           f"/core/api/objects/{object_id}/checkOut")
        client.request("POST", f"/core/api/objects/{wc}/edit", _retry=False,
                       params={"isNeedToLogModificationHistory": True}, json={})
        client.request("POST", f"/core/api/objects/{wc}/attributes", _retry=False,
                       json=[{"attributeID": attribute_id, "values": [value]}])
        client.request("POST", f"/core/api/objects/{wc}/saveChanges", _retry=False,
                       params={"isNeedToLogModificationHistory": True}, json={})
        version = client.request("POST", f"/core/api/objects/{wc}/checkIn", _retry=False,
                                 params={"isNeedToLogModificationHistory": True}, json={}).get("result")
        checked_in = True
        return {"objectId": object_id, "workingCopyId": wc, "versionId": version}
    except Exception:
        if not checked_in and isinstance(wc, int) and wc < 0:
            try:
                client.request("POST", "/core/api/objects/cancelChanges", _retry=False,
                               params={"isNeedToLogModificationHistory": True,
                                       "isNeedToIgnoreExceptions": True},
                               json=[wc])
            except Exception:
                pass  # исходная ошибка важнее; незавершённый checkout виден в аудите
        raise


def read_attr_value(client, object_id, attribute_id):
    for a in client.request("GET", f"/core/api/objects/{object_id}/attributesValues"):
        if a.get("attributeId") == attribute_id:
            return a.get("values")
    return None