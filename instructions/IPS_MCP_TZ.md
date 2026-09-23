# Техническое задание: IPS-MCP Bridge
## Мост между IPS Web API и протоколом Model Context Protocol (MCP)

**Версия:** 1.0  
**Дата:** 2026-09-23  
**Статус:** Черновик для согласования  

---

## 1. Назначение и контекст

### 1.1 Цель проекта

Создать MCP-сервер (`ips-mcp`), который транслирует операции LLM-агентов в вызовы IPS Web API (REST), соблюдая принципы безопасности (read-only по умолчанию), минимальной экспозиции endpoint'ов и прозрачного аудита.

### 1.2 Что уже сделано

- Связь с IPS Web API через JWT-аутентификацию — **подтверждена**.
- Получение `swagger.json` с описанием endpoint'ов — **работает**.
- Базовый транспорт MCP ↔ IPS REST — **проверен**.

### 1.3 Референсные проекты для анализа

Перед реализацией выполнить `git clone` следующих репозиториев и изучить их архитектурные решения:

```bash
# Официальный GitHub MCP Server — эталон read-only, toolsets, readOnlyHint
git clone https://github.com/github/github-mcp-server.git

# Официальный MCP SDK (Python) — транспорт, типы, регистрация tools
git clone https://github.com/modelcontextprotocol/python-sdk.git

# FastMCP — генерация MCP из OpenAPI/Swagger, from_openapi()
git clone https://github.com/jlowin/fastmcp.git

# Salesforce MCP — деструктивные операции, destructiveHint, human-approval
git clone https://github.com/salesforce/mcp.git

# openapi-mcp-generator — автогенерация из Swagger/OpenAPI 3.x
git clone https://github.com/harsha-iiiv/openapi-mcp-generator.git

# mcp-swagger-server — Swagger 2.0 / OpenAPI wrapper, аутентификация
git clone https://github.com/readyapi/mcp-swagger-server.git
```

**Что изучить в каждом:**

| Репозиторий | Что смотреть |
|---|---|
| `github-mcp-server` | `--read-only` флаг, toolsets, `ReadOnlyHint` реализация |
| `python-sdk` | Структура `Tool`, `CallToolResult`, транспорт stdio/SSE |
| `fastmcp` | `FastMCP.from_openapi()`, маппинг параметров, Bearer auth |
| `salesforce/mcp` | `destructiveHint`, human-approval flow, `Once` валидность |
| `openapi-mcp-generator` | Конвертация Swagger → tool definitions |
| `mcp-swagger-server` | Обработка Swagger 2.0, генерация схем |

---

## 2. Архитектурные принципы

### 2.1 Слоистая модель

```
┌─────────────────────────────────────┐
│  LLM-агент (Claude, GPT, др.)       │
│  MCP Client (stdio / SSE)           │
└──────────────┬──────────────────────┘
               │ MCP Protocol
┌──────────────▼──────────────────────┐
│  ips-mcp (MCP Server)               │
│  - Tool registry (toolsets)         │
│  - Auth layer (upstream creds)      │
│  - Response mapper / filter         │
│  - Audit log                        │
└──────────────┬──────────────────────┘
               │ IPS Web API (HTTPS + JWT)
┌──────────────▼──────────────────────┐
│  IPS Web API                        │
│  (источник истины, бизнес-логика)   │
└─────────────────────────────────────┘
```

**Ключевой принцип:** MCP-сервер — адаптер, не второй бэкенд. Вся бизнес-логика и права доступа остаются в IPS Web API.

### 2.2 Разделение токенов (обязательно)

```
MCP-клиент → [MCP-token] → ips-mcp → [IPS-JWT / service-account] → IPS Web API
```

MCP-токен не пробрасывается в IPS напрямую. IPS-мост аутентифицирует вызывающего, проверяет права на класс операции, затем обращается к IPS со своим upstream-креденшелом.

---

## 3. Структура инструментов (Tools)

### 3.1 Принцип проектирования

**Инструменты = задачи пользователя, а не endpoint'ы API.**

Не делать `POST_core_api_objects_composition`, делать `ips_get_bom`.

### 3.2 Toolsets — домены инструментов

