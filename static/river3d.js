// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Rowbox authors
// 3D-Flussfahrt fuer die Handy-Anzeige: Einer mit Ruderer im echten Schlagrhythmus,
// Ufer, Baeume, Bruecken und Orte. Gibt es fuer die Strecke einen echten Verlauf
// (static/routes/<id>.json aus OpenStreetMap), folgt der Fluss ihm, sonst einem erfundenen.
import * as THREE from "/static/three.module.min.js";

const CHUNK = 200, AHEAD = 1600, BEHIND = 250;

// ---------- Flusslinie ----------
// Jede Linie liefert at(s) = [x, z, fx, fz]: Mitte und Fahrtrichtung bei Strecke s (Meter).
// Die Fahrt geht grob nach -z; rechts in Fahrtrichtung ist (-fz, fx).
function wiggle() {
  const cx = s => 70 * Math.sin(s / 900) + 28 * Math.sin(s / 260 + 1.3);
  return {
    width: 90, real: false,
    at(s) {
      const d = (cx(s + 2) - cx(s - 2)) / 4, h = Math.hypot(d, 1);
      return [cx(s), -s, d / h, -1 / h];
    },
  };
}

function polyline(X, Z, s0) {
  const n = X.length, S = new Float64Array(n);
  for (let i = 1; i < n; i++) S[i] = S[i - 1] + Math.hypot(X[i] - X[i - 1], Z[i] - Z[i - 1]);
  function seg(t) {                                   // Abschnitt i mit S[i] <= t < S[i+1]
    let lo = 0, hi = n - 2;
    while (lo < hi) { const m = (lo + hi + 1) >> 1; if (S[m] <= t) lo = m; else hi = m - 1; }
    return lo;
  }
  function pt(t) {                                    // vor dem Anfang und nach dem Ende geradeaus weiter
    const i = t <= 0 ? 0 : t >= S[n - 1] ? n - 2 : seg(t), L = S[i + 1] - S[i] || 1, k = (t - S[i]) / L;
    return [X[i] + (X[i + 1] - X[i]) * k, Z[i] + (Z[i + 1] - Z[i]) * k];
  }
  return {
    length: S[n - 1], X, Z,
    at(s) {
      const t = s - s0, a = pt(t - 6), b = pt(t + 6), c = pt(t), h = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
      return [c[0], c[1], (b[0] - a[0]) / h, (b[1] - a[1]) / h];
    },
  };
}

// Echte Strecke aus OpenStreetMap: Punkte in Metern (Ost, Nord) ab der ersten Etappe.
function realRoute(R) {
  const n = R.pts.length / 2, X = [], Z = [];
  for (let i = 0; i < n; i++) { X.push(R.pts[2 * i]); Z.push(-R.pts[2 * i + 1]); }
  const line = polyline(X, Z, R.start || 0);
  return { width: R.width || 160, real: true, at: line.at, bridges: R.bridges || [], X, Z,
           landmarks: R.landmarks || [], relief: relief(R.relief, R.start || 0),
           sides: new Map((R.stops || []).map(([km, side]) => [km, side])), attribution: R.attribution };
}

// Gelaendehoehe ueber dem Wasserspiegel aus dem Hoehenraster der Strecke (Meter; off > 0 rechts).
function relief(F, s0) {
  if (!F || !F.h) return null;
  const n = F.offs.length, rows = F.h.length / n, inner = F.offs.findIndex(o => o > 0);
  function row(r, off) {
    const b = r * n, h = F.h, o = F.offs;
    if (off >= o[n - 1]) return h[b + n - 1];
    if (off <= o[0]) return h[b];
    let j = 0;
    while (o[j + 1] < off) j++;
    if (j === inner - 1) return null;                  // zwischen den Ufern: Wasser
    const k = (off - o[j]) / (o[j + 1] - o[j]);
    return h[b + j] + (h[b + j + 1] - h[b + j]) * k;
  }
  return (s, off, half) => {
    const t = Math.min(rows - 1, Math.max(0, (s - s0) / F.step)), r = Math.min(rows - 2, Math.floor(t)), k = t - r;
    const edge = off > 0 ? F.offs[inner] : F.offs[inner - 1];
    const lim = Math.abs(off) < Math.abs(edge) ? edge : off;   // naeher am Ufer: zum Ufer hin auf 0 auslaufen
    const v = row(r, lim) * (1 - k) + row(r + 1, lim) * k;
    if (lim === off) return v;
    return v * Math.max(0, (Math.abs(off) - half) / (Math.abs(edge) - half));
  };
}

const rng = seed => () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;

