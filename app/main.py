# app/main.py

import asyncio
import contextlib
import os
import time
import re
import json
import logging
import unicodedata
from typing import Set, Dict, Any, Optional, Deque, Tuple, List
from collections import deque
import copy

import httpx
import xmltodict

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

# ---------- Optional .env loader ----------
def load_dotenv(path: str = ".env"):
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                if not key or key in os.environ:
                    continue
                val = val.strip().strip('"').strip("'")
                os.environ[key] = val
    except FileNotFoundError:
        return
    except Exception as e:
        logging.warning(f"Could not load .env file {path}: {e}")


load_dotenv()

# ---------- Optional YAML config ----------
def load_yaml_config():
    path = os.getenv("CONFIG_FILE")
    if not path:
        return {}
    try:
        import yaml  # requires PyYAML in requirements.txt
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logging.warning(f"Could not load CONFIG_FILE {path}: {e}")
        return {}

_cfg = load_yaml_config()

def cfg_get(name: str, default: Any):
    # order: YAML -> ENV -> default
    if name in _cfg:
        return _cfg[name]
    val = os.getenv(name)
    if val is None:
        return default
    # coerce types similar to default
    if isinstance(default, bool):
        return str(val).lower() == "true"
    if isinstance(default, int):
        try:
            return int(val)
        except Exception:
            return default
    return val

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------- Config ----------
N3FJP_HOST = cfg_get("N3FJP_HOST", "127.0.0.1")
N3FJP_PORT = cfg_get("N3FJP_PORT", 1100)

# Added: secondary N3FJP connection on port 1000 (configurable)
SECONDARY_HOST = cfg_get("SECONDARY_HOST", N3FJP_HOST)
SECONDARY_PORT = cfg_get("SECONDARY_PORT", 1000)

N3FJP_CONNECTIONS: List[Dict[str, Any]] = [
    {"name": "primary", "host": N3FJP_HOST, "port": N3FJP_PORT},
]
if SECONDARY_HOST:
    N3FJP_CONNECTIONS.append({"name": "secondary", "host": SECONDARY_HOST, "port": SECONDARY_PORT})

# Bidirectional forwarding between two TCP ports (default: 1000 <-> 1001)
BRIDGE_HOST = cfg_get("BRIDGE_HOST", SECONDARY_HOST or N3FJP_HOST)
BRIDGE_PORT_A = cfg_get("BRIDGE_PORT_A", 1000)
BRIDGE_PORT_B = cfg_get("BRIDGE_PORT_B", 1001)
ENABLE_PORT_BRIDGE = cfg_get("ENABLE_PORT_BRIDGE", True)
PORT_FORWARD_RULES: Dict[int, int] = {}
if ENABLE_PORT_BRIDGE and BRIDGE_PORT_A and BRIDGE_PORT_B:
    PORT_FORWARD_RULES[BRIDGE_PORT_A] = BRIDGE_PORT_B
    PORT_FORWARD_RULES[BRIDGE_PORT_B] = BRIDGE_PORT_A

existing_ports = {int(conn.get("port", 0)) for conn in N3FJP_CONNECTIONS}
if ENABLE_PORT_BRIDGE and BRIDGE_HOST:
    for p in (BRIDGE_PORT_A, BRIDGE_PORT_B):
        if p and p not in existing_ports:
            N3FJP_CONNECTIONS.append({"name": f"bridge_{p}", "host": BRIDGE_HOST, "port": p})
            existing_ports.add(p)

WFD_MODE = cfg_get("WFD_MODE", False)
PREFER_SECTION_ALWAYS = cfg_get("PREFER_SECTION_ALWAYS", False)
TTL_SECONDS = cfg_get("TTL_SECONDS", 600)
BAND_FILTER = set([b.strip() for b in str(cfg_get("BAND_FILTER", "")).split(",") if b.strip()])
MODE_FILTER = set([m.strip().upper() for m in str(cfg_get("MODE_FILTER", "")).split(",") if m.strip()])

HEARTBEAT_SECONDS = max(3, cfg_get("HEARTBEAT_SECONDS", 5))

