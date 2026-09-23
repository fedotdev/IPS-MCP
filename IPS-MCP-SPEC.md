# IPS-MCP: спецификация

Версия документа: 0.1
Целевая версия IPS: 9.0.4.11310 / IPS Web API 1.0
Источник API: `C:\Users\fedor\AppData\Local\Temp\opencode\swagger.json`
Проверенный сервер: `http://192.168.80.70:8080/`

## 1. Цель

IPS-MCP — MCP-сервер, который предоставляет модели контролируемый доступ к IPS Web API через типизированные инструменты. Сервер должен поддерживать:

- чтение объектов, атрибутов, метаданных и состава;
- чтение связей и атрибутов связей;
- поиск и навигацию по дереву состава;
- явно подтверждаемые операции изменения данных;
- безопасную JWT-аутентификацию и обновление токенов;
- журналирование запросов и изменений.

MCP-сервер не должен обращаться к БД IPS напрямую. Единственный рабочий канал — IPS Web API.

## 2. Подтверждённые возможности Web API

Из Swagger:

- OpenAPI `3.0.1`;
- API title: `IPS Server Web API`;
- API version: `1.0`;
- security scheme: `Bearer`, HTTP Bearer JWT;
- аутентификация: `POST /core/api/Auth/authenticate`;
- обновление токенов: `POST /core/api/Auth/refreshTokens`;
- получение ролей/уровней доступа без пароля: `GET /core/api/Auth/logins/{loginName}/options`;
- объекты: `/core/api/objects/*`;
- связи: `/core/api/relations/*`;
- метаданные: `/core/api/metadata/*`;
- состав объекта: `POST /core/api/objects/{projectVersionId}/composition`;
- включение объектов в состав: `POST /core/api/objects/{projectVersionId}/includeInComposition`;
- создание связи: `POST /core/api/relations`;
- изменение атрибутов объекта: `POST /core/api/objects/{objectId}/attributes`;
- изменение атрибутов связей: `POST /core/api/relations/attributes`;
- checkout/edit/save/checkin: `/checkOut`, `/edit`, `/saveChanges`, `/checkIn`;
- завершение создания объекта: `POST /core/api/objects/{objectId}/commitCreation`.

Практическая проверка на IPS 9.0.4.11310:

- `GET /` вернул `IPS Webinterface 9.0.4.11310`;
- `/swagger/index.html` доступен;
- Swagger-конфигурация ссылается на `/swagger/1.0/swagger.json`;
- защищённые методы без Bearer возвращают `401`;
- `GET /core/api/currentUsers/userInfo` с JWT работает;
- чтение метаданных и состава работает;
- запись объекта, атрибута и связи выполнена успешно на тестовых данных.

## 3. Аутентификация

### 3.1. Получение параметров входа

```http
GET /core/api/Auth/logins/{loginName}/options
```

Метод не проверяет пароль. Он возвращает доступные роли и уровни доступа для заполнения формы входа.

### 3.2. Вход

```http
POST /core/api/Auth/authenticate
Content-Type: application/json

{
  "loginName": "...",
  "password": "...",
  "passwordType": "plainText",
  "roleID": 0,
  "accessLevelID": 0
}
```

Ответ содержит:

```json
{
  "accessToken": "JWT",
  "refreshToken": "...",
  "expireTime": "..."
}
```

### 3.3. Обновление

```http
POST /core/api/Auth/refreshTokens
Content-Type: application/json

{
  "accessToken": "...",
  "refreshToken": "..."
}
```

Refresh token ротируется. После каждого обновления необходимо сохранять новую пару токенов. Старый refresh token нельзя повторно использовать.

### 3.4. Требования безопасности

- пароль не хранить в конфигурации и логах;
- JWT и refresh token не выводить в MCP-ответы без необходимости;
- токены хранить только в памяти или защищённом секрет-хранилище;
- передавать токены только по HTTPS в рабочей среде;
- при `401` один раз выполнить refresh, затем повторить запрос; при повторном `401` завершить операцию;
- привязать MCP-сессию к пользователю IPS и роли;
- в журнале хранить идентификатор сессии, не токены.

## 4. Модель идентификаторов IPS

IPS различает идентификатор объекта и идентификатор версии. Кроме того, после checkout/создания может использоваться отрицательный ID рабочей копии.

Пример из теста:

- объект операции: `objectID = 1349039`;
- версия: `id = 1349040`;
- рабочая копия после checkout: `-1349039`.

Правила реализации:

