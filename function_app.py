import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import azure.functions as func
import pymssql

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

# ============================================================
# SQL
# ============================================================

MERGE_STATION_SQL = """
MERGE dbo.stations AS t
USING (SELECT %s AS station_id) AS s
ON t.station_id = s.station_id
WHEN MATCHED THEN
	UPDATE SET
		t.uri            = %s,
		t.standardname   = %s,
		t.name           = %s,
		t.longitude      = %s,
		t.latitude       = %s,
		t.updated_at_utc = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
	INSERT (
		station_id, uri, standardname, name, longitude, latitude,
		is_top20, top_rank, active,
		last_ingested_at_utc,
		created_at_utc, updated_at_utc
	)
	VALUES (
		%s, %s, %s, %s, %s, %s,
		0, NULL, 1,
		NULL,
		SYSUTCDATETIME(), SYSUTCDATETIME()
	);
"""

SELECT_TOP20_SQL = """
SELECT TOP (%s) station_id
FROM dbo.stations
WHERE is_top20 = 1 AND active = 1
ORDER BY
	CASE WHEN top_rank IS NULL THEN 999 ELSE top_rank END ASC,
	CASE WHEN last_ingested_at_utc IS NULL THEN 0 ELSE 1 END ASC,
	last_ingested_at_utc ASC;
"""

UPDATE_STATION_LAST_INGEST_SQL = """
UPDATE dbo.stations
SET last_ingested_at_utc = SYSUTCDATETIME(),
	updated_at_utc = SYSUTCDATETIME()
WHERE station_id = %s;
"""

MERGE_DEPARTURE_LATEST_SQL = """
MERGE dbo.departures_latest AS t
USING (SELECT %s AS origin_station_id, %s AS vehicle_id, %s AS planned_time_utc) AS s
ON  t.origin_station_id = s.origin_station_id
AND t.vehicle_id        = s.vehicle_id
AND t.planned_time_utc  = s.planned_time_utc
WHEN MATCHED THEN
	UPDATE SET
		t.dest_station_id       = %s,
		t.dest_station_name     = %s,
		t.vehicle_shortname     = %s,
		t.train_type            = %s,
		t.platform              = %s,
		t.delay_sec             = %s,
		t.canceled              = %s,
		t.has_left              = %s,
		t.occupancy_name        = %s,
		t.departure_connection  = %s,
		t.api_timestamp_utc     = %s,
		t.last_seen_at_utc      = SYSUTCDATETIME()
WHEN NOT MATCHED THEN
	INSERT (
		origin_station_id, vehicle_id, planned_time_utc,
		dest_station_id, dest_station_name,
		vehicle_shortname, train_type, platform,
		delay_sec, canceled, has_left,
		occupancy_name, departure_connection,
		api_timestamp_utc,
		first_seen_at_utc, last_seen_at_utc
	)
	VALUES (
		%s, %s, %s,
		%s, %s,
		%s, %s, %s,
		%s, %s, %s,
		%s, %s,
		%s,
		SYSUTCDATETIME(), SYSUTCDATETIME()
	);
"""

INSERT_OBS_SQL = """
INSERT INTO dbo.departures_obs (
	origin_station_id, vehicle_id, planned_time_utc, observed_at_utc,
	delay_sec, canceled, has_left, platform, occupancy_name
)
VALUES (
	%s, %s, %s, SYSUTCDATETIME(),
	%s, %s, %s, %s, %s
);
"""

LIVEBOARD_READ_SQL = """
SELECT TOP (%s)
	planned_time_utc,
	realtime_time_utc,
	dest_station_id,
	dest_station_name,
	vehicle_id,
	vehicle_shortname,
	train_type,
	platform,
	delay_sec,
	canceled,
	has_left,
	occupancy_name,
	api_timestamp_utc,
	first_seen_at_utc,
	last_seen_at_utc
FROM dbo.departures_latest
WHERE origin_station_id = %s
  AND planned_time_utc >= DATEADD(MINUTE, -%s, SYSUTCDATETIME())
  AND planned_time_utc <= DATEADD(MINUTE,  %s, SYSUTCDATETIME())
ORDER BY planned_time_utc ASC;
"""

