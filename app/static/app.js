// static/app.js

// ---------- Shared UI & palette ----------
const connPill   = document.getElementById('connPill');
const prog       = document.getElementById('prog');
const api        = document.getElementById('api');
const originTxt  = document.getElementById('originTxt');
const lastEvt    = document.getElementById('lastEvt');
const workedCount= document.getElementById('workedCount');
const countriesCount = document.getElementById('countriesCount');
const qrzStatus  = document.getElementById('qrzStatus');
const qrzStatusText = document.getElementById('qrzStatusText');
const recentBox  = document.getElementById('recentBox');
const messageThread = document.getElementById('messageThread');
const bandSel    = document.getElementById('bandSel');
const modeSel    = document.getElementById('modeSel');
const operSel    = document.getElementById('operSel');
const bannerText = document.getElementById('bannerText');
const btnMap     = document.getElementById('btnMap');
const btnGlobe   = document.getElementById('btnGlobe');

const BAND_STYLE = {
  "160":"#6b4e16","80":"#8b4513","60":"#b5651d","40":"#1e90ff","30":"#4682b4",
  "20":"#00a86b","17":"#2e8b57","15":"#7b68ee","12":"#9370db","10":"#ff8c00",
  "6":"#32cd32","2":"#ff1493"
};
function bandColor(band){ return BAND_STYLE[band] || "#9aa7b2"; }

// Leaflet dash for Digital
const DIG_DASH = "2 8";

// Morse “K2FTS” dash for CW (Leaflet only)
const MORSE_UNIT = 6; // px
const MORSE_SEQ  = ['-','.','-',' ','.','.','-','-','-',' ','.','.','-','.', ' ','-',' ','.','.','.'];
function morseDashArray(){
  const arr = [];
  for (const sym of MORSE_SEQ) {
    if (sym === ' ') { if (!arr.length) arr.push(0); arr[arr.length-1] += MORSE_UNIT*3; continue; }
    arr.push(sym==='-'? MORSE_UNIT*3 : MORSE_UNIT, MORSE_UNIT);
  }
  return arr.join(' ');
}
const CW_DASH = morseDashArray();

const recentContacts = [];
const broadcastMessages = [];
const mapSegments = new Map();
let selectedContactId = null;
let selectedMapHighlight = null;
let globeHighlight = null;
const stationOrigins = new Map();
const stationOriginMarkers = new Map();
let primaryStationName = 'Primary Station';

function passFilters(meta){
  if (bandSel && bandSel.value && (meta.band||"") !== bandSel.value) return false;
  if (modeSel && modeSel.value && (meta.mode||"").toUpperCase() !== modeSel.value) return false;
  if (operSel && operSel.value && (meta.operator||"") !== operSel.value) return false;
  return true;
}

function formatLatLon(point){
  if (!point) return '';
  const { lat, lon } = point;
  if (typeof lat === 'number' && typeof lon === 'number'){
    return `${lat.toFixed(2)}, ${lon.toFixed(2)}`;
  }
  return '';
}

function formatLocation(point, fallback=''){
  if (!point) return fallback;
  if (point.grid) return point.grid;
  const latlon = formatLatLon(point);
  return latlon || fallback;
}

function canonicalStationKey(name){
  return (name || '').toString().trim().toUpperCase();
}

