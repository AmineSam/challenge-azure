/* =========================================================
   iRail schema (Azure SQL) 
   File: sql/schema.sql
   Tables: stations, departures_latest, departures_obs
   ========================================================= */

CREATE TABLE dbo.stations (
    station_id           VARCHAR(32)      NOT NULL,
    uri                  VARCHAR(255)     NULL,
    standardname         VARCHAR(255)     NULL,
    name                 VARCHAR(255)     NULL,
    longitude            DECIMAL(9,6)     NULL,
    latitude             DECIMAL(9,6)     NULL,

    is_top20             BIT              NOT NULL CONSTRAINT DF_stations_is_top20 DEFAULT (0),
    top_rank             INT              NULL,
    active               BIT              NOT NULL CONSTRAINT DF_stations_active DEFAULT (1),

    last_ingested_at_utc DATETIME2(0)     NULL,
    created_at_utc       DATETIME2(0)     NOT NULL CONSTRAINT DF_stations_created_at_utc DEFAULT (SYSUTCDATETIME()),
    updated_at_utc       DATETIME2(0)     NULL,

    CONSTRAINT PK_stations PRIMARY KEY CLUSTERED (station_id)
);

CREATE TABLE dbo.departures_latest (
    origin_station_id      VARCHAR(32)   NOT NULL,
    vehicle_id             VARCHAR(64)   NOT NULL,
    planned_time_utc       DATETIME2(0)  NOT NULL,

    dest_station_id        VARCHAR(32)   NULL,
    dest_station_name      VARCHAR(255)  NULL,
    vehicle_shortname      VARCHAR(64)   NULL,
    train_type             VARCHAR(64)   NULL,
    platform               VARCHAR(32)   NULL,

    delay_sec              INT           NULL,
    canceled               BIT           NOT NULL CONSTRAINT DF_departures_latest_canceled DEFAULT (0),
    has_left               BIT           NOT NULL CONSTRAINT DF_departures_latest_has_left DEFAULT (0),
    occupancy_name         VARCHAR(32)   NULL,

    departure_connection   VARCHAR(255)  NULL,
    api_timestamp_utc      DATETIME2(0)  NULL,
    first_seen_at_utc      DATETIME2(0)  NULL,
    last_seen_at_utc       DATETIME2(0)  NULL,
    realtime_time_utc      DATETIME2(0)  NULL,

    CONSTRAINT PK_departures_latest
        PRIMARY KEY CLUSTERED (origin_station_id, vehicle_id, planned_time_utc),

    CONSTRAINT FK_departures_latest_stations
        FOREIGN KEY (origin_station_id) REFERENCES dbo.stations (station_id)
);

CREATE TABLE dbo.departures_obs (
    origin_station_id   VARCHAR(32)   NOT NULL,
    vehicle_id          VARCHAR(64)   NOT NULL,
    planned_time_utc    DATETIME2(0)  NOT NULL,
    observed_at_utc     DATETIME2(0)  NOT NULL,

    delay_sec           INT           NULL,
    canceled            BIT           NOT NULL CONSTRAINT DF_departures_obs_canceled DEFAULT (0),
    has_left            BIT           NOT NULL CONSTRAINT DF_departures_obs_has_left DEFAULT (0),
    platform            VARCHAR(32)   NULL,
    occupancy_name      VARCHAR(32)   NULL,

    CONSTRAINT PK_departures_obs
        PRIMARY KEY CLUSTERED (origin_station_id, vehicle_id, planned_time_utc, observed_at_utc),

    CONSTRAINT FK_departures_obs_stations
        FOREIGN KEY (origin_station_id) REFERENCES dbo.stations (station_id)
);