1. Не называть параметр просто `id`, если API-контекст требует `objectId`, `versionId` или `workingCopyId`.
2. Сохранять в моделях ответа все доступные поля `objectID`, `id`, `versionID`, `guid`, `objectGUID`.
3. Для операций записи в архивных объектах использовать рабочую копию, возвращённую checkout.
4. Не превращать отрицательный ID в положительный автоматически.
5. В MCP-ответе явно показывать, на каком ID выполнена операция.

## 5. MCP-инструменты MVP

### 5.1. Авторизация и диагностика

#### `ips_auth_status`

Назначение: показать состояние API-соединения без выдачи токенов.

Вход: нет.

Выход:

- URL сервера;
- версия WebInterface;
- пользователь/роль из `currentUsers/userInfo`;
- срок действия токена;
- признак доступности API.

#### `ips_authenticate`

Назначение: интерактивно получить JWT.

Вход:

- `loginName`;
- `password` — секретное поле;
- `roleId`;
- `accessLevelId`;
- `passwordType`, default `plainText`.

По умолчанию инструмент не должен вызываться моделью автоматически. Нужен явный режим авторизации.

### 5.2. Чтение объектов

#### `ips_get_object`

API: `GET /core/api/objects/{objectId}`.

Вход:

- `objectId: int64`;
- `includeDeleted?: bool` — только если подтверждено конкретным endpoint.

Выход: `ObjectDto` и `isEntityPresent`.

#### `ips_get_object_info`

API: `GET /core/api/objects/{objectId}/objectInfo`.

Возвращает краткую информацию: caption, objectTypeID, objectID, versionGuid, id.

#### `ips_get_object_attributes`

API: `GET /core/api/objects/{objectId}/attributes`.

Параметр:

- `isNeedToExtendByAttributeType?: bool`.

#### `ips_get_attribute_values`

API: `GET /core/api/objects/{objectId}/attributesValues`.

Для ответа обязательно сохранять `attributeId`, `attributeType`, `values`, `extractedValues`, `readOnly`, `attributeTypeInfo`.

#### `ips_get_object_attribute`

API: `GET /core/api/objects/{objectId}/attributes/{attributeId}`.

### 5.3. Чтение состава

#### `ips_get_composition`

API: `POST /core/api/objects/{projectVersionId}/composition`.

Тело MVP: `{}`.

Возвращает пары `object` + `relation`.

#### `ips_get_filtered_composition`

API: `POST /core/api/objects/{projectVersionId}/compositionWithParams`.

Вход:

- `relationTypeId?`;
- `partTypeIds?`;
- `contextRule?`.

MVP может отложить до подтверждения всех DTO `ContextRuleDto`.

#### `ips_get_composition_tree`

Высокоуровневый инструмент, который рекурсивно вызывает `composition`, но обязан иметь:

- `maxDepth`, default 3;
- `maxNodes`, default 500;
- защиту от циклов по `objectID`/`guid`;
- режим `readOnly`.

### 5.4. Метаданные

#### `ips_get_object_type`

API: `/core/api/metadata/objectTypes/{id}` и связанные documented endpoints.

#### `ips_get_attribute_type`

API: `/core/api/metadata/attributeTypes/{id}` и связанные endpoints.

#### `ips_get_relation_type`

API: `/core/api/metadata/relationTypes/{id}`.

Обязательно использовать этот инструмент перед записью связи, если тип связи передаётся числом.

#### `ips_get_lifecycle`

API: `/core/api/metadata/lifeCycleSchemes`, `/lifeCycleSteps`, `/lifeCycleLevels`.

### 5.5. Чтение связей

#### `ips_get_relation`

API: `GET /core/api/relations/{relationId}`.

#### `ips_get_relation_attributes`

API: `GET /core/api/relations/{relationId}/attributesValues`.

Пример: у технологического состава `relationType = 1002` атрибут сортировки `1032` является целым числом. В тесте значение `0` помещало новую операцию первой; значение выше максимального переместило её в конец.

### 5.6. Запись — только с подтверждением

Все инструменты ниже должны требовать явного подтверждения пользователя непосредственно перед записью.

#### `ips_checkout_object`

API: `POST /core/api/objects/{objectId}/checkOut`.

Возвращает рабочую копию, часто отрицательный ID.

#### `ips_edit_object`

API: `POST /core/api/objects/{workingCopyId}/edit`.

#### `ips_set_object_attributes`

API: `POST /core/api/objects/{workingCopyId}/attributes`.

Пример DTO:

```json
[
  { "attributeID": 1066, "values": ["005 ТЕСТ"] }
]
```

#### `ips_save_changes`

API: `POST /core/api/objects/{workingCopyId}/saveChanges`.

#### `ips_checkin_object`

API: `POST /core/api/objects/{workingCopyId}/checkIn`.