QRZ_USERNAME = cfg_get("QRZ_USERNAME", "")
QRZ_PASSWORD = cfg_get("QRZ_PASSWORD", "")
QRZ_AGENT = cfg_get("QRZ_AGENT", "n3fjp-map") or "n3fjp-map"


class QRZClient:
    def __init__(self, username: str, password: str, agent: str):
        self.username = (username or "").strip()
        self.password = (password or "").strip()
        self.agent = (agent or "n3fjp-map").strip() or "n3fjp-map"
        self.session_key: Optional[str] = None
        self.session_expiry: float = 0.0
        self.lock = asyncio.Lock()

    async def _login(self) -> None:
        if not self.username or not self.password:
            return
        async with self.lock:
            if self.session_key and time.time() < self.session_expiry:
                return
            params = {
                "username": self.username,
                "password": self.password,
                "agent": self.agent,
            }
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.get("https://xmldata.qrz.com/xml/current/", params=params)
                resp.raise_for_status()
                data = xmltodict.parse(resp.text)
                session = data.get("QRZDatabase", {}).get("Session", {})
                key = session.get("Key")
                if key:
                    self.session_key = key
                    # QRZ sessions expire after a period; refresh periodically.
                    self.session_expiry = time.time() + 10 * 60
                else:
                    self.session_key = None
            except Exception as e:
                logging.warning(f"QRZ login failed: {e}")
                self.session_key = None

    async def lookup(self, call: Optional[str]) -> Optional[Dict[str, Any]]:
        if not call:
            return None
        if not self.username or not self.password:
            return None
        if not self.session_key or time.time() >= self.session_expiry:
            await self._login()
        if not self.session_key:
            return None
        params = {
            "s": self.session_key,
            "callsign": call,
            "agent": self.agent,
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get("https://xmldata.qrz.com/xml/current/", params=params)
            resp.raise_for_status()
            data = xmltodict.parse(resp.text)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                # session likely expired
                self.session_key = None
            logging.warning(f"QRZ lookup HTTP error for {call}: {e}")
            return None
        except Exception as e:
            logging.warning(f"QRZ lookup failed for {call}: {e}")
            return None

        root = data.get("QRZDatabase", {})
        if "Session" in root and root["Session"].get("Key"):
            self.session_key = root["Session"].get("Key")
            self.session_expiry = time.time() + 10 * 60

        callsign = root.get("Callsign")
        if not callsign:
            return None

        lat_s = callsign.get("lat") or callsign.get("latitude")
        lon_s = callsign.get("lon") or callsign.get("longitude")
        grid = callsign.get("grid") or callsign.get("Grid")
        country = callsign.get("country") or callsign.get("Country")

        dest: Optional[Dict[str, Any]] = None
        try:
            if lat_s and lon_s:
                lat = float(lat_s)
                lon = float(lon_s)
                dest = {"lat": lat, "lon": lon, "grid": grid or maidenhead_from_latlon(lat, lon)}
            elif grid:
                ll = latlon_from_maidenhead(grid)
                if ll:
                    dest = {"lat": ll["lat"], "lon": ll["lon"], "grid": grid}
        except Exception:
            dest = None

        result: Dict[str, Any] = {}
        if dest:
            result["dest"] = dest
        if country:
            result["country"] = country
        return result or None


qrz_client = QRZClient(QRZ_USERNAME, QRZ_PASSWORD, QRZ_AGENT)

# ---------- FastAPI ----------
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "service": "n3fjp-map"})

