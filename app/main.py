# app/main.py

import asyncio
import contextlib
import os
import time
import re
import json
import logging
import unicodedata
from pathlib import Path
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
    if isinstance(default, (dict, list)):
        try:
            return json.loads(val)
        except Exception:
            return default
    return val

# ---------- Logging ----------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------- Config ----------
N3FJP_HOST = cfg_get("N3FJP_HOST", "127.0.0.1")
_LEGACY_PORT = cfg_get("N3FJP_PORT", 1100)
N3FJP_API_PORT = cfg_get("N3FJP_API_PORT", _LEGACY_PORT)
N3FJP_STATUS_PORT = cfg_get("N3FJP_STATUS_PORT", 1000)
ENABLE_API_PORT = cfg_get("ENABLE_API_PORT", True)
ENABLE_STATUS_PORT = cfg_get("ENABLE_STATUS_PORT", True)

WFD_MODE = cfg_get("WFD_MODE", False)
PREFER_SECTION_ALWAYS = cfg_get("PREFER_SECTION_ALWAYS", False)
TTL_SECONDS = cfg_get("TTL_SECONDS", 600)
BAND_FILTER = set([b.strip() for b in str(cfg_get("BAND_FILTER", "")).split(",") if b.strip()])
MODE_FILTER = set([m.strip().upper() for m in str(cfg_get("MODE_FILTER", "")).split(",") if m.strip()])

HEARTBEAT_SECONDS = max(3, cfg_get("HEARTBEAT_SECONDS", 5))

PRIMARY_STATION_NAME = cfg_get("PRIMARY_STATION_NAME", "Primary Station")
STATION_LOCATIONS_RAW = cfg_get("STATION_LOCATIONS", {})

QRZ_USERNAME = cfg_get("QRZ_USERNAME", "")
QRZ_PASSWORD = cfg_get("QRZ_PASSWORD", "")
QRZ_AGENT = cfg_get("QRZ_AGENT", "n3fjp-map") or "n3fjp-map"


def canonical_station_key(name: Optional[str]) -> Optional[str]:
    if name is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(name)).strip()
    if not cleaned:
        return None
    return cleaned.upper()


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
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI()
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))

@app.get("/health")
async def health():
    return JSONResponse({"ok": True, "service": "n3fjp-map"})

