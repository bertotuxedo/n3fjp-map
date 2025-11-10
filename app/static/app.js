// static/app.js

// ---------- Shared UI & palette ----------
const connPill   = document.getElementById('connPill');
const prog       = document.getElementById('prog');
const api        = document.getElementById('api');
const originTxt  = document.getElementById('originTxt');
const lastEvt    = document.getElementById('lastEvt');
const workedCount= document.getElementById('workedCount');
const recentBox  = document.getElementById('recentBox');
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

function passFilters(meta){
  if (bandSel && bandSel.value && (meta.band||"") !== bandSel.value) return false;
  if (modeSel && modeSel.value && (meta.mode||"").toUpperCase() !== modeSel.value) return false;
  if (operSel && operSel.value && (meta.operator||"") !== operSel.value) return false;
  return true;
}

// ---------- Leaflet map (kept) ----------
const map = L.map('map', { worldCopyJump:true }).setView([40,-95], 4);
L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 12, attribution: "&copy; OpenStreetMap"
}).addTo(map);

let originMarker;
function setOrigin(o){
  if (o.lat == null || o.lon == null) return;
  if (!originMarker) originMarker = L.marker([o.lat,o.lon], { title:`Origin ${o.grid||''}`}).addTo(map);
  else originMarker.setLatLng([o.lat,o.lon]);
  originTxt.textContent = `${o.grid||''} (${o.lat.toFixed(3)}, ${o.lon.toFixed(3)})`;
}

// Sections (boundaries + centroids) on map
const workedSections = new Set();

const sectionPolygonGroup = L.layerGroup().addTo(map);
map.createPane('sectionHighlightPane');
map.getPane('sectionHighlightPane').style.zIndex = 650;
const sectionHighlightGroup = L.layerGroup().addTo(map);
let sectionPinsLayer = L.layerGroup().addTo(map);
let sectionCentroids = {};

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

function colorForDivision(name){
  const key = (name || '').toUpperCase();
  if (!divisionColorMap.has(key)){
    const color = DIVISION_COLOR_PALETTE[divisionColorIndex % DIVISION_COLOR_PALETTE.length];
    divisionColorIndex += 1;
    divisionColorMap.set(key, color);
  }
  return divisionColorMap.get(key);
}

function markPinWorked(pin){
  pin.setStyle({ fillColor: '#bbb', fillOpacity: 0.9, color: '#888' });
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

map.on('click', clearSectionHighlight);

async function loadSections(){
  try {
    const res = await fetch('/static/sections.json');
    if (!res.ok) return;
    sectionCentroids = await res.json();
    Object.entries(sectionCentroids).forEach(([code, pt])=>{
      const pin = L.circleMarker([pt.lat, pt.lon], {
        radius: 7, color:'#999', weight:1, fillColor:'#ddd', fillOpacity:0.4
      }).bindTooltip(`${code}`, {sticky:true});
      pin._sectionCode = code.toUpperCase();
      pin.on('click', (ev)=>{ highlightSection(pin._sectionCode); L.DomEvent.stop(ev); });
      sectionPinsLayer.addLayer(pin);
      if (workedSections.has(pin._sectionCode)) markPinWorked(pin);
    });
  } catch {}
}
loadSections();

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
            const code = feature?.properties?.section_code || feature?.properties?.SECTION || '';
            const name = feature?.properties?.section_name || feature?.properties?.SECTION_NAME || '';
            const uc = code.toUpperCase();
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
  sectionPinsLayer.eachLayer(l=>{ if (l._sectionCode===uc) markPinWorked(l); });
  applyWorkedStyle(uc);
}

