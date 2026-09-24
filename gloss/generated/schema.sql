-- IPS-MCP generated glossary schema
-- Данные создаются генератором из Excel, этот файл является схемой.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS object_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    object_name TEXT,
    versioning TEXT,
    comment TEXT,
    default_relation TEXT,
    guid TEXT,
    domain TEXT,
    descriptor_attribute TEXT,
    any_attribute TEXT,
    lifecycle TEXT,
    short_name TEXT,
    deleted_lifetime TEXT,
    options TEXT,
    lifecycle_schema_id TEXT,
    source_file TEXT NOT NULL,
    source_modified_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_object_types_name
    ON object_types(name);

CREATE INDEX IF NOT EXISTS ix_object_types_guid
    ON object_types(guid);

CREATE TABLE IF NOT EXISTS attribute_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    short_name TEXT,
    alias TEXT,
    comment TEXT,
    data_type TEXT,
    default_value TEXT,
    values_list TEXT,
    computed TEXT,
    promotion_level TEXT,
    formula TEXT,
    language_variant TEXT,
    guid TEXT,
    domain TEXT,
    uniqueness TEXT,
    operations_optimization TEXT,
    affects_content_date TEXT,
    options TEXT,
    input_mask TEXT,
    master_attribute TEXT,
    data_source TEXT,
    mult_default_values TEXT,
    size_type TEXT,
    source_file TEXT NOT NULL,
    source_modified_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_attribute_types_name
    ON attribute_types(name);

CREATE INDEX IF NOT EXISTS ix_attribute_types_alias
    ON attribute_types(alias);

CREATE INDEX IF NOT EXISTS ix_attribute_types_guid
    ON attribute_types(guid);

CREATE TABLE IF NOT EXISTS relation_types (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    relation_name TEXT,
    inverse_relation_name TEXT,
    comment TEXT,
    extract_files TEXT,
    relation_kind TEXT,
    guid TEXT,
    domain TEXT,
    any_attribute TEXT,
    short_name TEXT,
    options TEXT,
    source_file TEXT NOT NULL,
    source_modified_at TEXT
);

CREATE INDEX IF NOT EXISTS ix_relation_types_name
    ON relation_types(name);

CREATE INDEX IF NOT EXISTS ix_relation_types_guid
    ON relation_types(guid);

CREATE TABLE IF NOT EXISTS object_type_attributes (
    object_type_id INTEGER NOT NULL,
    attribute_id INTEGER NOT NULL,
    required INTEGER,
    read_only INTEGER,
    visible INTEGER,
    position INTEGER,
    PRIMARY KEY (object_type_id, attribute_id),
    FOREIGN KEY (object_type_id) REFERENCES object_types(id),
    FOREIGN KEY (attribute_id) REFERENCES attribute_types(id)
);

CREATE TABLE IF NOT EXISTS relation_applicability (
    parent_object_type_id INTEGER NOT NULL,
    child_object_type_id INTEGER NOT NULL,
    relation_type_id INTEGER NOT NULL,
    enabled INTEGER,
    PRIMARY KEY (
        parent_object_type_id,
        child_object_type_id,
        relation_type_id
    ),
    FOREIGN KEY (parent_object_type_id) REFERENCES object_types(id),
    FOREIGN KEY (child_object_type_id) REFERENCES object_types(id),
    FOREIGN KEY (relation_type_id) REFERENCES relation_types(id)
);

CREATE TABLE IF NOT EXISTS validation_errors (
    entity TEXT NOT NULL,
    entity_id INTEGER,
    error_code TEXT NOT NULL,
    message TEXT NOT NULL
);
