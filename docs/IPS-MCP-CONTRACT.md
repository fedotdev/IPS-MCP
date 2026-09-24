# IPS-MCP: контракт read-only tools (MVP)

Зафиксирован по `swagger.json` (OpenAPI 3.0.1, IPS Server Web API 1.0).
Все записи ниже — read-only. Write-tools вынесены в отдельный этап.

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

`request_id` одноразовый. При ошибке после checkout выполняется `cancelChanges`; write-запросы не повторяются автоматически. Создание объектов, связей, удаление и массовая запись не реализованы.

Обязательное текстовое поле комментария не добавляется: проверенный Swagger IPS Web API 1.0 не содержит параметра комментария у этих endpoint'ов, поэтому нельзя гарантировать запись текста в колонку «Комментарии» журнала IPS.

## Отличие от устаревшего плана

`GENERATED-PLAN.md` и `table` toolset из ТЗ описывали набор до фактической сверки.
Настоящий контракт построен по реальному swagger.json и ему приоритет.
