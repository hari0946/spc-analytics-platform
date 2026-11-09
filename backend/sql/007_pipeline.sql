-- Phase 7: BRONZE -> SILVER -> GOLD pipeline support
ALTER TABLE SPC_PLATFORM.METADATA.DATASET_UPLOADS ADD COLUMN IF NOT EXISTS PROCESSING_REPORT VARIANT;

-- Additional table (explained in the Phase 7 report): rows that fail validation or are removed as duplicates,
-- with the reason, so nothing is silently dropped.
CREATE TABLE IF NOT EXISTS SPC_PLATFORM.SILVER.REJECTED_RECORDS (
    UPLOAD_ID   VARCHAR NOT NULL,
    SOURCE_ROW  NUMBER(38,0) NOT NULL,
    PARAMETER   VARCHAR,                     -- sensor measurement concerned (wide layout can reject one cell of a row)
    REASON      VARCHAR NOT NULL,
    RAW_RECORD  VARIANT,
    REJECTED_AT TIMESTAMP_NTZ NOT NULL DEFAULT CURRENT_TIMESTAMP()
)
CLUSTER BY (UPLOAD_ID);

-- In wide layouts one source row yields several measurements, so PARAMETER joins the (informational) key.
ALTER TABLE SPC_PLATFORM.SILVER.CLEAN_SENSOR_DATA DROP PRIMARY KEY;
ALTER TABLE SPC_PLATFORM.SILVER.CLEAN_SENSOR_DATA ADD CONSTRAINT PK_CLEAN_SENSOR PRIMARY KEY (UPLOAD_ID, SOURCE_ROW, PARAMETER);