HEALTH_SQL = """
SELECT
	-- stations
	MIN(last_ingested_at_utc) AS stations_last_ingested_min_utc,
	MAX(last_ingested_at_utc) AS stations_last_ingested_max_utc,

	-- obs
	(SELECT MAX(observed_at_utc) FROM dbo.departures_obs) AS departures_obs_last_observed_utc,

	-- latest
	(SELECT MAX(last_seen_at_utc) FROM dbo.departures_latest) AS departures_latest_last_seen_utc
FROM dbo.stations
WHERE is_top20 = 1 AND active = 1;
"""


# ============================================================
# Helpers
# ============================================================

def _dt_utc_sql_from_unix(ts) -> str | None:
	if ts is None:
		return None
	try:
		dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0)
		return dt.strftime("%Y-%m-%dT%H:%M:%S")
	except Exception:
		return None

def _int(x, default=0) -> int:
	try:
		return int(x)
	except Exception:
		return default

def _bit01(x) -> int:
	try:
		return 1 if int(x) else 0
	except Exception:
		return 0

def _clean_platform(p) -> str | None:
	if p is None:
		return None
	s = str(p).strip()
	if s in ("", "?", "unknown", "null", "None"):
		return None
	return s

def _train_type_from_shortname(shortname: str | None) -> str | None:
	if not shortname:
		return None
	m = re.match(r"^([A-Za-z]+)", shortname.strip())
	return m.group(1).upper() if m else None

def _parse_sql_conn_string(cs: str) -> dict:
	def _get(key: str) -> str | None:
		m = re.search(rf"{re.escape(key)}\s*=\s*([^;]+)", cs, flags=re.IGNORECASE)
		return m.group(1).strip() if m else None

	server = _get("Server") or _get("Data Source")
	database = _get("Database") or _get("Initial Catalog")
	user = _get("User ID") or _get("UID") or _get("User")
	password = _get("Password") or _get("PWD")

	if not (server and database and user and password):
		raise ValueError("SqlConnectionString missing one of: Server, Database, User ID, Password")

	server = server.replace("tcp:", "").strip()
	server = server.split(",")[0].strip()

	return {"server": server, "database": database, "user": user, "password": password}

def _get_conn() -> pymssql.Connection:
	cs = os.environ["SqlConnectionString"]
	p = _parse_sql_conn_string(cs)
	return pymssql.connect(
		server=p["server"],
		user=p["user"],
		password=p["password"],
		database=p["database"],
		login_timeout=10,
		timeout=30,
		autocommit=False,
	)

def _http_json(url: str, *, user_agent: str, timeout: int = 20) -> dict:
	req_http = Request(url, headers={"Accept": "application/json", "User-Agent": user_agent}, method="GET")
	with urlopen(req_http, timeout=timeout) as resp:
		return json.loads(resp.read().decode("utf-8"))

def _env_bool(name: str, default: bool = False) -> bool:
	v = (os.environ.get(name, "") or "").strip().lower()
	if v == "":
		return default
	return v in ("1", "true", "yes", "y", "on")

def _safe_str(x) -> str | None:
	if x is None:
		return None
	if isinstance(x, str):
		return x
	return str(x)

# ============================================================
# Core: refresh stations
# ============================================================

