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
    full_name TEXT,
    object_name TEXT,
    object_type TEXT,
    guid TEXT,
    parent_id INTEGER,
    description TEXT,
    category TEXT,
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
    guid TEXT,
    data_type TEXT,
    options TEXT,
    default_value TEXT,
    formula TEXT,
    note TEXT,
    multiple_valued TEXT,
    computed TEXT,
    size_type TEXT,
    is_content TEXT,
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
    short_name TEXT,
    guid TEXT,
    description TEXT,
    relation_kind TEXT,
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
