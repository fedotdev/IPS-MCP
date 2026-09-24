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

1. `ftObjectLink`/числовые ссылки на объект разворачивать в `{id, type, designation, name}`.
2. Числовые атрибуты маппить в имена через справочник `gloss.db` (подсказка), а актуальное значение — из Web API.
3. Всегда возвращать `objectID`/`id`/`versionID` рядом с читаемыми полями.
4. Убирать внутренние служебные поля, вложенность > 2 уровней — уплощать/пагинировать.
5. Пагинация list-инструментов: `{items, total, page, page_size, has_more}`.

## Запись (следующий этап, НЕ в read-only MVP)

checkout/edit/setAttributes/saveChanges/checkIn + создания объекта/связи —
все через preview → одноразовое подтверждение человеком → commit → verify-read.

## Отличие от устаревшего плана

`GENERATED-PLAN.md` и `table` toolset из ТЗ описывали набор до фактической сверки.
Настоящий контракт построен по реальному swagger.json и ему приоритет.