function normalizeStationOriginEntry(entry){
  if (!entry) return null;
  const lat = Number(entry.lat);
  const lon = Number(entry.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  const name = (entry.name || entry.station || primaryStationName || 'Station').toString().trim() || primaryStationName;
  const grid = entry.grid || entry.maidenhead || '';
  const key = canonicalStationKey(name || `${lat.toFixed(2)},${lon.toFixed(2)}`);
  return { key, name, lat, lon, grid };
}

function updateOriginSummary(){
  if (!originTxt) return;
  if (!stationOrigins.size){
    originTxt.textContent = '—';
    return;
  }
  const parts = Array.from(stationOrigins.values())
    .sort((a,b)=> a.name.localeCompare(b.name))
    .map(info => {
      const loc = info.grid || formatLatLon(info);
      return loc ? `${info.name} (${loc})` : info.name;
    });
  originTxt.textContent = parts.join(' • ');
}

function upsertStationMarker(info){
  if (!info || !Number.isFinite(info.lat) || !Number.isFinite(info.lon)) return;
  let marker = stationOriginMarkers.get(info.key);
  const tooltip = `${info.name}${info.grid ? ' • ' + info.grid : ''}`;
  if (!marker){
    marker = L.circleMarker([info.lat, info.lon], {
      radius: 6,
      color: '#0ea5e9',
      weight: 2,
      fillColor: '#38bdf8',
      fillOpacity: 0.9,
    }).addTo(map);
    marker.bindTooltip(tooltip, { direction:'top' });
    stationOriginMarkers.set(info.key, marker);
  } else {
    marker.setLatLng([info.lat, info.lon]);
    const tt = marker.getTooltip && marker.getTooltip();
    if (tt) tt.setContent(tooltip);
  }
}

function registerStationOrigin(entry){
  const info = normalizeStationOriginEntry(entry);
  if (!info) return;
  stationOrigins.set(info.key, info);
  upsertStationMarker(info);
  updateOriginSummary();
}

function applyStationOriginList(list){
  const seen = new Set();
  (list || []).forEach(entry => {
    const info = normalizeStationOriginEntry(entry);
    if (!info) return;
    seen.add(info.key);
    stationOrigins.set(info.key, info);
    upsertStationMarker(info);
  });
  Array.from(stationOrigins.keys()).forEach(key => {
    if (seen.has(key)) return;
    stationOrigins.delete(key);
    const marker = stationOriginMarkers.get(key);
    if (marker){
      map.removeLayer(marker);
      stationOriginMarkers.delete(key);
    }
  });
  updateOriginSummary();
}

function formatFromContact(contact){
  if (!contact) return 'Origin';
  const station = (contact.meta?.station || '').toString().trim();
  const operator = (contact.meta?.operator || '').toString().trim();
  if (station && operator && station.toUpperCase() !== operator.toUpperCase()) return `${station} (${operator})`;
  if (station) return station;
  if (operator) return operator;
  return formatLocation(contact.from, contact.from?.grid ? contact.from.grid : 'Origin');
}

function formatToContact(contact){
  if (!contact) return 'Destination';
  const call = contact.meta?.call || formatLocation(contact.to, 'Destination');
  const loc = formatLocation(contact.to, contact.meta?.country || '');
  const suffix = loc && loc !== call ? ` (${loc})` : '';
  return `${call}${suffix}`;
}

function formatMetaDetails(contact){
  if (!contact) return '';
  const parts = [];
  if (contact.meta?.band) parts.push(`${contact.meta.band}m`);
  if (contact.meta?.mode) parts.push((contact.meta.mode || '').toUpperCase());
  if (contact.meta?.section) parts.push(contact.meta.section);
  if (contact.meta?.country) parts.push(contact.meta.country);
  return parts.join(' • ');
}

function formatTimestamp(contact){
  if (!contact || !contact.timestamp) return '';
  const date = new Date(contact.timestamp * 1000);
  return date.toLocaleString();
}

function registerContact(data){
  if (!data || data.id == null) return;
  let ts = typeof data.timestamp === 'number' ? data.timestamp : parseFloat(data.timestamp);
  if (!Number.isFinite(ts)) ts = Date.now()/1000;
  const contact = {
    id: data.id,
    timestamp: ts,
    meta: data.meta ? { ...data.meta } : {},
    from: data.from ? { ...data.from } : null,
    to: data.to ? { ...data.to } : null,
  };
  const existingIndex = recentContacts.findIndex(c => c.id === contact.id);
  if (existingIndex >= 0) recentContacts.splice(existingIndex, 1);
  recentContacts.push(contact);
  recentContacts.sort((a,b)=>a.timestamp-b.timestamp);
  while (recentContacts.length > 150) recentContacts.shift();
  if (selectedContactId === contact.id) highlightContact(contact);
}

function toggleContactSelection(id){
  if (selectedContactId === id) clearSelectedContact();
  else selectContact(id);
}

function selectContact(id){
  const contact = recentContacts.find(c => c.id === id);
  if (!contact) return;
  if (!passFilters(contact.meta || {})) return;
  selectedContactId = id;
  highlightContact(contact);
  renderRecentList();
}

function clearSelectedContact(triggerRender = true){
  selectedContactId = null;
  if (selectedMapHighlight){
    map.removeLayer(selectedMapHighlight);
    selectedMapHighlight = null;
  }
  clearSectionHighlight();
  clearCountryHighlight();
  setGlobeHighlight(null);
  if (triggerRender) renderRecentList();
}

function highlightContact(contact){
  if (selectedMapHighlight){
    map.removeLayer(selectedMapHighlight);
    selectedMapHighlight = null;
  }
  if (contact && contact.from && contact.to && typeof contact.from.lat === 'number' && typeof contact.from.lon === 'number' && typeof contact.to.lat === 'number' && typeof contact.to.lon === 'number'){
    const latlngs = arcPoints({lat:contact.from.lat,lon:contact.from.lon},{lat:contact.to.lat,lon:contact.to.lon},0.18,200);
    selectedMapHighlight = L.polyline(latlngs, { color:'#ffffff', weight:4, opacity:0.85 }).addTo(map);
    if (selectedMapHighlight.bringToFront) selectedMapHighlight.bringToFront();
  }
  if (contact?.meta?.section) highlightSection(contact.meta.section);
  if (contact?.meta?.country) highlightCountry(contact.meta.country);
  setGlobeHighlight(contact);
}

function renderRecentList(){
  if (!recentBox) return;
  const frag = document.createDocumentFragment();
  const filtered = recentContacts
    .filter(c => passFilters(c.meta || {}))
    .sort((a,b)=>b.timestamp-a.timestamp)
    .slice(0, 50);
  filtered.forEach(contact => {
    const entry = document.createElement('div');
    entry.className = 'recent-entry' + (contact.id === selectedContactId ? ' active' : '');
    entry.dataset.contactId = contact.id;
    const title = document.createElement('div');
    title.className = 'recent-title';
    title.textContent = `${formatFromContact(contact)} → ${formatToContact(contact)}`;
    const metaLine = document.createElement('div');
    metaLine.className = 'recent-meta';
    const metaParts = [formatTimestamp(contact), formatMetaDetails(contact)].filter(Boolean);
    metaLine.textContent = metaParts.join(' • ');
    entry.appendChild(title);
    entry.appendChild(metaLine);
    entry.addEventListener('click', ()=> toggleContactSelection(contact.id));
    frag.appendChild(entry);
  });
  recentBox.replaceChildren(frag);
}

function formatBroadcastTime(entry){
  if (!entry) return '';
  if (typeof entry.timestamp === 'number' && Number.isFinite(entry.timestamp)){
    return new Date(entry.timestamp * 1000).toLocaleString();
  }
  return entry.time_text || '';
}

function renderMessageThread(){
  if (!messageThread) return;
  const frag = document.createDocumentFragment();
  const ordered = broadcastMessages.slice().sort((a,b)=>{
    const ta = typeof a.timestamp === 'number' ? a.timestamp : 0;
    const tb = typeof b.timestamp === 'number' ? b.timestamp : 0;
    return ta - tb;
  });

  if (!ordered.length){
    const empty = document.createElement('div');
    empty.className = 'message-meta';
    empty.textContent = 'No broadcasts yet.';
    frag.appendChild(empty);
  } else {
    ordered.forEach(entry => {
      const wrapper = document.createElement('div');
      wrapper.className = 'message-entry';

      const meta = document.createElement('div');
      meta.className = 'message-meta';
      const sender = document.createElement('span');
      sender.textContent = entry.from || 'Broadcast';
      const time = document.createElement('span');
      time.textContent = formatBroadcastTime(entry);
      meta.appendChild(sender);
      meta.appendChild(time);

      const body = document.createElement('div');
      body.className = 'message-body';
      body.textContent = entry.message || '';

      wrapper.appendChild(meta);
      wrapper.appendChild(body);
      frag.appendChild(wrapper);
    });
  }

  messageThread.replaceChildren(frag);
}

function registerBroadcastMessage(data, shouldRender = true){
  if (!data) return;
  const toField = (data.to || '').toString().trim();
  if (toField) return; // only broadcast messages
  const entry = {
    id: data.id || `msg-${Date.now()}-${broadcastMessages.length + 1}`,
    from: (data.from || data.sender || '').toString(),
    timestamp: Number.isFinite(Number(data.timestamp)) ? Number(data.timestamp) : null,
    time_text: data.time_text || '',
    message: (data.message || '').toString().trim(),
  };
  broadcastMessages.push(entry);
  while (broadcastMessages.length > 75) broadcastMessages.shift();
  if (shouldRender) renderMessageThread();
}

function replaceBroadcastMessages(list){
  broadcastMessages.length = 0;
  (list || []).forEach(item => registerBroadcastMessage(item, false));
  renderMessageThread();
}

renderMessageThread();

function setGlobeHighlight(contact){
  if (!contact || !contact.from || !contact.to || typeof contact.from.lat !== 'number' || typeof contact.from.lon !== 'number' || typeof contact.to.lat !== 'number' || typeof contact.to.lon !== 'number'){
    globeHighlight = null;
    syncGlobeArcs();
    return;
  }
  globeHighlight = {
    startLat: contact.from.lat,
    startLng: contact.from.lon,
    endLat: contact.to.lat,
    endLng: contact.to.lon,
    color: '#ffffff',
    dashLength: 1,
    dashGap: 0,
    animMs: 8000,
    initialGap: 0,
    expireAt: Date.now() + 60*60*1000,
    meta: contact.meta || {},
    id: `highlight-${contact.id}`,
  };
  syncGlobeArcs();
}

function setSegmentVisibility(segment, visible){
  if (!segment) return;
  segment._visible = visible;
  let target = 0;
  if (visible){
    if (typeof segment._born === 'number' && typeof segment._life === 'number' && segment._life > 0){
      const age = Date.now() - segment._born;
      const base = segment._baseOpacity || 0.95;
      target = Math.max(0, base * (1 - age/segment._life));
    } else {
      target = segment._baseOpacity || 0.95;
    }
    segment._currentOpacity = target;
  }
  segment.setStyle({ opacity: target });
}

function applyFiltersToSegments(){
  mapSegments.forEach(info => {
    const visible = passFilters(info.meta || {});
    setSegmentVisibility(info.segment, visible);
  });
  syncGlobeArcs();
  const selected = recentContacts.find(c => c.id === selectedContactId);
  if (selected && !passFilters(selected.meta || {})) clearSelectedContact(false);
  renderRecentList();
}

// ---------- Leaflet map (kept) ----------
const map = L.map('map', { worldCopyJump:true }).setView([40,-95], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 12, attribution: "&copy; OpenStreetMap"
}).addTo(map);

