CREATE DATABASE IF NOT EXISTS heaven_and_angel_scents
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE heaven_and_angel_scents;

-- ----------------------------------------------------------------------------
-- 1. Branches
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branches (
    branch_id   INT AUTO_INCREMENT PRIMARY KEY,
    branch_name VARCHAR(100) NOT NULL UNIQUE,
    location    VARCHAR(150),
    is_hq       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO branches (branch_id, branch_name, location, is_hq) VALUES
    (1, 'HQ Main Warehouse', 'Central Office', TRUE),
    (2, 'Balayan Branch',    'Balayan',        FALSE)
ON DUPLICATE KEY UPDATE branch_name = branch_name;

-- ----------------------------------------------------------------------------
-- 2. Users  (Admin = HQ office staff, Branch = retail branch staff)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    user_id              INT AUTO_INCREMENT PRIMARY KEY,
    username             VARCHAR(50) NOT NULL UNIQUE,
    password_hash        VARCHAR(255) NOT NULL,
    role                 ENUM('Admin', 'Branch') NOT NULL,
    branch_id            INT NULL,                -- NULL for Admin, set for Branch users
    is_active            BOOLEAN NOT NULL DEFAULT TRUE,
    -- Forces a password change at next login (temp passwords).
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE SET NULL
);

-- ----------------------------------------------------------------------------
-- 3. Master Products Catalog
-- price is the HQ reference price; the amount charged is stored on sales.
-- Bottled = fixed sizes; Bulk/Refill = custom mL at bulk_rate_settings.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS products (
    sku         VARCHAR(50) PRIMARY KEY,
    item_name   VARCHAR(100) NOT NULL,
    variant     ENUM('Male', 'Female') NOT NULL,
    category    ENUM('Bottled', 'Bulk/Refill') NOT NULL DEFAULT 'Bottled',
    unit        ENUM('85ML', '50ML', '10ML', '3ML', 'BULK') NOT NULL DEFAULT '50ML',
    price       DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    -- Photo path relative to static/; NULL = no image yet.
    image_path  VARCHAR(255) NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- No is_active: products are edited in place (see migration block).
    CHECK (price >= 0)
);

-- ----------------------------------------------------------------------------
-- 3a. Suppliers
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS suppliers (
    supplier_id     INT AUTO_INCREMENT PRIMARY KEY,
    supplier_name   VARCHAR(100) NOT NULL UNIQUE,
    contact_person  VARCHAR(100) NULL,
    phone           VARCHAR(30) NULL,
    email           VARCHAR(120) NULL,
    address         VARCHAR(255) NULL,
    notes           VARCHAR(255) NULL,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 3b. Raw Materials
-- Purchase log: cost_per_unit is worked out from package_cost / package_qty.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_materials (
    material_id    INT AUTO_INCREMENT PRIMARY KEY,
    material_name  VARCHAR(100) NOT NULL UNIQUE,
    unit           ENUM('Gram', 'Milliliter', 'Liter', 'Gallon', 'Piece') NOT NULL DEFAULT 'Piece',
    -- How the purchase is described (Package vs Individual); does not imply package_qty = 1.
    purchase_mode  ENUM('Package', 'Individual') NOT NULL DEFAULT 'Package',
    package_qty    DECIMAL(10, 3) NOT NULL DEFAULT 1.000,
    package_cost   DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    -- Optional receipt number for tracing the purchase.
    receipt_number VARCHAR(60) NULL,
    cost_per_unit  DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
    supplier_id    INT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL,
    CHECK (package_qty > 0),
    CHECK (package_cost >= 0)
);

-- ----------------------------------------------------------------------------
-- 4. Branch Inventory Levels
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS branch_inventory (
    inventory_id   INT AUTO_INCREMENT PRIMARY KEY,
    branch_id      INT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    stock_qty      INT NOT NULL DEFAULT 0,
    reorder_level  INT NOT NULL DEFAULT 10,   -- low-stock threshold, editable per branch/SKU
    updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    UNIQUE KEY unique_branch_sku (branch_id, sku),
    CHECK (stock_qty >= 0),
    CHECK (reorder_level >= 0)
);

-- ----------------------------------------------------------------------------
-- 5. HQ Production Logs
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS production_logs (
    log_id        INT AUTO_INCREMENT PRIMARY KEY,
    sku           VARCHAR(50) NOT NULL,
    batch_code    VARCHAR(50),               -- lets branches sell FIFO by batch
    qty_produced  INT NOT NULL,
    produced_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    CHECK (qty_produced > 0)
);

-- ----------------------------------------------------------------------------
-- 5a. Unit Formulas (Packaging Cost Reference): packaging recipe per size
-- One shared recipe per packaging size (not per scent); BULK has none.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unit_formula_items (
    formula_item_id INT AUTO_INCREMENT PRIMARY KEY,
    unit            ENUM('85ML', '50ML', '10ML', '3ML') NOT NULL,
    material_id     INT NOT NULL,
    qty_per_unit    DECIMAL(10, 4) NOT NULL,
    line_cost       DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,   -- qty_per_unit x raw_materials.cost_per_unit at save time; re-derived live on display, never trusted stale from here
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id),
    UNIQUE KEY unique_formula_unit_material (unit, material_id),
    INDEX idx_formula_items_unit (unit),
    CHECK (qty_per_unit > 0),
    CHECK (line_cost >= 0)
);

-- ----------------------------------------------------------------------------
-- 5a-bis. Bulk Rate Settings: the one shared per-mL rate for Bulk/Refill (row id = 1)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bulk_rate_settings (
    id            TINYINT PRIMARY KEY DEFAULT 1,
    rate_per_ml   DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (id = 1),
    CHECK (rate_per_ml >= 0)
);

INSERT IGNORE INTO bulk_rate_settings (id, rate_per_ml) VALUES (1, 0.0000);

