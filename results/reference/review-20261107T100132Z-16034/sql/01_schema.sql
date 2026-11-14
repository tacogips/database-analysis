-- Table shapes under test. Secondary indexes on the 100M table are built after load.
SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS bench;
USE bench;

CREATE TABLE IF NOT EXISTS seq1m (n INT UNSIGNED NOT NULL PRIMARY KEY) ENGINE=InnoDB;

-- S1 dense auto-increment BIGINT PK (100M, the main scale test)
CREATE TABLE IF NOT EXISTS t_dense (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  user_id INT UNSIGNED NOT NULL,
  status TINYINT NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  created_at DATETIME NOT NULL,
  note VARCHAR(32) NOT NULL
) ENGINE=InnoDB;

-- S2 BIGINT PK with huge gaps (mass delete / sharded id allocator)
CREATE TABLE IF NOT EXISTS t_sparse (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  v INT NOT NULL
) ENGINE=InnoDB;

-- S3 random BINARY(16) UUID PK
CREATE TABLE IF NOT EXISTS t_uuid (
  id BINARY(16) NOT NULL PRIMARY KEY,
  v INT NOT NULL
) ENGINE=InnoDB;

-- S4 composite PK with heavy tenant skew
CREATE TABLE IF NOT EXISTS t_composite (
  tenant_id INT UNSIGNED NOT NULL,
  seq BIGINT UNSIGNED NOT NULL,
  v INT NOT NULL,
  PRIMARY KEY (tenant_id, seq)
) ENGINE=InnoDB;

-- S5 no PK and no unique key (hidden GEN_CLUST_INDEX)
CREATE TABLE IF NOT EXISTS t_nopk (
  a INT NOT NULL,
  b INT NOT NULL,
  KEY k_a (a)
) ENGINE=InnoDB;

-- S6 no PK, UNIQUE NOT NULL (promoted to clustered index)
CREATE TABLE IF NOT EXISTS t_uniq_notnull (
  code BIGINT NOT NULL,
  v INT NOT NULL,
  UNIQUE KEY uk_code (code)
) ENGINE=InnoDB;

-- S7 no PK, UNIQUE on a nullable column (NULLs are not unique and vanish from `code > ?`)
CREATE TABLE IF NOT EXISTS t_uniq_nullable (
  code BIGINT NULL,
  v INT NOT NULL,
  UNIQUE KEY uk_code (code)
) ENGINE=InnoDB;

-- S8 parent / child / grandchild foreign keys
CREATE TABLE IF NOT EXISTS customers (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  name VARCHAR(32) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS orders (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id BIGINT UNSIGNED NOT NULL,
  total DECIMAL(12,2) NOT NULL,
  KEY k_customer (customer_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS order_items (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  sku INT UNSIGNED NOT NULL,
  qty INT NOT NULL,
  KEY k_order (order_id),
  CONSTRAINT fk_items_order FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
) ENGINE=InnoDB;

-- S9 many-to-many junction: composite PK made of two foreign keys
CREATE TABLE IF NOT EXISTS tags (
  id INT UNSIGNED NOT NULL PRIMARY KEY,
  name VARCHAR(32) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS customer_tags (
  customer_id BIGINT UNSIGNED NOT NULL,
  tag_id INT UNSIGNED NOT NULL,
  PRIMARY KEY (customer_id, tag_id),
  KEY k_tag (tag_id),
  CONSTRAINT fk_ct_customer FOREIGN KEY (customer_id) REFERENCES customers (id),
  CONSTRAINT fk_ct_tag FOREIGN KEY (tag_id) REFERENCES tags (id)
) ENGINE=InnoDB;

-- S10 self-referencing FK (tree; a child can have a smaller id than its parent)
CREATE TABLE IF NOT EXISTS categories (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  parent_id BIGINT UNSIGNED NULL,
  KEY k_parent (parent_id),
  CONSTRAINT fk_cat_parent FOREIGN KEY (parent_id) REFERENCES categories (id)
) ENGINE=InnoDB;

-- S11 circular FK (no valid parent-first load order)
CREATE TABLE IF NOT EXISTS circ_a (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  b_id BIGINT UNSIGNED NULL,
  KEY k_b (b_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS circ_b (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  a_id BIGINT UNSIGNED NULL,
  KEY k_a (a_id),
  CONSTRAINT fk_b_a FOREIGN KEY (a_id) REFERENCES circ_a (id)
) ENGINE=InnoDB;

-- S12 off-page LOB column (~8 KB per row)
CREATE TABLE IF NOT EXISTS t_lob (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  small_col INT NOT NULL,
  payload MEDIUMTEXT NOT NULL
) ENGINE=InnoDB;

-- S13 range partitioned; PK must include the partition key
CREATE TABLE IF NOT EXISTS t_part (
  id BIGINT UNSIGNED NOT NULL,
  created_at DATE NOT NULL,
  v INT NOT NULL,
  PRIMARY KEY (id, created_at)
) ENGINE=InnoDB
PARTITION BY RANGE (TO_DAYS(created_at)) (
  PARTITION p1 VALUES LESS THAN (TO_DAYS('2026-02-01')),
  PARTITION p2 VALUES LESS THAN (TO_DAYS('2026-03-01')),
  PARTITION p3 VALUES LESS THAN (TO_DAYS('2026-04-01')),
  PARTITION p4 VALUES LESS THAN (TO_DAYS('2026-05-01')),
  PARTITION p5 VALUES LESS THAN (TO_DAYS('2026-06-01')),
  PARTITION p6 VALUES LESS THAN MAXVALUE
);

-- S14 string PK with case-insensitive PAD SPACE collation (numeric-looking values)
CREATE TABLE IF NOT EXISTS t_strpk (
  code VARCHAR(40) CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci NOT NULL PRIMARY KEY,
  v INT NOT NULL
) ENGINE=InnoDB;

-- S15 random PK and non-unique time index only (duplicate timestamps at chunk edges)
CREATE TABLE IF NOT EXISTS t_events (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  created_at DATETIME NOT NULL,
  v INT NOT NULL,
  KEY k_created (created_at)
) ENGINE=InnoDB;

-- S16 MyISAM: any SELECT takes a table-level read lock
CREATE TABLE IF NOT EXISTS t_myisam (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  v INT NOT NULL
) ENGINE=MyISAM;

-- S17 high-churn table for consistency tests (updated, deleted, PK-moved during extraction)
CREATE TABLE IF NOT EXISTS t_hot (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  version BIGINT UNSIGNED NOT NULL DEFAULT 0,
  v INT NOT NULL
) ENGINE=InnoDB;

-- S18 replicated table (only this table is applied on the replica)
CREATE TABLE IF NOT EXISTS t_repl (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  v INT NOT NULL,
  note VARCHAR(32) NOT NULL
) ENGINE=InnoDB;

-- S19 MEMORY engine: table-level locks, HASH PK index by default (no range scan on PK)
CREATE TABLE IF NOT EXISTS t_memory (
  id BIGINT UNSIGNED NOT NULL PRIMARY KEY,
  v INT NOT NULL
) ENGINE=MEMORY;

-- S20 ARCHIVE engine: index only on the auto-increment column, no updates or deletes
CREATE TABLE IF NOT EXISTS t_archive (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  v INT NOT NULL
) ENGINE=ARCHIVE;

-- S21 same FK child shape without its FK and FK index, to compare optimizer plans
CREATE TABLE IF NOT EXISTS order_items_nofk (
  id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id BIGINT UNSIGNED NOT NULL,
  sku INT UNSIGNED NOT NULL,
  qty INT NOT NULL
) ENGINE=InnoDB;
