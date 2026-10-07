# IPS-MCP

MCP-сервер для доступа к [IPS Web API](https://www.intermech.ru/) — PLM/PDM платформе IPS 9.x.
Сервер транслирует типизированные MCP-инструменты в вызовы Web API, нормализует ответы для LLM
(числовые ID → читаемые имена, без служебных полей) и работает без клиентского IPS SDK.

Протокол MCP реализован на стандартной библиотеке (JSON-RPC 2.0 поверх stdio) — MCP SDK не используется.

## Возможности

- **Read-only инструменты**: объекты, безопасный поиск, состав, связи, метаданные, жизненный цикл.
- **Двухфазная запись** (опционально): `ips_prepare_* → ips_commit_*` для атрибута, checkout/finish checkout, создания объекта (в том числе по прототипу)/связи и узкого удаления; одноразовое подтверждение.
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
| Поиск | `ips_search_objects` |
| Запись* | `ips_prepare_checkout_object`, `ips_commit_checkout_object`, `ips_prepare_finish_checkout`, `ips_commit_finish_checkout`, update/create/create-by-prototype tools и delete tools |

\* Все write tools доступны только при `IPS_ENABLE_WRITE=1`; delete allowlist — ровно objectID=1406301/objectGUID=25fe60ea-a218-4278-90f0-542128d7ef03.

> По умолчанию сервер — strictly read-only. Запись включена явным флагом и ограничена подтверждёнными операциями над атрибутами, объектами и связями.

## Требования

- Python 3.10+
- `httpx` — единственная зависимость

## Установка

```bash
pip install -e .
ips-mcp init
```

`init` создаёт `config.json` в корне проекта, читая значения из одноимённых переменных
окружения, а недостающие (`IPS_LOGIN`, `IPS_PASSWORD`) спрашивает в терминале.
Пароль вводится без отображения. Существующий `config.json` не перезаписывается
без `--force` — в нём пароль.

```bash
ips-mcp init --config D:\путь\config.json   # другой путь
ips-mcp init --force                        # перезаписать
```

## Конфигурация

Секреты и параметры собираются в единый файл `config.json` в корне проекта
(в git не хранится; шаблон для копирования — `config.example.json`):

```json
{
  "IPS_LOGIN": "user_name",
  "IPS_PASSWORD": "user_password",
  "IPS_BASE_URL": "http://192.168.80.70:8080",
  "IPS_ROLE_ID": 782041,
  "IPS_GLOSS_DB": "gloss/generated/gloss.db",
  "IPS_AUDIT_LOG": "",
  "IPS_ENABLE_WRITE": false
}
```

Файл ищется в корне проекта независимо от текущего каталога, другой путь задаётся
переменной `IPS_CONFIG`. Относительные `IPS_GLOSS_DB` и `IPS_AUDIT_LOG` разрешаются
от каталога самого `config.json`, поэтому конфиг можно переносить вместе с проектом.
Одноимённые переменные окружения имеют приоритет над файлом — секреты можно выносить
из файла в env (CI/IDE и т.п.).

| Поле / env | Обязательно | Описание |
|---|---|---|
| `IPS_LOGIN` | да | Имя пользователя IPS |
| `IPS_PASSWORD` | да | Пароль пользователя IPS |
| `IPS_BASE_URL` | нет | По умолчанию `http://192.168.80.70:8080` |
| `IPS_ROLE_ID` | нет | ID роли при входе (по умолчанию `0`) |
| `IPS_GLOSS_DB` | нет | Путь к `gloss.db` (по умолчанию `gloss/generated/gloss.db`) |
| `IPS_AUDIT_LOG` | нет | Путь к JSONL-журналу вызовов (пусто = не писать) |
| `IPS_ENABLE_WRITE` | нет | `true` включает write-инструменты |

## Запуск

```bash
ips-mcp
```

Сервер читает MCP-запросы (JSON-RPC 2.0) из stdin построчно и отвечает в stdout —
стандартный stdio-транспорт MCP, подходит для любого MCP-клиента.

Или напрямую из любого каталога (после `pip install -e .`):

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

ips_prepare_checkout_object(object_id) → ips_commit_checkout_object(request_id)
    → отдельный checkout; используйте полученный workingCopyId без изменений
ips_prepare_create_by_prototype(prototype_id) → ips_commit_create_by_prototype(request_id)
    → CreateByPrototype → commitCreation(relatedObjectIds из ответа API)
ips_prepare_finish_checkout(working_copy_id, action=checkin|cancel)
    → ips_commit_finish_checkout(request_id)
```

- Коммит автоматически делает `cancelChanges`, если операция упала после checkout.
- Write-запросы **не ретраятся** (в отличие от чтения).
- Одноразовый `request_id` исключает повторную запись по одному подтверждению.
- `ips_prepare_*` не вызывает side-эффектов в IPS; запись выполняется только соответствующим `ips_commit_*`.
- Ошибка после начала create возвращается как `partial_unknown`: результат в IPS неизвестен, повторять commit нельзя.
- Создание по прототипу сохраняет отрицательные `relatedObjectIds` из ответа `CreateByPrototype` и передаёт их в `commitCreation`; при неизвестном результате сверяйте рабочую копию и не повторяйте.
- Создание связи через `ips_commit_create_relation` напрямую меняет состав; автоматических checkout/checkin родителя нет.
- Для безопасного изменения состава сначала выполните checkout родителя, передайте его workingCopyId как `project_version_id` при создании связи, затем завершите checkout через finish tools.
- Checkin делает saveChanges, затем checkIn. При partial/unknown исходе ничего не повторяйте вслепую; проверьте состояние через read tools.
- Checkout/finish и relation требуют активного checkout, записанного в памяти этого процесса; arbitrary workingCopyId отклоняется, а активные leases теряются после рестарта.
- Удаление необратимо; cascade semantics не проверена. При `unknown`/`partial_unknown` проверьте состояние read-only через `ips_get_object`, не повторяйте вслепую.

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

- Write по умолчанию отключён и доступен только при явном `IPS_ENABLE_WRITE=1`.
- Токены IPS живут только в памяти и не попадают ни в ответы MCP, ни в аудит.
- Пароль хранится только в `config.json` (в `.gitignore`) либо в переменной окружения; в ответах MCP и аудит-логе его нет.
- Права на операции остаются в IPS Web API; мост не дублирует бизнес-логику и не расширяет права.

## Ограничения

- `gloss.db` привязан к конкретной базе IPS; между базами требуется повторная выгрузка.
- `ftObjectLink` разворачивается до 10 ссылок на вызов и возвращает `caption`, а не полный набор атрибутов.
- При ошибке между create и commit состояние возвращается как `partial_unknown`; delete-компенсация не выполняется.
