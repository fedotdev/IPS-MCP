"""HTTP-клиент к IPS Web API + справочник gloss + нормализация ответов."""

import logging
import sqlite3
import threading

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
        for attempt in (0, 1):
            headers = dict(kw.pop("headers", {}))
            if self._token:
                headers["Authorization"] = "Bearer " + self._token
            resp = self._http.request(method, self._base + path,
                                      headers=headers, **kw)
            if resp.status_code == 401 and attempt == 0:
                if self._refresh:
                    self._refresh_tokens()
                else:
                    self._authenticate()
                continue
            if resp.status_code >= 400:
                raise IpsError(resp.status_code, resp.text[:500], path)
            return resp.json()
        raise IpsError(401, "не удалось обновить токен", path)

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