-- Further database and table designs. Small on purpose: these test correctness and plans, not scale.
SET NAMES utf8mb4;
USE bench;

-- Key types
CREATE TABLE IF NOT EXISTS d_negative_pk (id BIGINT NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_float_pk (id DOUBLE NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_decimal_pk (id DECIMAL(20,6) NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_datetime_pk (id DATETIME(6) NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_timestamp_pk (id TIMESTAMP(6) NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
-- ENUM sorts by declaration order in the index, but compares as text against a string parameter.
CREATE TABLE IF NOT EXISTS d_enum_pk (
  status ENUM('z','a','m') NOT NULL, id BIGINT NOT NULL, v INT NOT NULL, PRIMARY KEY (status, id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_desc_pk (id BIGINT NOT NULL, v INT NOT NULL, PRIMARY KEY (id DESC)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_snowflake (id BIGINT UNSIGNED NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_unsigned_max (id BIGINT UNSIGNED NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_str_bin (code VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
-- A prefix unique key does not identify a row by its full value order.
CREATE TABLE IF NOT EXISTS d_prefix_unique (code VARCHAR(40) NOT NULL, v INT NOT NULL, UNIQUE KEY uk_prefix (code(30))) ENGINE=InnoDB;
-- Generated invisible primary key: SELECT * does not return it.
SET SESSION sql_generate_invisible_primary_key=ON;
CREATE TABLE IF NOT EXISTS d_gipk (a INT NOT NULL, v INT NOT NULL) ENGINE=InnoDB;
SET SESSION sql_generate_invisible_primary_key=OFF;

-- Row and column design
CREATE TABLE IF NOT EXISTS d_compressed (id BIGINT NOT NULL PRIMARY KEY, body VARCHAR(255) NOT NULL) ENGINE=InnoDB ROW_FORMAT=COMPRESSED KEY_BLOCK_SIZE=8;
CREATE TABLE IF NOT EXISTS d_json_virtual (
  id BIGINT NOT NULL PRIMARY KEY, doc JSON NOT NULL,
  kind INT GENERATED ALWAYS AS (doc->>'$.k') VIRTUAL, KEY k_kind (kind)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_fulltext (id BIGINT NOT NULL PRIMARY KEY, body VARCHAR(255) NOT NULL, FULLTEXT KEY ft (body)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_spatial (id BIGINT NOT NULL PRIMARY KEY, g POINT NOT NULL SRID 4326, SPATIAL KEY sp (g)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_soft_delete (
  id BIGINT NOT NULL PRIMARY KEY, deleted_at DATETIME NULL, v INT NOT NULL, KEY k_deleted (deleted_at)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_history (
  entity_id BIGINT NOT NULL, valid_from DATETIME NOT NULL, v INT NOT NULL, PRIMARY KEY (entity_id, valid_from)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_eav (
  entity_id BIGINT NOT NULL, attr VARCHAR(20) NOT NULL, val VARCHAR(50) NULL, PRIMARY KEY (entity_id, attr)
) ENGINE=InnoDB;
-- Polymorphic association: a logical relation that information_schema cannot show.
CREATE TABLE IF NOT EXISTS d_polymorphic (
  id BIGINT NOT NULL PRIMARY KEY, owner_type VARCHAR(10) NOT NULL, owner_id BIGINT NOT NULL, KEY k_owner (owner_type, owner_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_triggered (id BIGINT NOT NULL PRIMARY KEY, v INT NOT NULL) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS d_trigger_log (id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY, ref_id BIGINT NOT NULL) ENGINE=InnoDB;
DROP TRIGGER IF EXISTS d_triggered_au;
CREATE TRIGGER d_triggered_au AFTER UPDATE ON d_triggered FOR EACH ROW INSERT INTO d_trigger_log (ref_id) VALUES (NEW.id);
CREATE OR REPLACE VIEW v_orders_with_customer AS
  SELECT o.id, o.total, c.name FROM orders o JOIN customers c ON c.id = o.customer_id;

-- Foreign key designs
CREATE TABLE IF NOT EXISTS fk_diamond_a (id BIGINT NOT NULL PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_diamond_b (id BIGINT NOT NULL PRIMARY KEY, a_id BIGINT NOT NULL, FOREIGN KEY (a_id) REFERENCES fk_diamond_a (id)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_diamond_c (id BIGINT NOT NULL PRIMARY KEY, a_id BIGINT NOT NULL, FOREIGN KEY (a_id) REFERENCES fk_diamond_a (id)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_diamond_d (
  id BIGINT NOT NULL PRIMARY KEY, b_id BIGINT NOT NULL, c_id BIGINT NOT NULL,
  FOREIGN KEY (b_id) REFERENCES fk_diamond_b (id), FOREIGN KEY (c_id) REFERENCES fk_diamond_c (id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_comp_parent (a INT NOT NULL, b INT NOT NULL, PRIMARY KEY (a, b)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_comp_child (
  id BIGINT NOT NULL PRIMARY KEY, a INT NOT NULL, b INT NOT NULL, FOREIGN KEY (a, b) REFERENCES fk_comp_parent (a, b)
) ENGINE=InnoDB;
-- FK that references a UNIQUE column rather than the parent's primary key
CREATE TABLE IF NOT EXISTS fk_uq_parent (id BIGINT NOT NULL PRIMARY KEY, code BIGINT NOT NULL, UNIQUE KEY uk (code)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_uq_child (id BIGINT NOT NULL PRIMARY KEY, parent_code BIGINT NOT NULL, FOREIGN KEY (parent_code) REFERENCES fk_uq_parent (code)) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_null_parent (id BIGINT NOT NULL PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_null_child (id BIGINT NOT NULL PRIMARY KEY, p BIGINT NULL, FOREIGN KEY (p) REFERENCES fk_null_parent (id) ON DELETE SET NULL) ENGINE=InnoDB;
-- FK across schemas, and schema-per-tenant databases
CREATE DATABASE IF NOT EXISTS bench_other;
CREATE TABLE IF NOT EXISTS bench_other.parent (id BIGINT NOT NULL PRIMARY KEY) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS fk_cross_schema (id BIGINT NOT NULL PRIMARY KEY, p BIGINT NOT NULL, FOREIGN KEY (p) REFERENCES bench_other.parent (id)) ENGINE=InnoDB;

-- No index at all: a whole-table count cannot be split into key ranges.
CREATE TABLE IF NOT EXISTS d_noindex (a INT NOT NULL, b INT NOT NULL) ENGINE=InnoDB;
-- Small summary table used by the count-into-a-table lock tests.
CREATE TABLE IF NOT EXISTS count_summary (t VARCHAR(64) PRIMARY KEY, c BIGINT NOT NULL) ENGINE=InnoDB;
INSERT IGNORE INTO count_summary VALUES ('t_hot', 0);