| Toolset | Инструменты | Режим |
|---|---|---|
| `objects` | `ips_search_objects`, `ips_get_object_card`, `ips_get_object_versions` | read-only |
| `composition` | `ips_get_bom`, `ips_get_bom_flat`, `ips_get_applicability` | read-only |
| `techprocess` | `ips_get_techprocess`, `ips_get_operations`, `ips_get_resources` | read-only |
| `files` | `ips_get_file_list`, `ips_get_file_meta` | read-only |
| `metadata` | `ips_get_attr_schema`, `ips_resolve_attr_value`, `ips_get_lifecycle_stages` | read-only |
| `changes` | `ips_get_changes_list`, `ips_get_change_card` | read-only |
| `write` | `ips_prepare_create_op`, `ips_commit_create_op`, `ips_update_attr` | write (отключён по умолчанию) |

### 3.3 Аннотации инструментов

Каждый инструмент обязан иметь:

```python
@tool(
    name="ips_get_bom",
    description="Получить состав изделия (BOM) по ID объекта IPS...",
    read_only_hint=True,       # ReadOnlyHint — фильтр в read-only режиме
    destructive_hint=False,    # DestructiveHint — требует human-approval
    idempotent_hint=True,
)
```

Write-инструменты: `destructive_hint=True`, `read_only_hint=False`.

---

## 4. Режимы запуска

### 4.1 Read-only режим (по умолчанию)

```bash
ips-mcp --config config.yaml
# или явно:
ips-mcp --config config.yaml --read-only
```

При включённом `read-only`:
- Все инструменты с `read_only_hint=False` **не регистрируются** — физически недоступны.
- Флаг `read-only` имеет приоритет над любыми другими настройками.
- Поведение идентично GitHub MCP Server (`--read-only` с `ReadOnlyHint`).

### 4.2 Write-enabled режим

```bash
ips-mcp --config config.yaml --enable-write
```

Включает toolset `write`. Write-операции требуют human-approval (см. раздел 5).

### 4.3 Выборочные toolsets

```bash
ips-mcp --config config.yaml --toolsets objects,composition,techprocess
```

---

## 5. Двухфазная запись и human-approval

По образцу Salesforce MCP.

### 5.1 Схема

```
1. LLM вызывает ips_prepare_create_op(...)
   → MCP возвращает preview (что будет создано), request_id

2. MCP удерживает операцию (НЕ выполняет)
   → Показывает человеку: «Подтвердите создание объекта X»

3. Человек одобряет → ips_commit_create_op(request_id)
   → Валидность: Once (разовое подтверждение)

4. IPS Web API создаёт объект
```

### 5.2 Аннотация write-tools

```python
@tool(destructive_hint=True, approval_required=True, approval_validity="once")
async def ips_commit_create_op(request_id: str) -> ToolResult:
    ...
```

`approval_validity="once"` — подтверждение действует строго на одну операцию. Никаких «30-минутных грантов» на write-класс.

---

## 6. Ответы инструментов: обогащение и фильтрация

### 6.1 Правила преобразования ответа IPS

| Поле IPS API | Действие | Причина |
|---|---|---|
| `ftObjectLink` (числовой ID) | Разворачивать в `{id, type, designation, name}` | LLM не может использовать числовой ID |
| Внутренние служебные поля | Удалять | Не нужны агенту, засоряют контекст |
| Вложенные объекты > 2 уровней | Уплощать или пагинировать | Защита контекстного окна |
| Атрибуты по числовым ключам `"10"`, `"9"` | Маппить в human-readable имена из схемы | Через `ips_get_attr_schema` |

### 6.2 Стабильные идентификаторы

Каждый ответ инструмента обязан содержать стабильный `object_id` IPS, который агент может использовать в последующих вызовах. Текстовые поля (`designation`, `name`) — дополнительно, не вместо.

### 6.3 Пагинация

```python
# Каждый list-инструмент возвращает:
{
  "items": [...],
  "total": 347,
  "page": 1,
  "page_size": 20,
  "has_more": True
}
```

Не класть весь ответ API в контекст.

---

## 7. Борьба с tool-bloat (обязательно)

### 7.1 Целевые метрики

