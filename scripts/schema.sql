-- The corpus is regenerated as one deterministic unit. Dropping in reverse dependency
-- order makes reruns replace the schema exactly, so removed columns and stale rows cannot
-- survive from an older generator version.
DROP TABLE IF EXISTS ops.claim_payments CASCADE;
DROP TABLE IF EXISTS ops.claims CASCADE;
DROP TABLE IF EXISTS ops.policies CASCADE;
DROP TABLE IF EXISTS ops.adjusters CASCADE;
DROP TABLE IF EXISTS ops.customers CASCADE;
DROP TABLE IF EXISTS ops.coverage_products CASCADE;
DROP TABLE IF EXISTS ops.regions CASCADE;

CREATE TABLE ops.regions (
    region_id integer PRIMARY KEY,
    region_name text NOT NULL UNIQUE,
    city text NOT NULL,
    province text NOT NULL
);

CREATE TABLE ops.adjusters (
    adjuster_id integer PRIMARY KEY,
    adjuster_name text NOT NULL,
    region_id integer NOT NULL,
    seniority text NOT NULL,
    is_active boolean NOT NULL,
    FOREIGN KEY (region_id) REFERENCES ops.regions (region_id),
    CHECK (seniority IN ('junior', 'senior', 'lead'))
);

CREATE TABLE ops.customers (
    customer_id bigint PRIMARY KEY,
    customer_name text NOT NULL,
    customer_type text NOT NULL,
    city text NOT NULL,
    region_id integer NOT NULL,
    email text,
    created_at timestamp without time zone NOT NULL,
    FOREIGN KEY (region_id) REFERENCES ops.regions (region_id),
    CHECK (customer_type IN ('individual', 'business'))
);

CREATE TABLE ops.coverage_products (
    product_id integer PRIMARY KEY,
    product_code text NOT NULL UNIQUE,
    product_name text NOT NULL UNIQUE,
    policy_document text NOT NULL,
    base_deductible_pkr numeric NOT NULL,
    coverage_limit_pkr numeric NOT NULL,
    CHECK (base_deductible_pkr >= 0),
    CHECK (coverage_limit_pkr >= 0)
);

CREATE TABLE ops.policies (
    policy_id bigint PRIMARY KEY,
    policy_number text NOT NULL UNIQUE,
    customer_id bigint NOT NULL,
    product_id integer NOT NULL,
    region_id integer NOT NULL,
    inception_date date NOT NULL,
    expiry_date date NOT NULL,
    status text NOT NULL,
    sum_insured_pkr numeric NOT NULL,
    deductible_pkr numeric NOT NULL,
    annual_premium_pkr numeric NOT NULL,
    FOREIGN KEY (customer_id) REFERENCES ops.customers (customer_id),
    FOREIGN KEY (product_id) REFERENCES ops.coverage_products (product_id),
    FOREIGN KEY (region_id) REFERENCES ops.regions (region_id),
    CHECK (status IN ('active', 'lapsed', 'cancelled')),
    CHECK (inception_date <= expiry_date),
    CHECK (sum_insured_pkr >= 0),
    CHECK (deductible_pkr >= 0),
    CHECK (annual_premium_pkr >= 0)
);

CREATE TABLE ops.claims (
    claim_id bigint PRIMARY KEY,
    claim_number text NOT NULL UNIQUE,
    policy_id bigint NOT NULL,
    adjuster_id integer,
    region_id integer NOT NULL,
    peril text NOT NULL,
    date_of_loss date NOT NULL,
    report_date date NOT NULL,
    closed_date date,
    status text NOT NULL,
    cause_description text NOT NULL,
    incurred_amount_pkr numeric NOT NULL,
    paid_amount_pkr numeric NOT NULL,
    reserve_amount_pkr numeric NOT NULL,
    deductible_applied_pkr numeric NOT NULL,
    FOREIGN KEY (policy_id) REFERENCES ops.policies (policy_id),
    FOREIGN KEY (adjuster_id) REFERENCES ops.adjusters (adjuster_id),
    FOREIGN KEY (region_id) REFERENCES ops.regions (region_id),
    CHECK (peril IN ('water_damage', 'fire', 'theft', 'storm', 'impact', 'liability')),
    CHECK (status IN ('open', 'closed', 'reopened', 'denied', 'withdrawn')),
    CHECK (incurred_amount_pkr = paid_amount_pkr + reserve_amount_pkr),
    CHECK (paid_amount_pkr <= incurred_amount_pkr),
    CHECK (incurred_amount_pkr >= 0),
    CHECK (paid_amount_pkr >= 0),
    CHECK (reserve_amount_pkr >= 0),
    CHECK (deductible_applied_pkr >= 0),
    CHECK (date_of_loss <= report_date),
    CHECK (report_date <= closed_date)
);

CREATE TABLE ops.claim_payments (
    payment_id bigint PRIMARY KEY,
    claim_id bigint NOT NULL,
    payment_date date NOT NULL,
    payment_type text NOT NULL,
    amount_pkr numeric NOT NULL,
    FOREIGN KEY (claim_id) REFERENCES ops.claims (claim_id),
    CHECK (payment_type IN ('indemnity', 'expense', 'recovery'))
);

CREATE INDEX adjusters_region_id_idx ON ops.adjusters (region_id);
CREATE INDEX adjusters_seniority_idx ON ops.adjusters (seniority);

CREATE INDEX customers_customer_name_idx ON ops.customers (customer_name);
CREATE INDEX customers_customer_type_idx ON ops.customers (customer_type);
CREATE INDEX customers_city_idx ON ops.customers (city);
CREATE INDEX customers_region_id_idx ON ops.customers (region_id);

CREATE INDEX coverage_products_policy_document_idx ON ops.coverage_products (policy_document);

CREATE INDEX policies_customer_id_idx ON ops.policies (customer_id);
CREATE INDEX policies_product_id_idx ON ops.policies (product_id);
CREATE INDEX policies_region_id_idx ON ops.policies (region_id);
CREATE INDEX policies_inception_date_idx ON ops.policies (inception_date);
CREATE INDEX policies_expiry_date_idx ON ops.policies (expiry_date);
CREATE INDEX policies_status_idx ON ops.policies (status);

CREATE INDEX claims_policy_id_idx ON ops.claims (policy_id);
CREATE INDEX claims_adjuster_id_idx ON ops.claims (adjuster_id);
CREATE INDEX claims_region_id_idx ON ops.claims (region_id);
CREATE INDEX claims_peril_idx ON ops.claims (peril);
CREATE INDEX claims_date_of_loss_idx ON ops.claims (date_of_loss);
CREATE INDEX claims_report_date_idx ON ops.claims (report_date);
CREATE INDEX claims_status_idx ON ops.claims (status);

CREATE INDEX claim_payments_claim_id_idx ON ops.claim_payments (claim_id);
CREATE INDEX claim_payments_payment_date_idx ON ops.claim_payments (payment_date);
CREATE INDEX claim_payments_payment_type_idx ON ops.claim_payments (payment_type);
