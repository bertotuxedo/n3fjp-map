# n3fjp-map
# WINTER FIELD DAY LOG - TESTING PHASE

Real-time map and globe visualization for stations logging contacts with the N3FJP suite. The service listens to the N3FJP TCP API, enriches contacts with optional QRZ.com lookups, and broadcasts live contact arcs to a web UI for operators and viewers.

<img width="1914" height="904" alt="image" src="https://github.com/user-attachments/assets/f8331f58-db75-4bc0-a5bd-c900584760ae" />
<img width="1895" height="880" alt="image" src="https://github.com/user-attachments/assets/ed23cddb-0356-4142-826b-9713bcfc49e4" />


## Features
- **Live contact visualization** on a Leaflet map (2D) and globe.gl (3D) with animated arcs.
- **Band and mode styling** (color by band, solid/dashed patterns by mode including "K2FTS" Morse pattern for CW).
- **Operator, band, and mode filters** applied client-side.
- **ARRL section tracking** with automatic greying of worked sections.
- **Status dashboard** showing API heartbeat, origin fix, and recent contacts.
- **Optional QRZ.com integration** to improve DX station grid / location data.
- **Multi-station awareness** so every networked logger can originate contacts from its own grid square or lat/lon.

## Prerequisites
- Docker and Docker Compose (for containerized deployment), or Python 3.10+ for a local run.
- An N3FJP logging application on your network with the **TCP API enabled** (Settings → Application Program Interface → Enable TCP API).

## Quick start with Docker Compose
1. Clone the repository and enter it:
   ```bash
   git clone https://github.com/bertotuxedo/n3fjp-map.git
   cd n3fjp-map
   ```
2. Adjust `config/config.yaml` for your station (see [Configuration](#configuration)).
3. (Optional but recommended) Create a `.env` file in the repo root for secrets such as QRZ credentials (these are consumed by `docker-compose.yaml`). You can start from `.env.example`:
   ```bash
   echo "QRZ_USERNAME=your_callsign" >> .env
   echo "QRZ_PASSWORD=your_password" >> .env
   echo "QRZ_AGENT=n3fjp-map" >> .env
   ```
4. Launch the stack:
   ```bash
   docker compose up --build -d
   ```
5. Open the UI at `http://<host>:8080`.

The compose file mounts `config/config.yaml` into the container at `/config/config.yaml` and exports port `8080` from the FastAPI app.

### Managing the container
```bash
# Follow the application logs
docker compose logs -f n3fjp-map

# Stop the stack
docker compose down
```

## Configuration
The primary configuration lives in `config/config.yaml` (mounted read-only by Docker). Example values:

```yaml
# N3FJP TCP server location
N3FJP_HOST: "192.168.1.123"
N3FJP_PORT: 1100

# Behavior
WFD_MODE: true                 # prefer ARRL section centroids when available
PREFER_SECTION_ALWAYS: false   # force all contacts to section centroids if true
TTL_SECONDS: 600               # how long a path persists (seconds)
HEARTBEAT_SECONDS: 5           # poll interval for liveness

# Identity & station origins
PRIMARY_STATION_NAME: "Run 1"   # label for the PC hosting the TCP API
STATION_LOCATIONS:
  "Run 1":
    grid: "FN31pr"              # grid or lat/lon for the primary logger
  "GOTA":
    lat: 34.932
    lon: -81.025

# Optional server-side filters (comma-separated)
BAND_FILTER: ""               # e.g. "20,40,80"
MODE_FILTER: ""               # e.g. "PH,CW"
```

Configuration values in the YAML file take precedence over environment variables (including values loaded from `.env` by Docker Compose). After editing the file, restart the container to apply the changes.

`STATION_LOCATIONS` is optional but highly recommended when you network multiple PCs via N3FJP's File Share or TCP methods. Each key should match the "Station Name" you configure in the Network Status Display form, and you can supply either a Maidenhead grid or explicit `lat`/`lon` coordinates. The UI will show a marker for every configured station so arcs originate from the correct location even when contacts are logged remotely. `PRIMARY_STATION_NAME` controls the label for the machine hosting the TCP API.

### Environment variables
You can override most configuration keys using environment variables (matching the YAML keys). The compose file sets `CONFIG_FILE=/config/config.yaml` so the application loads your YAML configuration automatically. Additional useful variables include:

- `QRZ_USERNAME`, `QRZ_PASSWORD`, `QRZ_AGENT` — credentials for QRZ.com lookups (set in `.env` and wired through `docker-compose.yaml`).
- `TTL_SECONDS`, `BAND_FILTER`, `MODE_FILTER` — control visibility and filtering.

For local overrides without editing the compose file, create a `.env` file and set your variables before running `docker compose`.

### Putting it all together
The Docker Compose definition now loads both configuration sources so everything is in play when the container boots:

- The `/config/config.yaml` volume is mounted read-only and pointed to via `CONFIG_FILE` so the service always reads the YAML options you commit to source control.
- The `.env` file (if present) is loaded via `env_file` and feeds sensitive values like `QRZ_USERNAME`/`QRZ_PASSWORD` into the container without hard-coding them in the YAML file.
- Environment variables set by `.env` supply the QRZ settings—`config/config.yaml` no longer includes those keys so secrets stay outside version control.

This holistic setup ensures your static config, runtime secrets, and container wiring stay aligned without manual edits in multiple places.

## Local development run (without Docker)
1. Ensure Python 3.10+ is installed.
2. Install dependencies and run the FastAPI app:
   ```bash
   cd app
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8080 --reload
   ```
3. Place `config/config.yaml` where the app can read it, or set `CONFIG_FILE=/path/to/config.yaml` before launching.

## API and UI endpoints
- `GET /` – Web UI containing the 2D map and 3D globe.
- `GET /status` – JSON snapshot with current connection status and metadata.
- `GET /recent` – Server-side buffer of the most recent contacts.
- `GET /static/*` – Static UI assets.
- `WS /ws` – WebSocket stream used by the UI for status/origin/path updates.

## How it works
1. On startup the FastAPI service connects to the N3FJP TCP API (`N3FJP_HOST:N3FJP_PORT`).
2. It sends `<APIVER>`, `<PROGRAM>`, `<OPINFO>`, and `<SETUPDATESTATE>TRUE</SETUPDATESTATE>` to subscribe to live updates.
3. As contacts are logged (`<ENTEREVENT>`), the server determines origin/destination grid squares or coordinates, enriches them via QRZ (when configured), and pushes path events to the browser over WebSocket.
4. The browser animates the arcs, updates filters, and tracks section status in real time.

## Preparing N3FJP
On the Windows host running your N3FJP logger:
1. Open the contest logging program (e.g., Winter Field Day Contest Log).
2. Navigate to **Settings → Application Program Interface**.
3. Check **TCP API Enabled**, ensure the port matches `N3FJP_PORT`, and note the machine's IP for `N3FJP_HOST`.

## QRZ.com integration
If QRZ credentials are supplied, the app performs lookups for non-local stations to supplement grid and location data. Sessions are cached and refreshed automatically; missing or invalid credentials simply skip QRZ lookups.

## Troubleshooting tips
- Ensure the Docker host can reach the Windows machine on the TCP port (default `1100`).
- If no contacts appear, verify the N3FJP API is enabled and the API version is ≥ 2.2.
- Review `docker compose logs` for connection or QRZ errors.

Enjoy visualizing your live contacts!
