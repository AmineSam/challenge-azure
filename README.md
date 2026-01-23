# Azure Pipeline (iRail API → Azure SQL → Power BI)

## Overview
This module implements a cloud-native ingestion pipeline for Belgian rail departures using the **iRail API**.
An **Azure Function App (Python)** ingests liveboard data on a schedule (and via HTTP endpoints), normalizes it, and stores it in **Azure SQL Database**.

Primary output: a near real-time dataset and a curated **SQL semantic layer** powering Power BI dashboards (DirectQuery or Import).

## Architecture
Services:
- Azure Function App (Python)
- Azure SQL Database
- Azure Storage Account (Function dependency)
- Application Insights (logs/metrics)

Data flow:
```
iRail API
   ↓
Azure Function (HTTP / Timer)
   ↓
Azure SQL (stations, departures_latest, departures_obs)
   ↓
SQL Views (Power BI semantic layer)
   ↓
Power BI (DirectQuery / Liveboard)
```
---

## Repository structure (pipeline module)

```
pipeline/
├── function_app.py # Azure Functions entrypoint
├── requirements.txt # Python dependencies
├── host.json # Azure Functions host config
├── local.settings.json # Local only
├──README.md
└── sql/ # DB schema + views (semantic layer)
   └─ERD
   └─schema.sql
   └─views.sql
   └─README.md
   
```

---
## Azure-independent demo
If the Azure deployment is unavailable (credits expired), the project still remains reviewable via:
- A portable dataset snapshot (csv)
- Power BI screenshots

See:
- `../data/` for a portable snapshot
- `./sql/` for schema and curated views used by Power BI
---
## Environment Variables

These **must** be configured in the Function App → *Configuration* → *Application settings*.

| Variable | Description |
|------|------|
| `SqlConnectionString` | Full Azure SQL connection string |
| `IRAIL_USER_AGENT` | Required by iRail API (identify yourself) |
| `IRAIL_LANG` | API language (default: `en`) |
| `IRAIL_REQ_MIN_INTERVAL_SEC` | Min delay between API calls (rate limiting) |
| `IRAIL_WRITE_OBS` | `true/false` — enable historical observations |

---

## Azure Functions — Endpoints

### Health Check

```
GET /api/ping
```

Simple availability check.

**Response**
```
ok-v7
```

---

### Refresh Stations

Fetches and upserts **all Belgian stations** from iRail.

```
GET /api/refresh_stations
```

**Use case**
- Initial DB setup
- Periodic metadata refresh

---

### Ingest Liveboards (Top Stations)

Fetches live departures for **top-N stations** and updates SQL.

```
GET /api/ingest_liveboard_top20?limit=20&write_obs=true
```

**Parameters**

| Param | Description |
|----|----|
| `limit` | Number of top stations (1–50) |
| `write_obs` | Store historical snapshots |

Behavior:
- Calls iRail liveboard per station
- Upserts `departures_latest`
- Optionally inserts into `departures_obs`
- Updates `stations.last_ingested_at_utc`

---

### SQL-backed liveboard (no iRail call)

`GET /api/liveboard?station_id=BE.NMBS.008821006`

Optional params:
- `n` (rows)
- `minutes_past`
- `minutes_future`

Intended usage: Power BI-ready read API backed by SQL.

---
### Monitoring summary

`GET /api/health` 

Returns ingestion freshness indicators (e.g., last observation time, station ingest range), useful to monitor refresh drift.

---
## Automation (timer trigger)
A timer-triggered function runs automatically:
- Frequency: every **10 minutes**
- Purpose: keep `departures_latest` fresh and optionally grow `departures_obs`

## Local development (optional)
Prerequisites:
- Python 3.10+
- Azure Functions Core Tools

Run locally:
1) Create `local.settings.json` (do not commit)
2) Install deps
3) Start Functions host

Notes:
- Local runs are best for endpoint testing and schema validation.
- For portfolio review, the offline snapshot + SQL views are sufficient.

## Notes & limitations
- API data can include late updates and temporary inconsistencies.
- Some fields may be missing depending on iRail availability and station updates.
- All timestamps are stored in UTC; views may include local time conversions for Belgium.
---

## 👤 Author

**Amine Samoudi**
- GitHub: [@AmineSam](https://github.com/AmineSam)
