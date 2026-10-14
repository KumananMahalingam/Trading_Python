-- Add virtual trading balance to users (default $100,000)

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS balance NUMERIC(18,2) NOT NULL DEFAULT 100000.00;
