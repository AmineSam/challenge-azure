import json
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import azure.functions as func
import pymssql

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

MERGE_SQL = """
MERGE dbo.departures AS t
USING (SELECT %s AS origin_station, %s AS vehicle, %s AS planned_time_utc) AS s
ON  t.origin_station   = s.origin_station
AND t.vehicle          = s.vehicle
AND t.planned_time_utc = s.planned_time_utc
WHEN MATCHED THEN
	UPDATE SET
		t.destination_station   = %s,
		t.vehicle_shortname     = %s,
		t.platform              = %s,
		t.delay_sec             = %s,
		t.canceled              = %s,
		t.has_left              = %s,
		t.departure_connection  = %s,
		t.occupancy             = %s,
		t.api_timestamp_utc     = %s
WHEN NOT MATCHED THEN
	INSERT (
		origin_station,
		destination_station,
		vehicle,
		vehicle_shortname,
		platform,
		planned_time_utc,
		delay_sec,
		canceled,
		has_left,
		departure_connection,
		occupancy,
		api_timestamp_utc
	)
	VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
"""

def _dt_utc_sql_from_unix(ts) -> str | None:
	if ts is None:
		return None
	try:
		dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(microsecond=0)
		# SQL datetime2 parses ISO without timezone fine
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

def _origin_station(payload: dict) -> str:
	stationinfo = payload.get("stationinfo") or {}
	if isinstance(stationinfo, dict):
		name = stationinfo.get("standardname") or stationinfo.get("name")
		if isinstance(name, str) and name.strip():
			return name.strip()
	station = payload.get("station")
	if isinstance(station, str) and station.strip():
		return station.strip()
	return "Antwerpen-Centraal"

def _clean_platform(p) -> str | None:
	if p is None:
		return None
	s = str(p).strip()
	if s in ("", "?", "unknown", "null", "None"):
		return None
	return s

def _parse_sql_conn_string(cs: str) -> dict:
	"""
	Parse an ADO-style Azure SQL conn string like:
	Server=tcp:xxx.database.windows.net,1433;Database=yyy;User ID=uuu;Password=ppp;Encrypt=True;...
	"""
	def _get(key: str) -> str | None:
		m = re.search(rf"{re.escape(key)}\s*=\s*([^;]+)", cs, flags=re.IGNORECASE)
		return m.group(1).strip() if m else None

	server = _get("Server") or _get("Data Source")
	database = _get("Database") or _get("Initial Catalog")
	user = _get("User ID") or _get("UID") or _get("User")
	password = _get("Password") or _get("PWD")

	if not (server and database and user and password):
		raise ValueError("SqlConnectionString missing one of: Server, Database, User ID, Password")

	# normalize server
	server = server.replace("tcp:", "").strip()
	# remove optional port suffix ",1433"
	server = server.split(",")[0].strip()

	return {"server": server, "database": database, "user": user, "password": password}

def _get_conn() -> pymssql.Connection:
	cs = os.environ["SqlConnectionString"]
	p = _parse_sql_conn_string(cs)

	# Azure SQL requires encryption; pymssql uses TDS and does not expose the same Encrypt flag.
	# For this learning project, this is acceptable; for production you'd use ODBC or msal + managed identity.
	return pymssql.connect(
		server=p["server"],
		user=p["user"],
		password=p["password"],
		database=p["database"],
		login_timeout=10,
		timeout=30,
		autocommit=False,
	)

@app.route(route="ping", methods=["GET"])
def ping(req: func.HttpRequest) -> func.HttpResponse:
	return func.HttpResponse("ok-v6", status_code=200)

@app.function_name(name="IngestLiveboardV6")
@app.route(route="ingest_liveboard_v6", methods=["GET"])
def ingest_liveboard_v6(req: func.HttpRequest) -> func.HttpResponse:
	try:
		station_id = os.environ.get("IRAIL_STATION_ID", "BE.NMBS.008821006")
		user_agent = os.environ.get("IRAIL_USER_AGENT", "challenge-azure/1.0")

		params = {
			"id": station_id,
			"arrdep": "departure",
			"format": "json",
			"lang": "en",
			"alerts": "false",
		}
		api_url = "https://api.irail.be/liveboard/?" + urlencode(params)

		req_http = Request(api_url, headers={"Accept": "application/json", "User-Agent": user_agent}, method="GET")
		with urlopen(req_http, timeout=20) as resp:
			payload = json.loads(resp.read().decode("utf-8"))

		origin_station = _origin_station(payload)
		api_ts = _dt_utc_sql_from_unix(payload.get("timestamp"))

		deps = ((payload.get("departures") or {}).get("departure") or [])
		if isinstance(deps, dict):
			deps = [deps]
		if not isinstance(deps, list):
			deps = []

		rows = []
		seen = set()

		for d in deps:
			if not isinstance(d, dict):
				continue

			planned_time = _dt_utc_sql_from_unix(d.get("time"))
			vehicle = d.get("vehicle")
			if planned_time is None or not vehicle:
				continue

			key = (origin_station, vehicle, planned_time)
			if key in seen:
				continue
			seen.add(key)

			rows.append(
				{
					"origin_station": origin_station,
					"destination_station": d.get("station"),
					"vehicle": vehicle,
					"vehicle_shortname": ((d.get("vehicleinfo") or {}).get("shortname")) if d.get("vehicleinfo") else None,
					"platform": _clean_platform(d.get("platform")),
					"planned_time_utc": planned_time,
					"delay_sec": _int(d.get("delay"), 0),
					"canceled": _bit01(d.get("canceled")),
					"has_left": _bit01(d.get("left")),
					"departure_connection": d.get("departureConnection"),
					"occupancy": ((d.get("occupancy") or {}).get("name")) if d.get("occupancy") else None,
					"api_timestamp_utc": api_ts,
				}
			)

		upsert_attempt = 0
		with _get_conn() as conn:
			cur = conn.cursor()
			for r in rows:
				params = (
					# USING match key
					r["origin_station"], r["vehicle"], r["planned_time_utc"],
					# UPDATE values
					r["destination_station"], r["vehicle_shortname"], r["platform"],
					r["delay_sec"], r["canceled"], r["has_left"],
					r["departure_connection"], r["occupancy"], r["api_timestamp_utc"],
					# INSERT values
					r["origin_station"], r["destination_station"], r["vehicle"], r["vehicle_shortname"],
					r["platform"], r["planned_time_utc"], r["delay_sec"], r["canceled"], r["has_left"],
					r["departure_connection"], r["occupancy"], r["api_timestamp_utc"],
				)
				cur.execute(MERGE_SQL, params)
				upsert_attempt += 1
			conn.commit()

		return func.HttpResponse(
			json.dumps(
				{
					"route": "ingest_liveboard_v6",
					"station_id": station_id,
					"origin_station": origin_station,
					"fetched_departures": len(deps),
					"upsert_attempt": upsert_attempt,
				}
			),
			status_code=200,
			mimetype="application/json",
		)

	except Exception as e:
		logging.exception("ingest_liveboard_v6 failed")
		return func.HttpResponse(f"{type(e).__name__}: {e}", status_code=500)
