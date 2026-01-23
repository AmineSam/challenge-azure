/* =========================================================
   File: sql/views.sql
   Purpose: Curated views powering Power BI dashboards
   DB: Azure SQL 
   Time: all source timestamps are UTC; local conversions use
         'Central European Standard Time' (Belgium, DST-safe)
   Dependencies:
     - dbo.stations
     - dbo.departures_latest
     - dbo.departures_obs
     - dbo.v_pbi_dim_stations_top20 (used by liveboard view)
   ========================================================= */

DROP VIEW IF EXISTS dbo.v_pbi_obs_train_type_10m;
DROP VIEW IF EXISTS dbo.v_pbi_obs_agg_10m;
DROP VIEW IF EXISTS dbo.v_pbi_liveboard_board;
DROP VIEW IF EXISTS dbo.v_pbi_dim_stations_top20;

/* ---------------------------
   DIMENSIONS
--------------------------- */

-- Top-20 active stations dimension (Power BI slicers/joins)
CREATE VIEW dbo.v_pbi_dim_stations_top20 AS
SELECT
    s.station_id,
    COALESCE(
        NULLIF(LTRIM(RTRIM(s.standardname)), ''),
        s.station_id
    ) AS station_name,
    s.name AS station_name_alt,
    s.longitude,
    s.latitude,
    s.top_rank,
    s.last_ingested_at_utc,
    s.active,
    s.is_top20
FROM dbo.stations AS s
WHERE
    s.is_top20 = 1
    AND s.active = 1;


/* ---------------------------
   LIVEBOARD (near real-time)
--------------------------- */
-- Station board view for next 24h using an "effective_time" inclusion window

CREATE VIEW dbo.v_pbi_liveboard_board AS
WITH base AS (
    SELECT
        dl.origin_station_id,
        st.station_name AS origin_station_name,
        dl.planned_time_utc,
        dl.realtime_time_utc,
        dl.delay_sec,
        CAST(dl.delay_sec / 60.0 AS decimal(10,2)) AS delay_min,

        -- EFFECTIVE time for inclusion:
        -- use realtime when present, else planned + delay
        -- (so late trains remain if still in future)
        COALESCE(
            dl.realtime_time_utc,
            DATEADD(SECOND, ISNULL(dl.delay_sec, 0), dl.planned_time_utc)
        ) AS effective_time_utc,

        dl.dest_station_name,
        dl.vehicle_shortname,
        dl.train_type,
        dl.platform,
        dl.canceled,
        dl.api_timestamp_utc,
        dl.first_seen_at_utc,
        dl.last_seen_at_utc
    FROM dbo.departures_latest AS dl
    JOIN dbo.v_pbi_dim_stations_top20 AS st
        ON st.station_id = dl.origin_station_id
)
SELECT
    b.*,

    -- Local scheduled time (displayed in the board)
    (b.planned_time_utc AT TIME ZONE 'UTC'
        AT TIME ZONE 'Central European Standard Time') AS planned_time_local,

    -- Local effective time (used for filtering / tooltip)
    (b.effective_time_utc AT TIME ZONE 'UTC'
        AT TIME ZONE 'Central European Standard Time') AS effective_time_local,

    (b.last_seen_at_utc AT TIME ZONE 'UTC'
        AT TIME ZONE 'Central European Standard Time') AS last_seen_local,

    -- Display HH:mm for scheduled time (station-board style)
    CONVERT(
        varchar(5),
        CAST(
            (b.planned_time_utc AT TIME ZONE 'UTC'
                AT TIME ZONE 'Central European Standard Time')
            AS datetime2
        ),
        108
    ) AS dep_hhmm_local,

    -- "+12" badge (blank if < 1 minute)
    CASE
        WHEN ISNULL(b.delay_sec, 0) >= 60
            THEN CONCAT('+', CAST(CEILING(b.delay_sec / 60.0) AS int))
        ELSE ''
    END AS delay_badge,

    -- Flags
    CASE
        WHEN b.last_seen_at_utc >= DATEADD(MINUTE, -60, SYSUTCDATETIME())
            THEN 1
        ELSE 0
    END AS is_recent,

    -- Status
    CASE
        WHEN b.canceled = 1
            THEN 'Canceled'
        WHEN ISNULL(b.delay_sec, 0) >= 60
            THEN 'Delayed'
        ELSE 'On time'
    END AS status,

    CASE
        WHEN b.canceled = 1 THEN 1
        WHEN ISNULL(b.delay_sec, 0) >= 60 THEN 2
        ELSE 3
    END AS status_rank
