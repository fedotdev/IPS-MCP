import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ips_mcp import ips
from ips_mcp.server import McpServer, init_config, load_config

TEMP_ROOT = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Temp" / "opencode"
TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def make_server(directory, handler, **kwargs):
    client = ips.IpsClient("http://ips.test", "tester", "test-password", role_id=7)
    client._http.close()
    client._http = httpx.Client(transport=httpx.MockTransport(handler))
    client._token = "test-access-token"
    client._refresh = "test-refresh-token"
    gloss = {"object_types": {}, "attribute_types": {}, "relation_types": {}}
    return McpServer(client, gloss, debug_dir=directory, **kwargs), client


def call(server, name, arguments=None):
    return server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                          "params": {"name": name, "arguments": arguments or {}}})["result"]


def body(result):
    return json.loads(result["content"][0]["text"])


def events(directory):
    return [json.loads(line) for path in Path(directory).glob("sessions/*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]


def evidence_id(result):
    return result["_meta"]["ips_debug"]["call_id"]


def test_debug_records_full_results_and_http_context_without_changing_response():
    payload = {"id": 1006, "name": "Технологическая связь", "details": "я" * 2000,
               "nested": [{"accessToken": "response-token", "values": [1, 2]}]}

    def handler(request):
        return httpx.Response(200, json=payload, headers={"ips-version": "9.0.4.11310"})

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, handler)
        result = call(server, "ips_get_relation_type", {"relation_type_id": 1006})
        assert body(result) == payload
        record, = events(tmp)
        assert record["status"] == "ok"
        assert record["inputs"] == {"relation_type_id": 1006}
        assert record["output"]["details"] == payload["details"]
        assert record["output"]["nested"][0]["accessToken"] == "[REDACTED]"
        assert record["source"] == {"base_url": "http://ips.test",
                                    "ips_version": "9.0.4.11310", "role_id": 7}
        assert record["http"][0]["path"] == "/core/api/metadata/relationTypes/1006"
        assert record["http"][0]["response"]["details"] == payload["details"]
        assert record["call_id"] == evidence_id(result)
        assert record["duration_ms"] >= 0 and record["session_id"]
        assert record["call_id"] in result["content"][1]["text"]
        client._http.close()


def test_debug_records_failures_and_redacts_secrets_in_inputs_and_details():
    detail = json.dumps({"detail": "test-password test-access-token test-refresh-token",
                         "accessToken": "unrelated-token", "reason": "Нет прав"})

    def handler(request):
        return httpx.Response(403, text=detail)

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        audit_path = Path(tmp) / "audit.jsonl"
        server, client = make_server(tmp, handler, audit_path=audit_path)
        failed = call(server, "ips_get_object", {"object_id": 42})
        assert failed["isError"] is True
        unknown = call(server, "missing_tool", {
            "password": "input-password", "nested": [{"Authorization": "Bearer auth-secret"}],
            "note": 'password="inline-secret"; Bearer bearer-secret',
        })
        assert unknown["isError"] is True
        internal = call(server, "ips_get_object", {})
        assert internal["isError"] is True
        records = events(tmp)
        assert [r["status"] for r in records] == ["error", "error", "error"]
        assert records[0]["error"]["status"] == 403
        assert records[0]["error"]["path"] == "/core/api/objects/42"
        assert "Нет прав" in records[0]["error"]["detail"]
        assert records[2]["error"]["type"] == "KeyError"
        text = json.dumps(records, ensure_ascii=False) + audit_path.read_text(encoding="utf-8")
        for secret in ("test-password", "test-access-token", "test-refresh-token",
                       "unrelated-token", "input-password", "auth-secret",
                       "inline-secret", "bearer-secret"):
            assert secret not in text, secret
        client._http.close()


def test_debug_traces_retries_and_omits_authentication_bodies():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/authenticate"):
            return httpx.Response(200, json={"accessToken": "new-access-token",
                                             "refreshToken": "new-refresh-token"})
        if len(calls) == 1:
            return httpx.Response(401)
        return httpx.Response(200, json={"name": "ГТП"})

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, handler)
        client._token = client._refresh = None
        call(server, "ips_get_object_type", {"type_id": 1270})
        record, = events(tmp)
        assert [entry["status_code"] for entry in record["http"]] == [401, 200]
        assert len(calls) == 3
        text = json.dumps(record)
        assert "/authenticate" not in text
        assert "new-access-token" not in text and "test-password" not in text

        def flaky(request):
            calls.append(request.url.path)
            if len(calls) == 4:
                return httpx.Response(500, text="Полная ошибка " + "x" * 2000)
            return httpx.Response(200, json={"name": "ГТП"})

        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(flaky))
        with patch("ips_mcp.ips.time.sleep"):
            call(server, "ips_get_object_type", {"type_id": 1270})
        record = events(tmp)[-1]
        assert [entry["status_code"] for entry in record["http"]] == [500, 200]
        assert record["http"][0]["response"].endswith("x" * 2000)
        client._http.close()


