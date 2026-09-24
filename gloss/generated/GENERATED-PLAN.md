# Generated glossary layer

Статус: план, runtime-файлы ещё не сгенерированы.
Версия плана: 0.1

## Назначение

`generated` — производный слой справочников для IPS-MCP. Он создаётся из Excel-файлов в `gloss` и предназначен для быстрого поиска ID, GUID, названий и метаданных без чтения Excel во время работы MCP.

Исходные Excel-файлы не изменяются и остаются административным источником:

- `Все типы объектов_*.xlsx`;
- `Все атрибуты_*.xlsx`;
- `Типы связей_*.xlsx`.

## Целевой результат

```text
generated/
├─ GENERATE-MANIFEST.json
├─ GENERATED-PLAN.md
├─ schema.sql
├─ gloss.db
├─ object-types.json
├─ attribute-types.json
├─ relation-types.json
├─ object-types.by-id.json
├─ attribute-types.by-id.json
└─ relation-types.by-id.json
```

До запуска генератора должны существовать только план, схема и сам генератор. Нельзя создавать пустые `gloss.db` или JSON-файлы, выдавая их за актуальные справочники.

## Источники на текущий момент

Проверенная выгрузка от 23.09.2026 (файлы в `gloss/`):

- `All-objects-list.xlsx`: 986 данных (ID уникальны);
- `All-attributes-list.xlsx`: 6542 данных (ID уникальны);
- `All-links-list.xlsx`: 89 данных (ID уникальны).

В каждом Excel-файле первая строка — путь/заголовок отчёта, вторая строка — заголовки таблицы. Импорт пропускает первую строку и использует вторую как header. Заголовки совпадают с фактическим набором колонок из файлов; колонки бывших выгрузок с русскими именами в `sourceFiles` manifest не используются.

## Основной runtime-формат

Основной формат — SQLite `gloss.db`.

Причины:

- быстрый поиск по ID, имени, GUID и alias;
- индексы;
- атомарная замена готового файла;
- отсутствие отдельного сервера БД;
- одинаково удобен для C#, Python и Node.js.

JSON-файлы — производный экспорт для диагностики и небольших lookup-операций. MCP не должен загружать все записи JSON в prompt.

## Таблицы SQLite

### object_types

Поля (источник — `All-objects-list.xlsx`, первая колонка `Идентификатор типа объектов`):

- `id INTEGER PRIMARY KEY`;
- `name TEXT NOT NULL` (`Наименование типа объектов`);
- `object_name TEXT` (`Наименование объекта`);
- `versioning TEXT` (`Версионность`);
- `comment TEXT` (`Комментарии`);
- `default_relation TEXT` (`Связь по умолчанию`);
- `guid TEXT` (`Глобальный идентификатор`);
- `domain TEXT` (`Предметная область`);
- `descriptor_attribute TEXT` (`Атрибут-описатель`);
- `any_attribute TEXT` (`Возможность присвоения любого атрибута`);
- `lifecycle TEXT` (`Жизненный цикл объектов`);
- `short_name TEXT` (`Краткое наименование`);
- `deleted_lifetime TEXT` (`Время жизни удалённых объектов`);
- `options TEXT` (`Опции`);
- `lifecycle_schema_id TEXT` (`Идентификатор схемы ЖЦ`);
- `source_file TEXT NOT NULL`;
- `source_modified_at TEXT`.

Индексы: `name`, `guid`.

### attribute_types

Поля (источник — `All-attributes-list.xlsx`, первая колонка `Идентификатор атрибута`):

- `id INTEGER PRIMARY KEY`;
- `name TEXT NOT NULL` (`Наименование`);
- `short_name TEXT` (`Краткое наименование`);
- `alias TEXT` (`Псевдоним`);
- `comment TEXT` (`Комментарии`);
- `data_type TEXT` (`Тип данных`);
- `default_value TEXT` (`Значение по умолчанию`, первая колонка);
- `values_list TEXT` (`Список`);
- `computed TEXT` (`Вычисление`);
- `promotion_level TEXT` (`Уровень продвижения`);
- `formula TEXT` (`Формула`);
- `language_variant TEXT` (`Языковой вариант`);
- `guid TEXT` (`Глобальный идентификатор`);
- `domain TEXT` (`Предметная область`);
- `uniqueness TEXT` (`Уникальность`);
- `operations_optimization TEXT` (`Оптимизация операций`);
- `affects_content_date TEXT` (`Влияет на дату модификации содержимого объекта`);
- `options TEXT` (`Опции`);
- `input_mask TEXT` (`Маска ввода значения`);
- `master_attribute TEXT` (`Мастер-атрибут`);
- `data_source TEXT` (`Источник данных`);
- `mult_default_values TEXT` (`F_MULTDEFAULT_VALUES`);
- `size_type TEXT` (`Размер/тип`);
- `source_file TEXT NOT NULL`;
- `source_modified_at TEXT`.

Вторая колонка `Значение по умолчанию` (поз. 23) задвоена в выгрузке; в схему она не переносится, используется первая (поз. 6).

Индексы: `name`, `alias`, `guid`.

### relation_types

Поля (источник — `All-links-list.xlsx`, первая колонка `Тип связи`):

