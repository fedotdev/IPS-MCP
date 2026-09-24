# IPS-MCP

MCP-сервер для доступа к [IPS Web API](https://www.intermech.ru/) — PLM/PDM платформе IPS 9.x.
Сервер транслирует типизированные MCP-инструменты в вызовы Web API, нормализует ответы для LLM
(числовые ID → читаемые имена, без служебных полей) и работает без клиентского IPS SDK.

Протокол MCP реализован на стандартной библиотеке (JSON-RPC 2.0 поверх stdio) — MCP SDK не используется.

## Возможности

- **12 read-only инструментов**: объекты, состав, связи, метаданные, жизненный цикл.
- **Двухфазная запись** (опционально): `preview → commit` с одноразовым подтверждением, без автоматических повторов.
- **JWT-авторизация**: lazy-вход и refresh после `401` без гонок; повтор только на сетевые ошибки и 5xx, не на 4xx и не на запись.
- **База знаний `gloss.db`**: ID типов/атрибутов/связей дополняются именами из сгенерированного справочника.
- **`ftObjectLink` разворачивается** в `{id, objectType, objectTypeName, caption}` (до 10 ссылок на вызов).
- **Пагинация** list-инструментов: `{items, total, page, page_size, has_more}`.
- **Дерево состава** с лимитами глубины и числа узлов и защитой циклов.
- **JSONL-аудит** каждого вызова без токенов (`IPS_AUDIT_LOG`).
- Ошибки IPS возвращаются читаемым текстом (`IPS: атрибут 1066 не найден (id=1349039)`), без стек-трейсов.

## Инструменты

| Домен | Инструмент |
|---|---|
| Сессия | `ips_get_current_user` |
| Объекты | `ips_get_object`, `ips_get_object_attributes` |
| Состав | `ips_get_composition`, `ips_get_composition_filtered`, `ips_get_composition_tree` |
| Связи | `ips_get_relation`, `ips_get_relation_attributes` |
| Метаданные | `ips_get_object_type`, `ips_get_attribute_type`, `ips_get_relation_type`, `ips_get_lifecycle` |
| Запись* | `ips_prepare_update_attribute`, `ips_commit_update_attribute` |

\* Доступны только при `IPS_ENABLE_WRITE=1`.

> По умолчанию сервер — strictly read-only. Запись включена явным флагом и ограничена изменением одного атрибута.

## Требования

- Python 3.10+
- `httpx` — единственная зависимость

## Установка

```bash
pip install -e .
```

## Конфигурация

Параметры задаются переменными окружения — секреты не хранятся в коде и конфигурации:

| Переменная | Обязательно | Описание |
|---|---|---|
| `IPS_LOGIN` | да | Имя пользователя IPS |
| `IPS_PASSWORD` | да | Пароль пользователя IPS |
| `IPS_BASE_URL` | нет | По умолчанию `http://192.168.80.70:8080` |
| `IPS_ROLE_ID` | нет | ID роли при входе (по умолчанию `0`) |
| `IPS_GLOSS_DB` | нет | Путь к `gloss.db` (по умолчанию `gloss/generated/gloss.db`) |
| `IPS_AUDIT_LOG` | нет | Путь к JSONL-журналу вызовов (пусто = не писать) |
| `IPS_ENABLE_WRITE` | нет | `1` включает write-инструменты |

Пример:

```bash
export IPS_LOGIN=user_name
export IPS_PASSWORD=user_password
export IPS_ROLE_ID=782041
```

## Запуск

```bash
ips-mcp
```

Сервер читает MCP-запросы (JSON-RPC 2.0) из stdin построчно и отвечает в stdout —
стандартный stdio-транспорт MCP, подходит для любого MCP-клиента.

Или напрямую:

```bash
python -m ips_mcp.server
```

## Запись (двухфазная)

Write-инструменты отключены по умолчанию. При `IPS_ENABLE_WRITE=1` любой write проходит два шага:

```text
ips_prepare_update_attribute(object_id, attribute_id, value)
    → preview текущего значения + одноразовый request_id (запись НЕ выполняется)

ips_commit_update_attribute(request_id)
    → повторная проверка старого значения → checkout → edit → attributes
    → saveChanges → checkIn → verify-read
```

- Коммит автоматически делает `cancelChanges`, если операция упала после checkout.
- Write-запросы **не ретраятся** (в отличие от чтения).
- Одноразовый `request_id` исключает повторную запись по одному подтверждению.
- Отклонение preview не вызывает side-эффектов: до commit никаких изменений в IPS не происходит.

> Комментарий к «Действиям над объектом» в IPS не передаётся: проверенный Swagger Web API
> не содержит параметра комментария у этих endpoint'ов. Зачем сделано изменение — в аудит-логе MCP.

## База знаний (gloss)

Имена типов, атрибутов и связей берутся из `gloss.db` (SQLite), который администратор генерирует
из Excel-выгрузок IPS:

```bash
pip install openpyxl
python gloss/generate_gloss.py
```

Результат — `gloss/generated/gloss.db` + манифест сборки. Файлы сборки не хранятся в репозитории
и воспроизводятся этой командой. Сервер работает и без справочника — просто без подстановки имён.

## Тесты

```bash
python tests/test_ips.py
```

Проверяются: вход и refresh по `401`, повтор сетевых ошибок, `cancelChanges` при падении write,
пагинация, разворачивание ссылок и одноразовость подтверждения — на `httpx.MockTransport`, без реального сервера IPS.

## Структура

```
src/ips_mcp/
├── server.py      # MCP-сервер (stdio, JSON-RPC), реестр tools, write-flow, аудит
└── ips.py         # HTTP-клиент IPS: JWT, ретраи, маппинг, пагинация, write
gloss/
├── generate_gloss.py   # генератор базы знаний из Excel
└── generated/          # схема, план; сборка (gloss.db) игнорируется git
docs/
└── IPS-MCP-CONTRACT.md # контракт tool → endpoint, DTO, правила нормализации
tests/test_ips.py
```

## Безопасность

- Write по умолчанию отключён и ограничен одним атрибутом при явном `IPS_ENABLE_WRITE=1`.
- Токены IPS живут только в памяти и не попадают ни в ответы MCP, ни в аудит.
- Пароль — только из переменной окружения.
- Права на операции остаются в IPS Web API; мост не дублирует бизнес-логику и не расширяет права.

## Ограничения

- `gloss.db` привязан к конкретной базе IPS; между базами требуется повторная выгрузка.
- `ftObjectLink` разворачивается до 10 ссылок на вызов и возвращает `caption`, а не полный набор атрибутов.
- Write поддерживает только изменение одного атрибута; создание объектов и связей, удаление и массовая запись не реализованы.