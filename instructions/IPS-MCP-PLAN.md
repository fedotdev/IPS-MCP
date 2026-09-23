# IPS-MCP: улучшенный план реализации

Версия плана: 0.2  
Дата: 2026-09-23  
Целевая среда: IPS 9.0.4.11310, Web API 1.0, Windows Server/Client

## 1. Цель проекта

Сделать MCP-сервер для IPS Web API, который:

1. Даёт LLM безопасный доступ к чтению данных IPS.
2. По умолчанию работает в read-only режиме.
3. Поддерживает ограниченные write-сценарии только через preview и явное подтверждение.
4. Не дублирует бизнес-логику IPS, а использует штатный Web API как источник истины.
5. Остаётся компактным по числу tools и понятным по диагностике.

## 2. Целевой результат MVP

MVP должен позволять через MCP:

1. Подключиться к настроенному IPS Web API.
2. Авторизовать пользователя или сервисный аккаунт IPS.
3. Получить сведения о текущем пользователе и правах сессии.
4. Читать объект, атрибуты, версии и состав.
5. Читать типы объектов, атрибутов, связей и этапов ЖЦ.
6. Строить ограниченное дерево состава.
7. Возвращать для LLM не «сырые» ID, а нормализованные данные.
8. Формировать preview изменения без фактической записи.
9. После явного подтверждения выполнять ограниченные write-операции.

В MVP не входят:

- удаление объектов;
- прямой SQL;
- массовые изменения;
- изменение схем ЖЦ, прав, метаданных;
- администраторские операции IPS.

## 3. Источники знаний для проекта

При разработке использовать источники в таком порядке:

1. Руководства IPS из `manuals/`.
2. Транскрипты обучений `GMT2025*.txt`.
3. `IPS Bridge.md` как прикладной документ по мосту.
4. Swagger / OpenAPI IPS Web API.
5. Референсные MCP-репозитории.

### 3.1. Как подтянуть manuals без дублирования

Дублировать руководства в репозитории `ips-mcp` не нужно. Правильный способ — подтягивать только каталог `manuals/` из репозитория-источника через sparse-checkout.

```bash
# Получить только папку manuals/ без полного дерева репозитория
 git clone --filter=blob:none --no-checkout \
   https://github.com/fedotdev/ips-helper-suite-5.git \
   ips-helper-suite-5

 cd ips-helper-suite-5
 git sparse-checkout init --cone
 git sparse-checkout set manuals
 git checkout main
```

Это даёт локальную папку `manuals/` с руководствами IPS и транскриптами, не таща лишние части репозитория.

### 3.2. Что обязательно изучить из manuals

- `IPS. Руководство пользователя.md`
- `IPS. Руководство конструктора.md`
- `IPS. Руководство технолога.md`
- `IPS. Руководство программиста.md`
- `IPS. Руководство программиста. Модули расширения.md`
- `IPS. Руководство администратора.md`
- `IPS Bridge.md`
- `IPS. Проведение изменений в ЭКД.md`
- `GMT2025*.txt`

## 4. Референсные MCP-репозитории

Перед реализацией изучить и локально клонировать:

```bash
# GitHub MCP: эталон read-only и toolsets
 git clone https://github.com/github/github-mcp-server.git

# Официальный Python SDK MCP
 git clone https://github.com/modelcontextprotocol/python-sdk.git

# FastMCP: генерация из OpenAPI
 git clone https://github.com/jlowin/fastmcp.git

# Salesforce MCP: destructiveHint и approval flow
 git clone https://github.com/salesforce/mcp.git

# Генераторы MCP из OpenAPI / Swagger
 git clone https://github.com/harsha-iiiv/openapi-mcp-generator.git
 git clone https://github.com/readyapi/mcp-swagger-server.git
```

### Что брать из референсов

| Репозиторий | Что заимствовать |
|---|---|
| `github-mcp-server` | `--read-only`, toolsets, фильтрация tools |
| `python-sdk` | Базовые типы MCP, сервер, transport |
| `fastmcp` | `from_openapi()`, быстрая генерация инструментов |
| `salesforce/mcp` | `destructiveHint`, approval, двухфазная запись |
| `openapi-mcp-generator` | идеи автогенерации из OpenAPI |
| `mcp-swagger-server` | работа со Swagger 2.0 / OpenAPI |

## 5. Архитектурные принципы

### 5.1. MCP — адаптер, а не новый backend