-- ----------------------------------------------------------------------------
-- 5b. Cost of Goods (COGS) Logs — one row per "Log material usage" batch
-- cogs_per_unit is frozen at log time, so later price changes never rewrite it.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS cogs_logs (
    cogs_log_id         BIGINT AUTO_INCREMENT PRIMARY KEY,
    sku                 VARCHAR(50) NOT NULL,
    production_log_id   INT NULL,
    qty_produced        DECIMAL(10, 3) NOT NULL,
    cogs_per_unit        DECIMAL(12, 4) NOT NULL,
    total_cogs           DECIMAL(12, 2) NOT NULL,
    notes                VARCHAR(255) NULL,
    created_by_user_id   INT NULL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (production_log_id) REFERENCES production_logs(log_id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_cogs_logs_sku (sku),
    INDEX idx_cogs_logs_created_at (created_at),
    CHECK (qty_produced > 0),
    CHECK (total_cogs >= 0)
);

-- ----------------------------------------------------------------------------
-- 5c. Material Usage Logs
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS material_usage_logs (
    usage_id            INT AUTO_INCREMENT PRIMARY KEY,
    material_id         INT NOT NULL,
    production_log_id   INT NULL,
    cogs_log_id         BIGINT NULL,
    qty_used            DECIMAL(10, 3) NOT NULL,
    notes               VARCHAR(255),
    created_by_user_id  INT NULL,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id),
    FOREIGN KEY (production_log_id) REFERENCES production_logs(log_id) ON DELETE SET NULL,
    FOREIGN KEY (cogs_log_id) REFERENCES cogs_logs(cogs_log_id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_material_usage_material (material_id),
    INDEX idx_material_usage_production (production_log_id),
    INDEX idx_material_usage_cogs_log (cogs_log_id),
    CHECK (qty_used > 0)
);

-- ----------------------------------------------------------------------------
-- 6. Stock Requests (Requisitions) — delivery header
-- delivery_number is DR-<request_id padded to 6 digits>.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_requests (
    request_id      INT AUTO_INCREMENT PRIMARY KEY,
    branch_id        INT NOT NULL,
    delivery_number  VARCHAR(20) NOT NULL UNIQUE,
    status           ENUM('Pending', 'In Transit', 'Fulfilled', 'Rejected') DEFAULT 'Pending',
    requested_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE
);

-- ----------------------------------------------------------------------------
-- 6b. Stock Request Items — one row per product on a delivery
-- unit_price is snapshotted from products.price when the request is submitted.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_request_items (
    item_id         INT AUTO_INCREMENT PRIMARY KEY,
    request_id      INT NOT NULL,
    sku             VARCHAR(50) NOT NULL,
    requested_qty   INT NOT NULL,
    unit_price      DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    dispatched_qty  INT NULL,                 -- filled in when HQ dispatches
    received_qty    INT NULL,                 -- filled in when branch confirms receipt
    damaged_qty     INT NOT NULL DEFAULT 0,    -- reported at receipt, logged as loss
    FOREIGN KEY (request_id) REFERENCES stock_requests(request_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    INDEX idx_sri_request (request_id),
    -- Nullable until dispatched/received; a CHECK passes on NULL.
    CHECK (requested_qty > 0),
    CHECK (unit_price >= 0),
    CHECK (dispatched_qty >= 0),
    CHECK (received_qty >= 0),
    CHECK (damaged_qty >= 0)
);

-- ----------------------------------------------------------------------------
-- 7. Sales — completes the inventory lifecycle at the point of customer sale
-- Freebie sales always have unit_price 0; every other type needs a price > 0.
-- buyer_name is free text, only used for Credit sales.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sales (
    sale_id          INT AUTO_INCREMENT PRIMARY KEY,
    branch_id        INT NOT NULL,
    sku              VARCHAR(50) NOT NULL,
    qty_sold         INT NOT NULL,
    unit_price       DECIMAL(10, 2) NOT NULL,
    sale_type        ENUM('Sale', 'Refill', 'Freebie') NOT NULL DEFAULT 'Sale',
    payment_method   ENUM('Cash', 'Credit') NOT NULL DEFAULT 'Cash',
    buyer_user_id    INT NULL,                 -- who's being charged, only set for Credit sales
    customer_name    VARCHAR(120) NULL,
    customer_address VARCHAR(255) NULL,
    sold_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- Entry time (sold_at can be back-dated); branch staff void only today's sales.
    recorded_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (buyer_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sales_branch_sold_at (branch_id, sold_at),
    INDEX idx_sales_payment_method (payment_method),
    INDEX idx_sales_customer_name (customer_name),
    CHECK (qty_sold > 0),
    CHECK (unit_price >= 0)
);

-- ----------------------------------------------------------------------------
-- 8. Universal Stock Movement Logs (Audit Trail / Ledger)
-- FREEBIE is its own movement_type so giveaways are easy to filter.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS stock_movement_logs (
    log_id             INT AUTO_INCREMENT PRIMARY KEY,
    branch_id          INT NOT NULL,
    sku                VARCHAR(50) NOT NULL,
    change_qty         INT NOT NULL,               -- positive for additions, negative for deductions
    movement_type      ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE',
                            'PACKAGE_ORDER', 'PACKAGE_RETURN', 'SALE_VOID') NOT NULL,
    notes              VARCHAR(255),
    -- Actor and stock before/after, so movements can be traced.
    created_by_user_id INT NULL,
    reference_type     VARCHAR(30) NULL,           -- e.g. 'STOCK_REQUEST', 'PRODUCTION_LOG', 'SALE'
    reference_id       INT NULL,
    before_qty         INT NULL,
    after_qty          INT NULL,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sml_reference (reference_type, reference_id)
);

-- ----------------------------------------------------------------------------
-- 9. Admin Actions (Audit Trail): non-inventory admin activity (stock has its own ledger)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_actions (
    action_id      BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor_user_id  INT NULL,
    actor_username VARCHAR(80) NULL,   -- kept alongside actor_user_id so the log still reads clearly if the account is later deleted
    action         VARCHAR(50) NOT NULL,
    target         VARCHAR(120) NULL,
    details        VARCHAR(255) NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (actor_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_admin_actions_created_at (created_at)
);

-- ----------------------------------------------------------------------------
-- app_settings: small key/value store for settings changed from the UI
-- (the backup schedule: backup_days, backup_auto, backup_hour). Also created
-- on first use by appsettings.py for a database that predates this.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_settings (
    setting_key   VARCHAR(60) PRIMARY KEY,
    setting_value VARCHAR(255) NOT NULL,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- ----------------------------------------------------------------------------
-- 10. Capital — no longer its own table.
-- Capital is derived (SUM of cogs_logs.total_cogs), so there is no table.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 10b. Partners (Distributors &amp; Resellers)
-- Partners have no branch_inventory row or login.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partners (
    partner_id      INT AUTO_INCREMENT PRIMARY KEY,
    partner_type    ENUM('Distributor', 'Reseller') NOT NULL,
    partner_name    VARCHAR(150) NOT NULL,
    contact_person  VARCHAR(100) NULL,
    phone           VARCHAR(30) NULL,
    email           VARCHAR(120) NULL,
    address         VARCHAR(255) NULL,
    notes           VARCHAR(255) NULL,
    -- Rollup of partner_inquiries (the real history lives there); only
    -- last_inquiry_at/inquiry_count change on a repeat inquiry.
    last_inquiry_at TIMESTAMP NULL,
    inquiry_count   INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_partners_type (partner_type)
);

-- ----------------------------------------------------------------------------
-- 10c. Partner Investments
-- Append-only ledger: corrections are new entries.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_investments (
    investment_id      INT AUTO_INCREMENT PRIMARY KEY,
    partner_id          INT NOT NULL,
    amount               DECIMAL(12, 2) NOT NULL,
    note                 VARCHAR(255) NULL,
    logged_by_user_id   INT NULL,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (partner_id) REFERENCES partners(partner_id) ON DELETE CASCADE,
    FOREIGN KEY (logged_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_partner_investments_partner (partner_id)
);

-- ----------------------------------------------------------------------------
-- 10d. Packages
-- Orders snapshot the discount, so later changes never rewrite them.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS packages (
    package_id       INT AUTO_INCREMENT PRIMARY KEY,
    package_name     VARCHAR(150) NOT NULL,
    description      VARCHAR(255) NULL,
    partner_scope    ENUM('Both', 'Distributor', 'Reseller') NOT NULL DEFAULT 'Both',
    discount_percent DECIMAL(5, 2) NOT NULL DEFAULT 0.00,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_packages_active (is_active),
    CHECK (discount_percent >= 0 AND discount_percent <= 100)
);

-- ----------------------------------------------------------------------------
-- 10e. Package Items — one row per product in a package, and how many
--      units of it make up one "set" of the package.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS package_items (
    package_item_id INT AUTO_INCREMENT PRIMARY KEY,
    package_id      INT NOT NULL,
    sku             VARCHAR(50) NOT NULL,
    qty             INT NOT NULL,
    FOREIGN KEY (package_id) REFERENCES packages(package_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    UNIQUE KEY unique_package_sku (package_id, sku),
    CHECK (qty > 0)
);

-- ----------------------------------------------------------------------------
-- 10f. Partner Inquiries — leads from the public partner portal
-- Snapshot columns (package_name_snapshot, order_amount) are frozen at inquiry time.
-- order_amount counts as a sale only once status = 'Closed'; NULL means unknown.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_inquiries (
    inquiry_id             INT AUTO_INCREMENT PRIMARY KEY,
    package_id              INT NULL,
    partner_id               INT NULL,
    partner_type             ENUM('Distributor', 'Reseller') NOT NULL,
    company_name              VARCHAR(150) NOT NULL,
    contact_person            VARCHAR(100) NULL,
    phone                     VARCHAR(30) NULL,
    email                     VARCHAR(120) NULL,
    address                   VARCHAR(255) NULL,
    message                   VARCHAR(500) NULL,
    preferred_contact         ENUM('Call', 'SMS', 'Viber', 'Email') NULL,
    remarks                   TEXT NULL,
    package_name_snapshot     VARCHAR(180) NOT NULL,
    order_amount              DECIMAL(12, 2) NULL,
    status                    ENUM('New', 'Contacted', 'Follow-up', 'On Hold', 'Closed', 'Declined')
                                  NOT NULL DEFAULT 'New',
    email_sent                BOOLEAN NOT NULL DEFAULT FALSE,
    -- Set by Fulfill (migration 41): when items left HQ stock; NULL = not shipped.
    fulfilled_at              DATETIME NULL,
    fulfilled_by_user_id      INT NULL,
    created_at                TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (package_id) REFERENCES packages(package_id) ON DELETE SET NULL,
    FOREIGN KEY (partner_id) REFERENCES partners(partner_id) ON DELETE SET NULL,
    FOREIGN KEY (fulfilled_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_partner_inquiries_created (created_at),
    INDEX idx_partner_inquiries_partner (partner_id),
    INDEX idx_partner_inquiries_status (status)
);

-- ----------------------------------------------------------------------------
-- 11. Migration block: every step checks information_schema first, so re-running is safe.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _has_migrations_applied$$

CREATE PROCEDURE _has_migrations_applied()
BEGIN
    -- products.unit -----------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'unit'
    ) THEN
        ALTER TABLE products
            ADD COLUMN unit ENUM('85ML', '50ML', '10ML', '3ML', 'BULK')
                NOT NULL DEFAULT '50ML' AFTER variant;
    END IF;

    -- products.image_path -------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'image_path'
    ) THEN
        ALTER TABLE products
            ADD COLUMN image_path VARCHAR(255) NULL AFTER price;
    END IF;

    -- branch_inventory.branch_price removed (all branches use products.price).
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'branch_inventory' AND column_name = 'branch_price'
    ) THEN
        ALTER TABLE branch_inventory DROP COLUMN branch_price;
    END IF;

    -- products.is_active removed (products are edited in place).
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE products DROP COLUMN is_active;
    END IF;

    -- raw_materials.is_active removed (edited in place).
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'is_active'
    ) THEN
        ALTER TABLE raw_materials DROP COLUMN is_active;
    END IF;

    -- raw_materials.supplier_id (suppliers table is created earlier).
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'supplier_id'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN supplier_id INT NULL AFTER cost_per_unit;
        ALTER TABLE raw_materials ADD CONSTRAINT fk_raw_materials_supplier
            FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL;
    END IF;

    -- sales.sale_type -----------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'sale_type'
    ) THEN
        ALTER TABLE sales
            ADD COLUMN sale_type ENUM('Sale', 'Refill', 'Freebie') NOT NULL DEFAULT 'Sale' AFTER unit_price;
    END IF;

    -- sales.payment_method --------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'payment_method'
    ) THEN
        ALTER TABLE sales
            ADD COLUMN payment_method ENUM('Cash', 'Credit') NOT NULL DEFAULT 'Cash' AFTER sale_type;
    END IF;

    -- Rename 'Salary Deduction' to 'Credit': widen enum, move rows, narrow again.
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'payment_method'
              AND column_type LIKE '%Salary Deduction%'
    ) THEN
        ALTER TABLE sales MODIFY COLUMN payment_method
            ENUM('Cash', 'Salary Deduction', 'Credit') NOT NULL DEFAULT 'Cash';
        UPDATE sales SET payment_method = 'Credit' WHERE payment_method = 'Salary Deduction';
        ALTER TABLE sales MODIFY COLUMN payment_method ENUM('Cash', 'Credit') NOT NULL DEFAULT 'Cash';
    END IF;

    -- sales.buyer_user_id -----------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'buyer_user_id'
    ) THEN
        ALTER TABLE sales ADD COLUMN buyer_user_id INT NULL AFTER payment_method;
        ALTER TABLE sales ADD CONSTRAINT fk_sales_buyer_user
            FOREIGN KEY (buyer_user_id) REFERENCES users(user_id) ON DELETE SET NULL;
        ALTER TABLE sales ADD INDEX idx_sales_payment_method (payment_method);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'buyer_name'
    ) THEN
        ALTER TABLE sales ADD COLUMN buyer_name VARCHAR(120) NULL AFTER buyer_user_id;
    END IF;

    -- sales.customer_name / customer_address: optional walk-in customer, added together.
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'customer_name'
    ) THEN
        ALTER TABLE sales ADD COLUMN customer_name VARCHAR(120) NULL AFTER buyer_name;
        ALTER TABLE sales ADD COLUMN customer_address VARCHAR(255) NULL AFTER customer_name;
        ALTER TABLE sales ADD INDEX idx_sales_customer_name (customer_name);
    END IF;

    -- stock_movement_logs.movement_type gains 'REFILL' -------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_movement_logs'
              AND column_name = 'movement_type' AND column_type LIKE '%REFILL%'
    ) THEN
        ALTER TABLE stock_movement_logs
            MODIFY COLUMN movement_type
            ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'ADJUSTMENT', 'DAMAGE') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _has_migrations_applied();
DROP PROCEDURE _has_migrations_applied;

-- ----------------------------------------------------------------------------
-- 12. Migration — stock_requests: single item -> delivery header + items
-- Runs only if the old shape (sku column on stock_requests) is found.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_stock_requests_to_multi_item$$