# ---------- Hub (WS fanout + state) ----------
class Hub:
    def __init__(self):
        self.clients: Set[WebSocket] = set()
        self.origin = {"lat": None, "lon": None, "grid": None}
        self.state = {
            "connected": False,
            "last_connect_ts": None,
            "last_disconnect_ts": None,
            "last_event_ts": None,
            "last_error": None,
            "apiver": None,
            "program": None,
            "last_raw": None,
        }
        self.connection_states: Dict[str, bool] = {}
        self.recent_raw: Deque[str] = deque(maxlen=100)
        self.recent_draw: Deque[Tuple[str, str, str, float]] = deque(maxlen=128)  # (call, band, mode, ts)
        self.recent_paths: Deque[Dict[str, Any]] = deque(maxlen=150)
        self.next_path_id: int = 1
        self.pending_meta: Dict[str, Dict[str, Any]] = {}
        self.operators_seen: Set[str] = set()
        self.sections_worked: Set[str] = set()
        self.countries_worked: Set[str] = set()
        self.station_status: Dict[str, Dict[str, Any]] = {}
        self.chat_messages: Deque[Dict[str, Any]] = deque(maxlen=200)
        # metrics
        self.metrics = {
            "frames_parsed_total": 0,
            "paths_drawn_total": 0,
            "ws_clients_gauge": 0,
            "sections_worked_total": 0,
            "countries_worked_total": 0,
        }

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        self.metrics["ws_clients_gauge"] = len(self.clients)
        await ws.send_json({"type": "status", "data": self.compose_status()})
        if self.origin["lat"] is not None:
            await ws.send_json({"type": "origin", "data": self.origin})
        if self.operators_seen:
            await ws.send_json({"type": "operators", "data": sorted(self.operators_seen)})
        if self.sections_worked:
            await ws.send_json({"type": "sections_worked", "data": sorted(self.sections_worked)})
        if self.countries_worked:
            await ws.send_json({"type": "countries_worked", "data": sorted(self.countries_worked)})

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)
        self.metrics["ws_clients_gauge"] = len(self.clients)

    def update_connection_state(self, name: str, connected: bool, error: Optional[str] = None):
        now = time.time()
        self.connection_states[name] = connected
        if connected:
            self.state["last_connect_ts"] = now
            self.state["last_error"] = None
        else:
            self.state["last_disconnect_ts"] = now
            if error:
                self.state["last_error"] = error
        self.state["connected"] = any(self.connection_states.values())
        if not self.state["connected"] and not error:
            self.state["last_error"] = None

    def store_station_status(self, station: str, payload: Dict[str, Any]):
        if not station:
            return
        self.station_status[station] = payload

    def store_chat_message(self, payload: Dict[str, Any]):
        if not payload:
            return
        self.chat_messages.append(payload)

    async def broadcast(self, payload: Dict[str, Any]):
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    def compose_status(self):
        return {
            **self.state,
            "origin": self.origin,
            "operators": sorted(self.operators_seen),
            "sections_worked": sorted(self.sections_worked),
            "countries_worked": sorted(self.countries_worked),
            "metrics": self.metrics,
            "wfd_mode": WFD_MODE,
            "prefer_section": PREFER_SECTION_ALWAYS,
            "ttl_seconds": TTL_SECONDS,
        }

    def should_draw(self, call: Optional[str], band: Optional[str], mode: Optional[str]) -> bool:
        now = time.time()
        key = (call or "", band or "", (mode or "").upper())
        # optional server-side filters
        if BAND_FILTER and key[1] and key[1] not in BAND_FILTER:
            return False
        if MODE_FILTER and key[2] and key[2] not in MODE_FILTER:
            return False
        # dedupe (2s)
        while self.recent_draw and now - self.recent_draw[0][3] > 3.0:
            self.recent_draw.popleft()
        for c, b, m, ts in self.recent_draw:
            if (c, b, m) == key and now - ts < 2.0:
                return False
        self.recent_draw.append((key[0], key[1], key[2], now))
        return True

    async def emit_path(
        self,
        dest: Dict[str, Any],
        meta: Optional[Dict[str, Any]],
        ttl: Optional[int] = None,
        origin_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not dest:
            return
        if origin_override is None:
            origin = copy.deepcopy(self.origin)
        else:
            origin = copy.deepcopy(origin_override)
        if origin.get("lat") is None or origin.get("lon") is None:
            return
        if dest.get("lat") is None or dest.get("lon") is None:
            return

        safe_meta = {k: v for k, v in (meta or {}).items() if v not in (None, "")}
        call = safe_meta.get("call")
        band = safe_meta.get("band")
        mode = safe_meta.get("mode")
        ttl_val = ttl or TTL_SECONDS

        if not self.should_draw(call, band, mode):
            return

        to = copy.deepcopy(dest)
        if to.get("grid") is None:
            try:
                to["grid"] = maidenhead_from_latlon(to["lat"], to["lon"])
            except Exception:
                pass

        path_id = self.next_path_id
        self.next_path_id += 1

        timestamp = time.time()
        payload_data = {
            "id": path_id,
            "from": origin,
            "to": to,
            "meta": safe_meta,
            "ttl": ttl_val,
            "timestamp": timestamp,
        }
        payload = {"type": "path", "data": payload_data}

        self.state["last_event_ts"] = timestamp
        self.metrics["paths_drawn_total"] += 1
        await self.broadcast(payload)
        await self.broadcast({"type": "status", "data": self.compose_status()})

        section = safe_meta.get("section")
        if section:
            if section not in self.sections_worked:
                self.sections_worked.add(section)
                self.metrics["sections_worked_total"] = len(self.sections_worked)
                await self.broadcast({"type": "section_hit", "data": section})
                await self.broadcast({"type": "sections_worked", "data": sorted(self.sections_worked)})

        country = safe_meta.get("country")
        if country:
            key = resolve_country_key(country)
            if key and key not in self.countries_worked:
                self.countries_worked.add(key)
                self.metrics["countries_worked_total"] = len(self.countries_worked)
                await self.broadcast({"type": "country_hit", "data": key})
                await self.broadcast({"type": "countries_worked", "data": sorted(self.countries_worked)})

        self.recent_paths.append({
            "id": path_id,
            "timestamp": timestamp,
            "meta": safe_meta,
            "from": origin,
            "to": to,
        })

hub = Hub()

# ---------- Sections & countries (centroids only) ----------
with open("static/data/centroids/sections.json", "r", encoding="utf-8") as f:
    SECTION_CENTROIDS: Dict[str, Dict[str, float]] = json.load(f)


COUNTRY_CENTROIDS: Dict[str, Dict[str, Any]] = {}
COUNTRY_ALIAS_INDEX: Dict[str, str] = {}


def canonical_country_key(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    normalized = unicodedata.normalize("NFD", str(name))
    normalized = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    normalized = re.sub(r"[^0-9A-Za-z]+", " ", normalized)
    normalized = normalized.strip().upper()
    return normalized or None


def resolve_country_key(name: Optional[str]) -> Optional[str]:
    key = canonical_country_key(name)
    if not key:
        return None
    if key in COUNTRY_CENTROIDS:
        return key
    return COUNTRY_ALIAS_INDEX.get(key, key)


def country_centroid(name: Optional[str]) -> Optional[Dict[str, Any]]:
    key = resolve_country_key(name)
    if not key:
        return None
    info = COUNTRY_CENTROIDS.get(key)
    if not info:
        return None
    lat = info.get("lat")
    lon = info.get("lon")
    if lat is None or lon is None:
        return None
    dest = {"lat": lat, "lon": lon, "grid": None}
    try:
        dest["grid"] = maidenhead_from_latlon(lat, lon)
    except Exception:
        dest["grid"] = None
    return dest


try:
    with open("static/data/centroids/countries.geojson", "r", encoding="utf-8") as f:
        countries_geo = json.load(f)
    for feature in countries_geo.get("features", []):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates") or []
        if len(coords) < 2:
            continue
        try:
            lon = float(coords[0])
            lat = float(coords[1])
        except (TypeError, ValueError):
            continue
        primary = props.get("COUNTRY") or props.get("preferred_term") or props.get("english_short") or props.get("NAME") or ""
        iso2 = str(props.get("ISO") or props.get("iso2_code") or props.get("AFF_ISO") or "").upper()
        iso3 = str(props.get("iso3_code") or "").upper()
        aliases_raw = [
            primary,
            props.get("COUNTRYAFF"),
            props.get("english_short"),
            props.get("spanish_short"),
            props.get("french_short"),
            props.get("russian_short"),
            props.get("chinese_short"),
            props.get("arabic_short"),
            iso2,
            iso3,
        ]
        alias_keys: List[str] = []
        for alias in aliases_raw:
            key = canonical_country_key(alias)
            if key and key not in alias_keys:
                alias_keys.append(key)
        if not alias_keys:
            continue
        base_key = next((k for k in alias_keys if k not in COUNTRY_CENTROIDS), alias_keys[0])
        info = {
            "lat": lat,
            "lon": lon,
            "name": primary or props.get("english_short") or iso2 or iso3 or base_key,
            "iso2": iso2,
            "iso3": iso3,
        }
        if base_key not in COUNTRY_CENTROIDS:
            COUNTRY_CENTROIDS[base_key] = info
        for alias_key in alias_keys:
            COUNTRY_ALIAS_INDEX[alias_key] = base_key
        COUNTRY_ALIAS_INDEX[base_key] = base_key
except FileNotFoundError:
    pass
except Exception as exc:
    logging.warning(f"Failed to load country centroids: {exc}")

# ---------- Helpers ----------
def tag(text: str, name: str) -> Optional[str]:
    m = re.search(rf"<{name}>(.*?)</{name}>", text, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else None

def first_tag(text: str, *names: str) -> Optional[str]:
    for n in names:
        v = tag(text, n)
        if v is not None:
            return v
    return None

def parse_station_status_record(rec: str, source: str) -> Optional[Dict[str, Any]]:
    rec_upper = rec.upper()
    if "STATIONSTATUS" not in rec_upper:
        return None
    station = tag(rec, "STATION") or tag(rec, "CALL") or tag(rec, "OPERATOR")
    if not station:
        return None
    payload: Dict[str, Any] = {
        "station": station,
        "system": tag(rec, "SYSTEM"),
        "band": tag(rec, "BAND"),
        "mode": tag(rec, "MODE") or tag(rec, "MODETEST"),
        "status": tag(rec, "STATUS") or tag(rec, "STATE"),
        "raw": rec,
        "source": source,
        "timestamp": time.time(),
    }
    # remove empty fields
    return {k: v for k, v in payload.items() if v not in (None, "")}

def parse_chat_message_record(rec: str, source: str) -> Optional[Dict[str, Any]]:
    rec_upper = rec.upper()
    if "<CHAT" not in rec_upper and "CHATMESSAGE" not in rec_upper:
        return None
    message = tag(rec, "MESSAGE") or tag(rec, "CHAT") or tag(rec, "TEXT")
    if not message:
        return None
    payload: Dict[str, Any] = {
        "message": message,
        "from": tag(rec, "FROM") or tag(rec, "CALL") or tag(rec, "OPERATOR"),
        "to": tag(rec, "TO") or tag(rec, "TARGET"),
        "raw": rec,
        "source": source,
        "timestamp": time.time(),
    }
    channel = tag(rec, "CHANNEL") or tag(rec, "ROOM")
    if channel:
        payload["channel"] = channel
    return {k: v for k, v in payload.items() if v not in (None, "")}

def parse_lon_west_positive(s: Optional[str]) -> Optional[float]:
    if s is None or s == "":
        return None
    try:
        return -float(s)  # west-positive -> standard negative
    except Exception:
        return None

def maidenhead_from_latlon(lat: float, lon: float, precision: int = 6) -> str:
    lon += 180.0
    lat += 90.0
    A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    a = "abcdefghijklmnopqrstuvwxyz"
    f1 = int(lon // 20); f2 = int(lat // 10)
    r1 = int((lon % 20) // 2); r2 = int(lat % 10)
    s1 = int(((lon % 2) * 60) // 5); s2 = int(((lat % 1) * 60) // 2.5)
    return f"{A[f1]}{A[f2]}{r1}{r2}{a[s1]}{a[s2]}"

def latlon_from_maidenhead(grid: str) -> Optional[Dict[str, float]]:
    if not grid: return None
    g = grid.strip()
    if len(g) < 4: return None
    A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"; a = "abcdefghijklmnopqrstuvwxyz"
    try:
        lon = (A.index(g[0].upper()) * 20) - 180
        lat = (A.index(g[1].upper()) * 10) - 90
        lon += int(g[2]) * 2; lat += int(g[3]) * 1
        if len(g) >= 6:
            lon += (a.index(g[4].lower()) * 5) / 60.0
            lat += (a.index(g[5].lower()) * 2.5) / 60.0
            lon += (5/60.0)/2; lat += (2.5/60.0)/2
        else:
            lon += 1.0; lat += 0.5
        return {"lat": lat, "lon": lon}
    except Exception:
        return None


def parse_bool(value: Optional[str]) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in {"y", "yes", "true", "1"}:
        return True
    if v in {"n", "no", "false", "0"}:
        return False
    return None


def section_to_latlon(section: Optional[str]) -> Optional[Dict[str, float]]:
    if not section: return None
    return SECTION_CENTROIDS.get(section.strip().upper())

# ---------- Status endpoints ----------
@app.get("/status")
async def status():
    return JSONResponse(hub.compose_status())

@app.get("/recent")
async def recent():
    return JSONResponse({"recent": list(hub.recent_paths)})

# ---------- Metrics ----------
@app.get("/metrics")
async def metrics():
    m = hub.metrics
    lines = [
        "# HELP n3fjp_frames_parsed_total Total API frames parsed",
        "# TYPE n3fjp_frames_parsed_total counter",
        f"n3fjp_frames_parsed_total {m['frames_parsed_total']}",
        "# HELP n3fjp_paths_drawn_total Total path events emitted",
        "# TYPE n3fjp_paths_drawn_total counter",
        f"n3fjp_paths_drawn_total {m['paths_drawn_total']}",
        "# HELP n3fjp_ws_clients_gauge Current WebSocket clients",
        "# TYPE n3fjp_ws_clients_gauge gauge",
        f"n3fjp_ws_clients_gauge {m['ws_clients_gauge']}",
        "# HELP n3fjp_sections_worked_total Distinct sections worked",
        "# TYPE n3fjp_sections_worked_total gauge",
        f"n3fjp_sections_worked_total {m['sections_worked_total']}",
        "# HELP n3fjp_countries_worked_total Distinct countries worked",
        "# TYPE n3fjp_countries_worked_total gauge",
        f"n3fjp_countries_worked_total {m['countries_worked_total']}",
    ]
    return PlainTextResponse("\n".join(lines), media_type="text/plain; version=0.0.4")

# ---------- WebSocket ----------
@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await hub.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        hub.disconnect(ws)

# ---------- TCP helpers ----------
def _extract_one_frame(buffer: bytearray) -> Optional[str]:
    start = buffer.find(b"<CMD>")
    if start == -1: return None
    end = buffer.find(b"</CMD>", start)
    if end == -1: return None
    rec_bytes = buffer[start + 5 : end]
    del buffer[: end + 6]
    return rec_bytes.decode(errors="ignore")

async def _send(writer: asyncio.StreamWriter, cmd: str):
    writer.write((cmd + "\r\n").encode())
    await writer.drain()

async def _heartbeat(writer: asyncio.StreamWriter):
    try:
        while True:
            await _send(writer, "<CMD><APIVER></CMD>")
            await asyncio.sleep(HEARTBEAT_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logging.info(f"Heartbeat ended: {e}")


connection_lock = asyncio.Lock()
connection_writers: Dict[str, asyncio.StreamWriter] = {}
connection_ports: Dict[str, int] = {}


async def register_writer(name: str, port: int, writer: asyncio.StreamWriter):
    async with connection_lock:
        connection_writers[name] = writer
        connection_ports[name] = port


async def unregister_writer(name: str):
    async with connection_lock:
        connection_writers.pop(name, None)
        connection_ports.pop(name, None)


async def forward_raw_frame(source_name: str, source_port: int, rec: str):
    target_port = PORT_FORWARD_RULES.get(source_port)
    if target_port is None:
        return
    async with connection_lock:
        target_name = next(
            (n for n, p in connection_ports.items() if p == target_port and n != source_name),
            None,
        )
        target_writer = connection_writers.get(target_name) if target_name else None
    if not target_writer:
        return
    try:
        await _send(target_writer, f"<CMD>{rec}</CMD>")
    except Exception as e:
        logging.warning(f"Failed to forward frame from {source_port} to {target_port}: {e}")

# ---------- N3FJP TCP client task ----------
async def n3fjp_client(conn: Dict[str, Any]):
    await asyncio.sleep(1)
    name = conn.get("name", "primary")
    host = conn.get("host", N3FJP_HOST)
    port = int(conn.get("port", N3FJP_PORT))
    while True:
        reader: Optional[asyncio.StreamReader] = None
        writer: Optional[asyncio.StreamWriter] = None
        hb_task: Optional[asyncio.Task] = None
        try:
            logging.info(f"Connecting to N3FJP ({name}) at {host}:{port} ...")
            reader, writer = await asyncio.open_connection(host, port)
            await register_writer(name, port, writer)
            hub.update_connection_state(name, True)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            logging.info(f"Connected to N3FJP ({name}).")

            # bootstrap
            await _send(writer, "<CMD><APIVER></CMD>")
            await _send(writer, "<CMD><PROGRAM></CMD>")
            await _send(writer, "<CMD><SETUPDATESTATE><VALUE>TRUE</VALUE></CMD>")
            await _send(writer, "<CMD><OPINFO></CMD>")

            hb_task = asyncio.create_task(_heartbeat(writer))
            buf = bytearray()
            last_emit = 0.0

            async def refresh_origin_from_opinfo():
                await _send(writer, "<CMD><OPINFO></CMD>")

            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    raise ConnectionError("N3FJP closed the socket")
                buf.extend(chunk)

                while True:
                    rec = _extract_one_frame(buf)
                    if rec is None:
                        break

                    hub.metrics["frames_parsed_total"] += 1
                    hub.state["last_raw"] = rec
                    hub.recent_raw.append(rec)
                    recU = rec.upper()
                    try:
                        # Added: capture station status frames from any connection
                        station_status = parse_station_status_record(rec, name)
                        if station_status:
                            hub.store_station_status(station_status.get("station"), station_status)
                            continue

                        # Added: capture chat messages without altering map behavior
                        chat_message = parse_chat_message_record(rec, name)
                        if chat_message:
                            hub.store_chat_message(chat_message)
                            continue

                        # Version/Program
                        if "APIVERRESPONSE" in recU:
                            hub.state["apiver"] = tag(rec, "APIVER")
                            await hub.broadcast({"type": "status", "data": hub.compose_status()})
                            continue
                        if "PROGRAMRESPONSE" in recU:
                            pgm = tag(rec, "PGM"); ver = tag(rec, "VER")
                            hub.state["program"] = f"{pgm or ''} {ver or ''}".strip()
                            await hub.broadcast({"type": "status", "data": hub.compose_status()})
                            continue

                        # Origin from OPINFO (GRID preferred)
                        if "OPINFORESPONSE" in recU:
                            grid = tag(rec, "GRID")
                            lat_s = tag(rec, "LAT")
                            lon_s = first_tag(rec, "LON", "LONG")
                            origin = None
                            if grid:
                                origin = latlon_from_maidenhead(grid)
                                if origin: origin["grid"] = grid
                            elif lat_s and lon_s:
                                lat = float(lat_s); lon = parse_lon_west_positive(lon_s)
                                if lon is not None:
                                    origin = {"lat": lat, "lon": lon}
                                    origin["grid"] = maidenhead_from_latlon(lat, lon)
                            if origin:
                                hub.origin = origin
                                await hub.broadcast({"type":"origin","data":hub.origin})
                                await hub.broadcast({"type":"status","data":hub.compose_status()})
                            continue

                        # Draw ONLY on ENTEREVENT (submit)
                        if "ENTEREVENT" in recU:
                            await refresh_origin_from_opinfo()

                            call = tag(rec, "CALL")
                            band = tag(rec, "BAND")
                            mode = (tag(rec, "MODE") or tag(rec, "MODETEST") or "").upper()
                            sect = (first_tag(rec, "SECTION", "ARRL_SECT") or "").upper()
                            oper = tag(rec, "OPERATOR") or tag(rec, "MYCALL") or ""
                            country = tag(rec, "COUNTRY") or ""

                            # track operators seen
                            if oper:
                                if oper not in hub.operators_seen:
                                    hub.operators_seen.add(oper)
                                    await hub.broadcast({"type":"operators","data":sorted(hub.operators_seen)})

                            # destination selection
                            tlat_s = tag(rec, "LAT")
                            tlon_s = first_tag(rec, "LON", "LONG")
                            dest = None
                            base_meta = {
                                "call": call,
                                "band": band,
                                "mode": mode,
                                "section": sect,
                                "operator": oper,
                            }
                            if country:
                                base_meta["country"] = country
                            call_key = (call or "").upper()

                            if (WFD_MODE or PREFER_SECTION_ALWAYS) and sect:
                                sec = section_to_latlon(sect)
                                if sec:
                                    dest = {"lat": sec["lat"], "lon": sec["lon"], "grid": None}
                            if not dest and tlat_s and tlon_s:
                                lat = float(tlat_s); lon = parse_lon_west_positive(tlon_s)
                                if lon is not None:
                                    dest = {"lat": lat, "lon": lon, "grid": None}

                            if not dest and call:
                                dx_flag = parse_bool(tag(rec, "DX"))
                                if dx_flag is None:
                                    dx_flag = bool(country and "USA" not in country.upper() and "UNITED STATES" not in country.upper())
                                if dx_flag:
                                    qrz_result = await qrz_client.lookup(call)
                                    if qrz_result:
                                        qrz_country = qrz_result.get("country")
                                        if qrz_country:
                                            base_meta["country"] = qrz_country
                                        qrz_dest = qrz_result.get("dest")
                                        if qrz_dest and qrz_dest.get("lat") is not None and qrz_dest.get("lon") is not None:
                                            dest = qrz_dest
                                        if not dest:
                                            centroid = country_centroid(base_meta.get("country") or qrz_country)
                                            if centroid:
                                                dest = centroid

                            if not dest and base_meta.get("country"):
                                centroid = country_centroid(base_meta.get("country"))
                                if centroid:
                                    dest = centroid

                            if dest:
                                hub.pending_meta.pop(call_key, None)
                                now = time.time()
                                if now - last_emit > 0.01:
                                    await hub.emit_path(dest, base_meta, TTL_SECONDS, origin_override=copy.deepcopy(hub.origin))
                                    last_emit = now
                                continue

                            if call:
                                hub.pending_meta[call_key] = {
                                    "meta": copy.deepcopy(base_meta),
                                    "origin": copy.deepcopy(hub.origin),
                                }
                                await _send(writer, f"<CMD><COUNTRYLISTLOOKUP><CALL>{call}</CALL></CMD>")

                            continue

                        # COUNTRYLISTLOOKUP fallback
                        if "COUNTRYLISTLOOKUPRESPONSE" in recU and hub.origin["lat"] is not None:
                            call = tag(rec, "CALL")
                            tlat_s = tag(rec, "LAT")
                            tlon_s = first_tag(rec, "LON", "LONG")
                            if tlat_s and tlon_s:
                                lat = float(tlat_s); lon = parse_lon_west_positive(tlon_s)
                                if lon is not None:
                                    dest = {"lat": lat, "lon": lon, "grid": maidenhead_from_latlon(lat, lon)}
                                    meta_info = hub.pending_meta.pop((call or "").upper(), None)
                                    meta_payload = {"call": call}
                                    origin_override = None
                                    if meta_info:
                                        meta_payload = meta_info.get("meta", meta_payload)
                                        origin_override = meta_info.get("origin")
                                    country_name = tag(rec, "COUNTRY") or tag(rec, "COUNTRY_NAME")
                                    if country_name and not meta_payload.get("country"):
                                        meta_payload["country"] = country_name
                                    await hub.emit_path(dest, meta_payload, TTL_SECONDS, origin_override=origin_override)
                            continue
                    finally:
                        await forward_raw_frame(name, port, rec)

        except asyncio.CancelledError:
            logging.warning(f"n3fjp_client task cancelled ({name})")
            hub.update_connection_state(name, False)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            raise
        except Exception as e:
            logging.exception(f"N3FJP connection loop crashed ({name})")
            hub.update_connection_state(name, False, str(e))
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            await asyncio.sleep(2)
        finally:
            if hb_task:
                hb_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await hb_task
            await unregister_writer(name)
            if writer:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()

@app.on_event("startup")
async def startup_event():
    app.state.n3fjp_tasks: List[asyncio.Task] = []
    for conn in N3FJP_CONNECTIONS:
        task = asyncio.create_task(n3fjp_client(conn))
        app.state.n3fjp_tasks.append(task)

@app.on_event("shutdown")
async def shutdown_event():
    tasks = getattr(app.state, "n3fjp_tasks", [])
    for t in tasks:
        if t and not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
