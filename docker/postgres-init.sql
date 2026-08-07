-- Base de datos separada para la suite de pruebas.
--
-- Las pruebas corren contra PostgreSQL real y no sobre SQLite: AC-01 exige que
-- modificar `agreed_total_cents` falle también por SQL directo, y eso lo
-- sostiene un trigger de PostgreSQL que ningún motor embebido reproduce.
CREATE DATABASE receipt_test OWNER receipt;