CREATE PROCEDURE _migrate_stock_requests_to_multi_item()
BEGIN
    DECLARE fk_name VARCHAR(128) DEFAULT NULL;
    DECLARE CONTINUE HANDLER FOR NOT FOUND SET fk_name = NULL;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_requests' AND column_name = 'sku'
    ) THEN
        INSERT INTO stock_request_items
            (request_id, sku, requested_qty, unit_price, dispatched_qty, received_qty, damaged_qty)
        SELECT sr.request_id, sr.sku, sr.requested_qty,
               COALESCE(p.price, 0.00), sr.dispatched_qty, sr.received_qty, sr.damaged_qty
        FROM stock_requests sr
        LEFT JOIN products p ON p.sku = sr.sku;

        SELECT CONSTRAINT_NAME INTO fk_name
        FROM information_schema.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stock_requests'
              AND COLUMN_NAME = 'sku' AND REFERENCED_TABLE_NAME = 'products'
        LIMIT 1;

        IF fk_name IS NOT NULL THEN
            SET @drop_fk_sql = CONCAT('ALTER TABLE stock_requests DROP FOREIGN KEY `', fk_name, '`');
            PREPARE stmt FROM @drop_fk_sql;
            EXECUTE stmt;
            DEALLOCATE PREPARE stmt;
        END IF;

        ALTER TABLE stock_requests
            ADD COLUMN delivery_number VARCHAR(20) NULL AFTER branch_id;

        UPDATE stock_requests
        SET delivery_number = CONCAT('DR-', LPAD(request_id, 6, '0'))
        WHERE delivery_number IS NULL;

        ALTER TABLE stock_requests
            MODIFY COLUMN delivery_number VARCHAR(20) NOT NULL,
            ADD UNIQUE KEY unique_delivery_number (delivery_number),
            DROP COLUMN sku,
            DROP COLUMN requested_qty,
            DROP COLUMN dispatched_qty,
            DROP COLUMN received_qty,
            DROP COLUMN damaged_qty;
    END IF;
END$$

DELIMITER ;

CALL _migrate_stock_requests_to_multi_item();
DROP PROCEDURE _migrate_stock_requests_to_multi_item;

-- ----------------------------------------------------------------------------
-- 13. Migration — partners: inquiry history rollup columns
-- Also backfills both columns from partner_inquiries.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_partner_inquiry_rollup$$

CREATE PROCEDURE _migrate_partner_inquiry_rollup()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partners' AND column_name = 'last_inquiry_at'
    ) THEN
        ALTER TABLE partners ADD COLUMN last_inquiry_at TIMESTAMP NULL AFTER notes;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partners' AND column_name = 'inquiry_count'
    ) THEN
        ALTER TABLE partners ADD COLUMN inquiry_count INT NOT NULL DEFAULT 0 AFTER last_inquiry_at;
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiry_rollup();
DROP PROCEDURE _migrate_partner_inquiry_rollup;

UPDATE partners p
LEFT JOIN (
    SELECT partner_id, COUNT(*) AS cnt, MAX(created_at) AS last_at
    FROM partner_inquiries
    WHERE partner_id IS NOT NULL
    GROUP BY partner_id
) agg ON agg.partner_id = p.partner_id
SET p.inquiry_count = COALESCE(agg.cnt, 0),
    p.last_inquiry_at = agg.last_at;

-- ----------------------------------------------------------------------------
-- 14. Migration — partner_inquiries.order_amount
-- Backfill uses current prices (best effort); unknown rows stay NULL.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_partner_inquiry_order_amount$$

CREATE PROCEDURE _migrate_partner_inquiry_order_amount()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries' AND column_name = 'order_amount'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN order_amount DECIMAL(12, 2) NULL AFTER package_name_snapshot;
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiry_order_amount();
DROP PROCEDURE _migrate_partner_inquiry_order_amount;

UPDATE partner_inquiries pinq
JOIN packages pkg ON pkg.package_id = pinq.package_id
JOIN (
    SELECT pi.package_id, COALESCE(SUM(pi.qty * p.price), 0) AS reference_total
    FROM package_items pi JOIN products p ON p.sku = pi.sku
    GROUP BY pi.package_id
) ref ON ref.package_id = pkg.package_id
SET pinq.order_amount = ref.reference_total * (1 - (pkg.discount_percent / 100))
WHERE pinq.order_amount IS NULL;

-- ----------------------------------------------------------------------------
-- 15. Migration — raw_materials.stock_qty
-- Backfill: package_qty minus logged usage, floored at 0.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_raw_materials_stock_qty$$

CREATE PROCEDURE _migrate_raw_materials_stock_qty()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'stock_qty'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN stock_qty DECIMAL(10, 3) NOT NULL DEFAULT 0.000 AFTER cost_per_unit;

        UPDATE raw_materials rm
        LEFT JOIN (
            SELECT material_id, COALESCE(SUM(qty_used), 0) AS used
            FROM material_usage_logs GROUP BY material_id
        ) u ON u.material_id = rm.material_id
        SET rm.stock_qty = GREATEST(rm.package_qty - COALESCE(u.used, 0), 0);
    END IF;
END$$

DELIMITER ;

CALL _migrate_raw_materials_stock_qty();
DROP PROCEDURE _migrate_raw_materials_stock_qty;

-- ----------------------------------------------------------------------------
-- 16. Migration — material_usage_logs: drop cost columns
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_drop_material_usage_cost_columns$$

CREATE PROCEDURE _migrate_drop_material_usage_cost_columns()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'material_usage_logs' AND column_name = 'line_cost'
    ) THEN
        ALTER TABLE material_usage_logs
            DROP COLUMN unit_cost_snapshot,
            DROP COLUMN line_cost;
    END IF;
END$$

DELIMITER ;

CALL _migrate_drop_material_usage_cost_columns();
DROP PROCEDURE _migrate_drop_material_usage_cost_columns;

-- ----------------------------------------------------------------------------
-- 17. Migration — drop capital_contributions
-- Intentionally drops the old ledger table and its history.
-- ----------------------------------------------------------------------------
DROP TABLE IF EXISTS capital_contributions;

-- ----------------------------------------------------------------------------
-- 18. Migration — partner_inquiries: remarks + expanded status pipeline
-- Only adds allowed status values; stored values are unchanged.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_partner_inquiries_remarks_and_status$$

CREATE PROCEDURE _migrate_partner_inquiries_remarks_and_status()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries' AND column_name = 'remarks'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN remarks TEXT NULL AFTER message;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries'
              AND column_name = 'status' AND column_type LIKE '%Declined%'
    ) THEN
        ALTER TABLE partner_inquiries
            MODIFY COLUMN status
            ENUM('New', 'Contacted', 'Follow-up', 'On Hold', 'Closed', 'Declined')
            NOT NULL DEFAULT 'New';
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiries_remarks_and_status();
DROP PROCEDURE _migrate_partner_inquiries_remarks_and_status;

-- ----------------------------------------------------------------------------
-- 19. Migration — raw_materials: purchase_mode + receipt_number
-- Backfill keeps the old Package/Individual split.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_raw_materials_purchase_mode_and_receipt$$

CREATE PROCEDURE _migrate_raw_materials_purchase_mode_and_receipt()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'purchase_mode'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN purchase_mode ENUM('Package', 'Individual') NOT NULL DEFAULT 'Package' AFTER unit;
        UPDATE raw_materials SET purchase_mode = 'Individual' WHERE package_qty = 1;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'receipt_number'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN receipt_number VARCHAR(60) NULL AFTER package_cost;
    END IF;
END$$

DELIMITER ;

CALL _migrate_raw_materials_purchase_mode_and_receipt();
DROP PROCEDURE _migrate_raw_materials_purchase_mode_and_receipt;

-- ----------------------------------------------------------------------------
-- 20. AI Stock Drafts — human-in-the-loop proposals from the AI assistant
-- A draft is inert until approved; approval inserts the real stock request.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_stock_drafts (
    draft_id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    branch_id             INT NOT NULL,
    created_by_user_id    INT NULL,
    created_by_username   VARCHAR(80) NULL,
    status                ENUM('Pending Review', 'Approved', 'Rejected') NOT NULL DEFAULT 'Pending Review',
    note                  VARCHAR(255) NULL,
    reasoning              VARCHAR(500) NULL,
    resulting_request_id  INT NULL,
    reviewed_by_user_id   INT NULL,
    reviewed_at           DATETIME NULL,
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (reviewed_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    FOREIGN KEY (resulting_request_id) REFERENCES stock_requests(request_id) ON DELETE SET NULL,
    INDEX idx_ai_stock_drafts_branch (branch_id),
    INDEX idx_ai_stock_drafts_status (status)
);

-- ----------------------------------------------------------------------------
-- 20b. AI Stock Draft Items — one row per proposed SKU on a draft
-- Price is snapshotted at approval, not at proposal.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_stock_draft_items (
    draft_item_id  BIGINT AUTO_INCREMENT PRIMARY KEY,
    draft_id       BIGINT NOT NULL,
    sku            VARCHAR(50) NOT NULL,
    suggested_qty  INT NOT NULL,
    FOREIGN KEY (draft_id) REFERENCES ai_stock_drafts(draft_id) ON DELETE CASCADE,
    FOREIGN KEY (sku) REFERENCES products(sku) ON DELETE CASCADE,
    INDEX idx_ai_stock_draft_items_draft (draft_id)
);

-- ----------------------------------------------------------------------------
-- 21. Login Activity — every sign-in attempt, successful or not
-- Append-only; user_id is NULL for failed attempts that match no account.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS login_activity (
    activity_id       BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id           INT NULL,
    username_attempted VARCHAR(80) NOT NULL,
    role_attempted    VARCHAR(20) NOT NULL,
    success           BOOLEAN NOT NULL,
    failure_reason    VARCHAR(30) NULL,
    ip_address        VARCHAR(45) NULL,
    user_agent        VARCHAR(255) NULL,
    created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_login_activity_created_at (created_at),
    INDEX idx_login_activity_username (username_attempted),
    INDEX idx_login_activity_success (success)
);

-- ----------------------------------------------------------------------------
-- 22. Note: CHECK constraints on quantity/price columns
-- CHECK constraints live in the CREATE TABLEs only. Not retrofitted: an ALTER
-- fails if any existing row violates it. Audit data before adding by hand.
-- ----------------------------------------------------------------------------

-- ----------------------------------------------------------------------------
-- 23. Migration — material_usage_logs.cogs_log_id
-- No backfill: older usage rows stay NULL (not part of a COGS batch).
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_material_usage_logs_cogs_log_id$$

CREATE PROCEDURE _migrate_material_usage_logs_cogs_log_id()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'material_usage_logs' AND column_name = 'cogs_log_id'
    ) THEN
        ALTER TABLE material_usage_logs
            ADD COLUMN cogs_log_id BIGINT NULL AFTER production_log_id;
        ALTER TABLE material_usage_logs ADD CONSTRAINT fk_material_usage_cogs_log
            FOREIGN KEY (cogs_log_id) REFERENCES cogs_logs(cogs_log_id) ON DELETE SET NULL;
        ALTER TABLE material_usage_logs ADD INDEX idx_material_usage_cogs_log (cogs_log_id);
    END IF;
END$$

DELIMITER ;

CALL _migrate_material_usage_logs_cogs_log_id();
DROP PROCEDURE _migrate_material_usage_logs_cogs_log_id;

-- ----------------------------------------------------------------------------
-- 24. Migration — product_formula_items (sku-keyed) -> unit_formula_items
--     (unit-keyed)
-- INSERT IGNORE: two old SKUs can fold onto the same (unit, material) key.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_product_formula_items_to_unit_formula_items$$

