CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS transactions;
CREATE SCHEMA IF NOT EXISTS payments;
CREATE SCHEMA IF NOT EXISTS accounts;
CREATE SCHEMA IF NOT EXISTS ledger;

COMMENT ON SCHEMA auth IS 'Owned by the authentication service';
COMMENT ON SCHEMA transactions IS 'Owned by the transaction service';
COMMENT ON SCHEMA payments IS 'Owned by the payment service';
COMMENT ON SCHEMA accounts IS 'Owned by the account service';
COMMENT ON SCHEMA ledger IS 'Owned by the ledger service';