export function start(el, state) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "low-power" });
  } catch (e) {
    return null;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  el.appendChild(renderer.domElement);
  const scene = new THREE.Scene();

  // ---------- Himmel nach Uhrzeit: Farbverlauf, Sonne, Dunst; spiegelt sich im Wasser ----------
  const camera = new THREE.PerspectiveCamera(42, 1, 0.5, 3200);
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  const hemi = new THREE.HemisphereLight(0xcfe3ff, 0x3a5a2a, 1.1);
  scene.add(sun, hemi);
  const skyU = { top: { value: new THREE.Color() }, horizon: { value: new THREE.Color() },
                 sunDir: { value: new THREE.Vector3() }, sunCol: { value: new THREE.Color() } };
  const skyMat = new THREE.ShaderMaterial({
    uniforms: skyU, side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: "varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }",
    fragmentShader: "uniform vec3 top, horizon, sunDir, sunCol; varying vec3 vDir;" +
      "void main(){ float h = clamp(vDir.y, 0.0, 1.0); vec3 c = mix(horizon, top, pow(h, 0.55));" +
      " float d = max(dot(normalize(vDir), sunDir), 0.0); c += sunCol * (pow(d, 900.0) * 1.2 + pow(d, 12.0) * 0.25);" +
      " gl_FragColor = vec4(c, 1.0); }",
  });
  const skyDome = new THREE.Mesh(new THREE.SphereGeometry(2800, 24, 12), skyMat);
  skyDome.renderOrder = -1;
  scene.add(skyDome);
  const pmrem = new THREE.PMREMGenerator(renderer), envScene = new THREE.Scene();
  envScene.add(new THREE.Mesh(new THREE.SphereGeometry(10, 24, 12), skyMat));
  let envTarget = null, WX = null, wxKey = "";          // Wetter an der Position (von der Box, wenn online)
  const mixC = (a, b, t) => new THREE.Color(a).lerp(new THREE.Color(b), Math.min(1, Math.max(0, t)));
  function wxKind() {
    const c = WX && WX.code;
    if (c == null) return "";
    if ([71, 73, 75, 77, 85, 86].includes(c)) return "snow";
    if ((c >= 51 && c <= 67) || (c >= 80 && c <= 82) || c >= 95) return "rain";
    return "";
  }
  function sky() {
    const h = new Date().getHours() + new Date().getMinutes() / 60;
    // Tageslauf: Sonnenhoehe grob nach Uhrzeit, Farben fuer Tag, Abendrot und Nacht
    const elev = Math.sin(Math.PI * (h - 6.5) / 13);           // >0 tagsueber
    const day = Math.min(1, Math.max(0, elev * 3)), glow = Math.max(0, 1 - Math.abs(elev) * 4);
    const top = mixC(0x0b1530, 0x3f7fd0, day), horizon = mixC(0x1a2440, 0xc9def0, day).lerp(new THREE.Color(0xf0a070), glow * 0.6);
    // Bewoelkung: Himmel grauer, Sonne schwaecher; Sicht bestimmt den Dunst
    const cloud = WX && WX.cloud != null ? WX.cloud / 100 : 0, wet = wxKind() === "rain" ? 0.25 : 0;
    top.lerp(mixC(0x1c2026, 0x8d98a4, day), Math.min(0.9, cloud * 0.8 + wet));
    horizon.lerp(mixC(0x22262c, 0xb9c1c9, day), Math.min(0.8, cloud * 0.6 + wet));
    skyU.top.value.copy(top);
    skyU.horizon.value.copy(horizon);
    skyU.sunDir.value.set(-0.45, Math.max(0.03, elev * 0.8), -0.85).normalize();
    skyU.sunCol.value.copy(mixC(0xffe2b0, 0xfff6e0, day)).multiplyScalar((day > 0 || glow > 0 ? 1 : 0) * Math.max(0, 1 - cloud * 1.2));
    sun.position.copy(skyU.sunDir.value).multiplyScalar(600);
    sun.color.copy(mixC(0xffb070, 0xffffff, day));
    sun.intensity = (0.3 + 1.4 * day) * (1 - 0.6 * cloud);
    hemi.intensity = 0.45 + 0.65 * day;
    scene.background = horizon;
    const vis = WX && WX.vis ? Math.min(2600, Math.max(250, WX.vis)) : 2600;
    scene.fog = new THREE.Fog(horizon, vis * 0.17, vis);
    if (envTarget) envTarget.dispose();
    envTarget = pmrem.fromScene(envScene, 0, 0.1, 50);
    waterMat.envMap = envTarget.texture;
    waterMat.needsUpdate = true;
  }

  // ---------- Boot und Ruderer ----------
  const white = new THREE.MeshLambertMaterial({ color: 0xf2f2ee });
  const orange = new THREE.MeshLambertMaterial({ color: 0xff5a1f });
  const dark = new THREE.MeshLambertMaterial({ color: 0x23272e });
  const boat = new THREE.Group();
  const hull = new THREE.Mesh(new THREE.SphereGeometry(1, 20, 8), white);
  hull.scale.set(0.24, 0.13, 4.1);
  boat.add(hull);
  const rig = new THREE.Mesh(new THREE.BoxGeometry(1.6, 0.04, 0.08), dark);
  rig.position.set(0, 0.22, 0.1);
  boat.add(rig);
  const seat = new THREE.Group();
  boat.add(seat);
  const legs = new THREE.Mesh(new THREE.BoxGeometry(0.28, 0.16, 0.7), dark);
  legs.position.set(0, 0.18, 0.35);
  const torso = new THREE.Group();
  const body = new THREE.Mesh(new THREE.BoxGeometry(0.38, 0.62, 0.22), orange);
  body.position.y = 0.31;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.12, 12, 8), new THREE.MeshLambertMaterial({ color: 0xe8b89a }));
  head.position.y = 0.74;
  torso.add(body, head);
  torso.position.y = 0.2;
  seat.add(legs, torso);
  const oars = [-1, 1].map(side => {
    const pivot = new THREE.Group();
    pivot.position.set(side * 0.8, 0.26, 0.1);
    const shaft = new THREE.Mesh(new THREE.CylinderGeometry(0.02, 0.02, 2.9, 6), white);
    shaft.rotation.z = Math.PI / 2;
    shaft.position.x = side * 1.15;
    const blade = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.2, 0.03), orange);
    blade.position.x = side * 2.45;
    pivot.add(shaft, blade);
    boat.add(pivot);
    return { pivot, blade, side };
  });
  scene.add(boat);
  // Regen und Schnee: Teilchen in einem Kasten um die Kamera
  const fall = (() => {
    const n = 1400, pos = new Float32Array(n * 6), r = rng(99);
    for (let i = 0; i < n; i++) {
      const x = (r() - 0.5) * 80, y = r() * 40, z = (r() - 0.5) * 80;
      pos.set([x, y, z, x, y - 0.9, z], i * 6);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.BufferAttribute(pos, 3));
    const lines = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ color: 0xc8d4e0, transparent: true, opacity: 0.45 }));
    lines.frustumCulled = false;
    lines.visible = false;
    scene.add(lines);
    return { lines, pos, n };
  })();
  function weatherStep(dt, cx, cz) {
    const kind = wxKind();
    fall.lines.visible = !!kind;
    if (!kind) return;
    const snow = kind === "snow", v = snow ? 1.2 : 11, p = fall.pos, wind = (WX.wind || 0) * 0.4;
    fall.lines.material.color.set(snow ? 0xffffff : 0xc8d4e0);
    fall.lines.material.opacity = snow ? 0.9 : Math.min(0.6, 0.25 + (WX.rain || 0) * 0.15);
    const len = snow ? 0.12 : 0.9;
    for (let i = 0; i < fall.n; i++) {
      const o = i * 6;
      let x = p[o] + wind * dt + (snow ? Math.sin((p[o + 1] + i) * 0.7) * dt * 0.6 : 0), y = p[o + 1] - v * dt, z = p[o + 2];
      // Kasten wandert mit der Kamera mit
      if (y < 0) y += 40;
      x = ((x - cx + 40) % 80 + 80) % 80 - 40 + cx;
      z = ((z - cz + 40) % 80 + 80) % 80 - 40 + cz;
      p[o] = x; p[o + 1] = y; p[o + 2] = z; p[o + 3] = x - wind * 0.05; p[o + 4] = y - len; p[o + 5] = z;
    }
    fall.lines.geometry.attributes.position.needsUpdate = true;
  }

  // Kielwasser: zwei auslaufende Streifen hinter dem Heck, Deckkraft je nach Tempo
  const wake = (() => {
    const pos = [], col = [], idx = [];
    for (const side of [-1, 1]) {
      const b = pos.length / 3;
      for (let i = 0; i <= 10; i++) {
        const t = i / 10, z = 3.8 + t * 34, x = side * (0.25 + t * 9), w = 0.35 + t * 1.6, a = (1 - t) * (1 - t);
        pos.push(x - side * w / 2, 0.06, z, x + side * w / 2, 0.06, z);
        col.push(1, 1, 1, a, 1, 1, 1, a * 0.6);
        if (i < 10) idx.push(b + 2 * i, b + 2 * i + 1, b + 2 * i + 2, b + 2 * i + 1, b + 2 * i + 3, b + 2 * i + 2);
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    g.setAttribute("color", new THREE.Float32BufferAttribute(col, 4));
    g.setIndex(idx);
    const m = new THREE.Mesh(g, new THREE.MeshBasicMaterial({ vertexColors: true, transparent: true, opacity: 0,
      depthWrite: false, side: THREE.DoubleSide }));
    boat.add(m);
    return m;
  })();

  // ---------- Landschaft in Abschnitten ----------
  const chunks = new Map();
  let path = wiggle(), WIDTH = path.width, routeId = null;
  // Punkt neben der Flussmitte: off > 0 rechts, along verschiebt laengs des Flusses
  function place(s, off, at = path.at) {
    const [x, z, fx, fz] = at(s);
    if (path.real && Math.abs(off) > 60) {             // Innenseite enger Kurven: nicht ueber den Kurvenmittelpunkt hinaus
      const h = Math.max(100, Math.abs(off) / 2), p = at(s - h), q = at(s + h);
      const turn = p[2] * q[3] - p[3] * q[2], ang = Math.abs(Math.atan2(turn, p[2] * q[2] + p[3] * q[3]));
      if (turn * off > 0) off = Math.sign(off) * Math.min(Math.abs(off), Math.max(60, 0.7 * 2 * h / Math.max(1e-4, ang)));
    }
    return [x - fz * off, z + fx * off];
  }
  const heading = s => { const p = path.at(s); return Math.atan2(-p[2], -p[3]); };
  const grass = [0x5d8a3c, 0x6f9a45, 0x4f7a34, 0x86a957].map(c => new THREE.Color(c));
  const treeGeo = new THREE.ConeGeometry(4, 13, 6), trunkGeo = new THREE.CylinderGeometry(0.6, 0.8, 3, 5);
  const leafGeo = new THREE.IcosahedronGeometry(5, 0);
  const treeMat = new THREE.MeshLambertMaterial({ color: 0xffffff }), trunkMat = new THREE.MeshLambertMaterial({ color: 0x5b4330 });
  const firCols = [0x24502a, 0x2f5d2a, 0x2a4f33].map(c => new THREE.Color(c));
  const leafCols = [0x4f7f2f, 0x5f8f35, 0x6b8e3a, 0x47703a, 0x7a9a40].map(c => new THREE.Color(c));
  // Wellen als Normal-Map aus Rauschen, wird mit der Stroemung verschoben
  const waves = (() => {
    const N = 128, data = new Uint8Array(N * N * 4), r = rng(4711), hgt = new Float32Array(N * N);
    const k = [];                                         // einige Sinuswellen, nahtlos kachelbar
    for (let i = 0; i < 9; i++) k.push([1 + Math.floor(r() * 6), Math.floor(r() * 9) - 4, r() * 6.28, 0.5 / (1 + i * 0.4)]);
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      let v = 0;
      for (const [a, b, ph, amp] of k) v += amp * Math.sin(2 * Math.PI * (a * x + b * y) / N + ph);
      hgt[y * N + x] = v;
    }
    for (let y = 0; y < N; y++) for (let x = 0; x < N; x++) {
      const dx = hgt[y * N + (x + 1) % N] - hgt[y * N + (x + N - 1) % N];
      const dy = hgt[((y + 1) % N) * N + x] - hgt[((y + N - 1) % N) * N + x];
      const n = new THREE.Vector3(-dx, -dy, 2).normalize(), o = (y * N + x) * 4;
      data[o] = (n.x * 0.5 + 0.5) * 255; data[o + 1] = (n.y * 0.5 + 0.5) * 255; data[o + 2] = (n.z * 0.5 + 0.5) * 255; data[o + 3] = 255;
    }
    const t = new THREE.DataTexture(data, N, N);
    t.wrapS = t.wrapT = THREE.RepeatWrapping;
    t.needsUpdate = true;
    return t;
  })();
  const waterMat = new THREE.MeshStandardMaterial({ color: 0x163a5c, roughness: 0.18, metalness: 0.0,
    normalMap: waves, normalScale: new THREE.Vector2(0.22, 0.22), envMapIntensity: 0.55, side: THREE.DoubleSide });
  sky();
  setInterval(sky, 60000);
  const groundMat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
  const wallMat = new THREE.MeshLambertMaterial({ color: 0xe8e2d5 }), roofMat = new THREE.MeshLambertMaterial({ color: 0xa8432c });
  const stoneMat = new THREE.MeshLambertMaterial({ color: 0x9a948a });

  function ribbon(s0, s1, offs, hfn, colfn, mat, uvScale) {
    const rows = Math.ceil((s1 - s0) / 10) + 1, cols = offs.length, pos = [], col = [], idx = [], uv = [];
    for (let r = 0; r < rows; r++) {
      const s = s0 + (s1 - s0) * r / (rows - 1);
      for (let c = 0; c < cols; c++) {
        const [px, pz] = place(s, offs[c]);
        pos.push(px, hfn(s, c), pz);
        if (uvScale) uv.push(px / uvScale, pz / uvScale);
        if (colfn) { const k = colfn(s, c); col.push(k.r, k.g, k.b); }
      }
    }
    for (let r = 0; r < rows - 1; r++)
      for (let c = 0; c < cols - 1; c++) {
        const a = r * cols + c, b = a + cols;
        idx.push(a, b, a + 1, a + 1, b, b + 1);
      }
    const g = new THREE.BufferGeometry();
    g.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
    if (colfn) g.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
    if (uvScale) g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
    g.setIndex(idx);
    g.computeVertexNormals();
    return new THREE.Mesh(g, mat);
  }

  const hill = (s, side) => 45 * Math.max(0, Math.sin(s / 1300 + side) * 0.6 + Math.sin(s / 410 + side * 2) * 0.4);
  // Hoehe des Gelaendes neben dem Fluss: echtes Relief, wenn die Strecke eins hat, sonst erfundene Huegel
  const BANK = [0, 6, 40, 160, 420, 900], BASE = [0.2, 1.5, 3, 8, 1, 1];
  function ground(s, off) {
    const d = Math.abs(off) - WIDTH / 2, side = Math.sign(off) || 1;
    let base = BASE[BASE.length - 1], c = BANK.length - 1;
    for (let j = 0; j < BANK.length - 1; j++)
      if (d < BANK[j + 1]) { c = j + Math.max(0, d - BANK[j]) / (BANK[j + 1] - BANK[j]); break; }
    const j = Math.floor(Math.min(c, BANK.length - 1)), f = c - j;
    base = j >= BANK.length - 1 ? BASE[j] : BASE[j] + (BASE[j + 1] - BASE[j]) * f;
    if (path.relief) return Math.max(Math.min(base, 1.5), path.relief(s, off, WIDTH / 2));
    return base + (c >= 2 ? hill(s, side) * (c - 1) / 3 : 0);
  }

  function sign(text, big) {
    const cv = document.createElement("canvas");
    cv.width = 512; cv.height = 128;
    const c = cv.getContext("2d");
    c.fillStyle = big ? "rgba(0,0,0,.55)" : "rgba(20,40,70,.5)";
    c.beginPath(); c.roundRect(0, 14, 512, 100, 24); c.fill();
    c.fillStyle = "#fff"; c.font = (big ? "bold 64px" : "600 50px") + " system-ui,sans-serif";
    c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(text, 256, 66, 480);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, fog: false }));
    sp.scale.set(big ? 48 : 34, big ? 12 : 8.5, 1);
    return sp;
  }

  function bridge(group, s, name) {
    const [bx, bz] = place(s, 0), rot = heading(s);
    const deck = new THREE.Mesh(new THREE.BoxGeometry(WIDTH + 60, 2, 9), stoneMat);
    deck.position.set(bx, 11, bz);
    deck.rotation.y = rot;
    group.add(deck);
    for (const p of [-1, 0, 1]) {
      const [px, pz] = place(s, p * WIDTH * 0.32);
      const pier = new THREE.Mesh(new THREE.BoxGeometry(4, 11, 7), stoneMat);
      pier.position.set(px, 5, pz);
      pier.rotation.y = rot;
      group.add(pier);
    }
    if (name) {
      const sp = sign(name, false);
      sp.position.set(bx, 19, bz);
      group.add(sp);
    }
  }

  // Landmarken aus OpenStreetMap: vereinfachte Modelle je Art, mit Namensschild
  const darkRoof = new THREE.MeshLambertMaterial({ color: 0x3c4450 }), ruinMat = new THREE.MeshLambertMaterial({ color: 0xb3aa98 });
  const whiteMat = new THREE.MeshLambertMaterial({ color: 0xf4f4f0 }), redMat = new THREE.MeshLambertMaterial({ color: 0xc8382c });
  const rockMat = new THREE.MeshLambertMaterial({ color: 0x6e6a62 });
  // Seitlicher Abstand etwas gestaucht, damit Landmarken beim Vorbeifahren im Bild bleiben
  const near = d => Math.min(750, WIDTH / 2 + 35 + 0.6 * Math.max(0, d - WIDTH / 2 - 35));
  function landmark(group, s, off, kind, name, rand) {
    const [x, z] = place(s, off), y = Math.abs(off) < WIDTH / 2 ? 0 : ground(s, off) - 1, rot = heading(s), part = new THREE.Group();
    const add = (geo, mat, px, py, pz, ry = 0) => { const m = new THREE.Mesh(geo, mat); m.position.set(px, py, pz); m.rotation.y = ry; part.add(m); return m; };
    let top = 30;
    if (kind === "castle") {
      add(new THREE.BoxGeometry(34, 16, 26), stoneMat, 0, 8, 0);
      add(new THREE.BoxGeometry(14, 30, 14), stoneMat, -6, 15, -4);           // Bergfried
      add(new THREE.ConeGeometry(11, 12, 4), darkRoof, -6, 36, -4, Math.PI / 4);
      for (const [px, pz] of [[-17, -13], [17, -13], [17, 13], [-17, 13]]) {
        add(new THREE.CylinderGeometry(4, 4.5, 22, 10), stoneMat, px, 11, pz);
        add(new THREE.ConeGeometry(5.5, 9, 10), darkRoof, px, 26.5, pz);
      }
      top = 44;
    } else if (kind === "ruin") {
      for (let k = 0; k < 5; k++)
        add(new THREE.BoxGeometry(4 + rand() * 18, 6 + rand() * 16, 3), ruinMat, (rand() - 0.5) * 28, 6, (rand() - 0.5) * 22, rand() * 3);
      add(new THREE.CylinderGeometry(5, 6, 24, 10, 1, true), ruinMat, 8, 12, -6);
      top = 30;
    } else if (kind === "cathedral") {
      add(new THREE.BoxGeometry(22, 26, 70), stoneMat, 0, 13, 0);
      add(new THREE.BoxGeometry(23, 7, 70), darkRoof, 0, 29, 0);
      for (const px of [-7, 7]) {
        add(new THREE.BoxGeometry(10, 78, 10), stoneMat, px, 39, 38);
        add(new THREE.ConeGeometry(7, 34, 4), stoneMat, px, 95, 38, Math.PI / 4);
      }
      top = 115;
    } else if (kind === "church") {
      add(new THREE.BoxGeometry(12, 12, 26), wallMat, 0, 6, 0);
      add(new THREE.ConeGeometry(10, 7, 4), roofMat, 0, 15.5, 0, Math.PI / 4).scale.set(1, 1, 1.9);
      add(new THREE.BoxGeometry(7, 30, 7), wallMat, 0, 15, 15);
      add(new THREE.ConeGeometry(5.5, 14, 8), darkRoof, 0, 37, 15);
      top = 48;
    } else if (kind === "lighthouse" || kind === "tower") {
      const lh = kind === "lighthouse";
      for (let k = 0; k < 4; k++) add(new THREE.CylinderGeometry(3.6 - k * 0.3, 4 - k * 0.3, 7, 12), lh && k % 2 ? redMat : (lh ? whiteMat : stoneMat), 0, 3.5 + k * 7, 0);
      add(new THREE.CylinderGeometry(3, 3, 4, 12), lh ? redMat : darkRoof, 0, 30, 0);
      top = 38;
    } else if (kind === "windmill") {
      add(new THREE.CylinderGeometry(3.2, 5.5, 18, 8), new THREE.MeshLambertMaterial({ color: 0x5a4636 }), 0, 9, 0);
      add(new THREE.ConeGeometry(3.8, 5, 8), darkRoof, 0, 20.5, 0);
      const hub = new THREE.Group();
      hub.position.set(0, 18, -4.2);
      hub.rotation.z = rand() * Math.PI;
      for (let k = 0; k < 4; k++) {
        const blade = new THREE.Mesh(new THREE.BoxGeometry(2.4, 13, 0.3), whiteMat);
        blade.position.y = 6.5;
        const arm = new THREE.Group();
        arm.rotation.z = k * Math.PI / 2;
        arm.add(blade);
        hub.add(arm);
      }
      part.add(hub);
      top = 32;
    } else if (kind === "rock") {
      if (!path.relief) {
        const r = add(new THREE.DodecahedronGeometry(40, 0), rockMat, 0, 20, 0);
        r.scale.set(1.2, 1.4, 1);
      }
      top = path.relief ? 20 : 75;
    }
    part.position.set(x, y, z);
    part.rotation.y = rot;
    group.add(part);
    if (name) {
      const sp = sign(name, false), k = 1.5 + Math.abs(off) / 300;
      sp.scale.multiplyScalar(k);
      sp.position.set(x, y + top + 6 * k, z);
      group.add(sp);
    }
  }

  function town(group, s, side, name, rand) {
    const rot = heading(s);
    for (let i = 0; i < 14; i++) {
      const w = 7 + rand() * 6, d = 8 + rand() * 5, h = 6 + rand() * 6;
      const hs = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), wallMat);
      const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.max(w, d) * 0.75, 4, 4), roofMat);
      const hsS = s + (rand() - 0.3) * 160, hsO = side * (WIDTH / 2 + 18 + rand() * 120);
      const [px, pz] = place(hsS, hsO), gy = ground(hsS, hsO) - 0.5;
      hs.position.set(px, gy + h / 2, pz);
      hs.rotation.y = rot;
      roof.position.set(px, gy + h + 2, pz);
      roof.rotation.y = rot + Math.PI / 4;
      group.add(hs, roof);
    }
    const [tx, tz] = place(s + 40, side * (WIDTH / 2 + 60)), ty = ground(s + 40, side * (WIDTH / 2 + 60)) - 0.5;
    const tower = new THREE.Mesh(new THREE.BoxGeometry(8, 34, 8), stoneMat);
    tower.position.set(tx, ty + 17, tz);
    const spire = new THREE.Mesh(new THREE.ConeGeometry(6.5, 16, 4), roofMat);
    spire.position.set(tx, ty + 42, tz);
    spire.rotation.y = Math.PI / 4;
    group.add(tower, spire);
    // Ortsschild ueber dem Wasser; erfundene Strecken bekommen dazu eine Bruecke
    const bs = s + 120;
    if (!path.real) bridge(group, bs, "");
    const sp = sign(name, true);
    const [sx, sz] = place(bs, 0);
    sp.position.set(sx, 28, sz);
    group.add(sp);
  }

  // Farbe des Gelaendes: Uferbefestigung, Wiesen und Felder im Flachen, Weinberge und Fels an steilen Haengen
  const C = { bank: new THREE.Color(0x8f8a7c), vine: new THREE.Color(0x6f7a34), rock: new THREE.Color(0x857d6c),
              field: new THREE.Color(0xa9a85a), field2: new THREE.Color(0x8fa04a) };
  function landColor(s, offs, c) {
    if (c === 0) return C.bank;
    const h = ground(s, offs[c]), a = ground(s, offs[Math.max(0, c - 1)]), b = ground(s, offs[Math.min(offs.length - 1, c + 1)]);
    const slope = Math.abs(b - a) / Math.abs(offs[Math.min(offs.length - 1, c + 1)] - offs[Math.max(0, c - 1)]);
    const base = grass[(c + Math.floor(s / 90)) % grass.length].clone().offsetHSL(0, 0, Math.sin(s / 37 + c) * 0.03);
    if (slope > 0.45) return base.lerp(C.rock, 0.55);
    if (slope > 0.22) return base.lerp(((Math.floor(s / 160) + c) % 3) ? C.vine : C.rock, 0.6);   // Weinberge
    if (h < 40 && c >= 3) {                                            // flaches Land: Felder in Flecken
      const f = (Math.floor(s / 260) * 7 + c * 3) % 5;
      if (f === 0) return base.lerp(C.field, 0.7);
      if (f === 1) return base.lerp(C.field2, 0.6);
    }
    return base;
  }

  function build(i, stops) {
    const s0 = i * CHUNK, s1 = s0 + CHUNK, rand = rng(i * 7919 + 13), g = new THREE.Group();
    g.add(ribbon(s0, s1, [-WIDTH / 2 - 2, WIDTH / 2 + 2], () => 0, null, waterMat, 24));
    for (const side of [-1, 1]) {
      const offs = (path.relief ? [0, 6, 40, 100, 200, 350, 600, 1000, 1500] : BANK).map(o => (WIDTH / 2 + o) * side);
      const mesh = ribbon(s0, s1, offs, (s, c) => ground(s, offs[c]), (s, c) => landColor(s, offs, c), groundMat);
      g.add(mesh);
      // Mischwald: Laubbaeume (runde Krone) und Nadelbaeume, in Gruppen, nicht auf Weinberghaengen
      const n = 64, firs = new THREE.InstancedMesh(treeGeo, treeMat, n), leaves = new THREE.InstancedMesh(leafGeo, treeMat, n);
      const trunks = new THREE.InstancedMesh(trunkGeo, trunkMat, 2 * n), m = new THREE.Matrix4(), q = new THREE.Quaternion();
      let nf = 0, nl = 0, nt = 0;
      const cluster = [s0 + rand() * CHUNK, WIDTH / 2 + 20 + rand() * 400];
      for (let k = 0; k < n; k++) {
        const grouped = rand() < 0.6;
        const s = grouped ? cluster[0] + (rand() - 0.5) * 70 : s0 + rand() * CHUNK;
        const d = grouped ? cluster[1] + (rand() - 0.5) * 90 : WIDTH / 2 + 10 + Math.pow(rand(), 1.3) * 700;
        if (d < WIDTH / 2 + 8) continue;
        const y = ground(s, side * d), slope = Math.abs(ground(s, side * (d + 30)) - ground(s, side * (d - 30))) / 60;
        if (slope > 0.22 && slope < 0.45 && rand() < 0.8) continue;      // Weinberg: kaum Baeume
        const [px, pz] = place(s, side * d), sc = 0.7 + rand() * 0.8, fir = rand() < (y > 120 ? 0.6 : 0.3);
        q.setFromAxisAngle(new THREE.Vector3(0, 1, 0), rand() * 6.28);
        if (fir) {
          m.compose(new THREE.Vector3(px, y + 7.5 * sc, pz), q, new THREE.Vector3(sc, sc, sc));
          firs.setMatrixAt(nf, m); firs.setColorAt(nf++, firCols[Math.floor(rand() * firCols.length)]);
        } else {
          m.compose(new THREE.Vector3(px, y + 6.5 * sc, pz), q, new THREE.Vector3(sc * 1.1, sc * 0.95, sc * 1.1));
          leaves.setMatrixAt(nl, m); leaves.setColorAt(nl++, leafCols[Math.floor(rand() * leafCols.length)]);
        }
        m.makeScale(sc, sc, sc).setPosition(px, y + 1.5 * sc, pz);
        trunks.setMatrixAt(nt++, m);
      }
      firs.count = nf; leaves.count = nl; trunks.count = nt;
      g.add(firs, leaves, trunks);
    }
    for (const [km, name] of stops) {
      const s = km * 1000;
      const side = (path.sides && path.sides.get(km)) || ((Math.round(km) % 2) ? 1 : -1);
      if (s >= s0 && s < s1) town(g, s, side, name, rng(Math.round(km * 13) + 5));
    }
    for (const [s, name] of path.bridges || []) if (s >= s0 && s < s1) bridge(g, s, name);
    for (const [s, side, d, kind, name] of path.landmarks || [])
      if (s >= s0 && s < s1)                           // im Fluss (Inselburg) oder am Ufer, hoechstens 1,3 km weg
        landmark(g, s, side * (d < 100 ? Math.min(d, WIDTH / 3) : near(d)), kind, name, rng(s));
    return g;
  }

  function dispose(g) {
    g.traverse(o => {
      if (o.geometry && o.geometry !== treeGeo && o.geometry !== trunkGeo && o.geometry !== leafGeo) o.geometry.dispose();
      if (o.material && o.material.map) { o.material.map.dispose(); o.material.dispose(); }
    });
  }

  // ---------- Kleine Karte und Quellenangabe (nur bei echtem Verlauf) ----------
  if (getComputedStyle(el).position === "static") el.style.position = "relative";
  const map = document.createElement("canvas"), credit = document.createElement("div");
  map.style.cssText = "position:absolute;top:8px;right:8px;width:84px;height:84px;margin:0;border-radius:12px;" +
    "background:rgba(10,22,40,.55);display:none;pointer-events:none";
  credit.style.cssText = "position:absolute;left:8px;bottom:6px;font:500 10px system-ui,sans-serif;" +
    "color:rgba(255,255,255,.85);text-shadow:0 1px 2px rgba(0,0,0,.6);display:none;pointer-events:none";
  const wxLabel = document.createElement("div");
  wxLabel.style.cssText = "position:absolute;left:8px;top:8px;padding:3px 8px;border-radius:10px;" +
    "background:rgba(10,22,40,.55);color:#fff;font:600 12px system-ui,sans-serif;display:none;pointer-events:none";
  el.append(map, credit, wxLabel);
  let mapAt = 0;
  function minimap(x, z, fx, fz) {
    const show = !!path.real;
    map.style.display = credit.style.display = show ? "block" : "none";
    if (!show) return;
    credit.textContent = path.attribution || "© OpenStreetMap";
    const dpr = Math.min(window.devicePixelRatio || 1, 2), W = 84 * dpr, R = 7000, k = (W / 2 - 8 * dpr) / R;
    if (map.width !== W) map.width = map.height = W;
    const c = map.getContext("2d");
    c.clearRect(0, 0, W, W);
    c.lineWidth = 3 * dpr; c.lineCap = c.lineJoin = "round"; c.strokeStyle = "#6fb3ff";
    c.beginPath();
    let pen = false;
    for (let i = 0; i < path.X.length; i++) {          // Norden oben, 7 km um das Boot
      const dx = path.X[i] - x, dz = path.Z[i] - z;
      if (Math.abs(dx) > R * 1.4 || Math.abs(dz) > R * 1.4) { pen = false; continue; }
      const px = W / 2 + dx * k, py = W / 2 + dz * k;
      if (pen) c.lineTo(px, py); else c.moveTo(px, py);
      pen = true;
    }
    c.stroke();
    c.fillStyle = "#ff5a1f";                           // Boot als Pfeil in Fahrtrichtung
    c.save(); c.translate(W / 2, W / 2); c.rotate(Math.atan2(fx, -fz));
    c.beginPath(); c.moveTo(0, -6 * dpr); c.lineTo(4 * dpr, 5 * dpr); c.lineTo(-4 * dpr, 5 * dpr); c.closePath(); c.fill();
    c.restore();
  }

  // ---------- Fahrt ----------
  let pos = null, speed = 0, last = performance.now(), running = false, stopsKey = "";

  // Eigene, gleichmaessige Schlaguhr: laeuft mit der Schlagdauer weiter und wird bei jedem
  // erkannten Zuganfang sanft nachgezogen (wie der Taktgeber), damit nichts springt.
  const clock = { phase: 0, period: 2.8, lastCatch: 0, warm: 0, idle: 1, curve: null, cum: null };
  function stroke(S, W, dt) {
    const now = performance.now() / 1000;
    const target = W.period || (S.drive && S.recover ? S.drive + S.recover : clock.period);
    clock.period += (Math.min(6, Math.max(1.2, target)) - clock.period) * Math.min(1, dt * 0.8);
    clock.phase = ((clock.phase + dt / clock.period) % 1 + 1) % 1;
    const age = Date.now() / 1000 - (W.ts || 0);
    for (const ago of W.catch_ago || []) {
      const c = now - ago - age;                       // Zeitpunkt des Zuganfangs in unserer Uhr
      if (c <= clock.lastCatch + 0.6 * clock.period) continue;
      const anchor = ((now - c) / clock.period) % 1;
      if (clock.warm < 1 || c - clock.lastCatch > 8) clock.phase = anchor;
      else clock.phase = (clock.phase - 0.35 * (((clock.phase - anchor + 0.5) % 1 + 1) % 1 - 0.5) + 1) % 1;
      clock.warm++;
      clock.lastCatch = c;
    }
    // Ruhen: ohne Ruderei langsam in die Endzug-Haltung gleiten
    const rowing = S.active && clock.lastCatch && now - clock.lastCatch < clock.period * 1.8;
    clock.idle += ((rowing ? 0 : 1) - clock.idle) * Math.min(1, dt * 1.5);
    // Kraftkurve des letzten Zugs: kumulierte Kraft = wie weit der Griff im Zug schon ist
    if (S.curve && S.curve !== clock.curve) {
      clock.curve = S.curve;
      const f = S.curve.map(v => Math.max(0, v)), tot = f.reduce((a, b) => a + b, 0) || 1;
      let acc = 0;
      clock.cum = f.map(v => (acc += v) / tot);
    }
    return S.drive && S.recover ? S.drive / (S.drive + S.recover) : 1 / 3;
  }
  const ease = t => t * t * (3 - 2 * t);
  const sstep = (a, b, t) => ease(Math.min(1, Math.max(0, (t - a) / (b - a))));
  function along(cum, t) {                              // Anteil des Zugwegs nach Zeitanteil t
    if (!cum) return ease(t);
    const x = t * (cum.length - 1), i = Math.floor(x), k = x - i;
    const a = i > 0 ? cum[i - 1] : 0, b = cum[Math.min(i, cum.length - 1)];
    return a + (b - a) * k;
  }
  function force(curve, t) {                            // Kraft 0..1 im Zug, fuer den Schub des Boots
    if (!curve) return Math.sin(t * Math.PI);
    const x = t * (curve.length - 1), i = Math.floor(x), k = x - i;
    const a = curve[i] || 0, b = curve[Math.min(i + 1, curve.length - 1)] || 0;
    return (a + (b - a) * k) / 100;
  }
  const pose = { seat: 0, torso: 0, oar: 0, lift: 0 };
  const cam = { fx: 0, fz: -1 };

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.max(0, Math.min(0.1, (now - last) / 1000));   // rAF-Zeit kann knapp vor `last` liegen
    last = now;
    const { S = {}, W = {}, G = null } = state();
    if (!G) return;
    const target = G.km * 1000;
    if (pos == null || Math.abs(pos - target) > 300) pos = target;
    const D = stroke(S, W, dt), p = clock.phase, inDrive = p < D;
    const want = S.active && S.pace ? 500 / S.pace : 0;
    speed += (want - speed) * Math.min(1, dt * (want > speed ? 1.2 : 0.35));
    // Schub aus der Kraftkurve: im Zug etwas schneller, im Vorrollen etwas langsamer
    const surge = clock.idle > 0.5 ? 1 : inDrive ? 1 + 0.22 * (force(clock.curve, p / D) - 0.45) : 0.94;
    pos += speed * surge * dt + (target - pos) * dt * 0.05;     // sanft am Wert der Box halten

    if (G.route !== routeId) {                         // echten Verlauf laden, falls es einen gibt
      routeId = G.route;
      const id = routeId;
      fetch(`/static/routes/${id}.json`).then(r => r.ok ? r.json() : null).then(R => {
        if (id !== routeId || !R || !R.pts) return;
        path = realRoute(R); WIDTH = path.width; stopsKey = "";
      }).catch(() => {});
      path = wiggle(); WIDTH = path.width; stopsKey = "";
    }
    const key = (G.stops || []).map(x => x[0]).join(",") + (path.real ? "+osm" : "");
    if (key !== stopsKey) { for (const g of chunks.values()) { scene.remove(g); dispose(g); } chunks.clear(); stopsKey = key; }
    const first = Math.floor((pos - BEHIND) / CHUNK), lastC = Math.floor((pos + AHEAD) / CHUNK);
    for (const [i, g] of chunks) if (i < first || i > lastC) { scene.remove(g); dispose(g); chunks.delete(i); }
    for (let i = Math.max(0, first); i <= lastC; i++) if (!chunks.has(i)) { const g = build(i, G.stops || []); chunks.set(i, g); scene.add(g); }

    // Boot auf der Flusslinie, in Fahrtrichtung; etwas rechts der Mitte wie auf echten Fluessen
    const [x0, z0, bfx, bfz] = path.at(pos), lane = path.real ? WIDTH * 0.12 : 0;
    const x = x0 - bfz * lane, z = z0 + bfx * lane;
    boat.position.set(x, 0.05, z);
    boat.rotation.y = Math.atan2(-bfx, -bfz);
    if (now - mapAt > 500) { mapAt = now; minimap(x, z, bfx, bfz); }

    // Ruderbewegung aus der Kraftkurve: Der Griffweg folgt der aufsummierten Kraft des letzten Zugs.
    // Zug: erst Beine, dann Oberkoerper; Vorrollen: erst Oberkoerper, dann Beine, gleichmaessig.
    let e;
    if (inDrive) e = along(clock.cum, p / D);
    else e = 1 - ease((p - D) / (1 - D));
    e = e * (1 - clock.idle) + 1 * clock.idle;         // ohne Rudern: Endzug-Haltung, Blaetter flach
    const legs = inDrive ? sstep(0, 0.65, e) : sstep(0.25, 1, e);
    const swing = inDrive ? sstep(0.3, 1, e) : sstep(0, 0.6, e);
    const k = Math.min(1, dt * 18);                     // letzte Glaettung gegen Ruckler
    pose.seat += (0.35 - legs * 0.7 - pose.seat) * k;
    pose.torso += (0.45 - swing * 0.85 - pose.torso) * k;
    pose.oar += (0.75 - e * 1.45 - pose.oar) * k;
    pose.lift += ((inDrive && clock.idle < 0.5 ? 1 : 0) - pose.lift) * Math.min(1, dt * 14);
    seat.position.z = pose.seat;
    torso.rotation.x = pose.torso;
    for (const o of oars) {
      o.pivot.rotation.y = o.side * pose.oar;
      o.pivot.rotation.z = o.side * (0.1 - 0.16 * pose.lift);
      o.blade.rotation.x = (1 - pose.lift) * Math.PI / 2.4;
    }

    // Kamera hinter dem Boot; Blickrichtung ueber ein laengeres Stueck gemittelt, damit sie ruhig bleibt
    const a = path.at(pos - 15), b = path.at(pos + 35), ab = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1;
    cam.fx += ((b[0] - a[0]) / ab - cam.fx) * Math.min(1, dt * 2);
    cam.fz += ((b[1] - a[1]) / ab - cam.fz) * Math.min(1, dt * 2);
    const n = Math.hypot(cam.fx, cam.fz) || 1, fx = cam.fx / n, fz = cam.fz / n;
    camera.position.set(x - fx * 11 - fz * 0.25, 3.6, z - fz * 11 + fx * 0.25);
    camera.lookAt(x + fx * 40, 0.8, z + fz * 40);
    const w = el.clientWidth, h = el.clientHeight;
    if (renderer.domElement.width !== Math.round(w * renderer.getPixelRatio()) || camera.aspect !== w / h) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    const wk = JSON.stringify(G.weather || null);
    if (wk !== wxKey) {
      wxKey = wk; WX = G.weather || null; sky();
      wxLabel.textContent = (WX && WX.label) || "";
      wxLabel.style.display = WX && WX.label ? "block" : "none";
      const w = WX && WX.wind != null ? WX.wind : 2;
      waterMat.normalScale.setScalar(Math.min(0.6, 0.14 + w * 0.045));
    }
    weatherStep(dt, camera.position.x, camera.position.z);
    skyDome.position.copy(camera.position);
    wake.material.opacity = Math.min(0.55, speed / 4.5 * 0.55) * (0.8 + 0.2 * (inDrive ? 1 : 0));
    const flow = 1 + (WX && WX.wind ? WX.wind * 0.15 : 0.3);
    waves.offset.x += dt * 0.012 * flow; waves.offset.y -= dt * 0.03 * flow;
    renderer.render(scene, camera);
  }

  return {
    run(on) {
      if (on && !running) { running = true; last = performance.now(); requestAnimationFrame(frame); }
      if (!on) running = false;
    },
  };
}