CREATE PROCEDURE _migrate_product_formula_items_to_unit_formula_items()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'product_formula_items'
    ) THEN
        INSERT IGNORE INTO unit_formula_items (unit, material_id, qty_per_unit)
        SELECT p.unit, pfi.material_id, MIN(pfi.qty_per_unit)
        FROM product_formula_items pfi
        JOIN products p ON p.sku = pfi.sku
        GROUP BY p.unit, pfi.material_id;

        DROP TABLE product_formula_items;
    END IF;
END$$

DELIMITER ;

CALL _migrate_product_formula_items_to_unit_formula_items();
DROP PROCEDURE _migrate_product_formula_items_to_unit_formula_items;

-- ----------------------------------------------------------------------------
-- 25. Migration — raw_materials: drop stock_qty
-- Runs after migration 15, which re-adds the column on old databases.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_drop_raw_materials_stock_qty$$

CREATE PROCEDURE _migrate_drop_raw_materials_stock_qty()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'stock_qty'
    ) THEN
        ALTER TABLE raw_materials DROP COLUMN stock_qty;
    END IF;
END$$

DELIMITER ;

CALL _migrate_drop_raw_materials_stock_qty();
DROP PROCEDURE _migrate_drop_raw_materials_stock_qty;

-- ----------------------------------------------------------------------------
-- 26. Migration — unit_formula_items.unit_cost
-- Backfill seeds unit_cost from the material's current cost_per_unit.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_unit_formula_items_unit_cost$$

CREATE PROCEDURE _migrate_unit_formula_items_unit_cost()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items' AND column_name = 'unit_cost'
    ) THEN
        ALTER TABLE unit_formula_items
            ADD COLUMN unit_cost DECIMAL(10, 4) NOT NULL DEFAULT 0.0000 AFTER qty_per_unit;

        UPDATE unit_formula_items ufi
        JOIN raw_materials rm ON rm.material_id = ufi.material_id
        SET ufi.unit_cost = rm.cost_per_unit;
    END IF;
END$$

DELIMITER ;

CALL _migrate_unit_formula_items_unit_cost();
DROP PROCEDURE _migrate_unit_formula_items_unit_cost;

-- ----------------------------------------------------------------------------
-- 27. Migration — unit_formula_items.unit_cost -> line_cost
-- Rename in place keeps the saved values as the flat line cost.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_unit_formula_items_unit_cost_to_line_cost$$

CREATE PROCEDURE _migrate_unit_formula_items_unit_cost_to_line_cost()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items' AND column_name = 'unit_cost'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items' AND column_name = 'line_cost'
    ) THEN
        ALTER TABLE unit_formula_items
            CHANGE COLUMN unit_cost line_cost DECIMAL(10, 4) NOT NULL DEFAULT 0.0000;
    END IF;
END$$

DELIMITER ;

CALL _migrate_unit_formula_items_unit_cost_to_line_cost();
DROP PROCEDURE _migrate_unit_formula_items_unit_cost_to_line_cost;

-- ----------------------------------------------------------------------------
-- 28. Migration — unit_formula_items.is_packaging / cogs_logs.sale_id: removed
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_remove_refill_cogs_feature$$

CREATE PROCEDURE _migrate_remove_refill_cogs_feature()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items' AND column_name = 'is_packaging'
    ) THEN
        ALTER TABLE unit_formula_items DROP COLUMN is_packaging;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'cogs_logs' AND column_name = 'sale_id'
    ) THEN
        IF EXISTS (
            SELECT 1 FROM information_schema.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'cogs_logs'
                  AND CONSTRAINT_NAME = 'fk_cogs_logs_sale' AND CONSTRAINT_TYPE = 'FOREIGN KEY'
        ) THEN
            ALTER TABLE cogs_logs DROP FOREIGN KEY fk_cogs_logs_sale;
        END IF;
        ALTER TABLE cogs_logs DROP COLUMN sale_id;
    END IF;
END$$

DELIMITER ;

CALL _migrate_remove_refill_cogs_feature();
DROP PROCEDURE _migrate_remove_refill_cogs_feature;

-- ----------------------------------------------------------------------------
-- 30. Migration — products.unit / unit_formula_items.unit: add 70ML
-- Steps 30/31 also skip when already in the post-34 form, otherwise a re-run
-- would widen back to a list without BULK and fail (error 1265).
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_add_70ml_unit$$

CREATE PROCEDURE _migrate_add_70ml_unit()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products'
              AND column_name = 'unit'
              AND (column_type LIKE '%70ML%' OR column_type LIKE '%BULK%')
    ) THEN
        ALTER TABLE products
            MODIFY COLUMN unit ENUM('85ML', '70ML', '50ML', '1L', '100ML', '10ML', '3ML Tester')
                NOT NULL DEFAULT '50ML';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items'
              AND column_name = 'unit'
              AND (column_type LIKE '%70ML%' OR column_type = "enum('85ML','50ML','10ML','3ML')")
    ) THEN
        ALTER TABLE unit_formula_items
            MODIFY COLUMN unit ENUM('85ML', '70ML', '50ML', '1L', '100ML', '10ML', '3ML Tester') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_add_70ml_unit();
DROP PROCEDURE _migrate_add_70ml_unit;

-- ----------------------------------------------------------------------------
-- 31. Migration — products.unit / unit_formula_items.unit: add 55ML
-- Separate step so a database that already ran 30 still picks up 55ML.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_add_55ml_unit$$

CREATE PROCEDURE _migrate_add_55ml_unit()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products'
              AND column_name = 'unit'
              AND (column_type LIKE '%55ML%' OR column_type LIKE '%BULK%')
    ) THEN
        ALTER TABLE products
            MODIFY COLUMN unit ENUM('85ML', '70ML', '55ML', '50ML', '1L', '100ML', '10ML', '3ML Tester')
                NOT NULL DEFAULT '50ML';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items'
              AND column_name = 'unit'
              AND (column_type LIKE '%55ML%' OR column_type = "enum('85ML','50ML','10ML','3ML')")
    ) THEN
        ALTER TABLE unit_formula_items
            MODIFY COLUMN unit ENUM('85ML', '70ML', '55ML', '50ML', '1L', '100ML', '10ML', '3ML Tester') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_add_55ml_unit();
DROP PROCEDURE _migrate_add_55ml_unit;

-- ----------------------------------------------------------------------------
-- 32. Migration — sales.sale_type / stock_movement_logs.movement_type: add Freebie
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_add_freebie$$

CREATE PROCEDURE _migrate_add_freebie()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales'
              AND column_name = 'sale_type' AND column_type LIKE '%Freebie%'
    ) THEN
        ALTER TABLE sales
            MODIFY COLUMN sale_type ENUM('Sale', 'Refill', 'Freebie') NOT NULL DEFAULT 'Sale';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_movement_logs'
              AND column_name = 'movement_type' AND column_type LIKE '%FREEBIE%'
    ) THEN
        ALTER TABLE stock_movement_logs
            MODIFY COLUMN movement_type
            ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_add_freebie();
DROP PROCEDURE _migrate_add_freebie;

-- ----------------------------------------------------------------------------
-- 33. Migration — unit_formula_items: drop any 1L rows
-- Plain DELETE; safe to re-run.
-- ----------------------------------------------------------------------------
DELETE FROM unit_formula_items WHERE unit = '1L';

-- ----------------------------------------------------------------------------
-- 34. Migration — products/unit_formula_items: 85ML/50ML/10ML/3ML(+BULK)
--     only, products.category, bulk_rate_settings
-- Fails loudly (by design) if a dropped size is still in use.
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_bulk_refill_category$$

CREATE PROCEDURE _migrate_bulk_refill_category()
BEGIN
    -- products.category ---------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products' AND column_name = 'category'
    ) THEN
        ALTER TABLE products
            ADD COLUMN category ENUM('Bottled', 'Bulk/Refill') NOT NULL DEFAULT 'Bottled' AFTER variant;
    END IF;

    -- products.unit ----------------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products'
              AND column_name = 'unit'
              AND column_type IN ("enum('85ML','50ML','10ML','3ML','BULK')", "enum('85ML','50ML','10ML','3ML')")
    ) THEN
        ALTER TABLE products
            MODIFY COLUMN unit ENUM('85ML', '50ML', '10ML', '3ML', 'BULK') NOT NULL DEFAULT '50ML';
    END IF;

    -- unit_formula_items.unit -------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'unit_formula_items'
              AND column_name = 'unit' AND column_type = "enum('85ML','50ML','10ML','3ML')"
    ) THEN
        ALTER TABLE unit_formula_items
            MODIFY COLUMN unit ENUM('85ML', '50ML', '10ML', '3ML') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_bulk_refill_category();
DROP PROCEDURE _migrate_bulk_refill_category;

CREATE TABLE IF NOT EXISTS bulk_rate_settings (
    id            TINYINT PRIMARY KEY DEFAULT 1,
    rate_per_ml   DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (id = 1),
    CHECK (rate_per_ml >= 0)
);

INSERT IGNORE INTO bulk_rate_settings (id, rate_per_ml) VALUES (1, 0.0000);

-- ----------------------------------------------------------------------------
-- 35. Migration — raw_materials.stock_qty (v2 — real on-hand stock)
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_raw_materials_stock_qty_v2$$

CREATE PROCEDURE _migrate_raw_materials_stock_qty_v2()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials' AND column_name = 'stock_qty'
    ) THEN
        ALTER TABLE raw_materials
            ADD COLUMN stock_qty DECIMAL(10, 3) NOT NULL DEFAULT 0.000 AFTER cost_per_unit;
        UPDATE raw_materials SET stock_qty = package_qty;
    END IF;
END$$

DELIMITER ;

CALL _migrate_raw_materials_stock_qty_v2();
DROP PROCEDURE _migrate_raw_materials_stock_qty_v2;

-- ----------------------------------------------------------------------------
-- 36. Bulk Batches — the "make a bulk mix first" step between raw
--     materials and bottling.
-- Costs are frozen at creation; remaining_ml is deducted by bottling runs.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bulk_batches (
    batch_id           INT AUTO_INCREMENT PRIMARY KEY,
    batch_code         VARCHAR(50) NULL UNIQUE,
    scent_name         VARCHAR(100) NOT NULL,
    input_qty          DECIMAL(10, 3) NOT NULL,
    input_unit         ENUM('Milliliter', 'Liter', 'Gallon') NOT NULL,
    total_volume_ml    DECIMAL(12, 3) NOT NULL,
    total_cost         DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
    cost_per_ml        DECIMAL(12, 6) NOT NULL DEFAULT 0.000000,
    remaining_ml       DECIMAL(12, 3) NOT NULL,
    notes              VARCHAR(255) NULL,
    created_by_user_id INT NULL,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_bulk_batches_created_at (created_at),
    CHECK (total_volume_ml > 0),
    CHECK (remaining_ml >= 0)
);