FROM base AS b
WHERE
    -- prevent zombie rows
    b.last_seen_at_utc >= DATEADD(MINUTE, -60, SYSUTCDATETIME())

    -- strict board window using EFFECTIVE time
    AND b.effective_time_utc >= DATEADD(MINUTE, -2, SYSUTCDATETIME())
    AND b.effective_time_utc <= DATEADD(HOUR, 24, SYSUTCDATETIME());


/* ---------------------------
   AGGREGATIONS (10-minute buckets)
--------------------------- */
-- Aggregated operational KPIs per station per 10-min bucket (UTC + local)

CREATE VIEW dbo.v_pbi_obs_agg_10m AS
SELECT
    o.origin_station_id,

    -- 10-minute bucket (UTC)
    DATEADD(
        MINUTE,
        (DATEDIFF(MINUTE, '20000101', o.observed_at_utc) / 10) * 10,
        '20000101'
    ) AS bucket_10m_utc,

    -- 10-minute bucket (Local Belgium, DST-safe)
    CAST(
        (
            DATEADD(
                MINUTE,
                (DATEDIFF(MINUTE, '20000101', o.observed_at_utc) / 10) * 10,
                '20000101'
            ) AT TIME ZONE 'UTC'
              AT TIME ZONE 'Central European Standard Time'
        ) AS datetime2
    ) AS bucket_10m_local,

    COUNT_BIG(*) AS departures_cnt,
    SUM(CASE WHEN o.canceled = 1 THEN 1 ELSE 0 END) AS canceled_cnt,

    -- delayed >= 1 minute and not canceled
    SUM(
        CASE
            WHEN o.canceled = 0 AND ISNULL(o.delay_sec, 0) >= 60 THEN 1
            ELSE 0
        END
    ) AS delayed_1m_cnt,

    -- sum of delay minutes for delayed trains only (not canceled)
    SUM(
        CASE
            WHEN o.canceled = 0 AND ISNULL(o.delay_sec, 0) >= 60
                THEN ISNULL(o.delay_sec, 0) / 60.0
            ELSE 0
        END
    ) AS delayed_delay_min_sum
FROM dbo.departures_obs AS o
GROUP BY
    o.origin_station_id,
    DATEADD(
        MINUTE,
        (DATEDIFF(MINUTE, '20000101', o.observed_at_utc) / 10) * 10,
        '20000101'
    );


-- Train type distribution per station per 10-min bucket (joined from latest)

CREATE VIEW dbo.v_pbi_obs_train_type_10m AS
SELECT
    o.origin_station_id,
    DATEADD(
        MINUTE,
        (DATEDIFF(MINUTE, '20000101', o.observed_at_utc) / 10) * 10,
        '20000101'
    ) AS bucket_10m_utc,
    COALESCE(NULLIF(LTRIM(RTRIM(dl.train_type)), ''), 'UNK') AS train_type,
    COUNT_BIG(*) AS cnt
FROM dbo.departures_obs AS o
LEFT JOIN dbo.departures_latest AS dl
    ON  dl.origin_station_id = o.origin_station_id
    AND dl.vehicle_id = o.vehicle_id
    AND dl.planned_time_utc = o.planned_time_utc
GROUP BY
    o.origin_station_id,
    DATEADD(
        MINUTE,
        (DATEDIFF(MINUTE, '20000101', o.observed_at_utc) / 10) * 10,
        '20000101'
    ),
    COALESCE(NULLIF(LTRIM(RTRIM(dl.train_type)), ''), 'UNK');

/* Power BI usage mapping (example)
   - v_pbi_dim_stations_top20: station slicers / station dimension
   - v_pbi_liveboard_board: live departures board page
   - v_pbi_obs_agg_10m: trends (delay/cancel KPIs over time)
   - v_pbi_obs_train_type_10m: composition by train type
*/