IPS Web API остаётся источником истины. MCP-сервер только:

- принимает вызовы tools;
- валидирует вход;
- применяет политику безопасности;
- вызывает IPS Web API;
- нормализует ответ для LLM;
- пишет аудит.

### 5.2. Инструменты должны отражать задачи, а не endpoint'ы

Нельзя просто публиковать каждый REST endpoint как отдельный tool. Нужны инструменты уровня задачи:

- `ips_get_object_card`
- `ips_get_bom`
- `ips_get_techprocess`
- `ips_prepare_update_attribute`
- `ips_commit_update_attribute`

### 5.3. Read-only по умолчанию

Write-tools по умолчанию не должны быть доступны MCP-клиенту.

### 5.4. Разделение аутентификации

MCP-аутентификация и IPS-аутентификация должны быть разведены. MCP-токен нельзя просто прокидывать в IPS как есть.

## 6. Предлагаемая архитектура

```text
MCP Client / LLM
        |
        v
IPS-MCP Server
  ├─ Tool registry
  ├─ Toolset filter
  ├─ ReadOnly / Write policy
  ├─ Approval layer
  ├─ IPS domain services
  │    ├─ AuthService
  │    ├─ ObjectService
  │    ├─ CompositionService
  │    ├─ RelationService
  │    ├─ MetadataService
  │    └─ ChangeService
  ├─ DTO / validation
  ├─ Mapper / normalizer
  ├─ TokenStore
  ├─ AuditLog
  └─ HttpClient
        |
        v
IPS Web API
```

## 7. Целевые toolsets

| Toolset | Назначение | Режим |
|---|---|---|
| `objects` | карточки, версии, атрибуты | read-only |
| `composition` | состав, применяемость, связи | read-only |
| `techprocess` | техпроцессы, операции, переходы | read-only |
| `metadata` | типы объектов, атрибутов, связей | read-only |
| `changes` | изменения и связанные данные | read-only |
| `files` | файлы и метаданные файлов | read-only |
| `write` | ограниченные операции записи | write |

### Ограничение на число tools

- В дефолтной поставке: не больше 10–15 tools.
- Полный набор: желательно не больше 30–40 tools.
- Инструменты группировать по toolsets, чтобы не раздувать контекст.

## 8. Нормализация ответов IPS

MCP-сервер не должен отдавать LLM только внутренние числовые поля IPS без расшифровки.

### Обязательные правила

1. `ftObjectLink` разворачивать в читаемую структуру.
2. Числовые атрибуты по возможности маппить в имена.
3. Возвращать одновременно:
   - `objectId`
   - `versionId`
   - `relationId`
   - `typeId`
   - человекочитаемые поля (`designation`, `name`, `typeName`).
4. Ограничивать глубину дерева и размер ответа.
5. Убирать лишние служебные поля, не нужные агенту.

## 9. Этапы реализации

### Этап 0. Зафиксировать стек и окружение

Перед кодом определить:

- язык и runtime;
- MCP SDK;
- transport: stdio / SSE / HTTP;
- способ установки: локально, service, Docker;
- модель секретов;
- один IPS-сервер или несколько профилей.

**Рекомендуемый базовый вариант:** Python + официальный MCP SDK или FastMCP, transport `stdio`, `httpx`, `pydantic`, конфигурация через env + yaml.

### Этап 1. Подготовить источники знаний

Задачи:

1. Подтянуть `manuals/` через sparse-checkout.
2. Сохранить список ключевых руководств для проекта.
3. Клонировать MCP-референсы.
4. Зафиксировать версию IPS, под которую делается мост.
5. Подготовить локальный каталог `docs-sources/` или отдельный README со ссылками на источники.

Результат этапа: разработчик и LLM работают по одним и тем же исходным документам, без дублирования PDF/MD в основном репозитории.

### Этап 2. Импорт и нормализация Swagger / OpenAPI

Задачи:

1. Получить исходный `swagger.json`.
2. Сохранить его как snapshot, не меняя оригинал.
3. Проверить: Swagger 2.0 или OpenAPI 3.x.
4. При необходимости выполнить конвертацию Swagger 2.0 → OpenAPI 3.x.
5. Построить индекс endpoint'ов:
   - method;
   - path;
   - operationId;
   - request schema;
   - response schema;
   - security.
6. Сопоставить endpoint'ы с будущими MCP tools.

