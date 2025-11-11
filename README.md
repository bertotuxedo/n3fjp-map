# n3fjp-map
N3FJP API to a Web Browser Man via Docker Compose

- Real-time Winter Field Day (and general N3FJP) contact visualizer.
- Listens to the N3FJP TCP API, then renders live arced paths between the origin grid and the worked grid:
-- Leaflet map (2D): color by band, line style by mode (PH solid, DIG dotted, CW = Morse “K2FTS”), animated 50% sliding segment.
-- Globe (globe.gl) (3D): moving dashed arcs like the reference demo.
-- Filter by band / mode / operator (K2FTS/1, /2, …).
-- Section “hit” greying as you work more ARRL sections.
-- Automatic QRZ lookup for DX stations to improve destination grids.
-- Status card, interactive recent contacts list (click to replay arcs), and a live banner (“last logged …”).
-- Forward-looking: multi-op, multi-site friendly; works with any N3FJP suite app exposing the TCP API (API ≥ 2.2).

  Quickstart (Docker)

Requirements: Docker + Docker Compose; N3FJP running on your LAN with its TCP API Enabled (Settings → Application Program Interface).

# 1) Clone
git clone https://github.com/bertotuxedo/n3fjp-map.git
cd n3fjp-map

# 2) Configure (optional – see config below)
# Edit config/config.yaml to match your N3FJP IP/port and any defaults

# 3) Build & run
docker compose up -d --build

# 4) Open the app
# http://<host>:8080


Default port is 8080 (customize in docker-compose.yml if needed).

Configuration
A) config/config.yaml (mounted into the container)
# config/config.yaml
n3fjp:
  host: 192.168.1.126     # N3FJP machine (Windows) running the logging app
  port: 1100              # N3FJP TCP API port (default 1100)

visual:
  ttl_seconds: 600        # how long each path stays on screen
  globe_enabled: true     # enable/disable the 3D globe view
  map_bulge: 0.15         # curvature factor for 2D arcs (0.1–0.2 looks nice)

origin:
  # Fallback origin if OPINFO doesn’t have lat/lon yet
  # You can set any of: grid, lat, lon. Grid overrides lat/lon when present.
  grid: "NN00aa"
  lat:  43.9637
  lon: -75.9319

ui:
  title: "K2FTS • Winter Field Day Live Map"
  banner_enabled: true


The app will try to learn the origin lat/lon from N3FJP (<OPINFORESPONSE>). The fallback here is used until that arrives.

B) Environment variables (optional overrides)

You can also override core settings via env vars in docker-compose.yml:

services:
  n3fjp-map:
    build: ./app
    ports: ["8080:8080"]
    environment:
      N3FJP_HOST: 192.168.1.126
      N3FJP_PORT: 1100
      TTL_SECONDS: 600
      QRZ_USERNAME: your_qrz_username
      QRZ_PASSWORD: your_qrz_password
      QRZ_AGENT: n3fjp-map

    volumes:
      - ./config:/app/config:ro
      - ./app/static:/app/static:ro   # optional live-edit of UI assets

You can also copy `.env.example` to `.env` and fill in QRZ credentials locally.


How it works (high level)

On startup, the server opens a TCP client to N3FJP (N3FJP_HOST:N3FJP_PORT), sends:

<APIVER>, <PROGRAM>, <OPINFO>, and enables all updates with <SETUPDATESTATE>TRUE</…>.

It also subscribes to <CALLTABEVENT> and <ENTEREVENT> automatically emitted by N3FJP.

When a contact is actually logged (ENTER), the app:

Maps operator’s grid/latlon (origin) and DX station’s grid/latlon (destination).

Emits a path message via WebSocket to the browser.

Browser draws the curved path in Leaflet and moving dashed arc on the Globe.

Filters are applied client-side; section “hits” grey the section’s centroid pin.

UI features

Band color palette (customizable in static/app.js).

Mode line style:

PH: solid

DIG: dotted

CW: Morse dash pattern that literally spells “K2FTS”

2D Map (Leaflet): animated 50% sliding segment traveling from origin to destination.

3D Globe (globe.gl): dash animation with randomized length/gap/phase → looks alive.

Filters: band / mode / operator (operator appears once that op has logged at least one QSO).

Sections: shows section pins; pins grey out as you work them.