-- ----------------------------------------------------------------------------
-- 36a. Bulk Batch Materials — which raw materials (and how much of each)
--     actually went into one specific bulk_batches row.
-- line_cost = qty_used x cost_per_unit_snapshot; also the audit trail for stock deducted.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bulk_batch_materials (
    item_id                INT AUTO_INCREMENT PRIMARY KEY,
    batch_id               INT NOT NULL,
    material_id            INT NOT NULL,
    qty_used               DECIMAL(10, 3) NOT NULL,
    cost_per_unit_snapshot DECIMAL(10, 4) NOT NULL,
    line_cost              DECIMAL(12, 4) NOT NULL,
    FOREIGN KEY (batch_id) REFERENCES bulk_batches(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id),
    INDEX idx_bulk_batch_materials_batch (batch_id),
    INDEX idx_bulk_batch_materials_material (material_id),
    CHECK (qty_used > 0)
);

-- ----------------------------------------------------------------------------
-- 37. Migration — cogs_logs.bulk_batch_id
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_cogs_logs_bulk_batch_id$$

CREATE PROCEDURE _migrate_cogs_logs_bulk_batch_id()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'cogs_logs' AND column_name = 'bulk_batch_id'
    ) THEN
        ALTER TABLE cogs_logs
            ADD COLUMN bulk_batch_id INT NULL AFTER production_log_id;
        ALTER TABLE cogs_logs ADD CONSTRAINT fk_cogs_logs_bulk_batch
            FOREIGN KEY (bulk_batch_id) REFERENCES bulk_batches(batch_id) ON DELETE SET NULL;
        ALTER TABLE cogs_logs ADD INDEX idx_cogs_logs_bulk_batch (bulk_batch_id);
    END IF;
END$$

DELIMITER ;

CALL _migrate_cogs_logs_bulk_batch_id();
DROP PROCEDURE _migrate_cogs_logs_bulk_batch_id;

-- ----------------------------------------------------------------------------
-- 38. Unit Cost-of-Goods Settings — the flat base cost per Bottled
--     packaging size that production() actually logs as cogs_per_unit
--     for a Bottled run.
-- production() logs this flat base cost as cogs_per_unit for Bottled runs.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS unit_cogs_settings (
    unit               ENUM('85ML', '50ML', '10ML', '3ML') PRIMARY KEY,
    base_cost_per_unit DECIMAL(10, 4) NOT NULL DEFAULT 0.0000,
    updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (base_cost_per_unit >= 0)
);

INSERT IGNORE INTO unit_cogs_settings (unit, base_cost_per_unit) VALUES
    ('85ML', 0.0000), ('50ML', 0.0000), ('10ML', 0.0000), ('3ML', 0.0000);

-- ----------------------------------------------------------------------------
-- 39. Migration — bulk_batch_materials.qty_used_unit + raw_materials.stock_qty
--     precision (mismatched-unit usage logging)
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_qty_used_unit_and_stock_precision$$

CREATE PROCEDURE _migrate_qty_used_unit_and_stock_precision()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batch_materials' AND column_name = 'qty_used_unit'
    ) THEN
        ALTER TABLE bulk_batch_materials
            ADD COLUMN qty_used_unit ENUM('Gram', 'Milliliter', 'Liter', 'Gallon', 'Piece') NULL AFTER material_id;
        UPDATE bulk_batch_materials bbm
        JOIN raw_materials rm ON rm.material_id = bbm.material_id
        SET bbm.qty_used_unit = rm.unit;
        ALTER TABLE bulk_batch_materials
            MODIFY COLUMN qty_used_unit ENUM('Gram', 'Milliliter', 'Liter', 'Gallon', 'Piece') NOT NULL;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'raw_materials'
              AND column_name = 'stock_qty' AND column_type = 'decimal(10,3)'
    ) THEN
        ALTER TABLE raw_materials
            MODIFY COLUMN stock_qty DECIMAL(14, 6) NOT NULL DEFAULT 0.000000;
    END IF;
END$$

DELIMITER ;

CALL _migrate_qty_used_unit_and_stock_precision();
DROP PROCEDURE _migrate_qty_used_unit_and_stock_precision;

-- ----------------------------------------------------------------------------
-- 40. Migration: partner_inquiries.preferred_contact
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_partner_inquiries_preferred_contact$$

CREATE PROCEDURE _migrate_partner_inquiries_preferred_contact()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries'
              AND column_name = 'preferred_contact'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN preferred_contact ENUM('Call', 'SMS', 'Viber', 'Email') NULL AFTER message;
    END IF;
END$$

DELIMITER ;

CALL _migrate_partner_inquiries_preferred_contact();
DROP PROCEDURE _migrate_partner_inquiries_preferred_contact;

-- ----------------------------------------------------------------------------
-- 41. Migration: package orders fulfill from HQ stock
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner_inquiry_items (
    inquiry_item_id INT AUTO_INCREMENT PRIMARY KEY,
    inquiry_id      INT NOT NULL,
    sku             VARCHAR(50) NOT NULL,
    item_name       VARCHAR(150) NOT NULL,
    unit            VARCHAR(10) NOT NULL,
    qty             INT NOT NULL,
    unit_price      DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
    FOREIGN KEY (inquiry_id) REFERENCES partner_inquiries(inquiry_id) ON DELETE CASCADE,
    UNIQUE KEY unique_inquiry_sku (inquiry_id, sku),
    CHECK (qty > 0)
);

DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_package_order_fulfillment$$

CREATE PROCEDURE _migrate_package_order_fulfillment()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_movement_logs'
              AND column_name = 'movement_type' AND column_type LIKE '%PACKAGE_ORDER%'
    ) THEN
        ALTER TABLE stock_movement_logs
            MODIFY COLUMN movement_type
            ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE',
                 'PACKAGE_ORDER', 'PACKAGE_RETURN') NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries'
              AND column_name = 'fulfilled_at'
    ) THEN
        ALTER TABLE partner_inquiries
            ADD COLUMN fulfilled_at DATETIME NULL AFTER email_sent,
            ADD COLUMN fulfilled_by_user_id INT NULL AFTER fulfilled_at,
            ADD CONSTRAINT fk_partner_inquiries_fulfilled_by
                FOREIGN KEY (fulfilled_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_package_order_fulfillment();
DROP PROCEDURE _migrate_package_order_fulfillment;

INSERT IGNORE INTO partner_inquiry_items (inquiry_id, sku, item_name, unit, qty, unit_price)
SELECT pinq.inquiry_id, pk.sku, p.item_name, p.unit, pk.qty, p.price
FROM partner_inquiries pinq
JOIN package_items pk ON pk.package_id = pinq.package_id
JOIN products p ON p.sku = pk.sku
WHERE pinq.package_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM partner_inquiry_items x WHERE x.inquiry_id = pinq.inquiry_id);

-- ----------------------------------------------------------------------------
-- 42. Migration: refills draw HQ bulk, and sales can be voided
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sale_voids (
    void_id            INT AUTO_INCREMENT PRIMARY KEY,
    sale_id            INT NOT NULL,
    branch_id          INT NOT NULL,
    sku                VARCHAR(50) NOT NULL,
    item_name          VARCHAR(150) NOT NULL,
    unit               VARCHAR(10) NOT NULL,
    qty_sold           INT NOT NULL,
    unit_price         DECIMAL(10, 2) NOT NULL,
    sale_type          VARCHAR(20) NOT NULL,
    payment_method     VARCHAR(20) NOT NULL,
    buyer_name         VARCHAR(120) NULL,
    customer_name      VARCHAR(120) NULL,
    sold_at            TIMESTAMP NULL,
    reason             VARCHAR(255) NOT NULL,
    voided_by_user_id  INT NULL,
    voided_by_username VARCHAR(80) NULL,
    voided_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (branch_id) REFERENCES branches(branch_id) ON DELETE CASCADE,
    FOREIGN KEY (voided_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_sale_voids_voided_at (voided_at)
);

DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_refill_bulk_and_sale_void$$

CREATE PROCEDURE _migrate_refill_bulk_and_sale_void()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'stock_movement_logs'
              AND column_name = 'movement_type' AND column_type LIKE '%SALE_VOID%'
    ) THEN
        ALTER TABLE stock_movement_logs
            MODIFY COLUMN movement_type
            ENUM('PRODUCTION', 'DISPATCH', 'RECEIPT', 'SALE', 'REFILL', 'FREEBIE', 'ADJUSTMENT', 'DAMAGE',
                 'PACKAGE_ORDER', 'PACKAGE_RETURN', 'SALE_VOID') NOT NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'recorded_at'
    ) THEN
        ALTER TABLE sales ADD COLUMN recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP AFTER sold_at;
        UPDATE sales SET recorded_at = sold_at;
    END IF;
END$$

DELIMITER ;

CALL _migrate_refill_bulk_and_sale_void();
DROP PROCEDURE _migrate_refill_bulk_and_sale_void;

-- ----------------------------------------------------------------------------
-- 43. Migration — bulk_batches.target_unit/target_bottle_qty,
--     bulk_batch_materials.is_packaging
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_bulk_batches_target_unit$$

CREATE PROCEDURE _migrate_bulk_batches_target_unit()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches' AND column_name = 'target_unit'
    ) THEN
        ALTER TABLE bulk_batches
            ADD COLUMN target_unit ENUM('85ML', '50ML', '10ML', '3ML') NULL AFTER notes,
            ADD COLUMN target_bottle_qty INT NULL AFTER target_unit;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batch_materials' AND column_name = 'is_packaging'
    ) THEN
        ALTER TABLE bulk_batch_materials
            ADD COLUMN is_packaging BOOLEAN NOT NULL DEFAULT FALSE AFTER line_cost;
    END IF;
END$$

DELIMITER ;

CALL _migrate_bulk_batches_target_unit();
DROP PROCEDURE _migrate_bulk_batches_target_unit;

-- ----------------------------------------------------------------------------
-- 44. Migration — backfill products.unit = 'BULK' for Bulk/Refill rows
-- ----------------------------------------------------------------------------
UPDATE products SET unit = 'BULK' WHERE category = 'Bulk/Refill' AND unit <> 'BULK';

-- ----------------------------------------------------------------------------
-- 45. Migration — sales: credit settlement (credit_settled_at/by)
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_sales_credit_settled$$

CREATE PROCEDURE _migrate_sales_credit_settled()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'credit_settled_at'
    ) THEN
        ALTER TABLE sales ADD COLUMN credit_settled_at TIMESTAMP NULL AFTER buyer_name;
        ALTER TABLE sales ADD COLUMN credit_settled_by VARCHAR(80) NULL AFTER credit_settled_at;
    END IF;
END$$

DELIMITER ;

CALL _migrate_sales_credit_settled();
DROP PROCEDURE _migrate_sales_credit_settled;