function setOrigin(o){
  if (!o || o.lat == null || o.lon == null) return;
  const payload = { ...o };
  if (!payload.name) payload.name = primaryStationName;
  registerStationOrigin(payload);
}

// Sections (boundaries + centroids) on map
const workedSections = new Set();
const workedCountries = new Set();

const sectionPolygonGroup = L.layerGroup().addTo(map);
map.createPane('sectionHighlightPane');
map.getPane('sectionHighlightPane').style.zIndex = 650;
const sectionHighlightGroup = L.layerGroup().addTo(map);
let sectionPinsLayer = L.layerGroup();
let sectionCentroids = {};

const countryPolygonGroup = L.layerGroup().addTo(map);
map.createPane('countryHighlightPane');
map.getPane('countryHighlightPane').style.zIndex = 645;
const countryHighlightGroup = L.layerGroup().addTo(map);
let countryPinsLayer = L.layerGroup();
let countryCentroids = {};
const countryAliasIndex = new Map();

function sectionInfo(code){
  const uc = (code || '').toUpperCase();
  if (!uc) return null;
  return sectionCentroids && sectionCentroids[uc] ? sectionCentroids[uc] : null;
}

function sectionName(code){
  const info = sectionInfo(code);
  return info && info.name ? info.name : '';
}

const sectionPolygonRegistry = new Map();
const divisionColorMap = new Map();
const DIVISION_COLOR_PALETTE = [
  '#2563eb','#059669','#d97706','#7c3aed','#dc2626',
  '#0891b2','#f97316','#14b8a6','#a855f7','#ef4444',
  '#22c55e','#6366f1','#fb7185','#0ea5e9','#f59e0b'
];
let divisionColorIndex = 0;
let highlightedSectionCode = null;
let pendingHighlightCode = null;

function canonicalCountryKey(name){
  if (!name) return '';
  return name
    .toString()
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .replace(/[^\p{Letter}\p{Number}]+/gu, ' ')
    .trim()
    .toUpperCase();
}

