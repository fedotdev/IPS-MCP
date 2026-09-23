# IPS-MCP: план реализации

Версия плана: 0.1
Целевая среда: IPS 9.0.4.11310, Web API 1.0, Windows Server/Client

## 1. Результат MVP

MVP должен позволять через MCP:

1. Подключиться к настроенному IPS Web API.
2. Авторизовать пользователя IPS.
3. Получить сведения о текущем пользователе и роли.
4. Прочитать объект, атрибуты и состав.
5. Прочитать типы объектов, атрибутов и связей.
6. Построить ограниченное дерево состава.
7. Сгенерировать preview изменения.
8. После явного подтверждения:
   - взять объект на редактирование;
   - изменить атрибут;
   - сохранить и выполнить checkin;
   - создать объект;
   - создать связь технологического состава;
   - изменить порядок через атрибут связи.

Удаление, SQL и массовые операции не входят в MVP.

## 2. Предлагаемая архитектура

```text
MCP Client / модель
        |
        v
MCP Server
  ├─ Tool layer
  ├─ Confirmation / policy layer
  ├─ IPS domain services
  │    ├─ AuthService
  │    ├─ ObjectService
  │    ├─ CompositionService
  │    ├─ RelationService
  │    └─ MetadataService
  ├─ Typed DTO / validation
  ├─ TokenStore
  ├─ AuditLog / redaction
  └─ HttpClient
        |
        v
IPS Web API 1.0
```

### 2.1. HttpClient

Обязанности:

- base URL из конфигурации;
- JSON UTF-8 без ручного перекодирования кириллицы;
- `Authorization: Bearer`;
- timeout и cancellation token;
- сохранение status code, headers, тела ошибки;
- один повтор после refresh при 401;
- запрет автоматического повтора записи после 500.

### 2.2. TokenStore

Минимальная модель:

```text
accessToken
refreshToken
expireTime
userId
roleId
accessLevelId
sessionId/loginId
```

Refresh token заменяется целиком после каждого `refreshTokens`. Конкурирующие запросы должны использовать lock/single-flight, чтобы два параллельных refresh не поглотили один и тот же одноразовый токен.

### 2.3. DTO

Не использовать `dynamic` на границах доменной логики. Создать DTO для:

- `ApiTokensDTO`;
- `AuthRequestDTO`;
- `ObjectDto`;
- `QuickObjectInfo`;
- `AttributeDto`;
- `AttributeValuesDto`;
- `CreateObjectDto`;
- `CreateRelationDto`;
- `UpdateRelationsAttributesDto`;
- `RelationDto`;
- `ObjectCompositionDto`;
- `ApiProblemDetails`.

Для неизвестных `values` использовать JSON value / object, потому что тип зависит от `FieldTypes`.

## 3. Этапы реализации

### Этап 0. Уточнить стек и границы

Перед кодом нужно определить:

- язык и runtime: C#/.NET или TypeScript/Node;
- используемый MCP SDK;
- транспорт MCP: stdio, HTTP или оба;
- способ установки: локальный процесс, Windows Service, Docker или серверный процесс;
- один IPS-сервер или несколько профилей подключения;
- хранение секретов Windows Credential Manager/DPAPI/Vault;
- требование к совместимости с OpenCode/другими MCP-клиентами.

Рекомендуемый минимальный вариант: C#/.NET, stdio MCP server, `HttpClient`, `System.Text.Json`, конфигурация через environment variables/JSON без паролей в файле.

### Этап 1. Импорт и нормализация Swagger

Задачи:

1. Скопировать исходную спецификацию в `spec/ips-webapi-1.0.json`.
2. Не менять оригинал.
3. Написать loader, который допускает duplicate JSON keys или выполнить контролируемую нормализацию.
4. Построить индекс:
   - operationId;
   - HTTP method;
   - path;
   - security requirement;
   - request/response schema.
5. Проверить конфликтующие пути с разным регистром.
6. Сгенерировать отчёт покрытия endpoint-ов.

Важно: стандартный PowerShell 5.1 `ConvertFrom-Json` не принимает текущую спеку из-за duplicate keys. Нельзя удалять один из конфликтующих endpoint-ов без решения, какой маршрут реально поддерживает сервер.

### Этап 2. Подключение и диагностика

Реализовать:

