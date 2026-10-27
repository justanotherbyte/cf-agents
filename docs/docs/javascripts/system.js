// Ecosystem diagram under the home page hero. It is plain SVG with a hand-written
// isometric projection, so the site needs no 3D library or build step. One
// timeline in seconds drives every element: render(t) is a pure function of t,
// which makes replay, stage seeking and reduced motion all "pick a t".
(() => {
  const figure = document.querySelector(".cw-system");
  if (!figure) return;

  const scene = figure.querySelector(".cw-system__scene");
  const svgs = [...scene.querySelectorAll("svg")];
  const tip = figure.querySelector(".cw-system__tip");
  const clock = figure.querySelector(".cw-system__clock");
  const messages = figure.querySelectorAll(".cw-system__message");
  const stageButtons = figure.querySelectorAll("[data-stage]");
  const NS = "http://www.w3.org/2000/svg";
  const STAGES = [0, 2.2, 5, 10, 13]; // stage start times; the last is the end of the story
  const END = STAGES[4];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;

  const MODULES = {
    agents: {
      n: "01", name: "Agents", pkg: "cf-agents", href: "agents/overview/",
      desc: "Stateful agents with WebSocket clients, schedules, durable tasks and streaming chat.",
    },
    workers: {
      n: "02", name: "Workers", pkg: "crosswind-workers", href: "workers/overview/",
      desc: "Typed replacement for the workers package, with typed bindings, requests and Durable Objects.",
    },
    containers: {
      n: "03", name: "Containers", pkg: "containers-py", href: "containers/overview/",
      desc: "Start, stop and route requests to Cloudflare Containers, and filter their outbound traffic.",
    },
    wynd: {
      n: "04", name: "Wynd", pkg: "wynd", href: "wynd/overview/",
      desc: "Replaces pywrangler. Before each upload it drops vendored files your Worker never imports.",
    },
  };

  // Projection and easing
  const C = Math.cos(Math.PI / 6);
  const iso = ([x, y, z = 0]) => [(x - y) * C, (x + y) / 2 - z];
  const r1 = (n) => Math.round(n * 10) / 10;
  const r3 = (n) => Math.round(n * 1000) / 1000;
  const pathOf = (pts, close) =>
    pts.map((p, i) => (i ? "L" : "M") + iso(p).map(r1).join(" ")).join("") + (close ? "Z" : "");
  const at = (p) => `translate(${iso(p).map(r1).join(" ")})`;
  const clamp = (n) => Math.min(1, Math.max(0, n));
  const prog = (t, start, dur) => clamp((t - start) / dur);
  const out3 = (p) => 1 - (1 - p) ** 3;
  const inOut = (p) => (p < 0.5 ? 4 * p ** 3 : 1 - (-2 * p + 2) ** 3 / 2);
  const lerp = (a, b, p) => a + (b - a) * p;
  // 1 in the middle of a 0..1 trip, fading in and out over its first and last 10%
  const trip = (p) => (p <= 0 || p >= 1 ? 0 : clamp(Math.min(p, 1 - p) * 10));
  const along = (pts, u) => {
    const lens = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1], p[2] - pts[i][2]));
    let d = clamp(u) * lens.reduce((s, l) => s + l, 0);
    for (let i = 0; i < lens.length; i++) {
      if (d <= lens[i] || i === lens.length - 1) {
        return pts[i].map((v, k) => lerp(v, pts[i + 1][k], lens[i] ? Math.min(1, d / lens[i]) : 1));
      }
      d -= lens[i];
    }
  };

  // DOM writes are cached so a settled scene costs no layout work per frame
  const make = (parent, tag, attrs = {}) => {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    parent.appendChild(e);
    return e;
  };
  const set = (e, k, v) => {
    if (e["_" + k] !== v) e.setAttribute(k, (e["_" + k] = v));
  };
  const toggle = (e, name, on) => {
    if (e["_c" + name] !== on) e.classList.toggle(name, (e["_c" + name] = on));
  };
  const write = (e, v) => {
    if (e._text !== v) e.textContent = e._text = v;
  };

  // The Python logo outlines (python.org artwork), sampled once into polygons
  const SNAKES = [
    "M54.92 0C50.34.02 45.96.41 42.1 1.1 30.76 3.1 28.7 7.3 28.7 15.03v10.22h26.81v3.4H18.64C10.85 28.66 4.02 33.34 1.89 42.25c-2.46 10.21-2.57 16.59 0 27.25 1.9 7.94 6.46 13.6 14.25 13.6h9.22V70.84c0-8.85 7.66-16.66 16.75-16.66h26.78c7.45 0 13.4-6.13 13.4-13.62V15.03c0-7.27-6.13-12.73-13.4-13.94C64.28.33 59.5-.02 54.92 0Z",
    "M85.64 28.66v11.9c0 9.24-7.83 17-16.75 17H42.1c-7.33 0-13.4 6.28-13.4 13.63v25.53c0 7.27 6.32 11.54 13.4 13.62 8.49 2.5 16.63 2.95 26.79 0 6.75-1.95 13.4-5.88 13.4-13.62V86.5H55.52v-3.4h40.19c7.79 0 10.7-5.44 13.4-13.6 2.8-8.4 2.69-16.47 0-27.25-1.92-7.75-5.6-13.59-13.4-13.59Z",
  ];
  const EYES = [[40.42, 13.34], [70.58, 98.4]];
  const LOGO = (() => {
    const probe = make(svgs[0], "path");
    const snakes = SNAKES.map((d) => {
      probe.setAttribute("d", d);
      const len = probe.getTotalLength();
      return Array.from({ length: 120 }, (_, i) => {
        const q = probe.getPointAtLength((len * i) / 120);
        return [q.x - 55.4, 55.4 - q.y];
      });
    });
    probe.remove();
    const eyes = EYES.map(([x, y]) =>
      Array.from({ length: 16 }, (_, i) => [x - 55.4 + 5 * Math.cos(i * 0.3927), 55.4 - y + 5 * Math.sin(i * 0.3927)]));
    return { snakes, eyes };
  })();

  let tracks = [];
  const frame = {}; // values tracks share within one render
  let compact = null;

  function build(isCompact) {
    compact = isCompact;
    tracks = [];
    figure.classList.toggle("is-compact", compact);
    for (const s of svgs) {
      s.replaceChildren();
      s.setAttribute("viewBox", compact ? "-330 -240 660 480" : "-500 -280 1000 560");
    }

    const on = (fn) => tracks.push(fn);
    const draw = (e, start, dur, out) => {
      e.setAttribute("pathLength", 1);
      e.setAttribute("stroke-dasharray", "1 1");
      on((t) => {
        const p = out3(prog(t, start, dur)) * (out ? 1 - prog(t, out, 0.4) : 1);
        set(e, "stroke-dashoffset", r3(1 - p));
        set(e, "visibility", p > 0 ? "visible" : "hidden");
      });
    };
    const fade = (e, start, dur, out) =>
      on((t) => set(e, "opacity", r3(out3(prog(t, start, dur)) * (out ? 1 - prog(t, out, 0.4) : 1))));
    // Edges draw in DOM order (base, then uprights, then the top), then faces and text fade in
    const assemble = (g, start, dur, out) => {
      const edges = g.querySelectorAll(".e");
      edges.forEach((e, i) => draw(e, start + (dur * 0.55 * i) / Math.max(1, edges.length - 1), dur * 0.45, out));
      g.querySelectorAll(".f, .h, .t, .dot").forEach((e) => fade(e, start + dur * 0.45, dur * 0.55, out));
    };

    const group = (parent, attrs) => make(parent, "g", attrs);
    const edge = (g, pts, cls = "", close) => make(g, "path", { d: pathOf(pts, close), class: "e " + cls });
    const face = (g, pts, cls) => make(g, "path", { d: pathOf(pts, true), class: "f " + cls });
    const dot = (g, p, r, cls = "") => {
      const [x, y] = iso(p).map(r1);
      return make(g, "circle", { cx: x, cy: y, r, class: "dot " + cls });
    };
    const label = (g, p, str, cls, dx = 0, dy = 0, anchor = "middle") => {
      const [x, y] = iso(p);
      const e = make(g, "text", { x: r1(x + dx), y: r1(y + dy), class: "t " + cls, "text-anchor": anchor });
      e.textContent = str;
      return e;
    };
    // Only the top and the +x and +y faces of a box face the camera
    const box = (g, [x, y, z], [w, d, h], cls = "", hidden) => {
      const [x0, x1, y0, y1, z0, z1] = [x - w / 2, x + w / 2, y - d / 2, y + d / 2, z, z + h];
      face(g, [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], "ft");
      face(g, [[x1, y0, z0], [x1, y1, z0], [x1, y1, z1], [x1, y0, z1]], "fr");
      face(g, [[x0, y1, z0], [x1, y1, z0], [x1, y1, z1], [x0, y1, z1]], "fl");
      if (hidden) {
        const p = [x0, y0, z0];
        for (const q of [[x0, y0, z1], [x1, y0, z0], [x0, y1, z0]]) make(g, "path", { d: pathOf([p, q]), class: "h" });
      }
      edge(g, [[x1, y0, z0], [x1, y1, z0], [x0, y1, z0]], cls);
      for (const [vx, vy] of [[x1, y1], [x1, y0], [x0, y1]]) edge(g, [[vx, vy, z0], [vx, vy, z1]], cls);
      edge(g, [[x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]], cls, true);
    };
    // A horizontal circle seen from the iso camera, split so the back half can sit behind things
    const arc = (g, [x, y, z], r, front, cls) => {
      const [cx, cy] = iso([x, y, z]).map(r1);
      const rx = r1(r * C * Math.SQRT2);
      const ry = r1(r * Math.SQRT1_2);
      const [from, to] = front ? [cx + rx, cx - rx] : [cx - rx, cx + rx];
      return make(g, "path", { d: `M${from} ${cy}A${rx} ${ry} 0 0 1 ${to} ${cy}`, class: cls });
    };

    const a = compact ? 118 : 150;
    const POS = { agents: [-a, -a], workers: [a, -a], containers: [a, a], wynd: [-a, a] };
    // Routes leave the core as a pinwheel and enter each pad from the side
    const ROUTES = {
      agents: [[0, -60, 0], [0, -a, 0], [-a + 42, -a, 0]],
      workers: [[60, 0, 0], [a, 0, 0], [a, -a + 42, 0]],
      containers: [[0, 60, 0], [0, a, 0], [a - 42, a, 0]],
      wynd: [[-60, 0, 0], [-a, 0, 0], [-a, a - 42, 0]],
    };
    const ROUTE_AT = { agents: 3.0, workers: 3.15, containers: 3.3, wynd: 3.45 };
    const ASSEMBLE_AT = (key) => ROUTE_AT[key] + 0.45;

    // Each SVG in the scene holds one layer under the camera transform. Only the
    // modules layer, which holds the links, is exposed to assistive tech.
    const camTargets = svgs.map((s) => group(s));
    const [floor, behind, modLayer, logoLayer, fxLayer] = camTargets;
    fxLayer.classList.add("fx");

    // The grid fades out under a rect that goes from clear to the background colour,
    // which looks the same as a mask but costs no offscreen buffer on every repaint
    const defs = make(svgs[0], "defs");
    const gradient = make(defs, "radialGradient", { id: "cw-system-fade" });
    make(gradient, "stop", { offset: "0.35", class: "fade", "stop-opacity": "0" });
    make(gradient, "stop", { offset: "1", class: "fade" });
    const gridLayer = group(floor, { class: "grid" });
    make(floor, "rect", { x: -720, y: -420, width: 1440, height: 840, fill: "url(#cw-system-fade)" });
    const annoLayer = group(behind);
    const routeLayer = group(behind);
    const underLayer = group(behind); // packets that pass under the core and behind modules

    // Floor grid, rippling out from the centre
    const R = 390;
    for (let i = -13; i <= 13; i++) {
      for (const k of [0, 1]) {
        const v = i * 30;
        const e = make(gridLayer, "path", {
          d: pathOf(k ? [[v, -R], [v, R]] : [[-R, v], [R, v]]),
          class: i % 4 ? "g" : "g major",
        });
        fade(e, Math.abs(i) * 0.035, 0.6);
      }
    }

    // Axis key, outside the camera so it stays put
    if (!compact) {
      const key = group(svgs[4], { class: "gizmo", transform: "translate(-462 248)" });
      for (const [p, name] of [[[20, 0, 0], "x"], [[0, 20, 0], "y"], [[0, 0, 20], "z"]]) {
        edge(key, [[0, 0, 0], p], "l2");
        label(key, p.map((v) => v * 1.35), name, "m s");
      }
      fade(key, 0.4, 0.6);
    }

    // Stage 1: annotations around the floating logo
    {
      const g = group(annoLayer);
      edge(g, [[-12, 0, 0], [12, 0, 0]], "l2");
      edge(g, [[0, -12, 0], [0, 12, 0]], "l2");
      arc(g, [0, 0, 0], 8, false, "e l2");
      arc(g, [0, 0, 0], 8, true, "e l2");
      make(g, "path", { d: pathOf([[0, 0, 50], [0, 0, 2]]), class: "h guide" });
      label(g, [0, 0, 22], "z 110", "m", 8, 0, "start");
      const B = { x: 84, top: -178, bottom: -42, len: 12 };
      for (const sx of [-1, 1]) {
        for (const [y, dir] of [[B.top, 1], [B.bottom, -1]]) {
          make(g, "path", {
            d: `M${sx * B.x} ${y + dir * B.len}V${y}H${sx * (B.x - B.len)}`,
            class: "e l2",
          });
        }
      }
      label(g, [0, 0, 0], "[0, 0, 110]", "m", -B.x, B.bottom + 14, "start");
      if (!compact) {
        make(g, "path", { d: "M70 -128L96 -150H190", class: "e l2" });
        label(g, [0, 0, 0], "python", "", 98, -156, "start");
        label(g, [0, 0, 0], "cpython on wasm", "m", 98, -138, "start");
      }
      assemble(g, 0.7, 1.0, 2.15);
    }

    // The Python logo: assembles, turns to face the camera, then folds flat onto the core
    {
      const parts = LOGO.snakes.map((pts, i) => {
        const cls = i ? "e a" : "e";
        const back = make(logoLayer, "path", { class: cls + " back" });
        const ribs = make(logoLayer, "path", { class: "e l2 back" });
        const fill = make(logoLayer, "path", { class: "f ft" });
        const front = make(logoLayer, "path", { class: cls });
        const eye = make(logoLayer, "path", { class: cls });
        draw(back, 0.35, 1.2);
        draw(ribs, 0.6, 1.0);
        draw(front, 0.15 + i * 0.15, 1.2);
        draw(eye, 1.0, 0.5);
        return { pts, eye: LOGO.eyes[i], back, ribs, fill, front, eyeEl: eye };
      });
      const backs = parts.flatMap((p) => [p.back, p.ribs]);
      let lastKey = "";
      on((t) => {
        const turn = out3(prog(t, 0, 2.0));
        const fold = inOut(prog(t, 2.2, 1.3));
        const pose = {
          yaw: lerp(-0.62, 0, turn),
          pitch: lerp(0, Math.PI / 2, fold),
          z: lerp(110, 20.5, fold),
          s: lerp(1, 0.5, fold),
          w: lerp(5, 0.5, fold),
        };
        for (const p of parts) set(p.fill, "opacity", r3(0.92 * prog(t, 1.2, 0.6)));
        for (const e of backs) set(e, "opacity", r3(1 - fold));
        const key = Object.values(pose).map(r3).join();
        if (key === lastKey) return;
        lastKey = key;
        const k = Math.SQRT1_2;
        const cy = Math.cos(pose.yaw);
        const sy = Math.sin(pose.yaw);
        const cp = Math.cos(pose.pitch);
        const sp = Math.sin(pose.pitch);
        const rot = ([x, y, z]) => [x * cy - y * sy, x * sy + y * cy, z];
        const U = rot([k, -k, 0]);
        const V = rot([-k * sp, -k * sp, cp]);
        const N = rot([k * cp, k * cp, sp]); // points at the camera
        const P = ([u, v], w) => [0, 1, 2].map((i) => pose.s * (u * U[i] + v * V[i]) + w * N[i] + (i === 2 ? pose.z : 0));
        for (const p of parts) {
          const front = p.pts.map((q) => P(q, pose.w));
          const d = pathOf(front, true);
          set(p.front, "d", d);
          set(p.fill, "d", d);
          set(p.back, "d", pathOf(p.pts.map((q) => P(q, -pose.w)), true));
          set(p.ribs, "d", p.pts.filter((_, i) => i % 10 === 0).map((q) => pathOf([P(q, pose.w), P(q, -pose.w)])).join(""));
          set(p.eyeEl, "d", pathOf(p.eye.map((q) => P(q, pose.w)), true));
        }
      });
    }

    // Core: a low two-step plinth with a socket where each route plugs in
    const core = group(modLayer, { class: "core" });
    {
      const socket = (sx, sy) => box(core, [sx * 62, sy * 62, 0], sx ? [5, 16, 5] : [16, 5, 5], "l2");
      socket(-1, 0);
      socket(0, -1);
      box(core, [0, 0, 0], [120, 120, 12], "", true);
      box(core, [0, 0, 12], [84, 84, 8]);
      edge(core, [[-36, -36, 20], [36, -36, 20], [36, 36, 20], [-36, 36, 20]], "l2", true);
      socket(1, 0);
      socket(0, 1);
      // The name is set into the front face of the plinth, running along x
      const [nx, ny] = iso([-57, 60, 3.5]);
      const name = make(core, "text", { class: "t b engraved", transform: `matrix(${r3(C)} 0.5 0 1 ${r1(nx)} ${r1(ny)})` });
      name.textContent = "CROSSWIND";
      assemble(core, 2.3, 1.0);
      const rim = make(core, "path", {
        d: pathOf([[-42, -42, 20], [42, -42, 20], [42, 42, 20], [-42, 42, 20]], true),
        class: "e a",
      });
      // Registered last (see the end of build) so it can read what passed through this frame
      frame.coreRim = rim;
    }

    // Each module is a link; its pad, body and label live inside it
    const modules = {};
    const fxFor = {};
    for (const key of ["agents", "wynd", "workers", "containers"]) {
      const m = MODULES[key];
      const [x, y] = POS[key];
      const link = make(modLayer, "a", {
        href: m.href,
        class: "mod",
        "data-mod": key,
        "aria-label": `${m.name}, ${m.pkg}. ${m.desc}`,
      });
      const pad = group(link, { class: "pad" });
      box(pad, [x, y, 0], [84, 84, 5], "l2");
      // Agents sit behind the core, so their name goes beside the pad instead of below it
      const name = group(link);
      const side = key === "agents";
      const corner = side ? [x - 42, y + 42] : [x + 42, y + 42];
      const [lx, ly] = iso(corner);
      const [dx, dy, anchor] = side ? [-14, -4, "end"] : [0, 22, "middle"];
      const t1 = make(name, "text", { x: r1(lx + dx), y: r1(ly + dy), class: "t b", "text-anchor": anchor });
      make(t1, "tspan", { class: "num" }).textContent = m.n + " ";
      t1.append(m.name.toUpperCase());
      label(name, corner, m.pkg, "m", dx, dy + 14, anchor);
      assemble(pad, ASSEMBLE_AT(key), 0.5);
      fade(name, ASSEMBLE_AT(key) + 0.7, 0.5);
      modules[key] = link;
      fxFor[key] = group(fxLayer, { "data-mod": key });
    }

    // Routes, drawn outward from the core
    const pulses = {};
    for (const key of Object.keys(ROUTES)) {
      const pts = ROUTES[key];
      const g = group(routeLayer, { "data-mod": key });
      const route = make(g, "path", { d: pathOf(pts), class: "route" });
      draw(route, ROUTE_AT[key], 0.55);
      const elbow = dot(g, pts[1], 2.2, "node");
      fade(elbow, ROUTE_AT[key] + 0.25, 0.3);
      pulses[key] = make(g, "path", { d: pathOf(pts), class: "pulse", pathLength: 1, "stroke-dasharray": "0.12 2" });
    }

    // 01 Agents: a small robot that takes a task, works on it and sends back a result
    {
      const [x, y] = POS.agents;
      const g = group(modules.agents, { class: "body" });
      const fx = fxFor.agents;
      box(g, [x + 2, y - 12.5, 9], [6, 5, 14]);
      box(g, [x, y, 5], [24, 20, 22]);
      box(g, [x + 2, y + 12.5, 9], [6, 5, 14]);
      edge(g, [[x + 12, y - 5, 12], [x + 12, y + 5, 12], [x + 12, y + 5, 21], [x + 12, y - 5, 21]], "l2", true);
      edge(g, [[x, y, 27], [x, y, 30]]);
      const ringBack = arc(g, [x, y, 37.5], 20, false, "ring");
      box(g, [x, y, 30], [22, 18, 15]);
      const eyes = [-1, 1].map((s) => edge(g, [[x + 11, y + s * 2.5, 38.5], [x + 11, y + s * 6.5, 38.5]], "eye"));
      edge(g, [[x, y, 45], [x, y, 54]]);
      const led = dot(g, [x, y, 56.5], 2.4);
      const ringFront = arc(g, [x, y, 37.5], 20, true, "ring");
      assemble(g, ASSEMBLE_AT("agents") + 0.15, 0.9);

      const state = label(fx, [x, y, 64], "", "m readout", 0, -6);
      fade(state, 5.0, 0.4);
      const card = (cls) => {
        const c = group(fx, { class: "card " + cls });
        make(c, "path", { d: pathOf([[-6, -6], [6, -6], [6, 6], [-6, 6]], true) });
        return c;
      };
      const task = card("");
      make(task, "path", { d: pathOf([[-3, -2.5], [3, -2.5]]) + pathOf([[-3, 1], [1.5, 1]]), class: "ink" });
      const result = card("a");
      make(result, "path", { d: pathOf([[-3, 0.5], [-0.8, 2.6], [3, -2.6]]), class: "ink" });
      const path = [[0, -84, 3], [0, -a, 3], [x + 42, y, 3], [x + 14, y, 38]];
      const back = [...path].reverse();
      // One task every 4.2s from stage 3 on; END lands in the rest gap, so the last frame is clean
      const FIRST = 5.15;
      const PERIOD = 4.2;
      on((t) => {
        const c = t < FIRST ? -1 : (t - FIRST) % PERIOD;
        const pIn = prog(c, 0, 1.1);
        const pOut = prog(c, 2.5, 1.1);
        set(task, "transform", at(along(path, inOut(pIn))));
        set(task, "opacity", r3(trip(pIn)));
        set(result, "transform", at(along(back, inOut(pOut))));
        set(result, "opacity", r3(trip(pOut)));
        if (pOut > 0.9 && pOut < 1) frame.flash = Math.max(frame.flash, 1);
        const busy = c >= 1.1 && c < 2.5;
        const ring = busy ? clamp(Math.min(c - 1.1, 2.5 - c) * 6) : 0;
        for (const r of [ringBack, ringFront]) {
          set(r, "opacity", r3(ring));
          if (ring) set(r, "stroke-dashoffset", r1(-t * 30)); // spinning an invisible ring still repaints
        }
        for (const e of eyes) toggle(e, "on", c >= 1.05 && c < 2.75);
        toggle(led, "on", busy && Math.floor(t * 6) % 2 === 0);
        write(state, c < 1.05 ? "agent: idle" : c < 2.5 ? "agent: working" : "agent: replied");
        toggle(state, "on", c >= 1.05 && c < 3.9);
      });
    }

    // 03 Containers: a glass cube; in stage 3 a process boots inside it and it starts running
    {
      const [x, y] = POS.containers;
      const link = modules.containers;
      const s = 25;
      const [X0, X1, Y0, Y1, Z0, Z1] = [x - s, x + s, y - s, y + s, 5, 55];
      const back = group(link, { class: "body" });
      edge(back, [[X0, Y0, Z0], [X0, Y0, Z1]], "l2");
      edge(back, [[X0, Y0, Z0], [X1, Y0, Z0]], "l2");
      edge(back, [[X0, Y0, Z0], [X0, Y1, Z0]], "l2");
      const proc = group(link);
      box(proc, [x, y, 19], [22, 22, 22], "proc");
      const scan = make(link, "path", { d: pathOf([[X0, Y0, 0], [X1, Y0, 0], [X1, Y1, 0], [X0, Y1, 0]], true), class: "scan" });
      const front = group(link, { class: "body" });
      face(front, [[X0, Y0, Z1], [X1, Y0, Z1], [X1, Y1, Z1], [X0, Y1, Z1]], "glass");
      face(front, [[X1, Y0, Z0], [X1, Y1, Z0], [X1, Y1, Z1], [X1, Y0, Z1]], "glass");
      face(front, [[X0, Y1, Z0], [X1, Y1, Z0], [X1, Y1, Z1], [X0, Y1, Z1]], "glass");
      edge(front, [[X1, Y0, Z0], [X1, Y1, Z0], [X0, Y1, Z0]]);
      for (const [vx, vy] of [[X1, Y1], [X1, Y0], [X0, Y1]]) edge(front, [[vx, vy, Z0], [vx, vy, Z1]]);
      edge(front, [[X0, Y0, Z1], [X1, Y0, Z1], [X1, Y1, Z1], [X0, Y1, Z1]], "", true);
      edge(front, [[X1, y - 6, 24], [X1, y + 6, 24], [X1, y + 6, 32], [X1, y - 6, 32]], "l2", true);
      const led = dot(front, [X1 - 9, Y1, 47], 2.2);
      assemble(back, ASSEMBLE_AT("containers") + 0.1, 0.8);
      assemble(front, ASSEMBLE_AT("containers") + 0.2, 0.9);
      assemble(proc, 5.6, 0.7);

      const fx = fxFor.containers;
      const port = label(fx, [X1, Y0, 28], ":8080", "m readout", 8, 3, "start");
      fade(port, 6.9, 0.4);
      const state = label(fx, [X1, Y0, Z1], "", "m readout", 8, -8, "start");
      fade(state, 5.0, 0.4);
      const procEdges = proc.querySelectorAll(".e");
      on((t) => {
        const p = prog(t, 6.25, 0.8);
        set(scan, "transform", `translate(0 ${r1(-lerp(Z0, Z1, inOut(p)))})`);
        set(scan, "opacity", r3(trip(p)));
        const running = t >= 7.05;
        for (const e of procEdges) toggle(e, "on", running);
        toggle(led, "on", running);
        write(state, t < 5.6 ? "container: stopped" : running ? "container: running" : "container: starting");
        toggle(state, "on", t >= 5.6);
      });
    }

    // 04 Wynd: dependencies ride a conveyor through an import scan; unused ones drop out,
    // the rest are pressed into one bundle, which travels through the core to Workers
    {
      const [x, y] = POS.wynd;
      const link = modules.wynd;
      const fx = fxFor.wynd;
      const bs = compact ? 10 : 12; // dependency block size
      const sp = compact ? 12 : 15; // spacing between blocks on the belt
      const lead = compact ? 13 : 18; // gap between the gate and the nearest block
      const G = x - (compact ? 96 : 120); // gate position on x
      const ss = compact ? 50 : 58; // stack slab size
      const belt = group(link, { class: "body" });
      const railEnd = x - 44;
      const railStart = G - lead - 3 * sp - 12;
      for (const o of [-15, 15]) make(belt, "path", { d: pathOf([[railStart, y + o], [railEnd, y + o]]), class: "h guide" });
      const gate = edge(belt, [[G, y - 18, 0], [G, y - 18, 26], [G, y + 18, 26], [G, y + 18, 0]]);
      const slabs = [0, 1, 2, 3].map((i) => {
        const slab = group(link, { class: "body" });
        box(slab, [x, y, 5 + i * 10], [ss - i * 4, ss - i * 4, 5]);
        return slab;
      });
      assemble(belt, ASSEMBLE_AT("wynd") + 0.1, 0.8);
      slabs.forEach((slab, i) => assemble(slab, ASSEMBLE_AT("wynd") + 0.15 + i * 0.12, 0.6));

      const UNUSED = new Set([1, 2, 4, 7]);
      const D = 2 * lead + 3 * sp; // how far the belt moves
      const blocks = Array.from({ length: 8 }, (_, i) => {
        const row = i >> 2;
        const col = i & 3;
        const g = group(fx);
        const body = group(g);
        box(body, [0, 0, 0], [bs, bs, bs]);
        assemble(body, 5.0 + i * 0.05, 0.45);
        return { g, body, x0: G - lead - (3 - col) * sp, y: y + (row ? 8 : -8), unused: UNUSED.has(i) };
      });
      const kept = blocks.filter((b) => !b.unused);
      const stackTop = 40 + 5;
      kept.forEach((b, i) => {
        b.target = [x + (i & 1 ? 1 : -1) * (bs / 2 + 0.6), y + (i & 2 ? 1 : -1) * (bs / 2 + 0.6), stackTop + 4];
      });
      const bundle = group(fx);
      box(bundle, [0, 0, 0], [bs * 1.1, bs * 1.1, bs * 0.8], "on");
      const travel = group(underLayer);
      box(travel, [0, 0, 0], [bs, bs, bs * 0.7], "on");
      const deploy = [[x, y - ss / 2, 4], [x, 0, 4], [a, 0, 4], [a, -a + 30, 4]];
      const state = label(fx, [G, y - 18, 26], "", "m readout", 0, -14);
      fade(state, 5.0, 0.4);

      on((t) => {
        const belted = inOut(prog(t, 5.6, 1.2)) * D;
        const rise = inOut(prog(t, 6.85, 0.3));
        const slide = inOut(prog(t, 7.15, 0.35));
        const squeeze = inOut(prog(t, 7.5, 0.4));
        let scanning = false;
        for (const b of blocks) {
          const bx = b.x0 + belted;
          if (Math.abs(bx - G) < bs * 0.7 && belted < D) scanning = true;
          const past = bx > G;
          if (b.unused) {
            const drop = clamp((bx - G) / (lead * 0.8));
            set(b.g, "transform", at([bx, b.y, -14 * drop]));
            set(b.g, "opacity", r3(1 - drop));
            toggle(b.body, "dead", past);
          } else {
            const [tx, ty, tz] = b.target;
            const p = [lerp(bx, tx, slide), lerp(b.y, ty, slide), lerp(0, tz, rise)];
            const pressed = p.map((v, i) => lerp(v, [x, y, stackTop + 4][i], squeeze));
            set(b.g, "transform", `${at(pressed)} scale(${r3(1 - 0.45 * squeeze)})`);
            set(b.g, "opacity", r3(1 - prog(t, 7.75, 0.2)));
            toggle(b.body, "kept", past);
          }
        }
        toggle(gate, "on", scanning);
        // The stack presses down while the bundle forms, then springs back
        const press = squeeze * (1 - inOut(prog(t, 8.2, 0.4)));
        slabs.forEach((slab, i) => set(slab, "transform", `translate(0 ${r1(i * 4 * press)})`));
        const sink = inOut(prog(t, 7.95, 0.3));
        set(bundle, "transform", at([x, y, stackTop + 4 - 22 * sink]));
        set(bundle, "opacity", r3(prog(t, 7.7, 0.2) * (1 - sink)));
        const go = prog(t, 8.2, 1.15);
        const pos = along(deploy, inOut(go));
        set(travel, "transform", at(pos));
        set(travel, "opacity", r3(trip(go)));
        if (go > 0 && go < 1 && Math.abs(pos[0]) < 60 && Math.abs(pos[1]) < 60) frame.flash = 1;
        const unusedGone = t >= 6.6;
        write(state, t < 5.6 ? "deps: 8" : !unusedGone ? "scanning imports" : t < 7.8 ? "unused: 4 dropped" : "bundle: 4 of 8");
        toggle(state, "on", t >= 5.6);
      });
    }

    // 02 Workers: a runtime that comes up when the bundle lands, then serves requests
    {
      const [x, y] = POS.workers;
      const link = modules.workers;
      const fx = fxFor.workers;
      const g = group(link, { class: "body" });
      const H = 52;
      box(g, [x, y, 5], [40, 40, H]);
      for (const k of [1, 2]) {
        const z = 5 + (k * H) / 3;
        edge(g, [[x + 20, y - 20, z], [x + 20, y + 20, z], [x - 20, y + 20, z]], "l2");
      }
      for (const o of [-8, 0, 8]) edge(g, [[x - 12, y + o, 57], [x + 12, y + o, 57]], "l2");
      const leds = [0, 1, 2].map((k) => {
        const z = 5 + ((k + 0.5) * H) / 3;
        edge(g, [[x - 6, y + 20, z], [x + 13, y + 20, z]], "l2");
        return dot(g, [x - 12, y + 20, z], 2);
      });
      assemble(g, ASSEMBLE_AT("workers") + 0.15, 0.9);

      const reach = compact ? 72 : 112;
      const lanes = [-12, 0, 12].map((o) => {
        const pts = [[x + 20 + reach, y + o, 30], [x + 22, y + o, 30]];
        const lane = make(link, "path", { d: pathOf(pts), class: "h guide" });
        fade(lane, 9.5, 0.5);
        const tick = make(link, "path", { d: pathOf([[pts[0][0], y + o, 26], [pts[0][0], y + o, 34]]), class: "e l2" });
        draw(tick, 9.6, 0.3);
        return pts;
      });
      const clients = label(link, [x + 20 + reach, y + 12, 22], "clients", "m readout", 6, 14, "start");
      fade(clients, 9.7, 0.4);
      const dots = lanes.map(() => dot(fx, [0, 0, 0], 2.6, "packet"));
      const state = label(fx, [x - 20, y - 20, 57], "", "m readout", 0, -26);
      const served = label(fx, [x - 20, y - 20, 57], "", "m readout", 0, -13);
      fade(state, 5.0, 0.4);
      fade(served, 9.8, 0.4);
      const LIVE = 9.85;
      const PERIOD = 2.7;
      on((t) => {
        leds.forEach((led, k) => toggle(led, "on", t >= 9.35 + k * 0.15));
        let count = 0;
        dots.forEach((d, i) => {
          const s = t - (LIVE + i * 0.5);
          const phase = s >= 0 ? s % PERIOD : -1;
          if (s >= 1) count += Math.floor((s - 1) / PERIOD) + 1;
          const inbound = phase >= 0 && phase < 0.55;
          const outbound = phase >= 0.55 && phase < 1;
          const p = inbound ? phase / 0.55 : (phase - 0.55) / 0.45;
          const pts = inbound ? lanes[i] : [...lanes[i]].reverse();
          set(d, "transform", at(inbound || outbound ? along(pts, inOut(p)) : lanes[i][0]));
          set(d, "opacity", inbound || outbound ? 1 : 0);
          toggle(d, "on", outbound);
        });
        write(state, t < 9.2 ? "runtime: no deploy" : t < 9.85 ? "runtime: deploying" : "runtime: live");
        write(served, `served: ${count}`);
        toggle(state, "on", t >= 9.2);
      });
    }

    // Stage 4: a pulse leaves the core along one route at a time
    {
      const ORDER = ["agents", "workers", "containers", "wynd"];
      const START = 10.6;
      const EVERY = 2.6;
      on((t) => {
        const s = t - START;
        const n = Math.floor(s / EVERY);
        const u = s >= 0 ? (s % EVERY) / 1.3 : -1;
        for (const key of ORDER) {
          const live = s >= 0 && ORDER[n % 4] === key && u <= 1;
          set(pulses[key], "visibility", live ? "visible" : "hidden");
          if (live) set(pulses[key], "stroke-dashoffset", r3(lerp(0.12, -1, out3(u))));
        }
        if (s >= 0 && u < 0.2) frame.flash = Math.max(frame.flash, 1 - u * 5);
      });
    }

    // Camera: close on the logo, back for the ecosystem, a little further for the finale
    {
      const Z = compact ? [1.3, 0.92, 0.86] : [1.5, 1.08, 1.0];
      on((t) => {
        const pull = inOut(prog(t, 2.2, 2.2));
        const settle = inOut(prog(t, 10, 1.6));
        const zoom = lerp(lerp(Z[0], Z[1], pull), Z[2], settle);
        const fy = lerp(-95, -4, pull);
        const transform = `scale(${r3(zoom)}) translate(0 ${r1(-fy)})`;
        for (const g of camTargets) set(g, "transform", transform);
      });
    }

    // Anything that passed through the core this frame lights its rim
    on(() => set(frame.coreRim, "opacity", r3(frame.flash)));

    for (const link of Object.values(modules)) {
      link.addEventListener("pointerenter", () => hover(link));
      link.addEventListener("pointerleave", () => hover(null));
      link.addEventListener("focus", () => hover(link));
      link.addEventListener("blur", () => hover(null));
    }
  }

  // Hover and focus: dim the other modules and show a short description
  function hover(link) {
    const key = link?.dataset.mod;
    scene.querySelectorAll("[data-mod]").forEach((e) => {
      e.classList.toggle("is-dim", Boolean(key) && e.dataset.mod !== key);
      e.classList.toggle("is-hot", e.dataset.mod === key);
    });
    if (!key) {
      tip.hidden = true;
      return;
    }
    const m = MODULES[key];
    tip.querySelector(".cw-system__tip-name").textContent = m.name;
    tip.querySelector(".cw-system__tip-pkg").textContent = m.pkg;
    tip.querySelector(".cw-system__tip-desc").textContent = m.desc;
    tip.hidden = false;
    const box = link.querySelector(".pad").getBoundingClientRect();
    const host = figure.getBoundingClientRect();
    const left = Math.min(Math.max(box.left + box.width / 2 - host.left - tip.offsetWidth / 2, 8), host.width - tip.offsetWidth - 8);
    let topPx = box.top - host.top - tip.offsetHeight - 8 - box.height * 0.6;
    if (topPx < 8) topPx = box.bottom - host.top + 8;
    tip.style.transform = `translate(${Math.round(left)}px, ${Math.round(topPx)}px)`;
  }

  // Playback
  let t = 0;
  let raf = 0;
  let last = null;

  function render(time) {
    frame.flash = 0;
    for (const fn of tracks) fn(time);
    const stage = STAGES.findLastIndex((s) => time >= s);
    messages.forEach((m, i) => toggle(m, "is-on", i === Math.min(stage, 3)));
    stageButtons.forEach((b, i) => {
      const p = clamp((time - STAGES[i]) / (STAGES[i + 1] - STAGES[i])).toFixed(3);
      if (b._p !== p) b.style.setProperty("--p", (b._p = p));
      const current = i === Math.min(stage, 3);
      if (b._current !== current) {
        b._current = current;
        if (current) b.setAttribute("aria-current", "step");
        else b.removeAttribute("aria-current");
      }
    });
    write(clock, `t+${Math.min(time, 99.99).toFixed(2).padStart(5, "0")}s`);
  }

  function tick(now) {
    // The cap only stops a jump after the tab was hidden; a low cap would play slow phones in slow motion
    if (last !== null) t += Math.min(0.25, (now - last) / 1000);
    last = now;
    render(t);
    raf = requestAnimationFrame(tick);
  }

  const play = () => {
    if (reduced || raf) return;
    last = null;
    raf = requestAnimationFrame(tick);
  };
  const pause = () => {
    cancelAnimationFrame(raf);
    raf = 0;
  };

  // Reduced motion shows the finished frame of a stage instead of playing it
  const seek = (time) => {
    t = time;
    render(t);
    if (visible) play();
  };

  stageButtons.forEach((b, i) =>
    b.addEventListener("click", () => seek(reduced ? STAGES[i + 1] - 0.01 : STAGES[i])));
  figure.querySelector(".cw-system__replay").addEventListener("click", () => {
    if (reduced) return seek(END);
    figure.classList.add("is-rewinding");
    setTimeout(() => {
      figure.classList.remove("is-rewinding");
      seek(0);
    }, 250);
  });

  let visible = false;
  new IntersectionObserver(
    ([entry]) => {
      visible = entry.isIntersecting;
      if (visible) play();
      else pause();
    },
    { threshold: 0.35 },
  ).observe(figure);

  new ResizeObserver(() => {
    const next = figure.clientWidth < 640;
    if (next === compact) return;
    hover(null);
    build(next);
    render(t);
  }).observe(figure);

  build(figure.clientWidth < 640);
  t = reduced ? END : 0;
  render(t);
})();