- `id INTEGER PRIMARY KEY`;
- `name TEXT NOT NULL` (`Наименование`);
- `relation_name TEXT` (`Название связи`);
- `inverse_relation_name TEXT` (`Обратное название связи`);
- `comment TEXT` (`Комментарий`);
- `extract_files TEXT` (`Извлечение файлов объектов`);
- `relation_kind TEXT` (`Вид связи`);
- `guid TEXT` (`Глобальный идентификатор`);
- `domain TEXT` (`Предметная область`);
- `any_attribute TEXT` (`Возможность присвоения любого атрибута`);
- `short_name TEXT` (`Краткое наименование`);
- `options TEXT` (`Опции`);
- `source_file TEXT NOT NULL`;
- `source_modified_at TEXT`.

Индексы: `name`, `guid`.

### object_type_attributes

Таблица резервируется для будущей выгрузки назначения атрибутов конкретным типам объектов:

```sql
CREATE TABLE object_type_attributes (
    object_type_id INTEGER NOT NULL,
    attribute_id INTEGER NOT NULL,
    required INTEGER,
    read_only INTEGER,
    visible INTEGER,
    position INTEGER,
    PRIMARY KEY (object_type_id, attribute_id)
);
```

Пока она не заполняется из имеющихся трёх Excel-файлов.

### relation_applicability

Таблица резервируется для будущей выгрузки применимости связей:

```sql
CREATE TABLE relation_applicability (
    parent_object_type_id INTEGER NOT NULL,
    child_object_type_id INTEGER NOT NULL,
    relation_type_id INTEGER NOT NULL,
    enabled INTEGER,
    PRIMARY KEY (
        parent_object_type_id,
        child_object_type_id,
        relation_type_id
    )
);
```

Без источника применимости нельзя считать, что отсутствие строки означает запрет связи.

## JSON-экспорт

Основной JSON-список — массив объектов:

```json
[
  {
    "id": 1075,
    "name": "Операция",
    "guid": "...",
    "sourceFile": "..."
  }
]
```

Индексированный JSON — объект с ключами-строками ID:

```json
{
  "1075": {
    "id": 1075,
    "name": "Операция",
    "guid": "..."
  }
}
```

ID всегда сохранять и в SQLite как INTEGER, и в JSON как JSON number. В URL/API-конфигурации допускается преобразование в строку, но не в справочнике.

## Manifest

`GENERATE-MANIFEST.json` должен описывать конкретную сборку:

```json
{
  "schemaVersion": 1,
  "generatedAt": "2026-09-23T00:00:00Z",
  "sourceFiles": [],
  "counts": {
    "objectTypes": 0,
    "attributeTypes": 0,
    "relationTypes": 0
  },
  "databaseId": null,
  "ipsVersion": null,
  "metadataGeneration": null,
  "validation": {
    "duplicateIds": 0,
    "invalidGuids": 0,
    "emptyNames": 0
  }
}
```

Поля `databaseId`, `ipsVersion`, `metadataGeneration` должны заполняться, если выгрузка сделана непосредственно из IPS. Для Excel-файлов, где эти данные не указаны, значения должны оставаться `null`, а не выдумываться.

## Валидация генератора

Генератор обязан завершаться ошибкой при:

- дублирующемся ID внутри одного справочника;
- пустом обязательном имени;
- некорректном GUID, если GUID заполнен;
- невозможности преобразовать ID в целое число;
- отсутствии исходного файла;
- несоответствии количества колонок ожидаемой схеме.

Предупреждения, не блокирующие сборку:

- пустой GUID;
- неизвестное значение перечисления;
- незнакомая колонка Excel;
- отсутствие будущих таблиц применимости/назначения атрибутов.

## Атомарная сборка

Генератор: `gloss/generate_gloss.py` (Python 3, `openpyxl`, SQLite из стандартной библиотеки).

Запуск:

```bash
python gloss/generate_gloss.py
```

Сборка идёт во временный каталог:

```text
generated/.build/<timestamp>/
```

Порядок:

1. прочитать Excel;
2. нормализовать значения;
3. проверить ID/GUID/имена;
4. создать временный SQLite;
5. создать JSON и индексированные JSON;
6. сформировать manifest;
7. выполнить smoke-query;
8. атомарно заменить runtime-файлы в `generated/`.

При ошибке старые рабочие файлы не удалять.

## Smoke-проверка

После генерации обязательно выполнить:

```sql
SELECT COUNT(*) FROM object_types;
SELECT COUNT(*) FROM attribute_types;
SELECT COUNT(*) FROM relation_types;
SELECT id, name FROM relation_types WHERE id = 1002;
SELECT id, name FROM attribute_types WHERE id IN (9, 10, 1066, 1032);
SELECT id, name FROM object_types WHERE id IN (1075, 1110, 1212);
```

Ожидаемо для текущей базы:

- relation `1002` — «Технологический состав»;
- attribute `1066` — «Номер объекта»;
- attribute `1032` — атрибут сортировки связи, если он присутствует в выгрузке атрибутов;
- object type `1075` — «Операция».

Если имя в Excel не совпадает с фактическим именем метаданных IPS, генератор не должен исправлять его автоматически. Нужно зафиксировать расхождение и обновить источник.

## Ограничения

- Эти справочники относятся к конкретной базе IPS и не являются универсальными для всех баз.
- ID типов/атрибутов/связей нельзя переносить между базами без повторной выгрузки.
- Excel не содержит полный граф применимости и назначения атрибутов типам объектов.
- Справочник не заменяет проверку прав и бизнес-правил IPS.
- Перед записью MCP всё равно должен запрашивать актуальные данные через Web API.