def test_debug_cases_preserve_summary_and_real_evidence():
    responses = [httpx.Response(403, text="Требуется другая роль"),
                 httpx.Response(200, json={"id": 1006, "name": "Связь с ГТП"})]

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, lambda request: responses.pop(0))
        failed = call(server, "ips_get_relation_type", {"relation_type_id": 1006})
        success = call(server, "ips_get_relation_type", {"relation_type_id": 1006})
        saved = call(server, "ips_save_debug_case", {
            "title": "Связь ЕТП и ГТП",
            "outcome": "success",
            "summary": "Задача: проверить связь.\nНовый факт: тип 1006 — Связь с ГТП.\n"
                       "Применимость: только эта база.\nСекрет test-password.",
            "evidence_call_ids": [evidence_id(failed), evidence_id(success)],
        })
        assert not saved.get("isError"), saved
        path = Path(body(saved)["path"])
        assert path.parent == Path(tmp) / "cases"
        text = path.read_text(encoding="utf-8")
        assert "Связь ЕТП и ГТП" in text and "Новый факт" in text
        assert "success" in text and "http://ips.test" in text
        assert evidence_id(failed) in text and evidence_id(success) in text
        assert "Требуется другая роль" in text and '"relation_type_id": 1006' in text
        assert "test-password" not in text
        assert "ips_prepare_create_object" not in server._by_name
        client._http.close()


def test_debug_rejects_fabricated_evidence_and_preview_as_success():
    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, lambda request: httpx.Response(500), enable_write=True)
        preview = call(server, "ips_prepare_create_object", {"object_type_id": 1270})
        assert events(tmp)[0]["status"] == "preview"
        args = {"title": "Проверка", "outcome": "success", "summary": "Объект создан",
                "evidence_call_ids": [evidence_id(preview)]}
        assert call(server, "ips_save_debug_case", args)["isError"] is True
        for evidence in ([], ["0" * 32], "not-a-list"):
            assert call(server, "ips_save_debug_case", {
                **args, "evidence_call_ids": evidence})["isError"] is True
        unknown = call(server, "ips_commit_create_object", {"request_id": body(preview)["request_id"]})
        assert body(unknown)["status"] == "unknown"
        args["evidence_call_ids"] = [evidence_id(preview), evidence_id(unknown)]
        assert call(server, "ips_save_debug_case", args)["isError"] is True
        assert not list(Path(tmp).glob("cases/*.md"))
        saved = call(server, "ips_save_debug_case", {**args, "outcome": "unknown"})
        assert not saved.get("isError"), saved
        assert "unknown" in Path(body(saved)["path"]).read_text(encoding="utf-8")
        client._http.close()


def test_debug_keeps_partial_and_rejected_write_outcomes():
    def handler(request):
        if request.url.path == "/core/api/objects":
            return httpx.Response(200, json={"result": {"objectID": -42}})
        return httpx.Response(500, text="Ошибка commitCreation")

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, handler, enable_write=True)
        preview = call(server, "ips_prepare_create_object", {"object_type_id": 1270})
        result = call(server, "ips_commit_create_object", {"request_id": body(preview)["request_id"]})
        assert body(result)["status"] == "partial_unknown"
        record = events(tmp)[-1]
        assert record["status"] == "partial_unknown"
        assert record["http"][-1]["response"] == "Ошибка commitCreation"
        client._http.close()
        identities = ["before", "changed"]
        client._http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
            200, json={"entity": {"objectID": 10, "objectGUID": identities.pop(0)}})))
        preview = call(server, "ips_prepare_create_object", {"prototype_id": 10})
        result = call(server, "ips_commit_create_object", {"request_id": body(preview)["request_id"]})
        assert body(result)["status"] == "pre_send_rejected"
        record = events(tmp)[-1]
        assert record["status"] == "pre_send_rejected"
        assert len(record["http"]) == 1 and record["http"][0]["method"] == "GET"
        client._http.close()


def test_debug_network_errors_and_broken_http_recorder_do_not_change_retry_policy():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        raise httpx.ConnectError("test-password: connection lost", request=request)

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, handler)
        with patch("ips_mcp.ips.time.sleep"):
            result = call(server, "ips_get_object_type", {"type_id": 1270})
        assert result["isError"] is True and len(requests) == 2
        record, = events(tmp)
        assert record["status"] == "error" and len(record["http"]) == 2
        assert record["http"][0]["error"]["type"] == "ConnectError"
        assert "test-password" not in json.dumps(record)
        client._http.close()
        client._http = httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"name": "ГТП"})))
        with patch.object(client, "debug_hook", side_effect=OSError("recorder unavailable")), \
                patch("ips_mcp.ips.log.warning") as warning:
            result = call(server, "ips_get_object_type", {"type_id": 1270})
        assert body(result) == {"name": "ГТП"}
        warning.assert_called_once()
        client._http.close()