# ---------- Hub (WS fanout + state) ----------
class Hub:
    def __init__(
        self,
        initial_station_origins: Optional[Dict[str, Dict[str, Any]]] = None,
        primary_station_name: Optional[str] = None,
    ):
        self.clients: Set[WebSocket] = set()
        self.origin = {"lat": None, "lon": None, "grid": None}
        self.primary_station_name = (primary_station_name or "Primary Station").strip() or "Primary Station"
        self.state = {
            "connected": False,
            "last_connect_ts": None,
            "last_disconnect_ts": None,
            "last_event_ts": None,
            "last_error": None,
            "apiver": None,
            "program": None,
            "last_raw": None,
            "connections": {},
        }
        self.recent_raw: Deque[str] = deque(maxlen=100)
        self.recent_draw: Deque[Tuple[str, str, str, float]] = deque(maxlen=128)  # (call, band, mode, ts)
        self.recent_paths: Deque[Dict[str, Any]] = deque(maxlen=150)
        self.next_path_id: int = 1
        self.pending_meta: Dict[str, Dict[str, Any]] = {}
        self.operators_seen: Set[str] = set()
        self.sections_worked: Set[str] = set()
        self.countries_worked: Set[str] = set()
        self.station_origins: Dict[str, Dict[str, Any]] = {}
        # metrics
        self.metrics = {
            "frames_parsed_total": 0,
            "paths_drawn_total": 0,
            "ws_clients_gauge": 0,
            "sections_worked_total": 0,
            "countries_worked_total": 0,
        }
        self.connection_states: Dict[str, Dict[str, Any]] = {}
        self._preload_station_origins(initial_station_origins or {})

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        self.metrics["ws_clients_gauge"] = len(self.clients)
        await ws.send_json({"type": "status", "data": self.compose_status()})
        if self.origin["lat"] is not None:
            await ws.send_json({"type": "origin", "data": self.origin_payload()})
        if self.station_origins:
            await ws.send_json({"type": "station_origins", "data": self.station_origin_entries()})
        if self.operators_seen:
            await ws.send_json({"type": "operators", "data": sorted(self.operators_seen)})
        if self.sections_worked:
            await ws.send_json({"type": "sections_worked", "data": sorted(self.sections_worked)})
        if self.countries_worked:
            await ws.send_json({"type": "countries_worked", "data": sorted(self.countries_worked)})

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)
        self.metrics["ws_clients_gauge"] = len(self.clients)

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
            "primary_station_name": self.primary_station_name,
            "station_origins": self.station_origin_entries(),
            "operators": sorted(self.operators_seen),
            "sections_worked": sorted(self.sections_worked),
            "countries_worked": sorted(self.countries_worked),
            "metrics": self.metrics,
            "wfd_mode": WFD_MODE,
            "prefer_section": PREFER_SECTION_ALWAYS,
            "ttl_seconds": TTL_SECONDS,
        }

    def update_connection_state(
        self,
        name: str,
        *,
        connected: bool,
        port: Optional[int] = None,
        error: Optional[str] = None,
    ) -> None:
        now = time.time()
        entry = self.connection_states.setdefault(
            name,
            {
                "connected": False,
                "last_connect_ts": None,
                "last_disconnect_ts": None,
                "last_error": None,
                "port": port,
            },
        )
        if port is not None:
            entry["port"] = port
        if connected:
            if not entry["connected"]:
                entry["last_connect_ts"] = now
            entry["connected"] = True
            entry["last_error"] = None
        else:
            if entry["connected"]:
                entry["last_disconnect_ts"] = now
            entry["connected"] = False
            if error:
                entry["last_error"] = error
        if error:
            entry["last_error"] = error
            self.state["last_error"] = error
        if connected:
            self.state["last_connect_ts"] = now
        else:
            self.state["last_disconnect_ts"] = now
        self.state["connections"] = {k: {**v} for k, v in self.connection_states.items()}
        self.state["connected"] = any(v.get("connected") for v in self.connection_states.values())

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

    def origin_payload(self) -> Dict[str, Any]:
        payload = copy.deepcopy(self.origin)
        payload["name"] = self.primary_station_name
        return payload

    def station_origin_entries(self) -> List[Dict[str, Any]]:
        entries = [self._public_station_entry(v) for v in self.station_origins.values()]
        entries.sort(key=lambda item: (item.get("name") or "").upper())
        return entries

    def _public_station_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        payload = copy.deepcopy(entry)
        sources = payload.get("sources")
        if isinstance(sources, set):
            payload["sources"] = sorted(sources)
        return payload

    def _preload_station_origins(self, initial: Dict[str, Dict[str, Any]]):
        for key, entry in initial.items():
            if not entry:
                continue
            safe = self._safe_station_origin(entry)
            if not safe:
                continue
            canon = canonical_station_key(entry.get("name") or key)
            if not canon:
                continue
            self.station_origins[canon] = safe
        prim_key = canonical_station_key(self.primary_station_name)
        if prim_key and self.origin["lat"] is None:
            entry = self.station_origins.get(prim_key)
            if entry:
                self.origin = {k: entry.get(k) for k in ("lat", "lon", "grid")}

    def _safe_station_origin(self, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if not data:
            return None
        lat = data.get("lat")
        lon = data.get("lon")
        if lat is None or lon is None:
            return None
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            return None
        payload = {"lat": lat_f, "lon": lon_f}
        grid = data.get("grid") or data.get("maidenhead")
        if grid:
            payload["grid"] = str(grid).upper()
        else:
            try:
                payload["grid"] = maidenhead_from_latlon(lat_f, lon_f)
            except Exception:
                payload["grid"] = None
        name = data.get("name") or data.get("station")
        if name:
            payload["name"] = str(name)
        return payload

    async def set_station_origin(self, name: Optional[str], origin: Optional[Dict[str, Any]]):
        if not name or not origin:
            return
        safe = self._safe_station_origin({"name": name, **origin})
        if not safe:
            return
        canon = canonical_station_key(name)
        if not canon:
            return
        entry = self.station_origins.get(canon)
        changed = False
        if not entry:
            entry = {"name": safe.get("name", name)}
            self.station_origins[canon] = entry
            changed = True
        for key in ("lat", "lon", "grid"):
            if safe.get(key) is not None and entry.get(key) != safe.get(key):
                entry[key] = safe.get(key)
                changed = True
        if entry.get("name") != safe.get("name"):
            entry["name"] = safe.get("name")
            changed = True
        if not changed:
            return
        if canonical_station_key(self.primary_station_name) == canon:
            self.origin = {k: entry.get(k) for k in ("lat", "lon", "grid")}
            await self.broadcast({"type": "origin", "data": self.origin_payload()})
        await self.broadcast({"type": "station_origin", "data": self._public_station_entry(entry)})
        await self.broadcast({"type": "status", "data": self.compose_status()})

    async def update_station_presence(
        self,
        name: Optional[str],
        meta: Optional[Dict[str, Any]] = None,
        location: Optional[Dict[str, Any]] = None,
        *,
        source: Optional[str] = None,
        force_broadcast: bool = False,
    ) -> None:
        if not name:
            return
        canon = canonical_station_key(name)
        if not canon:
            return
        entry = self.station_origins.get(canon)
        if not entry:
            entry = {"name": name}
            self.station_origins[canon] = entry
        if entry.get("name") != name:
            entry["name"] = name
        changed = False
        loc_payload = None
        if location:
            loc_payload = self._safe_station_origin({"name": name, **location})
        if loc_payload:
            for key in ("lat", "lon", "grid"):
                if loc_payload.get(key) is not None and entry.get(key) != loc_payload.get(key):
                    entry[key] = loc_payload.get(key)
                    changed = True
        if meta:
            for key in ("call", "operator", "band", "mode", "status", "section", "country", "message"):
                value = meta.get(key)
                if value is not None and entry.get(key) != value:
                    entry[key] = value
                    changed = True
        if source:
            sources = entry.get("sources")
            if isinstance(sources, set):
                current_sources = sources
            elif isinstance(sources, list):
                current_sources = set(sources)
            else:
                current_sources = set()
            if source not in current_sources:
                current_sources.add(source)
                entry["sources"] = current_sources
                changed = True
            else:
                entry["sources"] = current_sources
            if entry.get("last_source") != source:
                entry["last_source"] = source
                changed = True
        entry["last_seen"] = time.time()
        if changed or force_broadcast:
            await self.broadcast({"type": "station_origin", "data": self._public_station_entry(entry)})
            await self.broadcast({"type": "status", "data": self.compose_status()})

    def get_station_origin(self, name: Optional[str]) -> Optional[Dict[str, Any]]:
        target = None
        if name:
            canon = canonical_station_key(name)
            if canon:
                target = self.station_origins.get(canon)
        if not target and self.origin.get("lat") is not None:
            target = {**self.origin, "name": self.primary_station_name}
        if not target:
            return None
        return {k: target.get(k) for k in ("lat", "lon", "grid")}

# ---------- Sections & countries (centroids only) ----------
with open(STATIC_DIR / "data/centroids/sections.json", "r", encoding="utf-8") as f:
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
    with open(STATIC_DIR / "data/centroids/countries.geojson", "r", encoding="utf-8") as f:
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


STATION_NAME_TAGS = (
    "STATIONNAME",
    "THISSTATIONNAME",
    "STATION",
    "STATIONID",
    "STATION_ID",
    "STATIONCALL",
    "CLIENTSTATION",
    "CLIENTNAME",
    "COMPUTERNAME",
    "PCNAME",
    "NETWORKSTATION",
)


def extract_station_name(text: str) -> Optional[str]:
    for key in STATION_NAME_TAGS:
        value = tag(text, key)
        if value:
            cleaned = value.strip()
            if cleaned:
                return cleaned
    return None

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


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_key_value_pairs(text: str) -> Dict[str, str]:
    pairs: Dict[str, str] = {}
    if not text:
        return pairs
    tokens = re.split(r"[|,\r\n]+", text)
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if ":" in token:
            key, value = token.split(":", 1)
        elif "=" in token:
            key, value = token.split("=", 1)
        else:
            continue
        key = key.strip().lower()
        if not key:
            continue
        pairs[key] = value.strip()
    return pairs


def parse_station_status_payload(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    pairs = _parse_key_value_pairs(text)
    station = extract_station_name(text)
    if not station:
        for key in ("station", "stationname", "clientname", "networkstation"):
            if key in pairs:
                station = pairs[key]
                break
    call = first_tag(text, "CALL", "THISCALL", "STATIONCALL") or pairs.get("call")
    operator = first_tag(text, "OPERATOR", "MYCALL", "OP") or pairs.get("operator")
    band = first_tag(text, "BAND") or pairs.get("band")
    mode = first_tag(text, "MODE", "MODETEST") or pairs.get("mode")
    status_text = first_tag(text, "STATUS", "STATE", "CLIENTSTATUS", "CHATSTATUS") or pairs.get("status") or pairs.get("state")
    section = first_tag(text, "SECTION", "ARRL_SECT") or pairs.get("section")
    country = first_tag(text, "COUNTRY") or pairs.get("country")
    grid = first_tag(text, "GRID", "MYGRID", "DXGRID", "STATIONGRID") or pairs.get("grid")
    lat_s = first_tag(text, "LAT", "LATITUDE") or pairs.get("lat") or pairs.get("latitude")
    lon_s = first_tag(text, "LON", "LONG", "LONGITUDE", "LONWEST") or pairs.get("lon") or pairs.get("longitude")
    message = first_tag(text, "CHATMESSAGE", "MESSAGE", "TEXT") or pairs.get("message") or pairs.get("text")
    timestamp = first_tag(text, "TIMESTAMP", "TIME", "LASTUPDATE") or pairs.get("timestamp") or pairs.get("time")

    if "|" in text and not pairs:
        parts = [p.strip() for p in text.split("|")]
        if parts and not station:
            station = parts[0]
        if len(parts) > 1 and not operator:
            operator = parts[1]
        if len(parts) > 2 and not band:
            band = parts[2]
        if len(parts) > 3 and not mode:
            mode = parts[3]
        if len(parts) > 4 and not status_text:
            status_text = parts[4]
        if len(parts) > 5 and not grid:
            grid = parts[5]

    band = (band or "").strip()
    if band.upper().endswith("M"):
        band = band[:-1]
    band = band or None
    mode = (mode or "").strip().upper() or None
    status_text = (status_text or "").strip() or None
    section = (section or "").strip().upper() or None
    country = (country or "").strip() or None
    grid = (grid or "").strip().upper() or None

    lat = _float_or_none(lat_s)
    lon = parse_lon_west_positive(lon_s)
    if lon is None:
        lon = _float_or_none(lon_s)

    if not station and not call and not operator:
        return None
    payload: Dict[str, Any] = {
        "station": station,
        "call": (call or "").strip() or None,
        "operator": (operator or "").strip() or None,
        "band": band,
        "mode": mode,
        "status": status_text,
        "section": section,
        "country": country,
        "grid": grid,
        "message": (message or "").strip() or None,
    }
    if lat is not None:
        payload["lat"] = lat
    if lon is not None:
        payload["lon"] = lon
    ts_val = _float_or_none(timestamp)
    if ts_val is not None:
        payload["timestamp"] = ts_val
    return payload


def _station_origin_from_spec(spec: Any) -> Optional[Dict[str, Any]]:
    if spec is None:
        return None
    if isinstance(spec, str):
        text = spec.strip()
        if not text:
            return None
        if re.fullmatch(r"[A-Za-z]{2}\d{2}[A-Za-z]{0,2}", text):
            coords = latlon_from_maidenhead(text)
            if coords:
                coords["grid"] = text.upper()
                return coords
        try:
            parsed = json.loads(text)
        except Exception:
            return None
        return _station_origin_from_spec(parsed)
    if isinstance(spec, dict):
        lat_val = spec.get("lat") if spec.get("lat") is not None else spec.get("latitude")
        lon_val = spec.get("lon") if spec.get("lon") is not None else spec.get("longitude")
        lat = _float_or_none(lat_val)
        lon = _float_or_none(lon_val)
        grid = spec.get("grid") or spec.get("maidenhead")
        if lat is not None and lon is not None:
            dest = {"lat": lat, "lon": lon}
            if grid:
                dest["grid"] = str(grid).upper()
            else:
                try:
                    dest["grid"] = maidenhead_from_latlon(lat, lon)
                except Exception:
                    dest["grid"] = None
            return dest
        if grid:
            coords = latlon_from_maidenhead(str(grid))
            if coords:
                coords["grid"] = str(grid).upper()
                return coords
    return None


def build_station_origin_map(raw: Any) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return result
    for name, spec in raw.items():
        key = canonical_station_key(name)
        if not key:
            continue
        entry = _station_origin_from_spec(spec)
        if not entry:
            continue
        entry["name"] = str(name)
        result[key] = entry
    return result


STATION_PRESETS = build_station_origin_map(STATION_LOCATIONS_RAW)
hub = Hub(initial_station_origins=STATION_PRESETS, primary_station_name=PRIMARY_STATION_NAME)

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


def _extract_line(buffer: bytearray) -> Optional[str]:
    newline = buffer.find(b"\n")
    if newline == -1:
        return None
    raw = buffer[:newline]
    del buffer[: newline + 1]
    return raw.decode(errors="ignore").strip()

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

# ---------- N3FJP TCP client task ----------

async def n3fjp_api_client():
    await asyncio.sleep(1)
    while True:
        try:
            logging.info(f"Connecting to N3FJP at {N3FJP_HOST}:{N3FJP_API_PORT} ...")
            reader, writer = await asyncio.open_connection(N3FJP_HOST, N3FJP_API_PORT)
            hub.update_connection_state("api", connected=True, port=N3FJP_API_PORT)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            logging.info("Connected to N3FJP.")

            hb_task: Optional[asyncio.Task] = None
            try:
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

                        if "APIVERRESPONSE" in recU:
                            hub.state["apiver"] = tag(rec, "APIVER")
                            await hub.broadcast({"type": "status", "data": hub.compose_status()})
                            continue
                        if "PROGRAMRESPONSE" in recU:
                            pgm = tag(rec, "PGM"); ver = tag(rec, "VER")
                            hub.state["program"] = f"{pgm or ''} {ver or ''}".strip()
                            await hub.broadcast({"type": "status", "data": hub.compose_status()})
                            continue

                        if "OPINFORESPONSE" in recU:
                            grid = tag(rec, "GRID")
                            lat_s = tag(rec, "LAT")
                            lon_s = first_tag(rec, "LON", "LONG")
                            origin = None
                            if grid:
                                origin = latlon_from_maidenhead(grid)
                                if origin:
                                    origin["grid"] = grid
                            elif lat_s and lon_s:
                                lat = float(lat_s)
                                lon = parse_lon_west_positive(lon_s)
                                if lon is not None:
                                    origin = {"lat": lat, "lon": lon}
                                    origin["grid"] = maidenhead_from_latlon(lat, lon)
                            station_name = extract_station_name(rec)
                            if station_name:
                                hub.primary_station_name = station_name
                            if origin:
                                target_station = station_name or hub.primary_station_name
                                if target_station:
                                    await hub.set_station_origin(target_station, origin)
                                else:
                                    hub.origin = origin
                                    await hub.broadcast({"type": "origin", "data": hub.origin_payload()})
                                    await hub.broadcast({"type": "status", "data": hub.compose_status()})
                            continue

                        if "ENTEREVENT" in recU:
                            await refresh_origin_from_opinfo()

                            call = tag(rec, "CALL")
                            band = tag(rec, "BAND")
                            mode = (tag(rec, "MODE") or tag(rec, "MODETEST") or "").upper()
                            sect = (first_tag(rec, "SECTION", "ARRL_SECT") or "").upper()
                            oper = tag(rec, "OPERATOR") or tag(rec, "MYCALL") or ""
                            country = tag(rec, "COUNTRY") or ""
                            station_name = extract_station_name(rec)

                            if oper and oper not in hub.operators_seen:
                                hub.operators_seen.add(oper)
                                await hub.broadcast({"type": "operators", "data": sorted(hub.operators_seen)})

                            if station_name:
                                await hub.update_station_presence(
                                    station_name,
                                    meta={
                                        "call": call,
                                        "band": band,
                                        "mode": mode,
                                        "section": sect,
                                        "operator": oper,
                                        "country": country or None,
                                    },
                                    source="api",
                                )

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
                            if station_name:
                                base_meta["station"] = station_name
                            if country:
                                base_meta["country"] = country
                            call_key = (call or "").upper()
                            station_origin = hub.get_station_origin(station_name)
                            origin_snapshot = copy.deepcopy(station_origin) if station_origin else None
                            if origin_snapshot is None and hub.origin.get("lat") is not None:
                                origin_snapshot = copy.deepcopy(hub.origin)

                            if (WFD_MODE or PREFER_SECTION_ALWAYS) and sect:
                                sec = section_to_latlon(sect)
                                if sec:
                                    dest = {"lat": sec["lat"], "lon": sec["lon"], "grid": None}
                            if not dest and tlat_s and tlon_s:
                                lat = float(tlat_s)
                                lon = parse_lon_west_positive(tlon_s)
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
                                    await hub.emit_path(dest, base_meta, TTL_SECONDS, origin_override=copy.deepcopy(origin_snapshot) if origin_snapshot else None)
                                    last_emit = now
                                continue

                            if call:
                                hub.pending_meta[call_key] = {
                                    "meta": copy.deepcopy(base_meta),
                                    "origin": copy.deepcopy(origin_snapshot) if origin_snapshot else copy.deepcopy(hub.origin),
                                }
                                await _send(writer, f"<CMD><COUNTRYLISTLOOKUP><CALL>{call}</CALL></CMD>")

                            continue

                        if "COUNTRYLISTLOOKUPRESPONSE" in recU and hub.origin["lat"] is not None:
                            call = tag(rec, "CALL")
                            tlat_s = tag(rec, "LAT")
                            tlon_s = first_tag(rec, "LON", "LONG")
                            if tlat_s and tlon_s:
                                lat = float(tlat_s)
                                lon = parse_lon_west_positive(tlon_s)
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
                if hb_task:
                    hb_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await hb_task
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
        except asyncio.CancelledError:
            logging.warning("n3fjp_api_client task cancelled")
            hub.update_connection_state("api", connected=False, port=N3FJP_API_PORT)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            raise
        except Exception as e:
            logging.exception("N3FJP connection loop crashed")
            hub.update_connection_state("api", connected=False, port=N3FJP_API_PORT, error=str(e))
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            await asyncio.sleep(2)



async def n3fjp_status_client():
    await asyncio.sleep(1)
    while True:
        try:
            logging.info(f"Connecting to N3FJP status feed at {N3FJP_HOST}:{N3FJP_STATUS_PORT} ...")
            reader, writer = await asyncio.open_connection(N3FJP_HOST, N3FJP_STATUS_PORT)
            hub.update_connection_state("status", connected=True, port=N3FJP_STATUS_PORT)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            logging.info("Connected to N3FJP status feed.")
            try:
                buf = bytearray()
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        raise ConnectionError("N3FJP status feed closed the socket")
                    buf.extend(chunk)
                    while True:
                        rec = _extract_one_frame(buf)
                        if rec is None:
                            rec = _extract_line(buf)
                        if rec is None:
                            break
                        payload = parse_station_status_payload(rec)
                        if not payload:
                            continue
                        hub.metrics["frames_parsed_total"] += 1
                        hub.state["last_raw"] = rec
                        hub.recent_raw.append(rec)
                        station_name = payload.pop("station", None)
                        lat = payload.pop("lat", None)
                        lon = payload.pop("lon", None)
                        grid = payload.get("grid")
                        location = None
                        if lat is not None and lon is not None:
                            location = {"lat": lat, "lon": lon, "grid": grid or maidenhead_from_latlon(lat, lon)}
                        elif grid:
                            coords = latlon_from_maidenhead(grid)
                            if coords:
                                location = {"lat": coords["lat"], "lon": coords["lon"], "grid": grid}
                        await hub.update_station_presence(
                            station_name,
                            meta=payload,
                            location=location,
                            source="status",
                        )
                        operator = payload.get("operator")
                        if operator and operator not in hub.operators_seen:
                            hub.operators_seen.add(operator)
                            await hub.broadcast({"type": "operators", "data": sorted(hub.operators_seen)})
            finally:
                with contextlib.suppress(Exception):
                    writer.close()
                    await writer.wait_closed()
        except asyncio.CancelledError:
            logging.warning("n3fjp_status_client task cancelled")
            hub.update_connection_state("status", connected=False, port=N3FJP_STATUS_PORT)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            raise
        except Exception as e:
            logging.exception("N3FJP status connection loop crashed")
            hub.update_connection_state("status", connected=False, port=N3FJP_STATUS_PORT, error=str(e))
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    tasks = []
    if ENABLE_API_PORT:
        tasks.append(asyncio.create_task(n3fjp_api_client()))
    if ENABLE_STATUS_PORT:
        tasks.append(asyncio.create_task(n3fjp_status_client()))
    app.state.n3fjp_tasks = tasks

@app.on_event("shutdown")
async def shutdown_event():
    tasks = getattr(app.state, "n3fjp_tasks", []) or []
    for task in tasks:
        if task and not task.done():
            task.cancel()
    for task in tasks:
        if task:
            with contextlib.suppress(asyncio.CancelledError):
                await task