#### `ips_create_object`

API: `POST /core/api/objects`.

Для операции типа 1075 использовался `CreateObjectDto` с атрибутами. Созданный объект первоначально возвращается в режиме создания, затем требуется `commitCreation`.

#### `ips_commit_creation`

API: `POST /core/api/objects/{workingCopyId}/commitCreation`.

#### `ips_create_relation`

API: `POST /core/api/relations`.

```json
{
  "relationType": 1002,
  "projVersionId": -1349037,
  "partVersionId": 1397044
}
```

Для редактируемого родителя `projVersionId` должен быть ID его рабочей копии. В тесте `1002` — «Технологический состав».

#### `ips_update_relation_attributes`

API: `POST /core/api/relations/attributes`.

```json
{
  "relationsAttributes": [
    {
      "relationId": 1397047,
      "attributes": [
        { "attributeId": 1032, "value": 3127750000 }
      ]
    }
  ]
}
```

Атрибуты связи следует обновлять только после checkout/edit родительского объекта.

## 6. Сценарий добавления операции в цехозаход

Подтверждённый сценарий на IPS 9.0.4:

1. Создать объект операции типа `1075` с атрибутами.
2. Выполнить `commitCreation`.
3. Получить его `objectID` и `id` версии.
4. Выполнить checkout цехозахода `1349037`.
5. Создать связь `relationType=1002` на рабочую копию цехозахода.
6. Установить атрибут связи `1032` больше максимального значения существующих связей.
7. Сохранить родителя.
8. Выполнить checkin родителя.
9. Проверить состав и номера операций.

Важно: вызов `includeInComposition` с телом `[versionId]` вернул успешный HTTP-ответ, но не добавил операцию в фактический состав. Для детерминированного результата в тесте использовался `POST /core/api/relations`.

## 7. Безопасность записи

Перед каждой записью MCP должен сформировать preview:

- сервер;
- пользователь, роль, уровень доступа;
- родительский объект;
- дочерний объект;
- тип связи и его имя;
- список изменяемых атрибутов: ID, имя, старое значение, новое значение;
- операции checkout/save/checkin;
- ожидаемый побочный эффект.

Подтверждение должно быть отдельным вызовом или обязательным `confirmed=true`, выданным после preview. Для опасных операций `confirmed=true` без предварительного preview запрещён.

Запрещено по умолчанию:

- SQL;
- удаление объектов и связей;
- массовая запись;
- изменение жизненного цикла;
- изменение прав доступа;
- работа с файлами и подписание документов;
- обход прав пользователя.

## 8. Обработка ошибок

Маппинг минимум:

- `400`: ошибка DTO, права, состояние объекта или бизнес-правило; вернуть IPS `detail` и `Код сообщения`;
- `401`: обновить JWT один раз и повторить;
- `403`: недостаточно прав, не повторять автоматически;
- `404`: объект/endpoint не найден;
- `405`: неверный HTTP-метод, сверить Swagger;
- `415`: отсутствует `Content-Type` или тело запроса;
- `500`: не повторять запись автоматически без idempotency strategy.

Для `400` не скрывать русское сообщение IPS. В ответе MCP указывать endpoint, HTTP-метод, objectId/relationId и безопасную часть тела ошибки.

## 9. Наблюдаемость

Логировать:

- requestId;
- время и длительность;
- HTTP-метод и endpoint;
- статус;
- IPS user/session ID;
- objectId/relationId;
- operation type: read/write;
- результат и код IPS.

Не логировать:

- пароль;
- accessToken;
- refreshToken;
- полные тела файлов;
- чувствительные атрибуты без настройки redaction.

## 10. Ограничения текущей версии спецификации

- Swagger содержит конфликтующие пути, отличающиеся только регистром, например `/core/api/relations/consistFrom` и `/core/api/Relations/ConsistFrom`. Стандартный `ConvertFrom-Json` PowerShell 5.1 не смог разобрать файл из-за duplicate keys. Клиенту нужен парсер, допускающий такие ключи, либо нормализация спеки с сохранением исходных путей.
- Не все операции Web API проверены фактическим вызовом.
- Описания API в Swagger на сервере могут иметь неверную кодировку при отображении в консоли, хотя JSON содержит корректную UTF-8 кириллицу.
- Не подтверждены все правила DTO `ContextRuleDto`, `CurrentProjectDto`, фильтры состава и создание объектов всех типов.
- Не подтверждён универсальный механизм отмены незавершённого checkout для всех типов объектов.
- Нельзя считать числовые атрибуты и relation attributes универсальными: их смысл зависит от типа объекта/связи и версии метаданных.