| Метрика | Цель |
|---|---|
| Число инструментов по умолчанию | ≤ 15 |
| Число инструментов в полном наборе | ≤ 40 |
| Токены на определения tools | < 20% контекстного окна |
| Токены одного tool definition | ≤ 400 токенов |

### 7.2 Стратегии

- **Консолидация:** близкие операции → один tool с параметром режима, а не по endpoint'у.
- **Toolsets:** активировать только нужный домен через `--toolsets`.
- **Динамическая загрузка** (P2): мета-tool `ips_load_domain(domain: str)`, регистрирует tools по требованию и шлёт `notifications/tools/list_changed`.

---

## 8. Конфигурация

### 8.1 Файл config.yaml

```yaml
ips:
  base_url: "https://ips.your-plant.ru/core/api"
  service_account:
    username: "ips_mcp_svc"
    password_env: "IPS_MCP_SVC_PASSWORD"   # из переменной окружения
  token_refresh_before_expiry_sec: 60
  timeout_sec: 30
  retry:
    max_attempts: 2
    backoff_factor: 1.5

mcp:
  transport: "stdio"          # или "sse"
  read_only: true             # по умолчанию
  toolsets:
    - objects
    - composition
    - techprocess
    - files
    - metadata
  rate_limit:
    requests_per_minute: 120

audit:
  enabled: true
  log_path: "/var/log/ips-mcp/audit.jsonl"
  log_inputs: true
  log_outputs: false          # не логировать полные ответы (размер)
```

### 8.2 Переменные окружения

```
IPS_MCP_SVC_PASSWORD    — пароль сервисного аккаунта IPS
IPS_MCP_BASE_URL        — переопределяет base_url из конфига
IPS_MCP_LOG_LEVEL       — DEBUG / INFO / WARNING
```

---

## 9. Swagger 2.0 → OpenAPI 3.x

IPS может отдавать Swagger 2.0. FastMCP и ряд генераторов требуют OpenAPI 3.x.

**Обязательный шаг при инициализации:**

```python
from swagger_converter import convert_swagger_to_openapi

raw_spec = fetch_swagger_json(config.ips.base_url)
if raw_spec.get("swagger", "").startswith("2."):
    spec = convert_swagger_to_openapi(raw_spec)   # конвертация
else:
    spec = raw_spec

# Далее FastMCP.from_openapi(spec, ...)
```

Инструмент конвертации: `prance` или `swagger2openapi` (npm) через subprocess, либо встроенный конвертер `fastmcp`.

---

## 10. Обработка ошибок

### 10.1 Маппинг ошибок IPS → понятный текст

| HTTP-код IPS | Причина | Текст для агента |
|---|---|---|
| 401 | Токен истёк | «IPS: требуется повторная аутентификация» |
| 403 | Нет прав на объект | «IPS: у сервисного аккаунта нет прав на объект {id}» |
| 404 | Объект не найден | «IPS: объект с ID {id} не найден» |
| 409 | Конфликт версии / блокировка | «IPS: объект заблокирован другим пользователем» |
| 5xx | Ошибка сервера IPS | «IPS: внутренняя ошибка сервера, повторите позже» |
| timeout | Нет ответа | «IPS: запрос превысил таймаут ({n}с)» |

### 10.2 Правило ретраев

- Ретрай только на сетевые ошибки и 5xx.
- Максимум 2 попытки с экспоненциальной задержкой.
- НЕ ретраить 4xx (логическая ошибка, ретрай бессмысленен).

---

## 11. Аудит и наблюдаемость

Каждый вызов tool пишет запись в audit log (JSONL):

```json
{
  "ts": "2026-09-23T14:52:01Z",
  "tool": "ips_get_bom",
  "inputs": {"object_id": "123456", "depth": 2},
  "ips_endpoint": "GET /objects/123456/composition",
  "duration_ms": 312,
  "status": "ok",
  "result_items": 47
}
```

Write-операции логируют дополнительно: `request_id`, `approval_by`, `approval_ts`.

---

## 12. Структура проекта

