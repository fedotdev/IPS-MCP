# IPS-MCP: контракт read-only tools (MVP)

Зафиксирован по `swagger.json` (OpenAPI 3.0.1, IPS Server Web API 1.0).
По умолчанию инструменты read-only; write-tools регистрируются только при `IPS_ENABLE_WRITE=1`.

Endpoint'ы подтверждены по Swagger; схемы DTO сверены с `components.schemas`.

## Идентификаторы IPS

Один объект различает три ID, все обязаны сохраняться в ответе без переименования:

- `objectID` — идентификатор версии объекта (то, по чему читают/пишут);
- `id` — идентификатор версии (см. ObjectDto);
- `versionID` — поле версии в ObjectDto;
- `guid` / `objectGUID` — глобальные идентификаторы;
- рабочая копия после checkout — отрицательный ID, не приводить к положительному.

## Table: tool → endpoint

| Tool | Метод | Endpoint | operationId |
|---|---|---|---|
| ips_auth_status | GET | /core/api/currentUsers/userInfo | CurrentUsers_GetCurrentUserInfo |
| ips_get_object | GET | /core/api/objects/{objectId} | Objects_GetObject |
| ips_get_object_info | GET | /core/api/objects/{objectId}/objectInfo | Objects_GetObjectInfo |
| ips_get_object_attributes | GET | /core/api/objects/{objectId}/attributes | ObjectAttributes_GetAttributes |
| ips_get_object_attribute_values | GET | /core/api/objects/{objectId}/attributesValues | ObjectAttributes_GetAttributesValues |
| ips_get_composition | POST | /core/api/objects/{projectVersionId}/composition | Objects_GetObjectComposition |
| ips_get_composition_filtered | POST | /core/api/objects/{projectVersionId}/compositionWithParams | Objects_GetObjectCompositionWithParams |
| ips_get_relation | GET | /core/api/relations/{relationId} | Relations_GetRelation |
| ips_get_relation_attributes | GET | /core/api/relations/{relationId}/attributesValues | RelationAttributes_GetAttributesValues |
| ips_get_object_type | GET | /core/api/metadata/objectTypes/{id} | Metadata_GetObjectTypeById |
| ips_get_attribute_type | GET | /core/api/metadata/attributeTypes/{id} | Metadata_GetAttributeTypeById |
| ips_get_relation_type | GET | /core/api/metadata/relationTypes/{id} | Metadata_GetRelationTypeById |
| ips_get_lifecycle | GET | /core/api/metadata/lifeCycleSchemes | Metadata_GetLifeCycleSchemeList |
| ips_search_objects | POST | /core/api/objects/select | Objects_GetSelectsObjects |

`ips_get_composition_tree` — высокоуровневый инструмент (рекурсия по `ips_get_composition`),
не отдельный endpoint; лимиты `maxDepth`/`maxNodes`/защита от циклов.

## DTO (сверены с Swagger)

`ObjectDto`: objectID, id, versionID, guid, objectGUID, caption, objectType,
isBaseVersion, isCreationMode, readOnly, lcStep, checkoutBy, modifyDate, versionsCount.

`AttributeValuesDto`: attributeId, attributeName, attributeGuid, attributeAlias,
attributeType, values, extractedValues, descriptions, multipleValued, computeMode,
readOnly, groupName, isNew, isForceDelete, attributeTypeInfo.

`RelationDto`: relationID, projID, partObjectID, partID, relationType, creatorID,
createDate, guid, readOnly.

`CreateObjectDto`: objectType, attributes[AttributeDto], contextRule, currentProjectDto.
Swagger's `AttributeDto` exposes many response fields (including read-only fields); create accepts the deliberately limited `{attributeID, values}` input subset, consistent with `AttributeValuesDto`'s attribute identifier/value structure. Other AttributeDto fields are not currently supported by this tool.
`CreateRelationDto`: relationType, projVersionId, partVersionId, attributeValues[AttributeValuesDto].
`UpdateRelationAttributeDto`: attributeId, value.
`CurrentUserInfoDto`: sessionId, userVersionId, userName, roleVersionId, accessLevel, isAdmin, loginName.

## Нормализация (правила mapper)

