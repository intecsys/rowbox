// SPDX-License-Identifier: GPL-3.0-or-later
// Copyright (C) 2026 The Rowbox authors
// 3D-Flussfahrt fuer die Handy-Anzeige: Einer mit Ruderer im echten Schlagrhythmus,
// Ufer, Baeume und Orte an den Etappen. Alles wird aus der Strecke erzeugt, ohne Kartendaten.
import * as THREE from "/static/three.module.min.js";

const CHUNK = 200, AHEAD = 1600, BEHIND = 250, WIDTH = 90;

// Flussverlauf: x-Versatz der Flussmitte bei Strecke s (Meter); die Fahrt geht nach -z.
const cx = s => 70 * Math.sin(s / 900) + 28 * Math.sin(s / 260 + 1.3);
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
  const camera = new THREE.PerspectiveCamera(42, 1, 0.5, 2400);
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-300, 400, 200);
  const hemi = new THREE.HemisphereLight(0xcfe3ff, 0x3a5a2a, 1.1);
  scene.add(sun, hemi);

  // ---------- Himmel nach Uhrzeit ----------
  function sky() {
    const h = new Date().getHours() + new Date().getMinutes() / 60;
    const day = new THREE.Color(0x9cc8f0), dusk = new THREE.Color(0xf2a36b), night = new THREE.Color(0x0f1a33);
    let c;
    if (h >= 8 && h < 17.5) c = day;
    else if (h >= 17.5 && h < 20) c = day.clone().lerp(dusk, (h - 17.5) / 2.5);
    else if (h >= 20 && h < 21.5) c = dusk.clone().lerp(night, (h - 20) / 1.5);
    else if (h >= 6 && h < 8) c = night.clone().lerp(day, (h - 6) / 2);
    else c = night;
    scene.background = c;
    scene.fog = new THREE.Fog(c, 350, 1500);
    const dark = c.getHSL({}).l < 0.25;
    sun.intensity = dark ? 0.35 : 1.6;
    hemi.intensity = dark ? 0.45 : 1.1;
  }
  sky();
  setInterval(sky, 60000);

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

  // ---------- Landschaft in Abschnitten ----------
  const chunks = new Map();
  const grass = [0x5d8a3c, 0x6f9a45, 0x4f7a34, 0x86a957].map(c => new THREE.Color(c));
  const treeGeo = new THREE.ConeGeometry(4, 13, 6), trunkGeo = new THREE.CylinderGeometry(0.6, 0.8, 3, 5);
  const treeMat = new THREE.MeshLambertMaterial({ color: 0x2f5d2a }), trunkMat = new THREE.MeshLambertMaterial({ color: 0x5b4330 });
  const waterMat = new THREE.MeshPhongMaterial({ color: 0x2a62c4, specular: 0x9fc4ff, shininess: 60, side: THREE.DoubleSide });
  const groundMat = new THREE.MeshLambertMaterial({ vertexColors: true, side: THREE.DoubleSide });
  const wallMat = new THREE.MeshLambertMaterial({ color: 0xe8e2d5 }), roofMat = new THREE.MeshLambertMaterial({ color: 0xa8432c });
  const stoneMat = new THREE.MeshLambertMaterial({ color: 0x9a948a });

  function ribbon(s0, s1, offs, hfn, colfn, mat) {
    const rows = Math.ceil((s1 - s0) / 10) + 1, cols = offs.length, pos = [], col = [], idx = [];
    for (let r = 0; r < rows; r++) {
      const s = s0 + (s1 - s0) * r / (rows - 1), x0 = cx(s);
      for (let c = 0; c < cols; c++) {
        pos.push(x0 + offs[c], hfn(s, c), -s);
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
    g.setIndex(idx);
    g.computeVertexNormals();
    return new THREE.Mesh(g, mat);
  }

  const hill = (s, side) => 45 * Math.max(0, Math.sin(s / 1300 + side) * 0.6 + Math.sin(s / 410 + side * 2) * 0.4);

  function town(group, s, side, name, rand) {
    const x0 = cx(s), dir = side;
    for (let i = 0; i < 14; i++) {
      const w = 7 + rand() * 6, d = 8 + rand() * 5, h = 6 + rand() * 6;
      const hs = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), wallMat);
      const roof = new THREE.Mesh(new THREE.ConeGeometry(Math.max(w, d) * 0.75, 4, 4), roofMat);
      const px = x0 + dir * (WIDTH / 2 + 18 + rand() * 120), pz = -(s + (rand() - 0.3) * 160);
      hs.position.set(px, h / 2, pz);
      roof.position.set(px, h + 2, pz);
      roof.rotation.y = Math.PI / 4;
      group.add(hs, roof);
    }
    const tx = x0 + dir * (WIDTH / 2 + 60), tz = -(s + 40);
    const tower = new THREE.Mesh(new THREE.BoxGeometry(8, 34, 8), stoneMat);
    tower.position.set(tx, 17, tz);
    const spire = new THREE.Mesh(new THREE.ConeGeometry(6.5, 16, 4), roofMat);
    spire.position.set(tx, 42, tz);
    spire.rotation.y = Math.PI / 4;
    group.add(tower, spire);
    // Bruecke kurz hinter dem Ort
    const bs = s + 120, bx = cx(bs);
    const deck = new THREE.Mesh(new THREE.BoxGeometry(WIDTH + 60, 2, 9), stoneMat);
    deck.position.set(bx, 11, -bs);
    group.add(deck);
    for (const p of [-1, 0, 1]) {
      const pier = new THREE.Mesh(new THREE.BoxGeometry(4, 11, 7), stoneMat);
      pier.position.set(bx + p * WIDTH * 0.32, 5, -bs);
      group.add(pier);
    }
    // Ortsschild ueber dem Wasser
    const cv = document.createElement("canvas");
    cv.width = 512; cv.height = 128;
    const c = cv.getContext("2d");
    c.fillStyle = "rgba(0,0,0,.55)";
    c.beginPath(); c.roundRect(0, 14, 512, 100, 24); c.fill();
    c.fillStyle = "#fff"; c.font = "bold 64px system-ui,sans-serif"; c.textAlign = "center"; c.textBaseline = "middle";
    c.fillText(name, 256, 66, 480);
    const tex = new THREE.CanvasTexture(cv);
    tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, fog: false }));
    sp.scale.set(48, 12, 1);
    sp.position.set(bx, 28, -bs);
    group.add(sp);
  }

  function build(i, stops) {
    const s0 = i * CHUNK, s1 = s0 + CHUNK, rand = rng(i * 7919 + 13), g = new THREE.Group();
    g.add(ribbon(s0, s1, [-WIDTH / 2 - 2, WIDTH / 2 + 2], () => 0, null, waterMat));
    for (const side of [-1, 1]) {
      const offs = [WIDTH / 2, WIDTH / 2 + 6, WIDTH / 2 + 40, WIDTH / 2 + 160, WIDTH / 2 + 420, WIDTH / 2 + 900].map(o => o * side);
      const hs = [0.2, 1.5, 3, 8, 1, 1];
      const mesh = ribbon(s0, s1, offs, (s, c) => c < 2 ? hs[c] : hs[c] + hill(s, side) * (c - 1) / 3,
        (s, c) => grass[(c + Math.floor(s / 90)) % grass.length].clone().offsetHSL(0, 0, (Math.sin(s / 37 + c) * 0.03)), groundMat);
      g.add(mesh);
      const n = 26;
      const trees = new THREE.InstancedMesh(treeGeo, treeMat, n), trunks = new THREE.InstancedMesh(trunkGeo, trunkMat, n);
      const m = new THREE.Matrix4();
      for (let k = 0; k < n; k++) {
        const s = s0 + rand() * CHUNK, d = WIDTH / 2 + 10 + Math.pow(rand(), 1.6) * 300, sc = 0.7 + rand() * 0.8;
        const y = d > WIDTH / 2 + 40 ? 3 + hill(s, side) * Math.min(1, (d - WIDTH / 2 - 40) / 360) : 1.5;
        m.makeScale(sc, sc, sc).setPosition(cx(s) + side * d, y + 7.5 * sc, -s);
        trees.setMatrixAt(k, m);
        m.makeScale(sc, sc, sc).setPosition(cx(s) + side * d, y + 1.5 * sc, -s);
        trunks.setMatrixAt(k, m);
      }
      g.add(trees, trunks);
    }
    for (const [km, name] of stops) {
      const s = km * 1000;
      if (s >= s0 && s < s1) town(g, s, (Math.round(km) % 2) ? 1 : -1, name, rng(Math.round(km * 13) + 5));
    }
    return g;
  }

  function dispose(g) {
    g.traverse(o => {
      if (o.geometry && o.geometry !== treeGeo && o.geometry !== trunkGeo) o.geometry.dispose();
      if (o.material && o.material.map) { o.material.map.dispose(); o.material.dispose(); }
    });
  }

  // ---------- Fahrt ----------
  let pos = null, speed = 0, last = performance.now(), running = false, stopsKey = "";
  function phaseOf(S, W, now) {
    // Schlagphase 0..1 aus dem letzten Zuganfang; Anteil Zug aus Zug- und Rollzeit
    const ago = (W.catch_ago || []).length ? Math.min(...W.catch_ago) + (Date.now() / 1000 - (W.ts || 0)) : null;
    const period = W.period || (S.drive && S.recover ? S.drive + S.recover : 2.8);
    const drive = S.drive && S.recover ? S.drive / (S.drive + S.recover) : 1 / 3;
    if (!S.active || ago == null || ago > period * 1.6) return { p: drive, drive, idle: true };
    return { p: (ago / period) % 1, drive, idle: false };
  }

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;
    const { S = {}, W = {}, G = null } = state();
    if (!G) return;
    const target = G.km * 1000;
    if (pos == null || Math.abs(pos - target) > 300) pos = target;
    const want = S.active && S.pace ? 500 / S.pace : 0;
    speed += (want - speed) * Math.min(1, dt * (want > speed ? 1.2 : 0.35));
    pos += speed * dt + (target - pos) * dt * 0.05;     // sanft am Wert der Box halten

    const key = (G.stops || []).map(x => x[0]).join(",");
    if (key !== stopsKey) { for (const g of chunks.values()) { scene.remove(g); dispose(g); } chunks.clear(); stopsKey = key; }
    const first = Math.floor((pos - BEHIND) / CHUNK), lastC = Math.floor((pos + AHEAD) / CHUNK);
    for (const [i, g] of chunks) if (i < first || i > lastC) { scene.remove(g); dispose(g); chunks.delete(i); }
    for (let i = Math.max(0, first); i <= lastC; i++) if (!chunks.has(i)) { const g = build(i, G.stops || []); chunks.set(i, g); scene.add(g); }

    // Boot auf der Flusslinie, leicht nach der Kurve ausgerichtet
    const x = cx(pos), ahead = cx(pos + 6);
    boat.position.set(x, 0.05, -pos);
    boat.rotation.y = -Math.atan2(ahead - x, 6);

    // Ruderbewegung: im Zug schnell nach hinten, im Vorrollen langsam nach vorn
    const ph = phaseOf(S, W, now), p = ph.p, D = ph.drive;
    const t = p < D ? p / D : 1 - (p - D) / (1 - D);                 // 0 = Auslage, 1 = Endzug
    const e = 0.5 - 0.5 * Math.cos(t * Math.PI);
    seat.position.z = 0.35 - e * 0.7;
    torso.rotation.x = 0.45 - e * 0.85;
    for (const o of oars) {
      o.pivot.rotation.y = o.side * (0.75 - e * 1.45);
      const inWater = p < D && !ph.idle;
      o.pivot.rotation.z = o.side * (inWater ? -0.06 : 0.1);
      o.blade.rotation.x = inWater ? 0 : Math.PI / 2.4;
    }

    // Kamera schraeg hinter dem Boot
    // Kamera genau hinter dem Boot, in seiner Fahrtrichtung
    const fx = (ahead - x) / Math.hypot(ahead - x, 6), fz = -6 / Math.hypot(ahead - x, 6);
    camera.position.set(x - fx * 9.5 + 0.25, 2.5, -pos - fz * 9.5);
    camera.lookAt(x + fx * 30, 1.6, -pos + fz * 30);
    const w = el.clientWidth, h = el.clientHeight;
    if (renderer.domElement.width !== Math.round(w * renderer.getPixelRatio()) || camera.aspect !== w / h) {
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    renderer.render(scene, camera);
  }

  return {
    run(on) {
      if (on && !running) { running = true; last = performance.now(); requestAnimationFrame(frame); }
      if (!on) running = false;
    },
  };
}