// Curved arc via quadratic bezier (for map)
function arcPoints(from, to, bulge=0.15, steps=160){
  const lat1=from.lat, lon1=from.lon, lat2=to.lat, lon2=to.lon;
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
  const periodMs=2000, start=performance.now();
  function update(now){
    const t=((now-start)%periodMs)/periodMs;
    const head=Math.floor(t*(total-1));
    const tail=Math.max(0, head - segPts);
    line.setLatLngs(latlngs.slice(tail, head+1));
    line._animHandle=requestAnimationFrame(update);
  }
  line._animHandle=requestAnimationFrame(update);
  const born=Date.now(), life=(lifeSec||60)*1000;
  (function fade(){
    const age=Date.now()-born, op=Math.max(0,0.95*(1-age/life));
    line.setStyle({opacity:op});
    if (age<life) requestAnimationFrame(fade); else { cancelAnimationFrame(line._animHandle); map.removeLayer(line); }
  })();
  return line;
}

// Draw path on map
function drawPathMap(payload){
  const {from,to,meta,ttl}=payload; if (!from||!to) return;
  if (!passFilters(meta||{})) return;

  const clr=bandColor(meta?.band);
  const mode=(meta?.mode||"").toUpperCase();
  const style={ color:clr, weight:3, opacity:0.95 };
  if (mode==="DIG") style.dashArray=DIG_DASH;
  else if (mode==="CW") style.dashArray=CW_DASH;

  const latlngs=arcPoints({lat:from.lat,lon:from.lon},{lat:to.lat,lon:to.lon},0.15,160);
  const seg=animateSlidingSegment(latlngs, ttl||60, style);
  const tip=[ meta?.call||"", meta?.band?`${meta.band}m`:"", mode||"", meta?.section?`→ ${meta.section}`:"", meta?.operator?`• ${meta.operator}`:"" ]
    .filter(Boolean).join(" • ");
  if (seg) seg.bindTooltip(tip,{sticky:true});
}

// ---------- Globe.gl (updated animation like reference) ----------
let globe, globeArcs=[];
const globeEl = document.getElementById('globeCanvas');

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
  return globe;
}

function purgeExpiredGlobeArcs(){
  const now = Date.now();
  const before = globeArcs.length;
  globeArcs = globeArcs.filter(a => now < a.expireAt);
  if (globe && globeArcs.length !== before) globe.arcsData(globeArcs.slice());
}

function addGlobeArc(from, to, meta, ttlSec){
  const bandClr = bandColor(meta?.band);

  // --- Make motion like the original demo ---
  // Randomized dash length/gap and animation time per arc,
  // plus a randomized initial phase so arcs aren’t in lockstep.
  const dashLength = Math.random();             // 0..1
  const dashGap    = Math.random();             // 0..1
  const animMs     = 500 + Math.random()*4000;  // 500..4500 ms
  const initialGap = Math.random();             // phase seed

  const arc = {
    startLat: from.lat, startLng: from.lon,
    endLat:   to.lat,   endLng:   to.lon,
    color:    bandClr,
    dashLength, dashGap, animMs, initialGap,
    expireAt: Date.now() + (ttlSec||60)*1000,
    meta
  };
  globeArcs.push(arc);
  if (globe) globe.arcsData(globeArcs.slice());
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
});

