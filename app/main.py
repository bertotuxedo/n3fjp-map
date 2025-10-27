# app/main.py

import asyncio
import contextlib
import os
import time
import re
import json
import logging
from typing import Set, Dict, Any, Optional, Deque, Tuple, List
from collections import deque

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

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

WFD_MODE = cfg_get("WFD_MODE", False)
PREFER_SECTION_ALWAYS = cfg_get("PREFER_SECTION_ALWAYS", False)
TTL_SECONDS = cfg_get("TTL_SECONDS", 60)
BAND_FILTER = set([b.strip() for b in str(cfg_get("BAND_FILTER", "")).split(",") if b.strip()])
MODE_FILTER = set([m.strip().upper() for m in str(cfg_get("MODE_FILTER", "")).split(",") if m.strip()])

HEARTBEAT_SECONDS = max(3, cfg_get("HEARTBEAT_SECONDS", 5))

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
        self.recent_raw: Deque[str] = deque(maxlen=100)
        self.recent_draw: Deque[Tuple[str, str, str, float]] = deque(maxlen=128)  # (call, band, mode, ts)
        self.operators_seen: Set[str] = set()
        self.sections_worked: Set[str] = set()
        # metrics
        self.metrics = {
            "frames_parsed_total": 0,
            "paths_drawn_total": 0,
            "ws_clients_gauge": 0,
            "sections_worked_total": 0,
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
            "operators": sorted(self.operators_seen),
            "sections_worked": sorted(self.sections_worked),
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

hub = Hub()

# ---------- Sections (centroids only) ----------
with open("static/sections.json", "r", encoding="utf-8") as f:
    SECTION_CENTROIDS: Dict[str, Dict[str, float]] = json.load(f)

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

def section_to_latlon(section: Optional[str]) -> Optional[Dict[str, float]]:
    if not section: return None
    return SECTION_CENTROIDS.get(section.strip().upper())

# ---------- Status endpoints ----------
@app.get("/status")
async def status():
    return JSONResponse(hub.compose_status())

@app.get("/recent")
async def recent():
    return JSONResponse({"recent": list(hub.recent_raw)})

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

# ---------- N3FJP TCP client task ----------
async def n3fjp_client():
    await asyncio.sleep(1)
    while True:
        try:
            logging.info(f"Connecting to N3FJP at {N3FJP_HOST}:{N3FJP_PORT} ...")
            reader, writer = await asyncio.open_connection(N3FJP_HOST, N3FJP_PORT)
            hub.state.update(connected=True, last_connect_ts=time.time(), last_error=None)
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            logging.info("Connected to N3FJP.")

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

                        # track operators seen
                        if oper:
                            if oper not in hub.operators_seen:
                                hub.operators_seen.add(oper)
                                await hub.broadcast({"type":"operators","data":sorted(hub.operators_seen)})

                        # destination selection
                        tlat_s = tag(rec, "LAT")
                        tlon_s = first_tag(rec, "LON", "LONG")
                        dest = None
                        if (WFD_MODE or PREFER_SECTION_ALWAYS) and sect:
                            sec = section_to_latlon(sect)
                            if sec: dest = {"lat": sec["lat"], "lon": sec["lon"], "grid": None}
                        if not dest and tlat_s and tlon_s:
                            lat = float(tlat_s); lon = parse_lon_west_positive(tlon_s)
                            if lon is not None: dest = {"lat": lat, "lon": lon, "grid": None}
                        if not dest and call:
                            await _send(writer, f"<CMD><COUNTRYLISTLOOKUP><CALL>{call}</CALL></CMD>")

                        if dest and hub.origin["lat"] is not None and hub.should_draw(call, band, mode):
                            if dest.get("grid") is None:
                                try: dest["grid"] = maidenhead_from_latlon(dest["lat"], dest["lon"])
                                except Exception: pass
                            hub.state["last_event_ts"] = time.time()
                            payload = {
                                "type":"path",
                                "data":{
                                    "from": hub.origin,
                                    "to": dest,
                                    "meta": {"call": call, "band": band, "mode": mode, "section": sect, "operator": oper},
                                    "ttl": TTL_SECONDS,
                                }
                            }
                            now = time.time()
                            if now - last_emit > 0.01:
                                hub.metrics["paths_drawn_total"] += 1
                                await hub.broadcast(payload)
                                await hub.broadcast({"type":"status","data":hub.compose_status()})

                                # mark section worked (for frontend grey-out indicator)
                                if sect:
                                    if sect not in hub.sections_worked:
                                        hub.sections_worked.add(sect)
                                        hub.metrics["sections_worked_total"] = len(hub.sections_worked)
                                        await hub.broadcast({"type":"section_hit","data":sect})
                                        await hub.broadcast({"type":"sections_worked","data":sorted(hub.sections_worked)})

                                last_emit = now
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
                                hub.state["last_event_ts"] = time.time()
                                hub.metrics["paths_drawn_total"] += 1
                                payload = {"type":"path","data":{"from": hub.origin, "to": dest, "meta": {"call": call}, "ttl": TTL_SECONDS}}
                                await hub.broadcast(payload)
                                await hub.broadcast({"type":"status","data":hub.compose_status()})
                        continue

        except asyncio.CancelledError:
            logging.warning("n3fjp_client task cancelled")
            raise
        except Exception as e:
            logging.exception("N3FJP connection loop crashed")
            hub.state.update(connected=False, last_disconnect_ts=time.time(), last_error=str(e))
            await hub.broadcast({"type": "status", "data": hub.compose_status()})
            await asyncio.sleep(2)

@app.on_event("startup")
async def startup_event():
    app.state.n3fjp_task = asyncio.create_task(n3fjp_client())

@app.on_event("shutdown")
async def shutdown_event():
    t = getattr(app.state, "n3fjp_task", None)
    if t and not t.done():
        t.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await t