Diagnostics: status pill (connected/disconnected), program/APIVER, origin fix, recent frames (pretty).

API endpoints (local)

GET / – Web UI

GET /status – JSON status snapshot

GET /recent – Most recent contact summaries (server-side buffer)

GET /static/* – UI assets

WS /ws – Live stream to browser (status/origin/path/events)

Non-Docker (dev) run

Requires Python 3.10+, pip install -r app/requirements.txt

cd app
uvicorn main:app --host 0.0.0.0 --port 8080 --reload


Use config/config.yaml the same way (the app reads it on startup).

Reverse proxy / HTTPS

You can front this with Nginx Proxy Manager or Caddy:

External → NPM/Cloudflare → http://<host>:8080

If you use Cloudflare Tunnel, just expose the HTTP service on 8080 and set your hostname (e.g., wfd-map.example.org) to the tunnel.

Networking & N3FJP settings

On the N3FJP machine (Windows):

Open the logging program (e.g., Winter Field Day Contest Log).

Settings → Application Program Interface, check TCP API Enabled.

Ensure Server Running = True.

Windows Defender (or other firewall) must allow inbound TCP on the API port (default 1100).

Make sure your map container host can reach that Windows IP and port.

Troubleshooting

1) Verify Docker is running and serving UI

docker compose ps
docker logs -f n3fjp-map
# open http://<host>:8080


2) Test raw TCP connectivity from inside the container

docker exec n3fjp-map python -c "import socket; s=socket.create_connection(('192.168.1.126',1100),5); print('TCP OK'); s.close()"


If this fails:

Check N3FJP TCP API Enabled.

Confirm IP/port in config/config.yaml or env vars.

Check Windows firewall inbound rule for port 1100.

3) Confirm API talks back

docker exec -i n3fjp-map python - <<'PY'
import socket, time
s = socket.create_connection(("192.168.1.126",1100),5)
s.sendall(b"<CMD><APIVER></CMD>\r\n")
s.sendall(b"<CMD><PROGRAM></CMD>\r\n")
s.sendall(b"<CMD><OPINFO></CMD>\r\n")
time.sleep(0.2)
print(s.recv(4096).decode(errors="ignore"))
s.close()
PY


Expected to see <APIVERRESPONSE>, <PROGRAMRESPONSE>, <OPINFORESPONSE>.

4) I see two lines on the map / a stray straight line

That was fixed. Ensure you’ve deployed the latest static/app.js and hard-refresh the browser (Ctrl+F5).

If you bind-mount static/, restart the container: docker compose restart n3fjp-map.

5) No origin coordinates yet

The app learns origin from OPINFO and/or ENTEREVENT.

Use origin.grid fallback in config/config.yaml until N3FJP provides lat/lon.

Project structure
n3fjp-map/
├─ app/
│  ├─ main.py           # FastAPI, TCP client to N3FJP, WS hub
│  ├─ requirements.txt  # fastapi, uvicorn, websockets, pyyaml, etc.
│  ├─ Dockerfile
│  └─ static/
│     ├─ index.html
│     └─ app.js         # Leaflet + Globe UI (filters, animations, etc.)
├─ config/
│  └─ config.yaml       # user-editable config, mounted read-only
└─ docker-compose.yml

Development

Run uvicorn in hot-reload mode (--reload) and edit static/app.js live.

UI stack is plain JS + Leaflet + globe.gl → no build step required.

Security notes

The app only reads from N3FJP’s API over LAN TCP; it does not forward or expose the TCP port externally.

If you put the web UI on the public Internet, protect it behind auth (e.g., Nginx Proxy Manager) if needed.

No persistence: contact paths expire after ttl_seconds. If you need persistence, open an issue.

Roadmap

Full ARRL section polygons (optional layer) & per-band heat layers.

Export KML/CSV of contacts drawn.

Multi-site aggregation (merge feeds from multiple N3FJP instances).

Theming & band palette editor in the UI.

License

MIT (proposed). See LICENSE (or choose your preferred license).

Acknowledgements

N3FJP API (Affirmatech, Inc.)

OpenStreetMap contributors, Leaflet, globe.gl

One-liner deploy (for the impatient)
git clone https://github.com/bertotuxedo/n3fjp-map.git && \
cd n3fjp-map && \
docker compose up -d --build && \
echo "Open http://localhost:8080"