-- ----------------------------------------------------------------------------
-- 46. Migration — dated raw-material restock events
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS material_restock_events (
    restock_id             INT AUTO_INCREMENT PRIMARY KEY,
    material_id            INT NOT NULL,
    material_name_snapshot VARCHAR(100) NOT NULL,
    unit_snapshot          ENUM('Gram', 'Milliliter', 'Liter', 'Gallon', 'Piece') NOT NULL,
    qty                    DECIMAL(10, 3) NOT NULL,
    cost                   DECIMAL(10, 2) NOT NULL,
    supplier_id            INT NULL,
    supplier_name_snapshot VARCHAR(100) NULL,
    purchased_at           DATETIME NOT NULL,
    created_by_user_id     INT NULL,
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(supplier_id) ON DELETE SET NULL,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_material_restock_material_date (material_id, purchased_at),
    INDEX idx_material_restock_supplier_date (supplier_id, purchased_at),
    CHECK (qty > 0),
    CHECK (cost >= 0)
);

-- ----------------------------------------------------------------------------
-- 47. Migration — repeatable partial stock-request shipments
-- ----------------------------------------------------------------------------
ALTER TABLE stock_requests
    MODIFY COLUMN status ENUM('Pending','Partially Fulfilled','In Transit','Fulfilled','Rejected')
    NOT NULL DEFAULT 'Pending';

CREATE TABLE IF NOT EXISTS stock_request_shipments (
    shipment_id          INT AUTO_INCREMENT PRIMARY KEY,
    request_id           INT NOT NULL,
    status               ENUM('In Transit','Received') NOT NULL DEFAULT 'In Transit',
    dispatched_by_user_id INT NULL,
    dispatched_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at          TIMESTAMP NULL,
    FOREIGN KEY (request_id) REFERENCES stock_requests(request_id) ON DELETE CASCADE,
    FOREIGN KEY (dispatched_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_stock_request_shipments_request (request_id, status)
);

CREATE TABLE IF NOT EXISTS stock_request_shipment_items (
    shipment_item_id INT AUTO_INCREMENT PRIMARY KEY,
    shipment_id      INT NOT NULL,
    request_item_id  INT NOT NULL,
    dispatched_qty   INT NOT NULL,
    received_qty     INT NULL,
    damaged_qty      INT NOT NULL DEFAULT 0,
    FOREIGN KEY (shipment_id) REFERENCES stock_request_shipments(shipment_id) ON DELETE CASCADE,
    FOREIGN KEY (request_item_id) REFERENCES stock_request_items(item_id) ON DELETE CASCADE,
    UNIQUE KEY uq_stock_request_shipment_item (shipment_id, request_item_id),
    CHECK (dispatched_qty > 0),
    CHECK (received_qty IS NULL OR received_qty >= 0),
    CHECK (damaged_qty >= 0)
);

INSERT INTO stock_request_shipments (request_id, status, dispatched_at, received_at)
SELECT sr.request_id,
       IF(sr.status = 'In Transit', 'In Transit', 'Received'),
       sr.updated_at,
       IF(sr.status = 'In Transit', NULL, sr.updated_at)
FROM stock_requests sr
WHERE sr.status IN ('In Transit', 'Fulfilled')
  AND EXISTS (SELECT 1 FROM stock_request_items sri
              WHERE sri.request_id = sr.request_id AND COALESCE(sri.dispatched_qty, 0) > 0)
  AND NOT EXISTS (SELECT 1 FROM stock_request_shipments ss WHERE ss.request_id = sr.request_id);

INSERT INTO stock_request_shipment_items
    (shipment_id, request_item_id, dispatched_qty, received_qty, damaged_qty)
SELECT ss.shipment_id, sri.item_id, sri.dispatched_qty,
       IF(ss.status = 'Received', COALESCE(sri.received_qty, 0), NULL),
       IF(ss.status = 'Received', COALESCE(sri.damaged_qty, 0), 0)
FROM stock_request_shipments ss
JOIN stock_request_items sri ON sri.request_id = ss.request_id
WHERE COALESCE(sri.dispatched_qty, 0) > 0
  AND NOT EXISTS (SELECT 1 FROM stock_request_shipment_items ssi
                  WHERE ssi.shipment_id = ss.shipment_id AND ssi.request_item_id = sri.item_id);

-- Reopen receipts marked Fulfilled with less than requested so HQ can ship the rest.
UPDATE stock_requests sr
SET sr.status = 'Partially Fulfilled'
WHERE sr.status = 'Fulfilled'
  AND EXISTS (
      SELECT 1 FROM stock_request_items sri
      WHERE sri.request_id = sr.request_id
         AND COALESCE(sri.received_qty, 0) < sri.requested_qty
  );

-- ----------------------------------------------------------------------------
-- 48. Migration — product variants are Male or Female
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_products_remove_unisex_variant$$

CREATE PROCEDURE _migrate_products_remove_unisex_variant()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'products'
          AND column_name = 'variant'
          AND column_type LIKE '%Unisex%'
    ) THEN
        IF EXISTS (SELECT 1 FROM products WHERE variant = 'Unisex') THEN
            SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Products still use the Unisex variant; reclassify those products before applying this migration.';
        END IF;
        ALTER TABLE products MODIFY COLUMN variant ENUM('Male', 'Female') NOT NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_products_remove_unisex_variant();
DROP PROCEDURE _migrate_products_remove_unisex_variant;

-- ----------------------------------------------------------------------------
-- 49. Migration — stats performance + package orders dated by close
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_stats_indexes_and_closed_at$$

CREATE PROCEDURE _migrate_stats_indexes_and_closed_at()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND index_name = 'idx_sales_sold_at'
    ) THEN
        ALTER TABLE sales ADD INDEX idx_sales_sold_at (sold_at);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'partner_inquiries' AND column_name = 'closed_at'
    ) THEN
        ALTER TABLE partner_inquiries ADD COLUMN closed_at DATETIME NULL AFTER fulfilled_by_user_id;
        UPDATE partner_inquiries SET closed_at = COALESCE(fulfilled_at, created_at) WHERE status = 'Closed';
        ALTER TABLE partner_inquiries ADD INDEX idx_partner_inquiries_closed (closed_at);
    END IF;
END$$

DELIMITER ;

CALL _migrate_stats_indexes_and_closed_at();
DROP PROCEDURE _migrate_stats_indexes_and_closed_at;

-- ----------------------------------------------------------------------------
-- 50. Migration — built-in bulk batch formulas (the built-in formulas)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS batch_formula_materials (
    role        ENUM('oil', 'fixative', 'alcohol', 'moisturizer', 'water', 'bottle', 'sticker', 'seal') PRIMARY KEY,
    material_id INT NOT NULL,
    FOREIGN KEY (material_id) REFERENCES raw_materials(material_id)
);

CREATE TABLE IF NOT EXISTS bulk_formulas (
    formula_id INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(30) NOT NULL UNIQUE,      -- e.g. '15%'
    is_default BOOLEAN NOT NULL DEFAULT FALSE    -- the formula for every scent not in bulk_formula_scents
);

CREATE TABLE IF NOT EXISTS bulk_formula_scents (
    base_code  VARCHAR(40) PRIMARY KEY,          -- product base code, e.g. 'A9' (SKU A9-85ML)
    formula_id INT NOT NULL,
    FOREIGN KEY (formula_id) REFERENCES bulk_formulas(formula_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS bulk_formula_sizes (
    size_id        INT AUTO_INCREMENT PRIMARY KEY,
    formula_id     INT NOT NULL,
    oil_ml         DECIMAL(10, 3) NOT NULL,      -- the timpla size: ml of oil scent
    fixative_g     DECIMAL(10, 3) NOT NULL,
    mix_ml         DECIMAL(10, 3) NOT NULL,      -- alcohol + distilled water, together
    moisturizer_ml DECIMAL(10, 3) NOT NULL,
    FOREIGN KEY (formula_id) REFERENCES bulk_formulas(formula_id) ON DELETE CASCADE,
    UNIQUE KEY uq_bulk_formula_size (formula_id, oil_ml),
    CHECK (oil_ml > 0),
    CHECK (fixative_g >= 0),
    CHECK (mix_ml >= 0),
    CHECK (moisturizer_ml >= 0)
);

CREATE TABLE IF NOT EXISTS bulk_formula_settings (
    id                INT PRIMARY KEY,
    bottle_unit       ENUM('85ML', '50ML', '10ML', '3ML') NOT NULL DEFAULT '85ML',
    mix_alcohol_parts DECIMAL(10, 3) NOT NULL DEFAULT 1000.000,
    mix_water_parts   DECIMAL(10, 3) NOT NULL DEFAULT 200.000,
    CHECK (id = 1),
    CHECK (mix_alcohol_parts > 0),
    CHECK (mix_water_parts >= 0)
);

CREATE TABLE IF NOT EXISTS bulk_formula_packaging (
    role           ENUM('bottle', 'sticker', 'seal') PRIMARY KEY,
    qty_per_bottle DECIMAL(10, 3) NOT NULL DEFAULT 1.000,
    CHECK (qty_per_bottle >= 0)
);

-- Old drafts had a free-text scents column: add is_default (15% formula), drop it.
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_bulk_formulas_default$$

CREATE PROCEDURE _migrate_bulk_formulas_default()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_formulas' AND column_name = 'is_default'
    ) THEN
        ALTER TABLE bulk_formulas ADD COLUMN is_default BOOLEAN NOT NULL DEFAULT FALSE;
        UPDATE bulk_formulas SET is_default = TRUE WHERE name = '15%';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_formulas' AND column_name = 'scents'
    ) THEN
        ALTER TABLE bulk_formulas DROP COLUMN scents;
    END IF;
END$$

DELIMITER ;

CALL _migrate_bulk_formulas_default();
DROP PROCEDURE _migrate_bulk_formulas_default;

-- Seed: the owner's costing sheet. INSERT IGNORE, so
-- re-running never overwrites a value you have since changed.
INSERT IGNORE INTO bulk_formulas (name, is_default) VALUES
    ('15%', TRUE),
    ('14%', FALSE),
    ('13%', FALSE);

-- the owner's SKU Map: these scents use 14% and 13%; every other
-- scent uses the default (15%).
INSERT IGNORE INTO bulk_formula_scents (base_code, formula_id)
SELECT v.base_code, f.formula_id
FROM (
    SELECT 'A9' AS base_code, '14%' AS name
    UNION ALL SELECT 'H14', '14%'
    UNION ALL SELECT 'H13', '14%'
    UNION ALL SELECT 'H10', '14%'
    UNION ALL SELECT 'H2', '14%'
    UNION ALL SELECT 'H8', '13%'
    UNION ALL SELECT 'H9', '13%'
) v
JOIN bulk_formulas f ON f.name = v.name;

INSERT IGNORE INTO bulk_formula_sizes (formula_id, oil_ml, fixative_g, mix_ml, moisturizer_ml)
SELECT f.formula_id, v.oil_ml, v.fixative_g, v.mix_ml, v.moisturizer_ml
FROM (
    SELECT '15%' AS name, 500 AS oil_ml, 10 AS fixative_g, 2800 AS mix_ml, 26 AS moisturizer_ml
    UNION ALL SELECT '15%', 250, 5, 1400, 13
    UNION ALL SELECT '15%', 125, 3, 700, 7
    UNION ALL SELECT '14%', 500, 10, 3000, 26
    UNION ALL SELECT '14%', 250, 5, 1500, 13
    UNION ALL SELECT '14%', 125, 3, 750, 7
    UNION ALL SELECT '13%', 500, 10, 3300, 26
    UNION ALL SELECT '13%', 250, 5, 1650, 13
    UNION ALL SELECT '13%', 125, 3, 825, 7
) v
JOIN bulk_formulas f ON f.name = v.name;

