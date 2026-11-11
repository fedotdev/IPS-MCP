"""Локальный журнал DEBUG и кейсы с проверяемыми исходными ответами."""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .ips import IpsError

SENSITIVE_KEY = re.compile(r"password|passwd|pwd|token|authorization|cookie|secret|apikey", re.IGNORECASE)
INLINE_SECRET = re.compile(
    r"""(?i)(\b(?:password|passwd|pwd|access[_-]?token|refresh[_-]?token|token|secret|api[_-]?key)\b["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)""")


def redact(value, secrets):
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SENSITIVE_KEY.search(re.sub(r"[_\-\s]", "", key))
                else redact(item, secrets) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if not isinstance(value, str):
        return value
    if value.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(value)
        except ValueError:
            pass
        else:
            return json.dumps(redact(parsed, secrets), ensure_ascii=False)
    for secret in sorted({s for s in secrets if isinstance(s, str) and s}, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    value = INLINE_SECRET.sub(lambda match: match[1] + "[REDACTED]", value)
    value = re.sub(r"(?i)\bBearer\s+[^\s\"'<>;,]+", "Bearer [REDACTED]", value)
    return re.sub(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+\b", "[REDACTED]", value)


class DebugLog:
    def __init__(self, directory, client):
        self.directory = Path(directory).absolute()
        self.client = client
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex
        self.journal_path = self.directory / "sessions" / (self.session_id + ".jsonl")
        self.http = []
        self.secrets = []

    @property
    def instructions(self):
        return (
            f"IPS DEBUG MODE включён. Журналы и кейсы: {self.directory}. "
            "Перед решением ищите подходящие cases/*.md и сверяйте базу, версию и роль. "
            "После успешной или неудачной задачи сохраните через ips_save_debug_case "
            "новые факты, шаги, ошибки, выводы и ограничения применимости, "
            "сравнив их с helper suite и существующими кейсами. "
            "Указывайте evidence_call_ids из DEBUG-ответов. Preview не подтверждает запись; "
            "unknown/partial_unknown требуют проверки состояния перед выводом об успехе. "
            "Итог задачи формулирует помощник; журнал автоматически фиксирует каждый вызов.")

    def _credentials(self):
        return [getattr(self.client, key, None) for key in ("_password", "_token", "_refresh")]

    def redact(self, value):
        return redact(value, self.secrets + self._credentials())

    def start_call(self):
        self.http = []
        self.secrets = self._credentials()

    def record_http(self, method, path, kwargs, response=None, error=None):
        entry = {"method": method, "path": path,
                 "params": kwargs.get("params"), "body": kwargs.get("json")}
        if response is not None:
            entry["status_code"] = response.status_code
            try:
                entry["response"] = response.json()
            except ValueError:
                entry["response"] = response.text
        if error is not None:
            entry["error"] = {"type": type(error).__name__, "detail": str(error)}
        self.http.append(entry)

    def record_call(self, record):
        try:
            call_id = uuid4().hex
            if record["status"] == "ok" and record["tool"].startswith("ips_prepare_"):
                record = {**record, "status": "preview"}
            record = self.redact({
                **record, "call_id": call_id, "session_id": self.session_id,
                "source": {"base_url": self.client.base_url,
                           "ips_version": self.client.version,
                           "role_id": getattr(self.client, "_role_id", None)},
                "http": self.http,
            })
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with self.journal_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            return {"call_id": call_id, "session_id": self.session_id,
                    "journal_path": str(self.journal_path)}
        except Exception as error:  # noqa: BLE001 - Диагностика не должна менять исход операции IPS.
            sys.stderr.write(f"IPS-MCP DEBUG: вызов не сохранён ({type(error).__name__}).\n")
            return None
        finally:
            self.http = []
            self.secrets = []

    def save_case(self, args):
        required = {"title", "outcome", "summary", "evidence_call_ids"}
        if (not isinstance(args, dict) or set(args) != required or
                any(not isinstance(args[key], str) or not args[key].strip()
                    for key in ("title", "outcome", "summary")) or
                len(args["title"]) > 200 or "\n" in args["title"] or "\r" in args["title"] or
                len(args["summary"]) > 20000 or
                args["outcome"] not in {"success", "failure", "partial", "unknown"}):
            raise IpsError(400, "нужны title, summary, outcome и evidence_call_ids", "/debug/cases")
        ids = args["evidence_call_ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 100 or
                any(not isinstance(value, str) for value in ids) or len(set(ids)) != len(ids)):
            raise IpsError(400, "evidence_call_ids: 1–100 уникальных ID вызовов", "/debug/cases")
        evidence = []
        try:
            with self.journal_path.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue  # Незавершённая строка не является подтверждением.
                    if (isinstance(record, dict) and record.get("call_id") in ids and
                            record.get("tool") != "ips_save_debug_case"):
                        evidence.append(record)
        except OSError as error:
            raise IpsError(400, f"журнал недоступен: {type(error).__name__}", "/debug/cases") from error
        if len(evidence) != len(ids):
            raise IpsError(400, "подтверждения должны быть из журнала текущего процесса", "/debug/cases")
        if args["outcome"] == "success" and evidence[-1]["status"] != "ok":
            raise IpsError(400, "успех требует завершающего успешного вызова, не preview/unknown/error",
                           "/debug/cases")
        cleaned = self.redact(args)
        evidence = self.redact(evidence)
        text = (f"# {cleaned['title'].strip()}\n\n"
                f"- Итог задачи: `{cleaned['outcome']}`\n"
                f"- Дата UTC: {datetime.now(timezone.utc).isoformat()}\n"
                f"- Сессия: `{self.session_id}`\n\n"
                "Выводы сформулированы помощником; исходные ответы ниже позволяют их проверить.\n\n"
                f"## Выводы и применимость\n\n{cleaned['summary'].strip()}\n\n"
                "## Подтверждающие вызовы (в порядке выполнения)\n\n"
                f"```json\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n```\n")
        path = self.directory / "cases" / (uuid4().hex + ".md")
        temporary = path.with_suffix(".tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(text, encoding="utf-8")
            temporary.replace(path)
        except OSError as error:
            raise IpsError(500, f"кейс не сохранён: {type(error).__name__}", "/debug/cases") from error
        return {"status": "saved", "path": str(path), "outcome": args["outcome"],
                "evidence_call_ids": [record["call_id"] for record in evidence]}

    def tool(self):
        return {
            "name": "ips_save_debug_case",
            "description": "DEBUG: после задачи сохранить локальный Markdown-кейс с новыми фактами, "
                           "шагами, ошибками и применимостью. evidence_call_ids — из DEBUG-ответов. "
                           "Записывает только локальный файл, данные IPS не меняет.",
            "inputSchema": {"type": "object", "additionalProperties": False,
                            "properties": {
                                "title": {"type": "string", "minLength": 1, "maxLength": 200},
                                "outcome": {"type": "string", "enum": ["success", "failure", "partial", "unknown"]},
                                "summary": {"type": "string", "minLength": 1, "maxLength": 20000},
                                "evidence_call_ids": {"type": "array", "minItems": 1, "maxItems": 100,
                                                      "uniqueItems": True, "items": {"type": "string"}},
                            }, "required": ["title", "outcome", "summary", "evidence_call_ids"]},
            "call": self.save_case,
        }