// ---------- “Pretty” recent frames ----------
function getTag(xml, tag){ const m = xml.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`, 'i')); return m ? m[1].trim() : null; }
function has(xml, token){ return xml.toUpperCase().includes(token.toUpperCase()); }
function prettyFrame(xml){
  try {
    if (has(xml,"APIVERRESPONSE")) return `API version: ${getTag(xml,"APIVER")||"?"}`;
    if (has(xml,"PROGRAMRESPONSE")){ const p=getTag(xml,"PGM")||"Program", v=getTag(xml,"VER")||""; return `Program: ${p}${v? " " + v:""}`; }
    if (has(xml,"OPINFORESPONSE")){
      const call=getTag(xml,"CALL")||"—", sect=getTag(xml,"SECTION")||getTag(xml,"ARRL_SECT")||"—";
      const cls=getTag(xml,"CLASS")||"—"; const lat=getTag(xml,"LAT"), lon=getTag(xml,"LONG")||getTag(xml,"LON");
      const loc=(lat&&lon)? ` @ ${parseFloat(lat).toFixed(3)}, ${parseFloat(lon).toFixed(3)}`:"";
      return `OPINFO: ${call} • ${cls} • ${sect}${loc}`;
    }
    if (has(xml,"SETUPDATESTATERESPONSE")) return `Subscribed to updates: ${getTag(xml,"VALUE")}`;
    if (has(xml,"ENTEREVENT")){
      const call=getTag(xml,"CALL")||"—", band=getTag(xml,"BAND")||"—", mode=getTag(xml,"MODE")||getTag(xml,"MODETEST")||"—";
      const sect=getTag(xml,"SECTION")||getTag(xml,"ARRL_SECT")||"", op=getTag(xml,"OPERATOR")||getTag(xml,"MYCALL")||"", ts=getTag(xml,"TIME_ON")||"";
      return `ENTER: ${call} • ${band}m • ${mode}${sect? " → "+sect:""}${op? " • "+op:""}${ts? " • "+ts:""}`;
    }
    if (has(xml,"COUNTRYLISTLOOKUPRESPONSE")){
      const call=getTag(xml,"CALL")||"—", lat=getTag(xml,"LAT"), lon=getTag(xml,"LON")||getTag(xml,"LONG"), ctry=getTag(xml,"COUNTRY")||"";
      const loc=(lat&&lon)? ` ${parseFloat(lat).toFixed(3)}, ${parseFloat(lon).toFixed(3)}`:"";
      return `Lookup: ${call}${loc} ${ctry? "• "+ctry:""}`.trim();
    }
  } catch {}
  return xml.replace(/<[^>]+>/g,' ').replace(/\s+/g,' ').trim();
}
function pushRecent(xml){
  const el=document.createElement('div'); el.textContent=prettyFrame(xml);
  recentBox && recentBox.prepend(el);
  if (recentBox) while (recentBox.childNodes.length>20) recentBox.removeChild(recentBox.lastChild);
}

// ---------- Status & WS ----------
function updateOperators(list){
  const cur=operSel.value;
  operSel.innerHTML='<option value="">All</option>';
  (list||[]).forEach(op=>{ const o=document.createElement('option'); o.value=op; o.textContent=op; operSel.appendChild(o); });
  if ([...operSel.options].some(o=>o.value===cur)) operSel.value=cur;
}

async function refreshStatus(){
  try{
    const s=await fetch('/status').then(r=>r.json());
    if (s.connected){ connPill.textContent='Connected'; connPill.className='pill ok'; }
    else { connPill.textContent='Disconnected'; connPill.className='pill bad'; }
    prog.textContent=s.program || '—';
    api.textContent=s.apiver || '—';
    if (s.origin?.lat) setOrigin(s.origin);
    if (s.last_event_ts) lastEvt.textContent=new Date(s.last_event_ts*1000).toLocaleString();
    if (s.sections_worked){ workedSections.clear(); s.sections_worked.forEach(graySection); }
    if (s.operators) updateOperators(s.operators);
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
      if (s.last_event_ts) lastEvt.textContent=new Date(s.last_event_ts*1000).toLocaleString();
    } else if (msg.type==='origin'){
      setOrigin(msg.data);
    } else if (msg.type==='path'){
      // map
      drawPathMap(msg.data);
      // globe
      addGlobeArc(msg.data.from, msg.data.to, msg.data.meta||{}, msg.data.ttl||60);
      // banner
      const c = msg.data.meta?.call || '—';
      bannerText.textContent = `Last logged: ${c} • ${new Date().toLocaleTimeString()}`;
      // section dim
      if (msg.data.meta?.section) graySection(msg.data.meta.section);
    } else if (msg.type==='operators'){
      updateOperators(msg.data||[]);
    } else if (msg.type==='section_hit'){
      graySection(msg.data);
    } else if (msg.type==='sections_worked'){
      (msg.data||[]).forEach(graySection);
    }
  } catch {}
};

// seed recent (pretty)
(async function loadRecent(){
  try{ const r=await fetch('/recent').then(r=>r.json()); (r.recent||[]).slice(-10).forEach(pushRecent); } catch {}
})();