INSERT IGNORE INTO bulk_formula_settings (id, bottle_unit, mix_alcohol_parts, mix_water_parts)
VALUES (1, '85ML', 1000, 200);

-- Mix is 1000 mL alcohol : 200 mL water; fix the old swapped seed (custom ratios kept).
ALTER TABLE bulk_formula_settings
    ALTER COLUMN mix_alcohol_parts SET DEFAULT 1000.000,
    ALTER COLUMN mix_water_parts SET DEFAULT 200.000;
UPDATE bulk_formula_settings
SET mix_alcohol_parts = 1000, mix_water_parts = 200
WHERE id = 1 AND mix_alcohol_parts = 200 AND mix_water_parts = 1000;

INSERT IGNORE INTO bulk_formula_packaging (role, qty_per_bottle) VALUES
    ('bottle', 1), ('sticker', 1), ('seal', 1);

DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_bulk_batches_formula_label$$

CREATE PROCEDURE _migrate_bulk_batches_formula_label()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches' AND column_name = 'formula_label'
    ) THEN
        ALTER TABLE bulk_batches ADD COLUMN formula_label VARCHAR(60) NULL;
    END IF;
END$$

DELIMITER ;

CALL _migrate_bulk_batches_formula_label();
DROP PROCEDURE _migrate_bulk_batches_formula_label;

-- ----------------------------------------------------------------------------
-- 50a. Migration — repair databases that ran an earlier version of 50
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_formula_materials_roles$$

CREATE PROCEDURE _migrate_formula_materials_roles()
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'batch_formula_materials'
          AND column_name = 'role' AND column_type NOT LIKE '%''seal''%'
    ) THEN
        DELETE FROM batch_formula_materials WHERE role = '';
        ALTER TABLE batch_formula_materials MODIFY COLUMN role
            ENUM('oil', 'fixative', 'alcohol', 'moisturizer', 'water', 'bottle', 'sticker', 'seal') NOT NULL;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches'
          AND constraint_name = 'fk_bulk_batches_formula'
    ) THEN
        ALTER TABLE bulk_batches DROP FOREIGN KEY fk_bulk_batches_formula;
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches' AND column_name = 'formula_id'
    ) THEN
        ALTER TABLE bulk_batches DROP COLUMN formula_id;
    END IF;
    DROP TABLE IF EXISTS batch_formula_items;
    DROP TABLE IF EXISTS batch_formulas;
END$$

DELIMITER ;

CALL _migrate_formula_materials_roles();
DROP PROCEDURE _migrate_formula_materials_roles;

-- ----------------------------------------------------------------------------
-- 51. Seed — suppliers and the constant raw materials the built-in formulas use
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO suppliers (supplier_name, address) VALUES
    ('BubbleMix Fragrances and Household Goods Store', '107 E. Rodriguez Sr. Ave., Brgy. Tatalon, Quezon City'),
    ('Centralpack', '58 F. Bautista St., San Francisco Del Monte, Quezon City'),
    ('Alfamart - Lipa', 'Lipa City, Batangas'),
    ('Shopee', NULL);

INSERT IGNORE INTO raw_materials
    (material_name, unit, purchase_mode, package_qty, package_cost, cost_per_unit, stock_qty, supplier_id)