- `GET /`;
- `GET /swagger/index.html` — только диагностика доступности;
- `GET /core/api/currentUsers/userInfo`;
- login options;
- authenticate;
- refresh.

Критерии готовности:

- корректный 401 до авторизации;
- успешный login;
- refresh с сохранением новой пары;
- отсутствие токенов в логах;
- корректная кириллица в JSON.

### Этап 3. Read-only инструменты

Порядок:

1. `ips_get_object`;
2. `ips_get_object_info`;
3. `ips_get_object_attributes`;
4. `ips_get_composition`;
5. `ips_get_relation_attributes`;
6. `ips_get_object_type` / `ips_get_attribute_type` / `ips_get_relation_type`;
7. `ips_get_composition_tree` с лимитами.

Критерии:

- объект операции 1349039 читается;
- ЕТП 1349025 раскрывается до КТД, цехозахода и операций;
- выводятся object ID, version ID, relation ID, relation type;
- дерево не уходит в бесконечную рекурсию;
- 401/400/405 отображаются без потери IPS detail.

### Этап 4. Policy и подтверждение записи

До записи реализовать отдельный слой политики:

```text
read: allow
create: confirm
update attribute: confirm
create relation: confirm
checkin: confirm
lifecycle transition: deny by default
delete: deny by default
security changes: deny by default
```

Preview должен быть машиночитаемым:

```json
{
  "action": "update_attribute",
  "server": "...",
  "user": "...",
  "objectId": 1349039,
  "attributeId": 1066,
  "attributeName": "Номер объекта",
  "oldValue": "005",
  "newValue": "005 ТЕСТ",
  "effects": ["checkout", "saveChanges", "checkIn"]
}
```

### Этап 5. Безопасное редактирование объекта

Реализовать транзакционный orchestration layer:

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

- не использовать base ID для записи, если checkout вернул рабочую копию;
- при ошибке после checkout выполнять безопасную попытку rollback/release, если конкретный endpoint подтверждён;
- не делать checkin автоматически, если пользователь запросил только «взять на редактирование»;
- после checkin проверять фактическое значение через GET.

### Этап 6. Создание операции и технологического состава

Реализовать сценарий на тестовом шаблоне:

1. Создать объект типа 1075.
2. Передать только подтверждённые атрибуты.
3. Не передавать вычисляемые/readOnly атрибуты (`9`, системные поля и дату).
4. Выполнить `commitCreation`.
5. Получить фактические `objectID`/`versionID`.
6. Checkout родительского цехозахода.
7. Создать `POST /core/api/relations` с `relationType=1002`.
8. Обновить relation attribute `1032` для положения в составе.
9. Save/checkin родителя.
10. Проверить порядок и все номера.

Для операции 1075 подтверждены на тестовой базе:

- `1066` — номер объекта;
- `10` — наименование;
- `1184` — разряд работ;
- `1185` — степень механизации, значение 0 не принимается при создании; отсутствие атрибута предпочтительнее;
- `4577` — количество исполнителей;
- `4578` — количество одновременно обрабатываемых деталей;
- `10049` — код операции;
- `10050` — инструкция по ТБ;
- `10051` — код профессии;
- `14654` — объём партии;
- `17755` — маршрутно-операционное описание;
- `17917`, `18059` — ссылочные атрибуты, но их значения нельзя переносить между ЕТП без проверки контекста;
- `9` — вычисляемое обозначение `[Номер объекта] + '  ' + [Наименование]`.

### Этап 7. Проверка и эксплуатация

Добавить:

- health check;
- structured audit log;
- конфигурацию уровней риска;
- dry-run для write tools;
- ограничение глубины/количества состава;
- отчёт о незавершённых checkout;
- тестовую команду с sandbox/test database.

## 4. Тестовый план

### 4.1. Unit-тесты

- сериализация кириллицы UTF-8;
- сериализация `values` string/int/bool/object link;
- parsing API error;
- token rotation;
- HTTP 401 refresh single-flight;
- отрицательные working copy IDs;
- preview redaction.

### 4.2. Integration-тесты на тестовой БД

Read-only:

- root/version;
- userInfo;
- object 1349025;
- composition 1349025;
- operation 1349039;
- relation attributes 1032.

Write test:

1. создать временную операцию с уникальным номером;
2. добавить в тестовый цехозаход;
3. задать relation attribute 1032;
4. проверить последнюю позицию;
5. вернуть/удалить тестовые данные только если delete-сценарий отдельно подтверждён.