```
ips-mcp/
├── pyproject.toml
├── README.md
├── config.example.yaml
│
├── ips_mcp/
│   ├── __init__.py
│   ├── server.py            # Точка входа, регистрация tools
│   ├── config.py            # Pydantic-модель конфига
│   ├── auth.py              # JWT-получение и refresh
│   ├── client.py            # HTTP-клиент к IPS Web API
│   ├── swagger.py           # Загрузка и конвертация Swagger
│   ├── mapper.py            # Преобразование ответов IPS
│   ├── audit.py             # Аудит-лог
│   │
│   └── tools/
│       ├── objects.py       # toolset: objects
│       ├── composition.py   # toolset: composition
│       ├── techprocess.py   # toolset: techprocess
│       ├── files.py         # toolset: files
│       ├── metadata.py      # toolset: metadata
│       ├── changes.py       # toolset: changes
│       └── write.py         # toolset: write (отключён по умолчанию)
│
└── tests/
    ├── test_auth.py
    ├── test_mapper.py
    └── test_tools/
```

---

## 13. Технологический стек

| Компонент | Выбор | Обоснование |
|---|---|---|
| MCP SDK | `modelcontextprotocol/python-sdk` | Официальный, типизированный |
| Генерация из OpenAPI | `fastmcp` (FastMCP.from_openapi) | Зрелый, Bearer-auth из коробки |
| HTTP-клиент | `httpx` (async) | Async, таймауты, retry |
| Конфиг | `pydantic-settings` | Валидация, env-подстановка |
| Конвертация Swagger | `prance` | Swagger 2.0 → OpenAPI 3.x |
| Логирование | `structlog` | JSONL, структурированный вывод |
| Тесты | `pytest` + `respx` | Мок IPS API без реального сервера |

---

## 14. Приоритеты реализации

### P0 — Фундамент (до первого релиза)

- [ ] Read-only режим по умолчанию с фильтром по `readOnlyHint`
- [ ] Разделение MCP-токена и upstream IPS-креденшела
- [ ] Базовые toolsets: `objects`, `composition`, `techprocess`
- [ ] Разворачивание `ftObjectLink` в человекочитаемые поля
- [ ] Пагинация во всех list-инструментах
- [ ] Audit log (JSONL)
- [ ] Конвертация Swagger 2.0 → OpenAPI 3.x при загрузке

### P1 — Расширение (итерация 2)

- [ ] Toolsets: `files`, `metadata`, `changes`
- [ ] Write-режим с двухфазным `prepare`→`commit` и human-approval (Once)
- [ ] Маппинг числовых атрибутов в имена через `ips_get_attr_schema`
- [ ] Toolset-фильтр через `--toolsets` CLI-параметр
- [ ] Rate limiting + graceful degradation

### P2 — Оптимизация (итерация 3)

- [ ] Динамическая загрузка toolsets через мета-tool + `list_changed`
- [ ] SSE-транспорт (в дополнение к stdio)
- [ ] Метрики (Prometheus или OpenTelemetry)
- [ ] Автообновление Swagger-схемы при изменении IPS API

---

## 15. Критерии приёмки

| Критерий | Проверка |
|---|---|
| Read-only по умолчанию | Write-tools не видны агенту без `--enable-write` |
| Изоляция токенов | IPS-JWT не передаётся клиенту MCP |
| `ftObjectLink` разворачивается | Ответ содержит `designation` и `name`, а не числовой ID |
| Tool-bloat контроль | ≤ 15 tools в дефолтном наборе, < 20% контекста |
| Ошибки читаемы | Все 4xx/5xx → понятный текст без стека исключений |
| Аудит | Каждый вызов записан в JSONL-лог |
| Swagger 2.0 | Спека конвертируется без ручного вмешательства |
| Write-safety | `commit` без `prepare` возвращает ошибку |

---

## Приложение A: Ключевые выводы из референсных проектов

| Проект | Паттерн | Применение в IPS-MCP |
|---|---|---|
| github-mcp-server | `ReadOnlyHint` + `--read-only` флаг | Идентичная реализация для read-only режима |
| salesforce/mcp | `destructiveHint` + human-approval + `Once` | Двухфазная запись для write-toolset |
| fastmcp | `from_openapi()` + Bearer-auth | Базовая генерация tool definitions из Swagger |
| openapi-mcp-generator | Swagger 2.0 → 3.x конвертация | Предобработка IPS Swagger |
| mcp omnisearch (паттерн) | 20 tools → 8 tools консолидация | Целевой уровень ≤ 15 tools по умолчанию |