function resolveCountryKey(name){
  const alias = canonicalCountryKey(name);
  if (!alias) return '';
  if (countryCentroids[alias]) return alias;
  return countryAliasIndex.get(alias) || alias;
}

function countryInfo(name){
  const key = resolveCountryKey(name);
  if (!key) return null;
  const info = countryCentroids[key];
  if (!info) return null;
  return { key, ...info };
}

function countryName(name){
  const info = countryInfo(name);
  return info && info.name ? info.name : (canonicalCountryKey(name) || '');
}

const countryPolygonRegistry = new Map();
let highlightedCountryKey = null;
let pendingCountryHighlight = null;

function colorForDivision(name){
  const key = (name || '').toUpperCase();
  if (!divisionColorMap.has(key)){
    const color = DIVISION_COLOR_PALETTE[divisionColorIndex % DIVISION_COLOR_PALETTE.length];
    divisionColorIndex += 1;
    divisionColorMap.set(key, color);
  }
  return divisionColorMap.get(key);
}

function markSectionPinWorked(pin){
  pin.setStyle({ fillColor: '#bbb', fillOpacity: 0.9, color: '#888' });
  if (pin.bringToFront) pin.bringToFront();
}

function markCountryPinWorked(pin){
  pin.setStyle({ fillColor: '#facc15', fillOpacity: 0.85, color: '#b45309' });
  if (pin.bringToFront) pin.bringToFront();
}

function applyWorkedStyle(code){
  if (!code) return;
  const uc = code.toUpperCase();
  const entry = sectionPolygonRegistry.get(uc);
  if (!entry) return;
  entry.worked = true;
  entry.layers.forEach(layer => {
    layer.setStyle({
      color: '#1f2937',
      weight: 2,
      opacity: 0.9,
      fillColor: entry.color,
      fillOpacity: 0.35
    });
    if (layer.bringToFront) layer.bringToFront();
  });
}

function applyCountryWorkedStyle(key){
  if (!key) return;
  const canon = resolveCountryKey(key);
  if (!canon) return;
  const entry = countryPolygonRegistry.get(canon);
  if (!entry) return;
  entry.worked = true;
  entry.layers.forEach(layer => {
    layer.setStyle({
      color: '#1e293b',
      weight: 1.5,
      opacity: 0.75,
      fillColor: '#facc15',
      fillOpacity: 0.25
    });
    if (layer.bringToFront) layer.bringToFront();
  });
}

function registerCountryPolygon(key, layer){
  const canon = resolveCountryKey(key);
  if (!canon) return;
  let entry = countryPolygonRegistry.get(canon);
  if (!entry){
    entry = { layers: [], worked: false };
    countryPolygonRegistry.set(canon, entry);
  }
  entry.layers.push(layer);
  if (workedCountries.has(canon)) applyCountryWorkedStyle(canon);
  if (pendingCountryHighlight === canon || highlightedCountryKey === canon) highlightCountry(canon);
}

function highlightCountry(name){
  const canon = resolveCountryKey(name);
  if (!canon){
    pendingCountryHighlight = null;
    return;
  }
  const entry = countryPolygonRegistry.get(canon);
  if (!entry){
    pendingCountryHighlight = canon;
    highlightedCountryKey = canon;
    countryHighlightGroup.clearLayers();
    return;
  }
  pendingCountryHighlight = null;
  highlightedCountryKey = canon;
  countryHighlightGroup.clearLayers();
  let drawn = false;
  entry.layers.forEach(layer => {
    try {
      const gj = layer.toGeoJSON();
      const highlight = L.geoJSON(gj, {
        interactive: false,
        pane: 'countryHighlightPane',
        style: () => ({
          color: '#facc15',
          weight: 2.5,
          opacity: 0.95,
          fillOpacity: 0
        })
      }).addTo(countryHighlightGroup);
      if (highlight.bringToFront) highlight.bringToFront();
      drawn = true;
    } catch {}
  });
  if (countryHighlightGroup.bringToFront) countryHighlightGroup.bringToFront();
  if (!drawn) pendingCountryHighlight = canon;
}

function clearCountryHighlight(){
  highlightedCountryKey = null;
  pendingCountryHighlight = null;
  countryHighlightGroup.clearLayers();
}

function registerSectionPolygon(code, layer, color){
  const uc = (code || '').toUpperCase();
  if (!uc) return;
  let entry = sectionPolygonRegistry.get(uc);
  if (!entry){
    entry = { color, layers: [], worked: false };
    sectionPolygonRegistry.set(uc, entry);
  }
  entry.color = color;
  entry.layers.push(layer);
  if (workedSections.has(uc)) applyWorkedStyle(uc);
  if (pendingHighlightCode === uc || highlightedSectionCode === uc) highlightSection(uc);
}

function highlightSection(code){
  const uc = (code || '').toUpperCase();
  if (!uc) return;
  const entry = sectionPolygonRegistry.get(uc);
  if (!entry){
    highlightedSectionCode = uc;
    pendingHighlightCode = uc;
    sectionHighlightGroup.clearLayers();
    return;
  }
  pendingHighlightCode = null;
  highlightedSectionCode = uc;
  sectionHighlightGroup.clearLayers();
  let drawn = false;
  entry.layers.forEach(layer => {
    try {
      const gj = layer.toGeoJSON();
      const highlight = L.geoJSON(gj, {
        interactive: false,
        pane: 'sectionHighlightPane',
        style: () => ({
          color: entry.color || '#fef3c7',
          weight: 3,
          opacity: 0.95,
          fillOpacity: 0
        })
      }).addTo(sectionHighlightGroup);
      if (highlight.bringToFront) highlight.bringToFront();
      drawn = true;
    } catch {}
  });
  if (sectionHighlightGroup.bringToFront) sectionHighlightGroup.bringToFront();
  if (!drawn) pendingHighlightCode = uc;
}