### 4.3. Регрессионные проверки

После каждой записи проверять:

- количество элементов состава;
- порядок элементов;
- уникальность номеров операций;
- значения 9/10/1066;
- relation type;
- relation attribute 1032;
- lifecycle step;
- отсутствие лишних созданных связей.

## 5. Что ещё нужно узнать

### Обязательно до production

1. Точный стек и MCP SDK.
2. Список поддерживаемых версий IPS 9.x.
3. HTTPS/сертификаты и reverse proxy.
4. Политика хранения секретов.
5. Пределы запросов и timeout сервера.
6. Что делать с незавершённым checkout при падении MCP.
7. Правила работы с архивными копиями.
8. Полный список допустимых ролей и access levels.
9. Нужно ли поддерживать несколько IPS баз/серверов.
10. Требуется ли Windows Integrated/Kerberos SSO.

### Нужно подтвердить по IPS-документации

1. Семантику `objectID`, `id`, `versionID` для всех типов объектов.
2. Официальный жизненный цикл `checkOut → edit → saveChanges → checkIn`.
3. Сценарий rollback/release рабочей копии.
4. Обязательность `edit` после checkout для разных endpoint-ов.
5. Семантику `relationType=1002` во всех предметных областях.
6. Семантику relation attribute `1032` и официальный способ изменения порядка.
7. Требования `ContextRuleDto` при создании/включении в состав.
8. Различие `includeInComposition` и прямого `POST /relations`.
9. Создание объектов типов 1075, 1212, 1048, 1118 через штатные прототипы.
10. Правила автонумерации операций и причины каскадного `edit/checkIn`.
11. Ограничения на изменение архивных копий.
12. Максимальный размер дерева состава и пакетных запросов.
13. Стратегию для файлов, документов, подписей и WebInterface Bridge.

### Нужно добавить в проект

- нормализованный локальный OpenAPI snapshot;
- generated API client или ручной typed client;
- error catalog IPS;
- таблицу object type/attribute/relation IDs, полученную из метаданных конкретной базы;
- mapping имён инструментов на operationId Swagger;
- examples запросов/ответов без токенов;
- конфигурацию sandbox/prod;
- миграции конфигурации, но не миграции данных IPS;
- runbook для refresh token, stuck checkout и аварийного отключения write tools.

## 6. Зависимости

### Runtime

- .NET 8 или утверждённая версия .NET;
- MCP SDK для выбранного транспорта;
- `System.Net.Http`;
- `System.Text.Json`;
- структурированный logger;
- Windows DPAPI/Credential Manager либо корпоративное секрет-хранилище.

### Не добавлять без необходимости

- отдельную ORM;
- прямой SQL-драйвер к БД IPS;
- генератор UI;
- собственный JWT-парсер, если достаточно стандартной библиотеки;
- новую базу данных только для токенов.

## 7. Рекомендуемая структура проекта

```text
IPS-MCP/
├─ spec/
│  ├─ ips-webapi-1.0.json
│  └─ endpoint-coverage.json
├─ src/
│  ├─ IpsMcp.Server/
│  ├─ IpsMcp.Transport/
│  ├─ IpsMcp.Tools/
│  ├─ IpsMcp.Client/
│  ├─ IpsMcp.Auth/
│  ├─ IpsMcp.Domain/
│  ├─ IpsMcp.Policy/
│  └─ IpsMcp.Audit/
├─ tests/
│  ├─ IpsMcp.UnitTests/
│  └─ IpsMcp.IntegrationTests/
├─ examples/
├─ config/
├─ IPS-MCP-SPEC.md
├─ IPS-MCP-PLAN.md
└─ README.md
```

`README.md` и исходный код пока не создаются автоматически: они требуют согласования стека, MCP SDK и политики секретов.

## 8. Definition of Done для MVP

- MCP-клиент видит минимум 10 read-only tools.
- JWT login/refresh работает без утечки секретов.
- Чтение объекта и дерева состава работает на тестовой базе.
- Все write tools требуют preview и подтверждение.
- Создание тестовой операции не меняет чужие номера и порядок.
- Ошибки IPS 400/401/403/405/415 отображаются с detail.
- После записи выполняется verify-read.
- Сохраняются audit events без токенов.
- Swagger duplicate keys обработаны явно.
- Документация содержит версию IPS и ограничения конкретной версии.
