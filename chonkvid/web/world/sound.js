/* Soundscape for the world map: a small sound-physics engine over Voxtral chatter.
 *
 * The model, in one paragraph: every contact link on the map is a conversation. Its
 * loudness falls off with the distance from the camera centre to the link's midpoint,
 * measured in screen pixels, and the radius you can hear shrinks as you zoom in. Far
 * links are not just quieter, they are low-passed, so distance reads as muffling rather
 * than as a volume slider. Clicking a link solos it: everything else ducks and loses its
 * highs, the clicked pair comes forward. Underneath it all is a noise bed that stands in
 * for the crowd you cannot pick out, which fades as local voices take over.
 *
 * No audio starts without a user gesture (the browser forbids it), every failure path
 * fails silent, and with no links loaded the engine is a no-op.
 */
window.SoundPhysics = (function () {
  "use strict";

  const MAX_VOICES = 8;          // simultaneous conversations
  const NEAR_PX = 10;            // click tolerance for picking a link line, in screen px
  const GATE = 0.055;            // below this a voice is silent and its nodes are stopped

  let on = false, ctx = null, master = null, comp = null, bed = null, bedGain = null, bedLP = null;
  let cam = null;                 // {k, x, y, W, H, project}
  let model = [];                 // [{key, lonA, latA, lonB, latB, audioA, audioB}]
  let vol = 0.85, solo = null;
  let blocked = null;              // why the context will not run, if it will not
  let unlockBound = false;         // pointer/key listener that retries resume() after a tap
  let lastError = null;

  // Speech mode. Without Voxtral (MOCK), the server's clips are only a word-less babble, so
  // the browser's own speech voices read the real lines of the nearest conversation, in its
  // office's language, louder the closer it is. One conversation at a time: speechSynthesis
  // is a single queue and cannot be panned or mixed.
  let speech = false, speaking = null, speechNext = 0;
  const LANG_TAG = { en: "en-GB", fr: "fr-FR", de: "de-DE", es: "es-ES", pt: "pt-PT" };
  function voiceFor(lang) {
    const vs = window.speechSynthesis ? speechSynthesis.getVoices() : [];
    const tag = (LANG_TAG[lang] || lang || "en").toLowerCase();
    return vs.find((v) => v.lang.toLowerCase() === tag)
      || vs.find((v) => v.lang.toLowerCase().startsWith((lang || "en").toLowerCase())) || null;
  }
  function sayPair(key, g) {
    const m = model.find((x) => x.key === key);
    if (!m || !m.lineA) return;
    speaking = { key };
    const loud = Math.max(0.2, Math.min(1, g * 1.15)) * vol;
    const utter = (text, pitch) => {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = LANG_TAG[m.lang] || m.lang || "en-GB";
      const v = voiceFor(m.lang); if (v) u.voice = v;
      u.volume = loud; u.rate = 1.02; u.pitch = pitch;   // two speakers, two pitches
      return u;
    };
    const a = utter(m.lineA, 1.12);
    const last = m.lineB ? utter(m.lineB, 0.86) : a;
    last.onend = last.onerror = () => {
      if (speaking && speaking.key === key) { speaking = null; speechNext = performance.now() + 700; pump(); }
    };
    speechSynthesis.speak(a);
    if (last !== a) speechSynthesis.speak(last);
  }
  function stopSpeech() {
    if (window.speechSynthesis) speechSynthesis.cancel();
    speaking = null;
  }

  const nodes = new Map();        // key -> {gain, lp, panA, panB, gainA, gainB, bufA, bufB, timer, playing}
  const buffers = new Map();      // url -> AudioBuffer

  // How far you can hear, in screen pixels. Wide when zoomed out so the whole map is a
  // faint murmur, tight when zoomed in so only the neighbourhood is in the room.
  function radius(k) {
    const scale = cam ? Math.max(0.7, Math.min(1.6, cam.W / 1600)) : 1;
    return 900 * scale / Math.pow(Math.max(1, k), 1.3);
  }
  function closeness(k, d) {
    const R = radius(k);
    if (!R || d >= R) return 0;
    return Math.pow(1 - d / R, 1.7);
  }
  // Distance muffles: even a loud link keeps its highs rolled off while you are zoomed out,
  // which is why the world view is chatter you can hear but not follow.
  function cutoffFor(g, isSolo, k) {
    if (isSolo) return 18000;
    const open = Math.min(1, Math.max(0.3, (k || 1) / 3));
    return (420 + 7000 * Math.pow(Math.max(0, Math.min(1, g)), 0.85)) * open;
  }

  // ---------------------------------------------------------------- graph
  function ensure() {
    if (ctx) return ctx;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    ctx = new AC();
    comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.knee.value = 12; comp.ratio.value = 3.2;
    comp.attack.value = 0.006; comp.release.value = 0.24;
    master = ctx.createGain(); master.gain.value = vol;
    comp.connect(master); master.connect(ctx.destination);
    makeBed();
    // the context can start suspended and refuse to run if the browser does not count the
    // click as a gesture; without this we would schedule sources into a frozen timeline,
    // mark them "playing", draw speech bubbles, and play nothing at all
    ctx.onstatechange = () => {
      if (ctx.state === "running") { blocked = null; if (on) pump(); }
      else if (ctx.state === "suspended" && on) blocked = "suspended";
    };
    return ctx;
  }

  function makeBed() {
    const sr = ctx.sampleRate, n = Math.floor(sr * 3);
    const buf = ctx.createBuffer(1, n, sr), d = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < n; i++) {           // brown-ish noise: a crowd with no words in it
      const w = Math.random() * 2 - 1;
      last = (last + 0.02 * w) / 1.02;
      d[i] = last * 3.2;
    }
    bed = ctx.createBufferSource(); bed.buffer = buf; bed.loop = true;
    bedLP = ctx.createBiquadFilter(); bedLP.type = "lowpass"; bedLP.frequency.value = 620; bedLP.Q.value = 0.4;
    bedGain = ctx.createGain(); bedGain.gain.value = 0;
    bed.connect(bedLP); bedLP.connect(bedGain); bedGain.connect(comp);
    bed.start();
  }

  function nodeFor(key) {
    if (nodes.has(key)) return nodes.get(key);
    const e = {
      lp: ctx.createBiquadFilter(), gain: ctx.createGain(),
      panA: ctx.createStereoPanner(), panB: ctx.createStereoPanner(),
      gainA: ctx.createGain(), gainB: ctx.createGain(),
      bufA: null, bufB: null, timer: null, playing: false, loading: false, key,
      sources: [],
    };
    e.lp.type = "lowpass"; e.lp.Q.value = 0.9; e.lp.frequency.value = 600;
    e.gain.gain.value = 0;
    e.gainA.gain.value = 0.95; e.gainB.gain.value = 0.95;
    e.panA.connect(e.gainA); e.panB.connect(e.gainB);
    e.gainA.connect(e.lp); e.gainB.connect(e.lp);
    e.lp.connect(e.gain); e.gain.connect(comp);
    nodes.set(key, e);
    return e;
  }

  async function loadClip(url) {
    if (!url) return null;
    if (buffers.has(url)) return buffers.get(url);
    try {
      const r = await fetch(url);
      if (!r.ok) return null;
      const buf = await ctx.decodeAudioData(await r.arrayBuffer());
      buffers.set(url, buf);
      return buf;
    } catch (err) { return null; }
  }

  function schedule(key) {
    const e = nodes.get(key);
    if (!e || !e.bufA || !e.bufB || !on) return;
    if (ctx.state !== "running") {
      // do not start into a stopped clock: nothing would be heard while the UI said it was
      blocked = ctx.state;
      if (ctx.state === "suspended") { ctx.resume().catch(() => {}); }
      e.playing = false;
      e.timer = setTimeout(() => schedule(key), 400);
      return;
    }
    const t0 = ctx.currentTime + 0.06;
    const a = ctx.createBufferSource(), b = ctx.createBufferSource();
    a.buffer = e.bufA; b.buffer = e.bufB;
    a.connect(e.panA); b.connect(e.panB);
    a.start(t0);
    const bAt = t0 + Math.max(0.8, e.bufA.duration) + 0.22;
    b.start(bAt);
    const total = (bAt - t0) + Math.max(0.8, e.bufB.duration) + 0.7;
    e.timer = setTimeout(() => schedule(key), total * 1000);
    e.playing = true;
    // keep the live sources so disable() can actually silence them: clearing the timer
    // alone leaves the already-scheduled pair talking for another ten seconds
    e.sources.push(a, b);
    const drop = (src) => () => {
      try { src.disconnect(); } catch (x) {}
      const i = e.sources.indexOf(src);
      if (i >= 0) e.sources.splice(i, 1);
    };
    a.onended = drop(a);
    b.onended = drop(b);
  }

  function killEdge(key) {
    const e = nodes.get(key);
    if (!e) return;
    if (e.timer) { clearTimeout(e.timer); e.timer = null; }
    e.playing = false;
    const now = ctx ? ctx.currentTime : 0;
    if (ctx) {
      e.gain.gain.cancelScheduledValues(now);
      e.gain.gain.setTargetAtTime(0, now, 0.015);
    }
    const srcs = e.sources.splice(0, e.sources.length);
    // small fade so a mute does not click; then hard-stop whatever is still scheduled
    setTimeout(() => { for (const s of srcs) { try { s.stop(); } catch (x) {} } }, 90);
  }

  function stopEdge(key) {
    const e = nodes.get(key);
    if (!e) return;
    if (e.timer) { clearTimeout(e.timer); e.timer = null; }
    e.playing = false;
  }

  function startEdge(key, m) {
    const e = nodeFor(key);
    if (e.loading || e.playing) return;
    e.loading = true;
    Promise.all([loadClip(m.audioA), loadClip(m.audioB)]).then(([a, b]) => {
      e.loading = false;
      if (!on) return;
      if (!a || !b) return;             // no audio, stay silent
      e.bufA = a; e.bufB = b;
      // gate on the gain the mixer is aiming at, not the value mid-ramp: at the moment a
      // cached clip decodes the gain may still be ~0 even though the link is in earshot
      if ((e.target || 0) > GATE || solo === key) schedule(key);
    });
  }

  function stopEdge(key) {
    const e = nodes.get(key);
    if (!e) return;
    if (e.timer) { clearTimeout(e.timer); e.timer = null; }
    e.playing = false;
  }

  // ---------------------------------------------------------------- the model
  function screenOf(lon, lat) {
    if (!cam) return [0, 0];
    const p = cam.project(lon, lat);
    return [p[0] * cam.k + cam.x, p[1] * cam.k + cam.y];
  }

  // distance from a point to the segment AB: a conversation is audible if either speaker
  // is near you, not if its midpoint happens to be. Without this, a link whose two agents
  // sit in different cities reads as "nearest" when its midpoint lands in open ocean.
  function segDist(px, py, ax, ay, bx, by) {
    const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
    let t = l2 ? ((px - ax) * dx + (py - ay) * dy) / l2 : 0;
    t = Math.max(0, Math.min(1, t));
    return { d: Math.hypot(ax + t * dx - px, ay + t * dy - py), t };
  }

  function compute() {
    if (!cam) return [];
    const cx = cam.W / 2, cy = cam.H / 2;
    const out = [];
    for (const m of model) {
      const A = screenOf(m.lonA, m.latA), B = screenOf(m.lonB, m.latB);
      // the conversation happens at the two speakers, so the nearest speaker decides how
      // loud it is: a link whose line crosses the middle of the map is not "nearby"
      const dA = Math.hypot(A[0] - cx, A[1] - cy), dB = Math.hypot(B[0] - cx, B[1] - cy);
      const d = Math.min(dA, dB);
      const mx = dA <= dB ? A[0] : B[0], my = dA <= dB ? A[1] : B[1];
      const g = closeness(cam.k, d);
      out.push({ key: m.key, x1: A[0], y1: A[1], x2: B[0], y2: B[1], g, d, mx, my,
                 panA: Math.max(-0.9, Math.min(0.9, (A[0] - cx) / (cam.W / 2) * 0.8)),
                 panB: Math.max(-0.9, Math.min(0.9, (B[0] - cx) / (cam.W / 2) * 0.8)) });
    }
    out.sort((p, q) => q.g - p.g);
    return out;
  }

  function apply() {
    if (!on || !ctx) return;
    const view = compute();
    const byKey = new Map(view.map((v) => [v.key, v]));
    const audible = view.filter((v) => v.g > GATE).slice(0, MAX_VOICES);
    const keep = new Set(audible.map((v) => v.key));
    if (solo && !keep.has(solo)) keep.add(solo);

    if (speech) {
      // no babble clips: the nearest (or soloed) conversation is read aloud instead
      for (const key of nodes.keys()) killEdge(key);
      const target = solo || (audible[0] && audible[0].key) || null;
      const tg = solo ? 1 : (audible[0] ? audible[0].g : 0);
      if (speaking && (solo ? speaking.key !== solo : !keep.has(speaking.key))) stopSpeech();
      if (!speaking && target && performance.now() >= speechNext && !speechSynthesis.speaking) sayPair(target, tg);
      bedGain.gain.setTargetAtTime(0.012 * (1 - tg), ctx.currentTime, 0.25);   // barely-there room tone
      return;
    }

    // duck everything except a soloed link; mute everything out of earshot
    for (const [key, e] of nodes) {
      const v = byKey.get(key);
      const isSolo = solo === key;
      let g = v ? v.g : 0;
      if (solo) g = isSolo ? Math.max(0.85, g) : g * 0.06;
      const target = keep.has(key) ? Math.max(0, Math.min(1, g)) : 0;
      e.target = target;
      const t = ctx.currentTime, at = 0.08;
      e.gain.gain.setTargetAtTime(target * 0.9, t, at);
      e.lp.frequency.setTargetAtTime(cutoffFor(target, isSolo, cam && cam.k), t, at);
      e.panA.pan.setTargetAtTime(v ? v.panA : 0, t, at);
      e.panB.pan.setTargetAtTime(v ? v.panB : 0, t, at);
      // a ducked neighbour is still audible, quietly: do not kill it just because the
      // solo pushed its gain under the normal gate
      const floor = (solo && !isSolo) ? 0.01 : GATE;
      if (target <= floor) killEdge(key);
    }

    for (const v of audible) {
      const m = model.find((x) => x.key === v.key);
      if (m) startEdge(v.key, m);
    }
    if (solo) {
      const m = model.find((x) => x.key === solo);
      if (m) startEdge(solo, m);
    }

    // the crowd bed stands in for everything you cannot pick out
    const local = audible.length ? audible[0].g : 0;
    bedGain.gain.setTargetAtTime((0.05 + 0.03 * (1 - local)) * (solo ? 0.35 : 1), ctx.currentTime, 0.25);
    bedLP.frequency.setTargetAtTime(520 + 900 * (1 - local), ctx.currentTime, 0.25);
  }

  let raf = 0, tick = 0;
  function pump() {
    if (!on) return;
    if (raf) return;
    raf = requestAnimationFrame(() => { raf = 0; try { apply(); } catch (e) {} });
  }

  // ---------------------------------------------------------------- public API
  return {
    get on() { return on; },
    async enable() {
      if (!ensure()) return false;
      on = true;
      try { await ctx.resume(); } catch (e) {}
      pump();
      // a slow tick so clips that finish decoding after the last camera move still start,
      // and so links leaving earshot get silenced without needing a zoom event
      if (!tick) tick = setInterval(pump, 400);
      // Safari (and Chrome after a blocked start) will only resume inside a gesture, and the
      // first gesture can be spent on the overlay: retry on any later tap while sound is on
      if (!unlockBound) {
        unlockBound = true;
        const unlock = () => {
          if (on && ctx && ctx.state !== "running") {
            ctx.resume().then(() => { if (on) pump(); }).catch(() => {});
          }
        };
        document.addEventListener("pointerdown", unlock, { passive: true });
        document.addEventListener("keydown", unlock);
      }
      return true;
    },
    disable() {
      on = false;
      stopSpeech();
      if (tick) { clearInterval(tick); tick = 0; }
      for (const key of nodes.keys()) killEdge(key);
      if (bedGain) {
        bedGain.gain.cancelScheduledValues(ctx.currentTime);
        bedGain.gain.setTargetAtTime(0, ctx.currentTime, 0.03);
      }
      solo = null;
    },
    setVolume(v) { vol = Math.max(0, Math.min(1, v)); if (master) master.gain.value = vol; },
    setWorld(edges) {
      model = (edges || []).map((e) => ({
        key: e.key, lonA: e.lonA, latA: e.latA, lonB: e.lonB, latB: e.latB,
        audioA: e.audio && e.audio.a, audioB: e.audio && e.audio.b,
        lineA: e.lines && e.lines.a, lineB: e.lines && e.lines.b, lang: e.lang || "en",
      }));
      stopSpeech();
      solo = null;
      for (const k of nodes.keys()) stopEdge(k);
    },
    camera(c) { cam = c; pump(); },
    view: () => compute().filter((v) => v.g > GATE),
    pick(x, y, maxPx) {
      if (!cam) return null;
      const tol = maxPx || NEAR_PX;
      let best = null, bd = Infinity;
      for (const v of compute()) {
        if (v.g <= 0.02) continue;
        // pick the drawn line, not the marker: clicking the agent itself still opens its card
        const d = segDist(x, y, v.x1, v.y1, v.x2, v.y2).d;
        if (d < tol && d < bd) { bd = d; best = v.key; }
      }
      return best;
    },
    solo(key) {
      if (!key) return;
      if (speech) stopSpeech();       // the clicked conversation speaks next, straight away
      speechNext = 0;
      solo = key;
      const m = model.find((x) => x.key === key);
      if (m) startEdge(key, m);
      pump();
    },
    release() { solo = null; pump(); },
    get soloKey() { return solo; },
    // flat snapshot of the mixer, so a test can see the ducking rather than trust it
    levels() {
      const out = {};
      for (const [key, e] of nodes) {
        out[key] = { gain: +e.gain.gain.value.toFixed(4), cutoff: Math.round(e.lp.frequency.value),
                     panA: +e.panA.pan.value.toFixed(3), panB: +e.panB.pan.value.toFixed(3),
                     playing: e.playing };
      }
      return out;
    },
    // what is audible right now, with screen positions, for the speech bubbles
    // MOCK mode has no Voxtral clips: read the real lines with the browser's voices instead
    setSpeech(v) {
      speech = !!v && !!window.speechSynthesis;
      if (!speech) stopSpeech();
      pump();
    },
    get speech() { return speech; },
    active() {
      if (!cam) return [];
      const view = new Map(compute().map((v) => [v.key, v]));
      const out = [];
      if (speech && speaking) {
        const v = view.get(speaking.key);
        if (v) out.push({ key: v.key, x1: v.x1, y1: v.y1, x2: v.x2, y2: v.y2, g: Math.max(0.3, v.g), solo: solo === v.key });
      }
      for (const [key, e] of nodes) {
        if (!e.playing && !e.sources.length) continue;
        const v = view.get(key);
        if (!v) continue;
        out.push({ key, x1: v.x1, y1: v.y1, x2: v.x2, y2: v.y2,
                   g: Math.max(0, Math.min(1, e.gain.gain.value)), solo: solo === key });
      }
      return out;
    },
    // Is the problem the graph or the device? This plays a 1 s tone straight through the
    // master gain, so if nothing is heard, it is not the chatter clips.
    async probe() {
      if (!ensure()) return { ok: false, why: "no Web Audio in this browser" };
      try { await ctx.resume(); } catch (e) { lastError = String(e); }
      if (ctx.state !== "running") return { ok: false, why: "context is " + ctx.state, state: ctx.state };
      const o = ctx.createOscillator(), g = ctx.createGain();
      o.type = "sine"; o.frequency.value = 660;
      g.gain.value = 0.25;
      o.connect(g); g.connect(ctx.destination);
      o.start();
      o.stop(ctx.currentTime + 1.0);
      return { ok: true, state: ctx.state, sampleRate: ctx.sampleRate, out: "1 s of 660 Hz" };
    },
    diagnose() {
      const d = {
        ctx: ctx ? ctx.state : "none",
        blocked, lastError,
        master: master ? master.gain.value : null,
        volume: vol,
        sampleRate: ctx ? ctx.sampleRate : null,
        model: model.length, clipsDecoded: buffers.size,
        voices: [...nodes.values()].filter((e) => e.playing).map((e) => ({
          key: e.key, gain: +e.gain.gain.value.toFixed(3),
          cutoff: Math.round(e.lp.frequency.value),
          sources: e.sources.length,
          clipA: e.bufA ? +e.bufA.duration.toFixed(1) : null,
          clipB: e.bufB ? +e.bufB.duration.toFixed(1) : null,
        })),
        output: ctx ? ctx.state === "running" ? "graph runs; if still silent, check the OS/browser output" : "context NOT running: click the Sound pill again" : "no context",
      };
      console.log("[sound]", d);
      return d;
    },
    stats() {
      const view = compute();
      const nearest = view.slice(0, 4).map((v) => ({ key: v.key, d: Math.round(v.d), g: +v.g.toFixed(3) }));
      return {
        on, zoom: cam ? cam.k : null, solo,
        ctx: ctx ? ctx.state : "none", blocked,
        model: model.length, loaded: buffers.size,
        audible: view.filter((v) => v.g > GATE).slice(0, MAX_VOICES)
          .map((v) => ({ key: v.key, g: +v.g.toFixed(3), d: Math.round(v.d) })),
        playing: [...nodes.values()].filter((e) => e.playing).map((e) => e.key),
        nearest,
      };
    },
  };
})();