function clearSectionHighlight(){
  highlightedSectionCode = null;
  pendingHighlightCode = null;
  sectionHighlightGroup.clearLayers();
}

map.on('click', ()=>{
  clearSectionHighlight();
  clearCountryHighlight();
  if (selectedContactId !== null) clearSelectedContact();
});

async function loadSections(){
  try {
    const res = await fetch('/static/data/centroids/sections.json');
    if (!res.ok) return;
    sectionCentroids = await res.json();
    Object.entries(sectionCentroids).forEach(([code, pt])=>{
      const label = pt?.name ? `${code} • ${pt.name}` : `${code}`;
      const pin = L.circleMarker([pt.lat, pt.lon], {
        radius: 7, color:'#999', weight:1, fillColor:'#ddd', fillOpacity:0.4
      }).bindTooltip(label, {sticky:true});
      pin._sectionCode = code.toUpperCase();
      pin.on('click', (ev)=>{ highlightSection(pin._sectionCode); L.DomEvent.stop(ev); });
      sectionPinsLayer.addLayer(pin);
      if (workedSections.has(pin._sectionCode)) markSectionPinWorked(pin);
    });
  } catch {}
}
loadSections();

async function loadCountries(){
  try {
    const res = await fetch('/static/data/centroids/countries.geojson');
    if (!res.ok) return;
    const geojson = await res.json();
    countryCentroids = {};
    countryAliasIndex.clear();
    countryPinsLayer.clearLayers();
    const features = Array.isArray(geojson?.features) ? geojson.features : [];
    features.forEach(feature => {
      const props = feature?.properties || {};
      const geom = feature?.geometry || {};
      if (!geom || geom.type !== 'Point') return;
      const coords = Array.isArray(geom.coordinates) ? geom.coordinates : [];
      if (coords.length < 2) return;
      const lon = Number(coords[0]);
      const lat = Number(coords[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      const primary = props.COUNTRY || props.preferred_term || props.english_short || props.NAME || props.name || '';
      const iso2 = (props.ISO || props.iso2_code || props.AFF_ISO || '').toString().toUpperCase();
      const iso3 = (props.iso3_code || '').toString().toUpperCase();
      const aliases = [primary, props.COUNTRYAFF, props.english_short, props.spanish_short, props.french_short, props.russian_short, props.chinese_short, props.arabic_short, iso2, iso3];
      const canonicalAliases = aliases.map(canonicalCountryKey).filter(Boolean);
      const baseKey = canonicalAliases.find(alias => !countryCentroids[alias]) || canonicalAliases[0];
      if (!baseKey) return;
      const info = {
        lat,
        lon,
        name: primary || props.english_short || iso2 || iso3 || baseKey,
        iso2,
        iso3
      };
      if (!countryCentroids[baseKey]){
        countryCentroids[baseKey] = info;
      }
      canonicalAliases.forEach(alias => { if (alias) countryAliasIndex.set(alias, baseKey); });
      countryAliasIndex.set(baseKey, baseKey);
      const labelParts = [info.name, info.iso2].filter(Boolean);
      const marker = L.circleMarker([lat, lon], {
        radius: 5,
        color: '#92400e',
        weight: 1,
        fillColor: '#fbbf24',
        fillOpacity: 0.35
      }).bindTooltip(labelParts.join(' • '), { sticky: true });
      marker._countryKey = baseKey;
      marker.on('click', ev => { highlightCountry(baseKey); L.DomEvent.stop(ev); });
      countryPinsLayer.addLayer(marker);
      if (workedCountries.has(baseKey)) markCountryPinWorked(marker);
    });
    const normalized = new Set();
    workedCountries.forEach(key => {
      const canon = resolveCountryKey(key);
      if (canon) normalized.add(canon);
    });
    if (normalized.size){
      const changed = normalized.size !== workedCountries.size || [...normalized].some(k => !workedCountries.has(k));
      if (changed){
        workedCountries.clear();
        normalized.forEach(k => workedCountries.add(k));
      }
    }
    if (countriesCount) countriesCount.textContent = workedCountries.size.toString();
  } catch {}
}
const loadCountriesPromise = loadCountries();

async function loadCountryPolygons(){
  try {
    const res = await fetch('/static/data/boundaries/world-administrative-boundaries-countries.geojson');
    if (!res.ok) return;
    const geojson = await res.json();
    L.geoJSON(geojson, {
      style: () => ({
        color: '#1f2937',
        weight: 0.6,
        opacity: 0.35,
        fillColor: '#0f172a',
        fillOpacity: 0.03
      }),
      onEachFeature: (feature, layer) => {
        const props = feature?.properties || {};
        const aliasCandidates = [
          props.COUNTRY,
          props.preferred_term,
          props.english_short,
          props.NAME,
          props.name,
          props.country,
          props.Country,
          props.COUNTRYAFF,
          props.iso2_code,
          props.iso3_code,
          props.ISO,
          props.AFF_ISO
        ];
        const canonical = aliasCandidates.map(canonicalCountryKey).find(Boolean);
        if (!canonical) return;
        let key = resolveCountryKey(canonical);
        if (!key) {
          key = canonical;
          const baseName = props.english_short || props.preferred_term || props.COUNTRY || canonical;
          const geoPt = props.geo_point_2d;
          if (geoPt && typeof geoPt.lat === 'number' && typeof geoPt.lon === 'number' && !countryCentroids[key]){
            countryCentroids[key] = {
              lat: geoPt.lat,
              lon: geoPt.lon,
              name: baseName,
              iso2: (props.iso2_code || '').toString().toUpperCase(),
              iso3: (props.iso3_code || '').toString().toUpperCase()
            };
          }
        }
        if (!key) return;
        countryAliasIndex.set(canonical, key);
        layer._countryKey = key;
        const displayName = countryName(key) || canonical;
        layer.bindTooltip(displayName, { sticky: true });
        layer.on('click', ev => { highlightCountry(key); L.DomEvent.stop(ev); });
        registerCountryPolygon(key, layer);
        if (workedCountries.has(key)) applyCountryWorkedStyle(key);
      }
    }).addTo(countryPolygonGroup);
  } catch {}
}
loadCountriesPromise.finally(()=> loadCountryPolygons());

async function loadSectionPolygons(){
  try {
    const res = await fetch('/static/data/divisions.json');
    if (!res.ok) return;
    const manifest = await res.json();
    const entries = Object.entries(manifest);
    await Promise.all(entries.map(async ([division, path])=>{
      try {
        const gjRes = await fetch(path);
        if (!gjRes.ok) return;
        const geojson = await gjRes.json();
        const divColor = colorForDivision(division);
        L.geoJSON(geojson, {
          style: () => ({
            color: divColor,
            weight: 1,
            opacity: 0.45,
            fillColor: divColor,
            fillOpacity: 0.05
          }),
          onEachFeature: (feature, layer) => {
            const props = feature?.properties || {};
            let code = props.section_code || props.SECTION || props.Section || props.id || props.ID || '';
            if (typeof code !== 'string') code = `${code ?? ''}`;
            const uc = code.toUpperCase();
            let name = props.section_name || props.SECTION_NAME || props.name || props.NAME || '';
            if (!name) name = sectionName(uc);
            if (uc){
              layer._sectionCode = uc;
              layer.bindTooltip(`${uc}${name ? ` • ${name}` : ''}`, {sticky:true});
              registerSectionPolygon(uc, layer, divColor);
              layer.on('click', (ev)=>{ highlightSection(uc); L.DomEvent.stop(ev); });
              if (workedSections.has(uc)) applyWorkedStyle(uc);
            }
          }
        }).addTo(sectionPolygonGroup);
      } catch {}
    }));
  } catch {}
}
loadSectionPolygons();

function graySection(code){
  if (!code) return;
  const uc = code.toUpperCase();
  if (workedSections.has(uc)) return;
  workedSections.add(uc);
  workedCount.textContent = workedSections.size.toString();
  sectionPinsLayer.eachLayer(l=>{ if (l._sectionCode===uc) markSectionPinWorked(l); });
  applyWorkedStyle(uc);
}

function grayCountry(name){
  const info = countryInfo(name) || {};
  const key = info.key || resolveCountryKey(name);
  if (!key) return;
  if (workedCountries.has(key)) return;
  workedCountries.add(key);
  if (countriesCount) countriesCount.textContent = workedCountries.size.toString();
  countryPinsLayer.eachLayer(l => { if (l._countryKey === key) markCountryPinWorked(l); });
  applyCountryWorkedStyle(key);
}

// Curved arc via quadratic bezier (for map)
function arcPoints(from, to, bulge=0.15, steps=160){
  const lat1=Number(from?.lat);
  const lon1=Number(from?.lon);
  const lat2=Number(to?.lat);
  const lon2=Number(to?.lon);
  if (![lat1, lon1, lat2, lon2].every(Number.isFinite)) {
    // If we can't parse real coordinates, skip drawing.
    return [];
  }
  const mx=(lat1+lat2)/2, my=(lon1+lon2)/2;
  const dx=lat2-lat1, dy=lon2-lon1, len=Math.hypot(dx,dy)||1e-6;
  const nx=-(dy/len), ny=(dx/len);
  const ctrl={lat: mx+nx*bulge*Math.min(10,len), lon: my+ny*bulge*Math.min(10,len)};
  const pts=[];
  for(let t=0;t<=1.0001;t+=1/steps){
    const lat=(1-t)*(1-t)*lat1 + 2*(1-t)*t*ctrl.lat + t*t*lat2;
    const lon=(1-t)*(1-t)*lon1 + 2*(1-t)*t*ctrl.lon + t*t*lon2;
    pts.push([lat,lon]);
  }
  return pts;
}

// Sliding segment (~50%) along arc (map)
function animateSlidingSegment(latlngs, lifeSec, styleOpts){
  const total=latlngs.length; if (total<4) return null;
  const segPts=Math.max(3, Math.floor(total*0.5));
  const line=L.polyline([], styleOpts).addTo(map);
  const periodMs=4000, start=performance.now();
  function update(now){
    const t=((now-start)%periodMs)/periodMs;
    const head=Math.floor(t*(total-1));
    const tail=Math.max(0, head - segPts);
    line.setLatLngs(latlngs.slice(tail, head+1));
    line._animHandle=requestAnimationFrame(update);
  }
  line._animHandle=requestAnimationFrame(update);
  const born=Date.now(), life=(lifeSec||60)*1000;
  const baseOpacity=(styleOpts && typeof styleOpts.opacity==='number')? styleOpts.opacity : 0.95;
  line._baseOpacity = baseOpacity;
  line._visible = true;
  line._currentOpacity = baseOpacity;
  line._born = born;
  line._life = life;
  line._expiresAt = born + life;
  (function fade(){
    const age=Date.now()-born;
    const fadeOp=Math.max(0, baseOpacity*(1-age/life));
    const target=line._visible ? fadeOp : 0;
    line._currentOpacity = target;
    line.setStyle({opacity:target});
    if (age<life){
      requestAnimationFrame(fade);
    } else {
      cancelAnimationFrame(line._animHandle);
      map.removeLayer(line);
      if (typeof line._onExpire === 'function') line._onExpire();
    }
  })();
  return line;
}

// Draw path on map
function drawPathMap(payload){
  const {from,to,meta,ttl,id}=payload || {}; if (!from||!to) return;

  const clr=bandColor(meta?.band);
  const mode=(meta?.mode||"").toUpperCase();
  const style={ color:clr, weight:3, opacity:0.95 };
  if (mode==="DIG") style.dashArray=DIG_DASH;
  else if (mode==="CW") style.dashArray=CW_DASH;

  const latlngs=arcPoints({lat:from.lat,lon:from.lon},{lat:to.lat,lon:to.lon},0.15,160);
  const seg=animateSlidingSegment(latlngs, ttl||60, style);
  const tip=[ meta?.call||"", meta?.band?`${meta.band}m`:"", mode||"", meta?.section?`→ ${meta.section}`:"", meta?.operator?`• ${meta.operator}`:"" ]
    .filter(Boolean).join(" • ");
  if (seg){
    const segmentId = id != null ? id : `anon-${Date.now()}-${Math.random()}`;
    seg.bindTooltip(tip,{sticky:true});
    seg._pathId = segmentId;
    seg._onExpire = ()=>{ mapSegments.delete(segmentId); };
    mapSegments.set(segmentId, { segment: seg, meta: meta || {} });
    setSegmentVisibility(seg, passFilters(meta || {}));
  }
}

// ---------- Globe.gl (updated animation like reference) ----------
let globe, globeArcs=[];
const globeEl = document.getElementById('globeCanvas');

function syncGlobeArcs(){
  if (!globe) return;
  const base = globeArcs.filter(arc => passFilters(arc.meta || {}));
  const data = globeHighlight ? base.concat([globeHighlight]) : base;
  globe.arcsData(data);
}

function ensureGlobe(){
  if (globe) return globe;
  globe = Globe()(globeEl)
    .globeImageUrl('//unpkg.com/three-globe/example/img/earth-night.jpg')
    .backgroundColor('#0b0e12')
    .showAtmosphere(true)
    .atmosphereColor('#6db8ff')
    .atmosphereAltitude(0.18)
    .arcsTransitionDuration(0) // don’t tween on data changes
    .arcColor(d => d.color)
    .arcsData(globeArcs)
    // Reference-style dash animation (moving along arc):
    .arcDashLength(d => d.dashLength)        // 0..1 visible
    .arcDashGap(d => d.dashGap)              // 0..1 gap
    .arcDashInitialGap(d => d.initialGap)    // phase offset
    .arcDashAnimateTime(d => d.animMs)       // ms for full sweep
    .pointOfView({ lat: 22, lng: -20, altitude: 2.2 }, 0);

  // gentle autorotation
  let lastT = performance.now();
  (function spin(now){
    const dt=(now-lastT)/1000; lastT=now;
    const pov = globe.pointOfView();
    globe.pointOfView({ lat: pov.lat, lng: pov.lng + dt*2, altitude: pov.altitude }, 0);
    globe._spinHandle = requestAnimationFrame(spin);
  })(lastT);

  // TTL purge
  setInterval(()=> purgeExpiredGlobeArcs(), 1000);
  syncGlobeArcs();
  return globe;
}

function purgeExpiredGlobeArcs(){
  const now = Date.now();
  const before = globeArcs.length;
  globeArcs = globeArcs.filter(a => now < a.expireAt);
  if (globe && globeArcs.length !== before) syncGlobeArcs();
}

function addGlobeArc(path){
  if (!path) return;
  const { from, to, meta, ttl, id } = path;
  if (!from || !to) return;
  const bandClr = bandColor(meta?.band);

  // --- Make motion like the original demo ---
  // Randomized dash length/gap and animation time per arc,
  // plus a randomized initial phase so arcs aren’t in lockstep.
  const dashLength = Math.random();             // 0..1
  const dashGap    = Math.random();             // 0..1
  const animMs     = 2000 + Math.random()*5000; // slower sweep
  const initialGap = Math.random();             // phase seed

  const arc = {
    startLat: from.lat, startLng: from.lon,
    endLat:   to.lat,   endLng:   to.lon,
    color:    bandClr,
    dashLength, dashGap, animMs, initialGap,
    expireAt: Date.now() + (ttl||60)*1000,
    meta: meta || {},
    id
  };
  globeArcs.push(arc);
  syncGlobeArcs();
}

// ---------- View toggle ----------
btnMap.addEventListener('click', ()=>{
  btnMap.classList.add('active'); btnGlobe.classList.remove('active');
  document.getElementById('map').style.display='block';
  globeEl.style.display='none';
});

btnGlobe.addEventListener('click', ()=>{
  btnGlobe.classList.add('active'); btnMap.classList.remove('active');
  document.getElementById('map').style.display='none';
  globeEl.style.display='block';
  ensureGlobe();
  syncGlobeArcs();
});

if (recentBox) recentBox.addEventListener('click', (ev)=>{ if (ev.target === recentBox) clearSelectedContact(); });

[bandSel, modeSel, operSel].forEach(sel => {
  if (sel) sel.addEventListener('change', ()=> applyFiltersToSegments());
});

// ---------- Status & WS ----------
function renderQrzStatus(statusPayload){
  if (!qrzStatus || !qrzStatusText) return;
  const data = statusPayload?.qrz_logbook || statusPayload?.qrz || statusPayload;
  const configured = !!(data && data.configured);
  const connected = !!(data && data.connected);
  const lastError = data?.last_error || data?.last_reason;
  const attempted = !!(data?.last_attempt_ts || data?.last_check_ts);
  const hadSuccess = !!data?.last_success_ts;
  const result = (data?.last_result || "").toUpperCase();

  let cls = 'status-chip off';
  let label = 'QRZ disabled';
  let title = 'QRZ logbook credentials are not configured.';

  if (configured && (connected || result === 'OK')){
    cls = 'status-chip ok';
    label = 'QRZ logbook OK';
    title = 'Authenticated with the QRZ Logbook API.';
  } else if (configured && result === 'AUTH'){
    cls = 'status-chip bad';
    label = 'QRZ auth';
    title = lastError ? `Access denied: ${lastError}` : 'Logbook key lacks required privileges.';
  } else if (configured && (result === 'FAIL' || result === 'ERROR')){
    cls = 'status-chip bad';
    label = 'QRZ error';
    title = lastError ? `Last QRZ error: ${lastError}` : 'Logbook request failed.';
  } else if (configured && hadSuccess){
    cls = 'status-chip warn';
    label = 'QRZ idle';
    title = 'Previously authenticated; waiting for the next QRZ interaction.';
  } else if (configured && attempted){
    cls = 'status-chip warn';
    label = 'QRZ pending';
    title = 'Credentials are set; awaiting a logbook response.';
  } else if (configured){
    cls = 'status-chip warn';
    label = 'QRZ pending';
    title = 'Waiting for the first QRZ logbook status check.';
  }

  qrzStatus.className = `${cls}`;
  qrzStatusText.textContent = label;
  qrzStatus.title = title;
}

function updateOperators(list){
  const cur=operSel.value;
  operSel.innerHTML='<option value="">All</option>';
  (list||[]).forEach(op=>{ const o=document.createElement('option'); o.value=op; o.textContent=op; operSel.appendChild(o); });
  if ([...operSel.options].some(o=>o.value===cur)) operSel.value=cur;
  applyFiltersToSegments();
}

async function refreshStatus(){
  try{
    const s=await fetch('/status').then(r=>r.json());
    if (s.connected){ connPill.textContent='Connected'; connPill.className='pill ok'; }
    else { connPill.textContent='Disconnected'; connPill.className='pill bad'; }
    prog.textContent=s.program || '—';
    api.textContent=s.apiver || '—';
    renderQrzStatus(s);
    if (typeof s.primary_station_name === 'string' && s.primary_station_name.trim()) primaryStationName = s.primary_station_name;
    if (Array.isArray(s.station_origins)){
      applyStationOriginList(s.station_origins);
    } else if (s.origin?.lat != null) {
      setOrigin(s.origin);
    }
    if (s.last_event_ts) lastEvt.textContent=new Date(s.last_event_ts*1000).toLocaleString();
    if (s.sections_worked){
      workedSections.clear();
      workedCount.textContent = '0';
      s.sections_worked.forEach(graySection);
    }
    if (s.countries_worked){
      workedCountries.clear();
      if (countriesCount) countriesCount.textContent = '0';
      s.countries_worked.forEach(grayCountry);
    }
    if (s.operators) updateOperators(s.operators);
    if (s.broadcast_messages) replaceBroadcastMessages(s.broadcast_messages);
  } catch {}
}
refreshStatus(); setInterval(refreshStatus, 5000);

const proto = location.protocol === 'https:' ? 'wss' : 'ws';
const ws = new WebSocket(`${proto}://${location.host}/ws`);
ws.onopen = () => { connPill.textContent='Connected'; connPill.className='pill ok'; };
ws.onclose = () => { connPill.textContent='Disconnected'; connPill.className='pill bad'; };
ws.onmessage = (ev)=>{
  try{
    const msg=JSON.parse(ev.data);
    if (msg.type==='status'){
      const s=msg.data;
      if (s.program) prog.textContent=s.program;
      if (s.apiver)  api.textContent=s.apiver;
      renderQrzStatus(s);
      if (s.last_event_ts) lastEvt.textContent=new Date(s.last_event_ts*1000).toLocaleString();
      if (typeof s.primary_station_name === 'string' && s.primary_station_name.trim()) primaryStationName = s.primary_station_name;
      if (Array.isArray(s.station_origins)) applyStationOriginList(s.station_origins);
      if (s.broadcast_messages) replaceBroadcastMessages(s.broadcast_messages);
    } else if (msg.type==='origin'){
      setOrigin(msg.data);
    } else if (msg.type==='station_origins'){
      applyStationOriginList(msg.data || []);
    } else if (msg.type==='station_origin'){
      registerStationOrigin(msg.data);
    } else if (msg.type==='broadcast_message'){
      registerBroadcastMessage(msg.data);
    } else if (msg.type==='path'){
      const data = msg.data || {};
      // map
      drawPathMap(data);
      // globe
      addGlobeArc(data);
      // contacts panel
      registerContact(data);
      renderRecentList();
      // banner
      const c = data.meta?.call || '—';
      bannerText.textContent = `Last logged: ${c} • ${new Date().toLocaleTimeString()}`;
      // section dim
      if (data.meta?.section) graySection(data.meta.section);
      if (data.meta?.country) grayCountry(data.meta.country);
    } else if (msg.type==='operators'){
      updateOperators(msg.data||[]);
    } else if (msg.type==='section_hit'){
      graySection(msg.data);
    } else if (msg.type==='sections_worked'){
      (msg.data||[]).forEach(graySection);
    } else if (msg.type==='country_hit'){
      grayCountry(msg.data);
    } else if (msg.type==='countries_worked'){
      (msg.data||[]).forEach(grayCountry);
    }
  } catch {}
};

// seed recent (pretty)
(async function loadRecent(){
  try{
    const r=await fetch('/recent').then(r=>r.json());
    (r.recent||[]).forEach(registerContact);
    renderRecentList();
  } catch {}
})();