def refresh_stations_core(*, lang: str, user_agent: str) -> dict:
	url = "https://api.irail.be/stations/?" + urlencode({"format": "json", "lang": lang})
	payload = _http_json(url, user_agent=user_agent)

	raw = payload.get("station")
	if isinstance(raw, dict):
		stations = [raw]
	elif isinstance(raw, list):
		stations = raw
	else:
		stations = []

	upserted = 0
	skipped = 0

	with _get_conn() as conn:
		cur = conn.cursor()
		for s in stations:
			if not isinstance(s, dict):
				skipped += 1
				continue

			station_id = s.get("id")
			if not isinstance(station_id, str) or not station_id.strip():
				skipped += 1
				continue
			station_id = station_id.strip()

			uri = _safe_str(s.get("@id"))
			standardname = _safe_str(s.get("standardname") or s.get("name") or station_id) or station_id
			name = _safe_str(s.get("name"))

			lon = s.get("locationX")
			lat = s.get("locationY")
			try:
				lon = float(lon) if lon is not None else None
			except Exception:
				lon = None
			try:
				lat = float(lat) if lat is not None else None
			except Exception:
				lat = None

			params_merge = (
				station_id,
				uri, standardname, name, lon, lat,  # update set
				station_id, uri, standardname, name, lon, lat,  # insert values
			)
			cur.execute(MERGE_STATION_SQL, params_merge)
			upserted += 1

		conn.commit()

	return {"stations_seen": len(stations), "upserted": upserted, "skipped": skipped}

# ============================================================
# Core: ingest liveboards for top stations
# ============================================================

def ingest_liveboards_top_core(
	*,
	limit: int,
	lang: str,
	user_agent: str,
	min_interval_sec: float,
	write_obs: bool,
) -> dict:
	start = time.time()

	with _get_conn() as conn:
		cur = conn.cursor()
		cur.execute(SELECT_TOP20_SQL, (limit,))
		station_ids = [row[0] for row in cur.fetchall()]

	stations_ok = 0
	stations_failed = []
	deps_fetched_total = 0
	upsert_attempt_total = 0
	obs_insert_total = 0

	# Use one DB connection for the entire ingest (faster), commit per station (safer)
	with _get_conn() as conn:
		cur = conn.cursor()

		for idx, station_id in enumerate(station_ids):
			if idx > 0:
				time.sleep(max(0.0, min_interval_sec))

			try:
				api_url = "https://api.irail.be/liveboard/?" + urlencode(
					{
						"id": station_id,
						"arrdep": "departure",
						"format": "json",
						"lang": lang,
						"alerts": "false",
					}
				)
				payload = _http_json(api_url, user_agent=user_agent)
				api_ts = _dt_utc_sql_from_unix(payload.get("timestamp"))

				deps = ((payload.get("departures") or {}).get("departure") or [])
				if isinstance(deps, dict):
					deps = [deps]
				if not isinstance(deps, list):
					deps = []

				deps_fetched_total += len(deps)

				seen = set()
				rows = []

				for d in deps:
					if not isinstance(d, dict):
						continue

					planned_time = _dt_utc_sql_from_unix(d.get("time"))
					vehicle_id = d.get("vehicle")

					if planned_time is None or not isinstance(vehicle_id, str) or not vehicle_id.strip():
						continue
					vehicle_id = vehicle_id.strip()

					key = (station_id, vehicle_id, planned_time)
					if key in seen:
						continue
					seen.add(key)

					dest_info = d.get("stationinfo") or {}
					dest_station_id = dest_info.get("id") if isinstance(dest_info, dict) else None
					dest_station_name = _safe_str(d.get("station"))

					vehicleinfo = d.get("vehicleinfo") or {}
					vehicle_shortname = vehicleinfo.get("shortname") if isinstance(vehicleinfo, dict) else None
					vehicle_shortname = _safe_str(vehicle_shortname)
					train_type = _train_type_from_shortname(vehicle_shortname)

					occupancy = d.get("occupancy") or {}
					occupancy_name = occupancy.get("name") if isinstance(occupancy, dict) else None
					occupancy_name = _safe_str(occupancy_name)

					departure_connection = _safe_str(d.get("departureConnection"))

					rows.append(
						{
							"origin_station_id": station_id,
							"vehicle_id": vehicle_id,
							"planned_time_utc": planned_time,
							"dest_station_id": _safe_str(dest_station_id),
							"dest_station_name": dest_station_name,
							"vehicle_shortname": vehicle_shortname,
							"train_type": train_type,
							"platform": _clean_platform(d.get("platform")),
							"delay_sec": _int(d.get("delay"), 0),
							"canceled": _bit01(d.get("canceled")),
							"has_left": _bit01(d.get("left")),
							"occupancy_name": occupancy_name,
							"departure_connection": departure_connection,
							"api_timestamp_utc": api_ts,
						}
					)

				for r in rows:
					params_merge = (
						# match key
						r["origin_station_id"],
						r["vehicle_id"],
						r["planned_time_utc"],
						# update set
						r["dest_station_id"],
						r["dest_station_name"],
						r["vehicle_shortname"],
						r["train_type"],
						r["platform"],
						r["delay_sec"],
						r["canceled"],
						r["has_left"],
						r["occupancy_name"],
						r["departure_connection"],
						r["api_timestamp_utc"],
						# insert values
						r["origin_station_id"],
						r["vehicle_id"],
						r["planned_time_utc"],
						r["dest_station_id"],
						r["dest_station_name"],
						r["vehicle_shortname"],
						r["train_type"],
						r["platform"],
						r["delay_sec"],
						r["canceled"],
						r["has_left"],
						r["occupancy_name"],
						r["departure_connection"],
						r["api_timestamp_utc"],
					)
					cur.execute(MERGE_DEPARTURE_LATEST_SQL, params_merge)
					upsert_attempt_total += 1

					if write_obs:
						cur.execute(
							INSERT_OBS_SQL,
							(
								r["origin_station_id"],
								r["vehicle_id"],
								r["planned_time_utc"],
								r["delay_sec"],
								r["canceled"],
								r["has_left"],
								r["platform"],
								r["occupancy_name"],
							),
						)
						obs_insert_total += 1

				# only update last_ingested if station succeeded
				cur.execute(UPDATE_STATION_LAST_INGEST_SQL, (station_id,))
				conn.commit()
				stations_ok += 1

			except Exception as e:
				conn.rollback()
				logging.exception("Station ingest failed: %s", station_id)
				stations_failed.append({"station_id": station_id, "error": f"{type(e).__name__}: {e}"})

	dur = time.time() - start
	return {
		"stations_targeted": len(station_ids),
		"stations_ok": stations_ok,
		"stations_failed": stations_failed,
		"departures_fetched_total": deps_fetched_total,
		"upsert_attempt_total": upsert_attempt_total,
		"obs_insert_total": obs_insert_total,
		"duration_sec": round(dur, 3),
	}