VALUES
    ('Oil Scent', 'Milliliter', 'Package', 1, 0.00, 4.0000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'BubbleMix Fragrances and Household Goods Store')),
    ('Fixative', 'Gram', 'Package', 1, 0.00, 3.5000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'BubbleMix Fragrances and Household Goods Store')),
    ('Moisturizer', 'Milliliter', 'Package', 1, 0.00, 0.3100, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'BubbleMix Fragrances and Household Goods Store')),
    ('Perfume Alcohol', 'Milliliter', 'Package', 1, 0.00, 0.1563, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Centralpack')),
    ('Frosted 85 ml Bottle', 'Piece', 'Individual', 1, 0.00, 15.0000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Centralpack')),
    ('Distilled Water', 'Liter', 'Package', 1, 0.00, 8.2000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Alfamart - Lipa')),
    ('Sticker', 'Piece', 'Individual', 1, 0.00, 3.0000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Shopee')),
    ('Plastic Seal', 'Piece', 'Individual', 1, 0.00, 1.5000, 0,
        (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Shopee'));

-- Assign suppliers only where none is set, so Materials page choices stay.
UPDATE raw_materials SET supplier_id = (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Alfamart - Lipa')
WHERE material_name = 'Distilled Water' AND supplier_id IS NULL;
UPDATE raw_materials SET supplier_id = (SELECT supplier_id FROM suppliers WHERE supplier_name = 'Shopee')
WHERE material_name IN ('Sticker', 'Plastic Seal') AND supplier_id IS NULL;

INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'oil', material_id FROM raw_materials WHERE material_name = 'Oil Scent';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'fixative', material_id FROM raw_materials WHERE material_name = 'Fixative';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'alcohol', material_id FROM raw_materials WHERE material_name = 'Perfume Alcohol';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'moisturizer', material_id FROM raw_materials WHERE material_name = 'Moisturizer';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'water', material_id FROM raw_materials WHERE material_name = 'Distilled Water';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'bottle', material_id FROM raw_materials WHERE material_name = 'Frosted 85 ml Bottle';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'sticker', material_id FROM raw_materials WHERE material_name = 'Sticker';
INSERT IGNORE INTO batch_formula_materials (role, material_id)
SELECT 'seal', material_id FROM raw_materials WHERE material_name = 'Plastic Seal';

-- ----------------------------------------------------------------------------
-- 52. Seed — the product catalog (products_20260922_144849.csv)
-- ----------------------------------------------------------------------------
INSERT IGNORE INTO products (sku, item_name, variant, category, unit, price) VALUES
    ('A4-85ML', 'Abercrombie & Fitch', 'Male', 'Bottled', '85ML', 85.00),
    ('H10-85ML', 'Ariana Grande Cloud', 'Female', 'Bottled', '85ML', 85.00),
    ('H16-85ML', 'B&B Works Cucumber Melon', 'Female', 'Bottled', '85ML', 85.00),
    ('H8-85ML', 'B&B Works Gingham', 'Female', 'Bottled', '85ML', 85.00),
    ('A2-85ML', 'Bvlgari Extreme', 'Male', 'Bottled', '85ML', 85.00),
    ('H9-85ML', 'Chanel N5', 'Female', 'Bottled', '85ML', 85.00),
    ('A11-85ML', 'Christian Dior Fahrenheit', 'Male', 'Bottled', '85ML', 85.00),
    ('A8-85ML', 'Clinique Happy', 'Male', 'Bottled', '85ML', 85.00),
    ('H4-85ML', 'D''Arpage by Lanvin Eclat', 'Female', 'Bottled', '85ML', 85.00),
    ('H6-85ML', 'D&G Light Blue', 'Female', 'Bottled', '85ML', 85.00),
    ('A9-85ML', 'Drakkar Noir', 'Male', 'Bottled', '85ML', 85.00),
    ('H5-85ML', 'Escada Moonsparkle', 'Female', 'Bottled', '85ML', 85.00),
    ('A12-85ML', 'Ferrari Black', 'Male', 'Bottled', '85ML', 85.00),
    ('A5-85ML', 'Giorgio Armani Aqua De Gio', 'Male', 'Bottled', '85ML', 85.00),
    ('A7-85ML', 'Hugo Boss', 'Male', 'Bottled', '85ML', 85.00),
    ('H14-85ML', 'Jo Malone English Pear', 'Female', 'Bottled', '85ML', 85.00),
    ('H15-85ML', 'Jo Malone Nectarine', 'Female', 'Bottled', '85ML', 85.00),
    ('H2-85ML', 'Katy Perry Meow', 'Female', 'Bottled', '85ML', 85.00),
    ('A10-85ML', 'Lacoste Black', 'Male', 'Bottled', '85ML', 85.00),
    ('A14-85ML', 'Lacoste Essential', 'Male', 'Bottled', '85ML', 85.00),
    ('A1-85ML', 'Lacoste Red', 'Male', 'Bottled', '85ML', 85.00),
    ('H11-85ML', 'Lacoste Touch of Pink', 'Female', 'Bottled', '85ML', 85.00),
    ('H12-85ML', 'One Direction Our Moment', 'Female', 'Bottled', '85ML', 85.00),
    ('A6-85ML', 'Paco Rabbane Invictus', 'Male', 'Bottled', '85ML', 85.00),
    ('H7-85ML', 'Paris Hilton Heiress', 'Female', 'Bottled', '85ML', 85.00),
    ('A3-85ML', 'Ralph Lauren Polo Sport', 'Male', 'Bottled', '85ML', 85.00),
    ('H1-85ML', 'Salvatorre Ferragamo Incanto Shine', 'Female', 'Bottled', '85ML', 85.00),
    ('A13-85ML', 'Versace Eros', 'Male', 'Bottled', '85ML', 85.00),
    ('H3-85ML', 'Victoria''s Secret Bombshell', 'Female', 'Bottled', '85ML', 85.00),
    ('H17-85ML', 'Victoria''s Secret Pure Seduction', 'Female', 'Bottled', '85ML', 85.00),
    ('H13-85ML', 'Victoria''s Secret Vanilla Lace', 'Female', 'Bottled', '85ML', 85.00);

INSERT IGNORE INTO branch_inventory (branch_id, sku, stock_qty)
SELECT b.branch_id, p.sku, 0 FROM branches b CROSS JOIN products p;

-- ----------------------------------------------------------------------------
-- 54a. Default product photos
-- ----------------------------------------------------------------------------
UPDATE products p
JOIN (
    SELECT 'A1' AS code, 'img/scents/male/a1-zadkiel-thumb.webp' AS img
    UNION ALL SELECT 'A2', 'img/scents/male/a2-bvlgari-xtreme-thumb.webp'
    UNION ALL SELECT 'A3', 'img/scents/male/a3-raphael-thumb.webp'
    UNION ALL SELECT 'A4', 'img/scents/male/a4-nathaniel-thumb.webp'
    UNION ALL SELECT 'A5', 'img/scents/male/a5-gabriel-thumb.webp'
    UNION ALL SELECT 'A6', 'img/scents/male/a6-tabbris-thumb.webp'
    UNION ALL SELECT 'A7', 'img/scents/male/a7-ramiel-thumb.webp'
    UNION ALL SELECT 'A8', 'img/scents/male/a8-hadraniel-thumb.webp'
    UNION ALL SELECT 'A9', 'img/scents/male/a9-zuriel-thumb.webp'
    UNION ALL SELECT 'A10', 'img/scents/male/a10-afriel-thumb.webp'
    UNION ALL SELECT 'A11', 'img/scents/male/a11-kerubiel-thumb.webp'
    UNION ALL SELECT 'A12', 'img/scents/male/a12-michael-thumb.webp'
    UNION ALL SELECT 'A13', 'img/scents/male/a13-chamuel-thumb.webp'
    UNION ALL SELECT 'A14', 'img/scents/male/a14-barachiel-thumb.webp'
    UNION ALL SELECT 'H1', 'img/scents/female/h1-uriel-thumb.webp'
    UNION ALL SELECT 'H2', 'img/scents/female/h2-micah-thumb.webp'
    UNION ALL SELECT 'H3', 'img/scents/female/h3-raquel-thumb.webp'
    UNION ALL SELECT 'H4', 'img/scents/female/h4-dina-thumb.webp'
    UNION ALL SELECT 'H5', 'img/scents/female/h5-lailah-thumb.webp'
    UNION ALL SELECT 'H6', 'img/scents/female/h6-cassiel-thumb.webp'
    UNION ALL SELECT 'H7', 'img/scents/female/h7-azrael-thumb.webp'
    UNION ALL SELECT 'H8', 'img/scents/female/h8-sachael-thumb.webp'
    UNION ALL SELECT 'H9', 'img/scents/female/h9-jophiel-thumb.webp'
    UNION ALL SELECT 'H10', 'img/scents/female/h10-raziel-thumb.webp'
    UNION ALL SELECT 'H11', 'img/scents/female/h11-sariel-thumb.webp'
    UNION ALL SELECT 'H12', 'img/scents/female/h12-manakel-thumb.webp'
    UNION ALL SELECT 'H13', 'img/scents/female/h13-haniel-thumb.webp'
    UNION ALL SELECT 'H14', 'img/scents/female/h14-jeremiel-thumb.webp'
    UNION ALL SELECT 'H15', 'img/scents/female/h15-metatron-thumb.webp'
    UNION ALL SELECT 'H16', 'img/scents/female/h16-gavreel-thumb.webp'
    UNION ALL SELECT 'H17', 'img/scents/female/h17-ariel-thumb.webp'
) m ON m.code = LEFT(p.sku, CHAR_LENGTH(p.sku) - CHAR_LENGTH(p.unit) - 1)
SET p.image_path = m.img
WHERE p.image_path IS NULL OR p.image_path = ''
   OR p.image_path LIKE 'img/scents/%-bottle-m.webp';

-- ----------------------------------------------------------------------------
-- 55. Migration — bulk is sold from the bulk batches, not from BULK products
-- A batch is the scent's stock in mL, so separate BULK product rows are retired.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sale_batch_usage (
    usage_id    INT AUTO_INCREMENT PRIMARY KEY,
    sale_id     INT NOT NULL,
    batch_id    INT NOT NULL,
    ml          DECIMAL(12, 3) NOT NULL,
    cost_per_ml DECIMAL(12, 6) NOT NULL,
    FOREIGN KEY (sale_id) REFERENCES sales(sale_id) ON DELETE CASCADE,
    FOREIGN KEY (batch_id) REFERENCES bulk_batches(batch_id) ON DELETE CASCADE,
    INDEX idx_sale_batch_usage_batch (batch_id),
    CHECK (ml > 0)
);

DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_remove_bulk_products$$

CREATE PROCEDURE _migrate_remove_bulk_products()
BEGIN
    DECLARE fk_name VARCHAR(64) DEFAULT NULL;

    -- bulk_batches.base_code ---------------------------------------------------
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches' AND column_name = 'base_code'
    ) THEN
        ALTER TABLE bulk_batches
            ADD COLUMN base_code VARCHAR(40) NULL AFTER batch_code,
            ADD INDEX idx_bulk_batches_base_code (base_code, created_at);
        UPDATE bulk_batches b
        JOIN (SELECT DISTINCT LEFT(sku, CHAR_LENGTH(sku) - CHAR_LENGTH(unit) - 1) AS code, item_name
              FROM products WHERE unit <> 'BULK') x ON x.item_name = b.scent_name
        SET b.base_code = x.code
        WHERE b.base_code IS NULL;
    END IF;

    -- Link batches with no product code to the same-named catalog scent, every run.
    UPDATE bulk_batches b
    JOIN (SELECT DISTINCT LEFT(sku, CHAR_LENGTH(sku) - CHAR_LENGTH(unit) - 1) AS code, item_name
          FROM products) x ON x.item_name = b.scent_name
    SET b.base_code = x.code
    WHERE b.base_code IS NULL;

    -- sales.sku stops being a foreign key to products --------------------------
    SELECT constraint_name INTO fk_name
    FROM information_schema.key_column_usage
    WHERE table_schema = DATABASE() AND table_name = 'sales'
          AND column_name = 'sku' AND referenced_table_name = 'products'
    LIMIT 1;
    IF fk_name IS NOT NULL THEN
        SET @drop_fk = CONCAT('ALTER TABLE sales DROP FOREIGN KEY `', fk_name, '`');
        PREPARE drop_fk_stmt FROM @drop_fk;
        EXECUTE drop_fk_stmt;
        DEALLOCATE PREPARE drop_fk_stmt;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'sales'
              AND column_name = 'sku' AND seq_in_index = 1
    ) THEN
        ALTER TABLE sales ADD INDEX idx_sales_sku (sku);
    END IF;

    -- the BULK products and their stock rows go -----------------------------------
    DELETE FROM products WHERE unit = 'BULK' OR category = 'Bulk/Refill';

    -- products can no longer be BULK / Bulk/Refill ---------------------------------
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products'
              AND column_name = 'unit' AND column_type LIKE '%BULK%'
    ) THEN
        ALTER TABLE products MODIFY COLUMN unit ENUM('85ML', '50ML', '10ML', '3ML') NOT NULL DEFAULT '50ML';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'products'
              AND column_name = 'category' AND column_type LIKE '%Bulk/Refill%'
    ) THEN
        ALTER TABLE products MODIFY COLUMN category ENUM('Bottled') NOT NULL DEFAULT 'Bottled';
    END IF;
END$$

DELIMITER ;

CALL _migrate_remove_bulk_products();
DROP PROCEDURE _migrate_remove_bulk_products;

-- Products plus one virtual '<code>-BULK' row per product code, for joining
-- from sales (see the notes above). Never written to.
CREATE OR REPLACE VIEW sale_catalog AS
SELECT sku, item_name, variant, category, unit, price, image_path, created_at
FROM products
UNION ALL
SELECT CONCAT(b.code, '-BULK'), b.item_name, b.variant, 'Bulk/Refill', 'BULK', 0.00, NULL, NULL
FROM (SELECT LEFT(sku, CHAR_LENGTH(sku) - CHAR_LENGTH(unit) - 1) AS code,
             MIN(item_name) AS item_name, MIN(variant) AS variant
      FROM products GROUP BY code) b;

-- ----------------------------------------------------------------------------
-- 56. Migration — one order, several scents
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_sales_order_id$$

CREATE PROCEDURE _migrate_sales_order_id()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'sales' AND column_name = 'order_id'
    ) THEN
        ALTER TABLE sales ADD COLUMN order_id INT NULL, ADD INDEX idx_sales_order (order_id);
    END IF;
END$$

DELIMITER ;

CALL _migrate_sales_order_id();
DROP PROCEDURE _migrate_sales_order_id;

-- ----------------------------------------------------------------------------
-- 57. Migration — oil scent price and stock per scent
-- scent_oils holds one row per scent (price and stock).
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scent_oils (
    base_code     VARCHAR(40) PRIMARY KEY,
    scent_name    VARCHAR(150) NOT NULL,
    cost_per_unit DECIMAL(12, 6) NOT NULL DEFAULT 0,
    stock_qty     DECIMAL(14, 3) NOT NULL DEFAULT 0,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CHECK (cost_per_unit >= 0),
    CHECK (stock_qty >= 0)
);

CREATE TABLE IF NOT EXISTS scent_oil_restocks (
    restock_id         INT AUTO_INCREMENT PRIMARY KEY,
    base_code          VARCHAR(40) NOT NULL,
    qty                DECIMAL(14, 3) NOT NULL,
    cost               DECIMAL(12, 2) NOT NULL,
    purchased_at       DATE NOT NULL,
    created_by_user_id INT NULL,
    created_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (base_code) REFERENCES scent_oils(base_code) ON DELETE CASCADE,
    INDEX idx_scent_oil_restocks (base_code, purchased_at),
    CHECK (qty > 0),
    CHECK (cost >= 0)
);

-- ----------------------------------------------------------------------------
-- 58. Migration — a gallon is 3,200 mL
-- One gallon is 3,200 mL (not the US gallon).
-- ----------------------------------------------------------------------------
UPDATE bulk_batches
SET remaining_ml = GREATEST(0, remaining_ml + (input_qty * 3200 - total_volume_ml)),
    cost_per_ml  = ROUND(cost_per_ml * total_volume_ml / (input_qty * 3200), 6),
    total_volume_ml = input_qty * 3200
WHERE input_unit = 'Gallon' AND total_volume_ml <> input_qty * 3200;

-- Fix the opening alcohol import (9 gallons = 28,800 mL, not 34,068 mL) if it still
-- matches exactly; otherwise see scripts/gallon_stock_correction.py. Runs once.
UPDATE raw_materials
SET stock_qty = GREATEST(0, stock_qty - 5268.706),
    package_qty = 28800.000,
    cost_per_unit = ROUND(package_cost / 28800, 4)
WHERE material_name = 'Perfume Alcohol' AND unit = 'Milliliter'
  AND package_qty = 34068.706 AND package_cost = 4500.00;

-- ----------------------------------------------------------------------------
-- 59. Migration — chemist and labor, per bottle
-- ----------------------------------------------------------------------------
DELIMITER $$

DROP PROCEDURE IF EXISTS _migrate_chemist_labor$$

CREATE PROCEDURE _migrate_chemist_labor()
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_formula_settings' AND column_name = 'chemist_per_bottle'
    ) THEN
        ALTER TABLE bulk_formula_settings
            ADD COLUMN chemist_per_bottle DECIMAL(10, 2) NOT NULL DEFAULT 5.00,
            ADD COLUMN labor_per_bottle   DECIMAL(10, 2) NOT NULL DEFAULT 5.00;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = 'bulk_batches' AND column_name = 'chemist_cost'
    ) THEN
        ALTER TABLE bulk_batches
            ADD COLUMN chemist_cost DECIMAL(12, 2) NOT NULL DEFAULT 0.00,
            ADD COLUMN labor_cost   DECIMAL(12, 2) NOT NULL DEFAULT 0.00;
    END IF;
END$$

DELIMITER ;

CALL _migrate_chemist_labor();
DROP PROCEDURE _migrate_chemist_labor;