1. `ftObjectLink`/числовые ссылки на объект разворачивать в `{id, type, designation, name}`
   (реализовано: `resolvedValues` = `{id, objectType, objectTypeName, caption}`, лимит 10 ссылок на вызов).
2. Числовые атрибуты маппить в имена через справочник `gloss.db` (подсказка), а актуальное значение — из Web API.
3. Всегда возвращать `objectID`/`id`/`versionID` рядом с читаемыми полями.
4. Убирать внутренние служебные поля, вложенность > 2 уровней — уплощать/пагинировать.
5. Пагинация list-инструментов: `{items, total, page, page_size, has_more}` (страницы 1-based, page_size 1..1000, default 50).

## Запись (ограниченный этап)

При `IPS_ENABLE_WRITE=1` доступны только:

- `ips_prepare_update_attribute(object_id, attribute_id, value)` — читает текущее значение и возвращает preview + `request_id`, записи не выполняет;
- `ips_commit_update_attribute(request_id)` — повторно проверяет старое значение, выполняет `checkout → edit → attributes → saveChanges → checkIn`, затем verify-read.
- `ips_prepare_create_object` / `ips_commit_create_object` — POST `/objects` (`Objects_Create`), затем POST `/objects/{objectId}/commitCreation` (`Objects_CommitCreation`); commit response содержит `result.objectId` (integer).
- `ips_prepare_create_by_prototype` / `ips_commit_create_by_prototype` — preview читает identity прототипа; commit выполняет `POST /core/api/objects/CreateByPrototype` (`Objects_CreateByPrototype`), сохраняет отрицательный `objectDto.objectID` и `relatedObjectIds`, затем передаёт их без преобразования в `POST /core/api/objects/{workingCopyId}/commitCreation`. Create и commit — два отдельных mutation; unknown/partial_unknown сверять read-only, не повторять.
- `ips_prepare_create_relation` / `ips_commit_create_relation` — POST `/relations` (`Relations_CreateRelation`) с `attributeValues[].attributeId` без автоматического checkout/checkin родителя.
- `ips_prepare_checkout_object` / `ips_commit_checkout_object` — двухфазный отдельный checkout; identity (objectID, непустой objectGUID, objectType, caption и checkoutBy) проверяется до и после единственного mutation POST, а возвращённый workingCopyId сохраняется буквально.
- `ips_prepare_finish_checkout` / `ips_commit_finish_checkout` — принимают только workingCopyId успешного checkout этого процесса; checkin делает saveChanges → checkIn, cancelChanges получает ровно `[workingCopyId]`. Перед mutation и после него выполняется identity-проверка; частичный/неизвестный исход не повторять.
- Для изменения состава создавайте связь только с активным workingCopyId родителя и затем завершайте его через finish tools; implicit checkout нет.

`request_id` одноразовый. Mutation-запросы не повторяются автоматически, включая 401. Checkout/finish/relation approvals и active checkout store живут только в памяти процесса и теряются после рестарта; arbitrary ID после рестарта нельзя использовать. `pre_send_rejected` means creation did not reach the create endpoint; `unknown` after a create request means reconcile in IPS by type/attributes/time and do not blindly retry; `partial_unknown` means create succeeded but commitCreation is uncertain, so inspect that object before taking further action.

Delete разрешён только для точной пары `objectID=1406301` и `objectGUID=25fe60ea-a218-4278-90f0-542128d7ef03`: preview читает объект и проверяет оба поля, commit повторяет проверку непосредственно перед единственным `POST /core/api/objects/{objectId}/delete` с `deleteMode=0` (Swagger: зарезервировано) и `isNeedToLogModificationHistory=true`. Relations/children отдельно не удаляются. Операция необратима, cascade semantics не проверена. Ответ `unknown` означает сверить объект через read-only `ips_get_object`, не повторять commit. Инструменты существуют только при `IPS_ENABLE_WRITE=1`. Кодовая allowlist не подтверждает, что текущая конфигурация подключена к безопасной тестовой базе; config.json не читался.

Обязательное текстовое поле комментария не добавляется: проверенный Swagger IPS Web API 1.0 не содержит параметра комментария у этих endpoint'ов, поэтому нельзя гарантировать запись текста в колонку «Комментарии» журнала IPS.
