CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS transactions;
CREATE SCHEMA IF NOT EXISTS payments;

COMMENT ON SCHEMA auth IS 'Owned by the authentication service';
COMMENT ON SCHEMA transactions IS 'Owned by the transaction service';
COMMENT ON SCHEMA payments IS 'Owned by the payment service';
