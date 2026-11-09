-- Database and Medallion schemas. Idempotent.
CREATE DATABASE IF NOT EXISTS SPC_PLATFORM COMMENT = 'Historical SPC analytics platform';
CREATE SCHEMA IF NOT EXISTS SPC_PLATFORM.METADATA    COMMENT = 'Application metadata: users, roles, plants, assets, uploads, history';
CREATE SCHEMA IF NOT EXISTS SPC_PLATFORM.BRONZE      COMMENT = 'Raw uploaded historical data, as received';
CREATE SCHEMA IF NOT EXISTS SPC_PLATFORM.SILVER      COMMENT = 'Cleaned, validated, standardized data';
CREATE SCHEMA IF NOT EXISTS SPC_PLATFORM.GOLD        COMMENT = 'Analysis-ready historical data';
CREATE SCHEMA IF NOT EXISTS SPC_PLATFORM.SPC_RESULTS COMMENT = 'Stored SPC calculation results';