# ============================================================
# HTTP endpoints
# ============================================================

@app.route(route="ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
	return func.HttpResponse("ok-v7", status_code=200)

@app.function_name(name="RefreshStations")
@app.route(route="refresh_stations", methods=["GET"])
def refresh_stations(req: func.HttpRequest) -> func.HttpResponse:
	try:
		lang = req.params.get("lang") or os.environ.get("IRAIL_LANG", "en")
		user_agent = os.environ.get("IRAIL_USER_AGENT", "challenge-azure/1.0 (add-contact-email)")

		res = refresh_stations_core(lang=lang, user_agent=user_agent)
		return func.HttpResponse(json.dumps({"route": "refresh_stations", **res}), status_code=200, mimetype="application/json")
	except Exception as e:
		logging.exception("refresh_stations failed")
		return func.HttpResponse(f"{type(e).__name__}: {e}", status_code=500)

@app.function_name(name="IngestLiveboardTop20")
@app.route(route="ingest_liveboard_top20", methods=["GET"])
def ingest_liveboard_top20(req: func.HttpRequest) -> func.HttpResponse:
	try:
		user_agent = os.environ.get("IRAIL_USER_AGENT", "challenge-azure/1.0 (add-contact-email)")
		lang = req.params.get("lang") or os.environ.get("IRAIL_LANG", "en")

		limit = _int(req.params.get("limit"), 20)
		limit = max(1, min(limit, 50))

		min_interval_sec = os.environ.get("IRAIL_REQ_MIN_INTERVAL_SEC", "0.40")
		try:
			min_interval_sec = float(min_interval_sec)
		except Exception:
			min_interval_sec = 0.40

		write_obs_q = (req.params.get("write_obs") or "").strip().lower()
		write_obs = write_obs_q in ("1", "true", "yes") or (write_obs_q == "" and _env_bool("IRAIL_WRITE_OBS", False))

		res = ingest_liveboards_top_core(
			limit=limit,
			lang=lang,
			user_agent=user_agent,
			min_interval_sec=min_interval_sec,
			write_obs=write_obs,
		)

		return func.HttpResponse(
			json.dumps({"route": "ingest_liveboard_top20", "limit": limit, "write_obs": write_obs, **res}),
			status_code=200,
			mimetype="application/json",
		)
	except Exception as e:
		logging.exception("ingest_liveboard_top20 failed")
		return func.HttpResponse(f"{type(e).__name__}: {e}", status_code=500)

@app.function_name(name="Liveboard")
@app.route(route="liveboard", methods=["GET"])
def liveboard(req: func.HttpRequest) -> func.HttpResponse:
	try:
		station_id = (req.params.get("station_id") or "").strip()
		if not station_id:
			return func.HttpResponse("Missing query param: station_id", status_code=400)

		n = _int(req.params.get("n"), 20)
		n = max(1, min(n, 100))

		min_past = _int(req.params.get("minutes_past"), 60)
		min_future = _int(req.params.get("minutes_future"), 180)
		min_past = max(0, min(min_past, 24 * 60))
		min_future = max(0, min(min_future, 24 * 60))

		with _get_conn() as conn:
			cur = conn.cursor(as_dict=True)
			cur.execute(LIVEBOARD_READ_SQL, (n, station_id, min_past, min_future))
			rows = cur.fetchall()

		return func.HttpResponse(
			json.dumps({"station_id": station_id, "n": n, "rows": rows}, default=str),
			status_code=200,
			mimetype="application/json",
		)

	except Exception as e:
		logging.exception("liveboard failed")
		return func.HttpResponse(f"{type(e).__name__}: {e}", status_code=500)

@app.function_name(name="TimerIngestTop20")
@app.timer_trigger(schedule="0 */10 * * * *", arg_name="timer", run_on_startup=False, use_monitor=True)
def timer_ingest_top20(timer: func.TimerRequest) -> None:
	try:
		user_agent = os.environ.get("IRAIL_USER_AGENT", "challenge-azure/0.1 (becode; amine@becode.education)")
		lang = os.environ.get("IRAIL_LANG", "en")

		limit = 20
		min_interval_sec = float(os.environ.get("IRAIL_REQ_MIN_INTERVAL_SEC", "0.40"))
		write_obs = _env_bool("IRAIL_WRITE_OBS", False)

		res = ingest_liveboards_top_core(
			limit=limit,
			lang=lang,
			user_agent=user_agent,
			min_interval_sec=min_interval_sec,
			write_obs=write_obs,
		)
		logging.info("Timer ingest done: %s", json.dumps(res))
	except Exception:
		logging.exception("Timer ingest failed")

@app.function_name(name="Health")
@app.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
	try:
		with _get_conn() as conn:
			cur = conn.cursor(as_dict=True)
			cur.execute(HEALTH_SQL)
			row = cur.fetchone() or {}

		# small extras that help debugging
		row_out = {
			"route": "health",
			"utc_now": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
			**row,
		}

		return func.HttpResponse(
			json.dumps(row_out, default=str),
			status_code=200,
			mimetype="application/json",
		)

	except Exception as e:
		logging.exception("health failed")
		return func.HttpResponse(f"{type(e).__name__}: {e}", status_code=500)