### Этап 3. Базовый HTTP-клиент и аутентификация

Реализовать:

- базовый `HttpClient`;
- Bearer-аутентификацию;
- login;
- refresh token;
- lock/single-flight на refresh;
- корректную обработку кириллицы;
- timeout и retriable/non-retriable ошибки.

Критерии:

- 401 до логина обрабатывается предсказуемо;
- login успешен;
- refresh обновляет токены без гонок;
- токены не попадают в лог.

### Этап 4. Read-only MVP tools

Сначала реализовать именно чтение:

1. `ips_get_current_user`
2. `ips_get_object_card`
3. `ips_get_object_attributes`
4. `ips_get_object_versions`
5. `ips_get_bom`
6. `ips_get_relation_attributes`
7. `ips_get_object_type`
8. `ips_get_attribute_type`
9. `ips_get_relation_type`
10. `ips_get_composition_tree`

Критерии:

- тестовые объекты IPS читаются стабильно;
- дерево состава ограничено по глубине;
- ответы читаемы для LLM;
- ошибки IPS не теряют detail.

### Этап 5. Политика безопасности и режимы запуска

Реализовать:

- `--read-only`;
- `--enable-write`;
- `--toolsets objects,composition,...`;
- deny-by-default для опасных операций;
- аннотации tools: `readOnlyHint`, `destructiveHint`.

Политика минимум:

```text
read: allow
create: confirm
update attribute: confirm
create relation: confirm
checkin: confirm
delete: deny
security changes: deny
lifecycle transition: deny по умолчанию
```

### Этап 6. Preview и двухфазная запись

Сначала сделать preview, потом commit.

Пример потока:

```text
ips_prepare_update_attribute(...)
  -> preview
  -> request_id
  -> подтверждение человеком
ips_commit_update_attribute(request_id)
```

Требования:

- preview машиночитаемый;
- commit невозможен без prepare;
- подтверждение действует один раз (`Once`);
- после commit обязателен verify-read.

### Этап 7. Безопасное редактирование объекта

Реализовать orchestration layer:

```text
checkout(baseObjectId)
  -> workingCopyId
edit(workingCopyId)
setAttributes(workingCopyId)
saveChanges(workingCopyId)
checkIn(workingCopyId)
verify(baseObjectId)
```

Правила:

- записывать только в working copy, если она создана;
- base object не использовать для записи после checkout;
- rollback/release делать только если подтверждён соответствующий endpoint;
- не делать лишний checkin автоматически.

### Этап 8. Создание операции и технологического состава

Реализовать отдельный тестовый write-сценарий:

1. Создание объекта типа операции.
2. Передача только подтверждённых атрибутов.
3. Исключение вычисляемых и системных полей.
4. Commit creation.
5. Получение фактических `objectId/versionId`.
6. Checkout родителя.
7. Создание relation технологического состава.
8. Изменение атрибута порядка связи.
9. Save/checkin родителя.
10. Verify-read результата.

### Этап 9. Аудит, эксплуатация, диагностика

Добавить:

- health check;
- structured audit log;
- режим dry-run;
- ограничение глубины и размера состава;
- отчёт по незавершённым checkout;
- конфигурацию уровней риска;
- журнал approval-событий.

## 10. Технические требования к реализации

### 10.1. HttpClient

Обязанности:

- base URL из конфигурации;
- UTF-8 JSON;
- Bearer auth;
- timeout;
- сохранение status code и detail ошибки;
- один повтор после refresh на 401;
- отсутствие автоповтора записи после 5xx.

### 10.2. TokenStore

Минимально хранить:

- `accessToken`
- `refreshToken`
- `expireTime`
- `userId`
- `roleId`
- `accessLevelId`
- `sessionId` / `loginId`

### 10.3. DTO

Нужны typed DTO минимум для:

- токенов;
- auth;
- объекта;
- атрибута;
- значений атрибутов;
- relation;
- состава;
- create object;
- create relation;
- problem details.

## 11. Тестовый план

### Unit-тесты

Проверить:

- UTF-8 кириллицу;
- парсинг ошибок IPS;
- refresh single-flight;
- сериализацию object link / int / string / bool;
- preview redaction;
- mapper для `ftObjectLink`;
- поведение deny/confirm policy.

### Integration-тесты

Проверить:

- `userInfo`;
- чтение тестового объекта;
- чтение состава;
- чтение relation attributes;
- write preview;
- создание тестовой операции;
- включение в состав;
- изменение порядка;
- verify-read после записи.

