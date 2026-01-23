# SQL layer: schema, views, and BI contract

## Purpose
This folder defines the **database contract** for the iRail pipeline:
- Core tables (latest state + historical observations)
- Curated SQL views used by Power BI (semantic layer)

---

## Contents
- `schema.sql` — table definitions (Azure SQL)
- `views.sql` — curated views for analytics / Power BI
- `indexes.sql` — (optional) performance indexes

---

## Core tables
- `dbo.stations`
- `dbo.departures_latest`
- `dbo.departures_obs`

---

## Power BI mapping
| Power BI Page | Source view(s) | Notes |
|---|---|---|
| LiveBoard & DelayMonitor | `dbo.v_pbi_dim_stations_top20` | Station dimension for slicers/joins (Top-20 active only) |
| LiveBoard | `dbo.v_pbi_liveboard_board` | Near real-time station board for next 24h (effective-time window, local time fields, status flags) |
| LiveBoard & DelayMonitor | `dbo.v_pbi_obs_agg_10m` | 10-minute buckets per station (departures, canceled, delayed>=1m, delay minutes sum) |
| LiveBoard & DelayMonitor | `dbo.v_pbi_obs_train_type_10m` | Train type distribution per station per 10-min bucket (joined from latest) |

---

## Data collection architecture (context)

### Ingestion pipeline
- Data is collected **every 10 minutes**.
- An **Azure Function (serverless)** calls the iRail API, normalizes the payload, and upserts into **Azure SQL Database**.
- Two granularities are stored:
  - **Latest state per departure** (`departures_latest`)
  - **Observed time series** (`departures_obs`)

This supports both:
- **Near real-time monitoring**
- **Historical trend analysis**

---

## Database & infrastructure (high level)
- **Engine:** Azure SQL Database (Single Database)
- **Region:** West Europe (project default)


---

## Entity Relationship Diagram (ERD)

The database is composed of **three core tables** with inferred foreign keys.

* `stations.station_id` is the **reference entity**
* `origin_station_id` in both departure tables maps to `stations.station_id`

```
stations
  └──< departures_latest
  └──< departures_obs
```

![ERD](erd.png)

---

## Tables description

> Some fields may be null or constant depending on iRail API availability and update frequency.

### 1) `stations`
Static metadata for Belgian railway stations.

**Primary key:** `station_id`

| Column | Type | Description |
|---|---|---|
| station_id | varchar | Unique station identifier |
| uri | varchar | iRail station URI |
| standardname | varchar | Normalized station name |
| name | varchar | Display name |
| longitude | decimal | Longitude (WGS84) |
| latitude | decimal | Latitude (WGS84) |
| is_top20 | boolean | Whether station is in Top 20 by traffic |
| top_rank | int | Rank among major stations |
| active | boolean | Operational status |
| last_ingested_at_utc | datetime | Last API ingestion timestamp (UTC) |
| created_at_utc | datetime | Record creation time (UTC) |
| updated_at_utc | datetime | Last update time (UTC) |

---

### 2) `departures_latest`
Latest known state of each departure (one row per train & planned time).

**Primary key (composite):**
- `origin_station_id`
- `vehicle_id`
- `planned_time_utc`

**Foreign key (logical):**
- `origin_station_id → stations.station_id`

| Column | Type | Description |
|---|---|---|
| origin_station_id | varchar | Origin station |
| vehicle_id | varchar | Train identifier |
| planned_time_utc | datetime | Scheduled departure time (UTC) |
| dest_station_id | varchar | Destination station ID |
| dest_station_name | varchar | Destination name |
| vehicle_shortname | varchar | Human-readable train number |
| train_type | varchar | IC / S / L / etc. |
| platform | varchar | Platform number |
| delay_sec | int | Delay in seconds |
| canceled | boolean | Cancellation flag |
| has_left | boolean | Whether train departed |
| occupancy_name | varchar | Occupancy level |
| departure_connection | varchar | iRail connection URL |
| api_timestamp_utc | datetime | API response timestamp (UTC) |
| first_seen_at_utc | datetime | First detection time (UTC) |
| last_seen_at_utc | datetime | Last update time (UTC) |
| realtime_time_utc | datetime | Real-time adjusted departure (UTC) |

---

### 3) `departures_obs`
Historical observations of departures captured every 10 minutes.

**Primary key (composite):**
- `origin_station_id`
- `vehicle_id`
- `planned_time_utc`
- `observed_at_utc`

**Foreign key (logical):**
- `origin_station_id → stations.station_id`

| Column | Type | Description |
|---|---|---|
| origin_station_id | varchar | Origin station |
| vehicle_id | varchar | Train identifier |
| planned_time_utc | datetime | Scheduled departure (UTC) |
| observed_at_utc | datetime | Observation timestamp (UTC) |
| delay_sec | int | Delay at observation time (seconds) |
| canceled | boolean | Cancellation status |
| has_left | boolean | Departure state |
| platform | varchar | Platform |
| occupancy_name | varchar | Occupancy |

---

## Notes & limitations
- This SQL layer supports both **near real-time** and **historical** analytics.
- API-based data may include late updates or temporary inconsistencies.
- Foreign keys are **logically inferred** (not necessarily enforced at DB level).

---

## License & attribution
- Data source: iRail API (documentation): https://docs.irail.be/
- Intended use: education, analytics, and visualization.
- Please respect iRail API usage policies when redistributing.

---

## Author
**Amine Samoudi**
- GitHub: [@AmineSam](https://github.com/AmineSam)