def test_debug_io_failure_never_repeats_or_changes_a_successful_mutation():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path == "/core/api/objects":
            return httpx.Response(200, json={"result": {"objectID": -42}})
        return httpx.Response(200, json={"result": {"objectId": 42}})

    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        blocked = Path(tmp) / "debug"
        blocked.write_text("file, not a directory", encoding="utf-8")
        with patch("sys.stderr", new_callable=io.StringIO) as stderr:
            server, client = make_server(blocked, handler, enable_write=True)
            preview = call(server, "ips_prepare_create_object", {"object_type_id": 1270})
            arguments = {"request_id": body(preview)["request_id"]}
            result = call(server, "ips_commit_create_object", arguments)
            assert body(result)["status"] == "ok"
            assert call(server, "ips_commit_create_object", arguments)["isError"] is True
            assert calls == ["/core/api/objects", "/core/api/objects/-42/commitCreation"]
            assert "DEBUG" in stderr.getvalue()
            assert "test-password" not in stderr.getvalue()
            assert "call_id" not in result.get("_meta", {}).get("ips_debug", {})
        client._http.close()


def test_debug_config_and_init_support_flag_and_env_priority():
    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp, patch.dict(os.environ, {}, clear=True):
        path = Path(tmp) / "config.json"
        path.write_text(json.dumps({"IPS_DEBUG_MODE": True}), encoding="utf-8")
        load_config(str(path))
        assert os.environ["IPS_DEBUG_MODE"].lower() == "true"
        os.environ["IPS_DEBUG_MODE"] = "false"
        load_config(str(path))
        assert os.environ["IPS_DEBUG_MODE"] == "false"
        os.environ.update(IPS_LOGIN="tester", IPS_PASSWORD="test-password")
        init_path = Path(tmp) / "init.json"
        with patch("sys.stderr", new_callable=io.StringIO):
            assert init_config(["--config", str(init_path)]) == 0
        assert json.loads(init_path.read_text(encoding="utf-8"))["IPS_DEBUG_MODE"] is False
        os.environ["IPS_DEBUG_MODE"] = "true"
        with patch("sys.stderr", new_callable=io.StringIO):
            assert init_config(["--config", str(init_path), "--force"]) == 0
        assert json.loads(init_path.read_text(encoding="utf-8"))["IPS_DEBUG_MODE"] is True


def test_debug_redaction_failure_does_not_change_successful_tool_result():
    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        audit_path = Path(tmp) / "audit.jsonl"
        server, client = make_server(tmp, lambda request: httpx.Response(200, json={"id": 1006}),
                                     audit_path=audit_path)
        with patch.object(server._debug, "redact", side_effect=ValueError("test-password")), \
                patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = call(server, "ips_get_relation_type", {"relation_type_id": 1006})
        assert body(result) == {"id": 1006}
        assert not audit_path.exists()
        assert not events(tmp)
        assert "test-password" not in stderr.getvalue()
        client._http.close()


def test_debug_case_write_failure_reports_error_and_preserves_existing_file():
    with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
        server, client = make_server(tmp, lambda request: httpx.Response(200, json={"id": 1006}))
        result = call(server, "ips_get_relation_type", {"relation_type_id": 1006})
        blocked = Path(tmp) / "cases"
        blocked.write_text("existing file", encoding="utf-8")
        saved = call(server, "ips_save_debug_case", {
            "title": "Связь", "outcome": "success", "summary": "Прочитан тип связи",
            "evidence_call_ids": [evidence_id(result)],
        })
        assert saved["isError"] is True
        assert blocked.read_text(encoding="utf-8") == "existing file"
        assert events(tmp)[-1]["status"] == "error"
        client._http.close()


def test_stdio_flag_controls_debug_files_tools_and_instructions_from_other_cwd():
    root = Path(__file__).resolve().parents[1]
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "missing_tool"}},
    ]
    for flag, override, enabled in ((False, None, False), (True, None, True),
                                    (True, "false", False), (False, "true", True)):
        with tempfile.TemporaryDirectory(dir=TEMP_ROOT) as tmp:
            config_dir = Path(tmp) / "configuration"
            config_dir.mkdir()
            config_path = config_dir / "config.json"
            config_path.write_text(json.dumps({"IPS_LOGIN": "tester", "IPS_PASSWORD": "test-password",
                                               "IPS_DEBUG_MODE": flag}), encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("IPS_")}
            env.update(IPS_CONFIG=str(config_path), PYTHONPATH=str(root / "src"), PYTHONUTF8="1")
            if override is not None:
                env["IPS_DEBUG_MODE"] = override
            proc = subprocess.run([sys.executable, "-m", "ips_mcp.server"],
                                  input="\n".join(json.dumps(m) for m in messages) + "\n",
                                  text=True, encoding="utf-8", capture_output=True, env=env,
                                  cwd=tmp, timeout=20, check=False)
            assert proc.returncode == 0, proc.stderr
            replies = [json.loads(line)["result"] for line in proc.stdout.splitlines()]
            assert len(replies) == 3
            names = [tool["name"] for tool in replies[1]["tools"]]
            assert ("ips_save_debug_case" in names) is enabled
            assert bool(replies[0].get("instructions")) is enabled
            assert (config_dir / "debug").exists() is enabled
            assert bool(events(config_dir / "debug")) is enabled
            assert not (Path(tmp) / "debug").exists()
            assert ("_meta" in replies[2]) is enabled


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok: {test.__name__}")
