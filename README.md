# Azure Train Data Project — iRail API

**Repository:** `challenge-azure`  
**Type:** Learning Project (Solo)  
**Duration:** 2 days  

---

## 🎯 Project Overview

This project implements a **real-world, cloud-native data pipeline** that ingests live Belgian train departure data from the **iRail API**, normalizes it, and stores it in an **Azure SQL Database**, using **Azure Functions (Python)** as the ingestion layer.

The end goal is to power a **near–real-time dashboard** (Power BI) that provides operational insights into train traffic, delays, and station activity in Belgium.

---

## 🧠 Project Vision

Using public transport data from iRail, this project demonstrates how to:

- Build a **serverless ingestion pipeline**
- Design a **SQL schema optimized for live dashboards**
- Handle **real-time updates + historical observations**
- Prepare data for **DirectQuery Power BI dashboards**
- Scale toward production-grade DevOps practices

---

## 🏗️ Architecture (Current State)

**Azure Services Used**

| Service | Purpose |
|------|------|
| Azure Function App (Python 3.10) | Serverless ingestion & API layer |
| Azure SQL Database | Persistent storage (stations, departures) |
| Azure Storage Account | Required Function App dependency |
| App Service Plan (Consumption) | Auto-scaling execution |
| Application Insights | Logging & monitoring |

**High-level flow**

```
iRail API
   ↓
Azure Function (HTTP / Timer)
   ↓
Azure SQL Database
   ↓
Power BI (DirectQuery / Liveboard)
```

---

## 📂 Repository Structure

```
challenge-azure/
├── function_app.py        # Azure Functions entrypoint
├── requirements.txt       # Python dependencies
├── host.json              # Azure Functions host config
├── README.md              # Project documentation
```

---

## 🔑 Environment Variables

These **must** be configured in the Function App → *Configuration* → *Application settings*.

| Variable | Description |
|------|------|
| `SqlConnectionString` | Full Azure SQL connection string |
| `IRAIL_USER_AGENT` | Required by iRail API (identify yourself) |
| `IRAIL_LANG` | API language (default: `en`) |
| `IRAIL_REQ_MIN_INTERVAL_SEC` | Min delay between API calls (rate limiting) |
| `IRAIL_WRITE_OBS` | `true/false` — enable historical observations |

Example `IRAIL_USER_AGENT`:
```
challenge-azure/0.1 (becode; your.email@domain)
```

---

## 🗄️ Database Design

### Core Tables

| Table | Purpose |
|-----|--------|
| `stations` | Master list of Belgian stations |
| `departures_latest` | Latest known state per train departure |
| `departures_obs` | Time-series observations (optional but powerful) |

### Design Rationale

- **`departures_latest`** is optimized for *live dashboards*
- **`departures_obs`** enables *delay trends, peak hours, reliability*
- MERGE logic ensures **idempotent ingestion**
- UTC timestamps everywhere for consistency

---

## 🚀 Azure Functions — Endpoints

### 1️⃣ Health Check

```
GET /api/ping
```

Simple availability check.

**Response**
```
ok-v7
```

---

### 2️⃣ Refresh Stations

Fetches and upserts **all Belgian stations** from iRail.

```
GET /api/refresh_stations
```

**Use case**
- Initial DB setup
- Periodic metadata refresh

---

### 3️⃣ Ingest Liveboards (Top Stations)

Fetches live departures for **top-N stations** and updates SQL.

```
GET /api/ingest_liveboard_top20?limit=20&write_obs=true
```

**Parameters**

| Param | Description |
|----|----|
| `limit` | Number of top stations (1–50) |
| `write_obs` | Store historical snapshots |

**What happens**
- Calls `/liveboard` endpoint per station
- MERGE into `departures_latest`
- Optionally INSERT into `departures_obs`
- Updates station `last_ingested_at_utc`

---

### 4️⃣ Liveboard API (SQL-backed)

Reads directly from SQL — **no iRail call**.

```
GET /api/liveboard?station_id=BE.NMBS.008821006
```

**Optional params**
- `n` (rows)
- `minutes_past`
- `minutes_future`

This endpoint is **Power BI–ready**.

---

### 5️⃣ Health / Monitoring Endpoint

```
GET /api/health
```

Returns:
- Min / max station ingestion times
- Latest observation timestamp
- Latest liveboard update timestamp

Extremely useful for:
- Debugging refresh issues
- Power BI monitoring
- Ops visibility

---

## ⏱️ Automation (Timer Trigger)

A **Timer-triggered Azure Function** runs automatically:

```
Every 10 minutes
```

Purpose:
- Keep liveboard data fresh
- Simulate real-time ingestion
- Enable DirectQuery dashboards


## 🧪 Testing & Validation

- Test endpoints via Azure Portal
- Validate SQL tables after ingestion
- Monitor logs in Application Insights
- Use `/health` for sanity checks



---

## 👤 Author

**Amine Samoudi**  