### Регрессионные проверки

После write-сценариев проверять:

- число элементов состава;
- порядок;
- уникальность номеров;
- значения ключевых атрибутов;
- тип связи;
- атрибут порядка;
- отсутствие лишних связей и дублей.

## 12. Что нужно подтвердить до production

1. Точный стек и SDK.
2. Поддерживаемые версии IPS 9.x.
3. Правила HTTPS / сертификатов / reverse proxy.
4. Политику хранения секретов.
5. Таймауты и лимиты IPS Web API.
6. Поведение при незавершённом checkout.
7. Ограничения на архивные копии.
8. Допустимые роли и access levels.
9. Многобазовость / несколько IPS-серверов.
10. Нужен ли SSO / Kerberos.
11. Официальную семантику `objectID`, `id`, `versionID`.
12. Подтверждённый цикл `checkout → edit → saveChanges → checkIn`.
13. Семантику relation type и relation attributes в нужной предметной области.

## 13. Что добавить в репозиторий проекта

- `IPS-MCP-SPEC.md`
- `IPS-MCP-PLAN.md`
- `config.example.yaml`
- `README.md`
- snapshot Swagger / OpenAPI
- mapping MCP tool → operationId
- catalog типовых ошибок IPS
- examples запросов и ответов без токенов
- runbook по refresh token, stuck checkout и аварийному отключению write tools
- ссылки на `manuals/` как внешний источник знаний

## 14. Рекомендуемая структура проекта

```text
IPS-MCP/
├─ spec/
│  ├─ ips-webapi.json
│  ├─ ips-webapi.normalized.json
│  └─ endpoint-coverage.json
├─ docs/
│  ├─ IPS-MCP-SPEC.md
│  ├─ IPS-MCP-PLAN.md
│  ├─ references.md
│  └─ runbook.md
├─ src/
│  ├─ ips_mcp/
│  │  ├─ server.py
│  │  ├─ config.py
│  │  ├─ auth.py
│  │  ├─ client.py
│  │  ├─ mapper.py
│  │  ├─ audit.py
│  │  ├─ policy.py
│  │  ├─ approvals.py
│  │  └─ tools/
│  └─ ...
├─ tests/
├─ examples/
└─ README.md
```

## 15. Definition of Done для MVP

MVP считается готовым, если:

1. MCP-клиент видит рабочий набор read-only tools.
2. Login/refresh работает без утечки секретов.
3. Чтение объекта и состава работает на тестовой базе.
4. Ответы нормализованы и пригодны для LLM.
5. Все write tools скрыты по умолчанию.
6. Все write-сценарии требуют preview и явного подтверждения.
7. После записи всегда выполняется verify-read.
8. Ошибки IPS возвращаются с detail.
9. Аудит сохраняется без токенов.
10. Документация фиксирует версию IPS, ограничения и внешний источник manuals.

## 16. Короткий практический вывод

Сначала надо сделать не «все endpoint'ы IPS через MCP», а небольшой, безопасный и понятный read-only слой поверх Web API. После этого — добавить preview и строго ограниченные write-сценарии. Руководства IPS при этом лучше не копировать в проект, а подтягивать через sparse-checkout из отдельного репозитория-источника.

### 17. IPS SDK и примеры

IPS SDK использовать как источник для подтверждения штатной доменной модели IPS, 
а не как обязательную зависимость IPS-MCP.

Приоритет изучения:
1. Main Sample → Sample_015_LoadComposition.
2. Main Sample → Sample_014_Attributes и Sample_013_NewAttribbute.
3. Main Sample → Sample_019_Techcard.
4. Main Sample → Sample_011_ObjectGenerator.
5. Intermech.Samples.Server → Sample_003_TechCardServerClassifyObjectService.

Цель анализа:
- подтвердить семантику objectID, versionID и relationID;
- уточнить чтение и изменение атрибутов;
- проверить штатную модель состава и технологического состава;
- проверить корректный сценарий создания объектов ТП;
- сопоставить SDK-модель с endpoint’ами IPS Web API.

Ограничение:
- MCP-сервер не строить на COM API и не делать зависимым от клиентского SDK;
- основной транспорт MVP — IPS Web API;
- SDK использовать для верификации поведения IPS и как запасной вариант,
  если конкретного штатного действия нет в Web API.