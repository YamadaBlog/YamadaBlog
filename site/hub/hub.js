/* ═══════════════════════════════════════════════════════════════════════════
   THE ATRIUM — hub renderer
   Hand-rolled WebGL2. No three.js, no GSAP, no bundler, no dependency of any
   kind. ~30 KB of source, one network request, loaded only after the page has
   already painted a working index.

   THE IDEA
     Five slabs of cast glass stand in a slow ring. Behind each one hangs a
     plate carrying the real typography and palette of one portfolio. The glass
     is frosted, so a plate is nothing but a coloured smear — until the camera
     comes closer, at which point roughness falls, dispersion tightens, and the
     type resolves. Focus is the interaction. The meter in the corner is not
     decoration: it prints the roughness uniform, inverted.

   HOW IT DRAWS   (two passes, one extra scene render, that is the whole cost)
     A. backdrop → FBO at 0.34–0.62× canvas, mipmapped
          gradient · ground grid · five plates · five slabs drawn flat
     B. default framebuffer
          blit the backdrop · five slabs drawn with refraction, sampling A

     Frosting is textureLod against A's mip chain, which is why it costs
     nothing. Dispersion is the same offset scaled per channel. Both collapse
     to a single tap when the frame budget says so.
   ═══════════════════════════════════════════════════════════════════════════ */
'use strict';

/* ── linear algebra ──────────────────────────────────────────────────────
   Column-major, WebGL convention: m[col*4 + row]. Every model transform here
   is rigid (rotation then translation, never scale), so the normal matrix is
   just mat3(view * model) — no inverse-transpose anywhere. */
const M = {
  persp(o, fovy, aspect, near, far) {
    const f = 1 / Math.tan(fovy / 2), d = near - far;
    o.fill(0);
    o[0] = f / aspect; o[5] = f; o[10] = (far + near) / d; o[11] = -1; o[14] = 2 * far * near / d;
    return o;
  },
  look(o, ex, ey, ez, tx, ty, tz) {
    let zx = ex - tx, zy = ey - ty, zz = ez - tz;
    let l = Math.hypot(zx, zy, zz) || 1; zx /= l; zy /= l; zz /= l;
    // up = (0,1,0); x = normalize(up × z)
    let xx = zz, xy = 0, xz = -zx;
    l = Math.hypot(xx, xy, xz) || 1; xx /= l; xy /= l; xz /= l;
    const yx = zy * xz - zz * xy, yy = zz * xx - zx * xz, yz = zx * xy - zy * xx;
    o[0] = xx; o[1] = yx; o[2] = zx; o[3] = 0;
    o[4] = xy; o[5] = yy; o[6] = zy; o[7] = 0;
    o[8] = xz; o[9] = yz; o[10] = zz; o[11] = 0;
    o[12] = -(xx * ex + xy * ey + xz * ez);
    o[13] = -(yx * ex + yy * ey + yz * ez);
    o[14] = -(zx * ex + zy * ey + zz * ez);
    o[15] = 1;
    return o;
  },
  mul(o, a, b) {
    for (let c = 0; c < 4; c++) for (let r = 0; r < 4; r++) {
      o[c * 4 + r] = a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1] + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3];
    }
    return o;
  },
  /* translate(p) · rotateY(ry) · rotateZ(rz), right-handed.
     rotY columns are (c,0,-s) (0,1,0) (s,0,c) — getting that sign backwards
     points every slab the wrong way round the ring, which is what the unit
     test in this repo is for. */
  trs(o, px, py, pz, ry, rz) {
    const ca = Math.cos(ry), sa = Math.sin(ry), cb = Math.cos(rz), sb = Math.sin(rz);
    o[0] = ca * cb; o[1] = sb; o[2] = -sa * cb; o[3] = 0;
    o[4] = -ca * sb; o[5] = cb; o[6] = sa * sb; o[7] = 0;
    o[8] = sa; o[9] = 0; o[10] = ca; o[11] = 0;
    o[12] = px; o[13] = py; o[14] = pz; o[15] = 1;
    return o;
  },
  mat3(o9, m16) {
    o9[0] = m16[0]; o9[1] = m16[1]; o9[2] = m16[2];
    o9[3] = m16[4]; o9[4] = m16[5]; o9[5] = m16[6];
    o9[6] = m16[8]; o9[7] = m16[9]; o9[8] = m16[10];
    return o9;
  }
};

const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
const lerp = (a, b, t) => a + (b - a) * t;
const easeIO = t => t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
const TAU = Math.PI * 2;
/* shortest signed angular difference, in (-π, π] */
const angDiff = (a, b) => { let d = (a - b) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU; return d; };

/* ── geometry: a slab of glass ───────────────────────────────────────────
   A cube grid pushed onto a rounded-box surface. For a surface direction k on
   the unit cube, let c = clamp(k * extents, -core, core) with core = extents-r;
   then N = normalize(k*extents - c) and P = c + N*r. Exact normals, no
   smoothing pass. The parameter is warped so half the segments land on the
   bevel, which is 6% of the surface but all of the light. */
function roundedBox(ex, ey, ez, r, seg) {
  r = Math.min(r, Math.min(ex, ey, ez) * 0.92);
  const E = [ex, ey, ez], core = [ex - r, ey - r, ez - r];
  const flat = [core[0] / ex, core[1] / ey, core[2] / ez];
  const F = [ // n, u, w  with  cross(u,w) === n
    [[1, 0, 0], [0, 0, -1], [0, 1, 0]], [[-1, 0, 0], [0, 0, 1], [0, 1, 0]],
    [[0, 1, 0], [1, 0, 0], [0, 0, -1]], [[0, -1, 0], [1, 0, 0], [0, 0, 1]],
    [[0, 0, 1], [1, 0, 0], [0, 1, 0]], [[0, 0, -1], [-1, 0, 0], [0, 1, 0]]
  ];
  const pos = [], nrm = [], idx = [];
  const remap = (t, a) => { const s = t < 0 ? -1 : 1, m = Math.abs(t);
    return s * (m < 0.5 ? (m / 0.5) * a : a + ((m - 0.5) / 0.5) * (1 - a)); };
  const axisOf = v => v[0] ? 0 : v[1] ? 1 : 2;

  for (const [n, u, w] of F) {
    const base = pos.length / 3;
    const au = axisOf(u), aw = axisOf(w);
    for (let j = 0; j <= seg; j++) for (let i = 0; i <= seg; i++) {
      const p = remap(i / seg * 2 - 1, flat[au]), q = remap(j / seg * 2 - 1, flat[aw]);
      const k = [n[0] + u[0] * p + w[0] * q, n[1] + u[1] * p + w[1] * q, n[2] + u[2] * p + w[2] * q];
      const s = [k[0] * E[0], k[1] * E[1], k[2] * E[2]];
      const c = [clamp(s[0], -core[0], core[0]), clamp(s[1], -core[1], core[1]), clamp(s[2], -core[2], core[2])];
      let d = [s[0] - c[0], s[1] - c[1], s[2] - c[2]];
      let L = Math.hypot(d[0], d[1], d[2]);
      if (L < 1e-9) { d = n.slice(); L = 1; }
      const N = [d[0] / L, d[1] / L, d[2] / L];
      pos.push(c[0] + N[0] * r, c[1] + N[1] * r, c[2] + N[2] * r);
      nrm.push(N[0], N[1], N[2]);
    }
    for (let j = 0; j < seg; j++) for (let i = 0; i < seg; i++) {
      const a = base + j * (seg + 1) + i, b = a + 1, c2 = a + seg + 1, d2 = c2 + 1;
      idx.push(a, b, d2, a, d2, c2);
    }
  }
  return { pos: new Float32Array(pos), nrm: new Float32Array(nrm), idx: new Uint16Array(idx) };
}

/* ── the plates: each universe's art direction, as a poster ──────────────
   512 x 704, drawn once at startup, uploaded once.

   These went through a rewrite after the first render: the originals carried
   body copy and hairline marks, which was pointless. A plate is seen through
   frosted glass, inside a backdrop buffer rendered at 0.34-0.62x, where it
   occupies on the order of 120 px. Nothing under about 30 canvas pixels
   survives that. So these are posters: a wordmark, the palette, and one
   signature gesture. What you read through the glass is an identity, not a
   paragraph — the paragraph is on the other side. */
const PLATE_W = 512, PLATE_H = 704;

function plateCanvas(u) {
  const cv = document.createElement('canvas');
  cv.width = PLATE_W; cv.height = PLATE_H;
  const x = cv.getContext('2d');
  const MONO  = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace';
  const SANS  = '-apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif';
  const SERIF = 'Georgia, "Iowan Old Style", "Times New Roman", serif';
  const DISP  = 'Impact, Haettenschweiler, "Arial Narrow Bold", "Arial Black", sans-serif';
  const fit = (t, face, px, max) => {            // never let a wordmark overflow
    let n = px;
    do { x.font = `400 ${n}px ${face}`; n -= 4; } while (x.measureText(t).width > max && n > 24);
    return n + 4;
  };

  x.fillStyle = u.ground; x.fillRect(0, 0, PLATE_W, PLATE_H);

  if (u.id === 'field') {
    // one mark per day, y = rank. Big enough to survive the mip chain.
    let seed = 3771;
    const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
    for (let i = 0; i < 132; i++) {
      const t = i / 131, v = Math.pow(rnd(), 1.7);
      const px = 40 + t * 432, py = 392 - v * 300;
      x.fillStyle = v < 0.45 ? '#5ad2e8' : v < 0.78 ? '#8fa8ff' : '#f5b95c';
      x.globalAlpha = 0.45 + v * 0.55;
      x.beginPath(); x.arc(px, py, 5 + v * 11, 0, 6.2832); x.fill();
    }
    x.globalAlpha = 1;
    x.fillStyle = '#5ad2e8'; x.fillRect(40, 430, 432, 8);
    x.fillStyle = '#e6ecf5'; x.font = `400 ${fit('FIELD', SANS, 150, 432)}px ${SANS}`;
    x.fillText('FIELD', 36, 600);
    x.fillStyle = '#5ad2e8'; x.font = `600 30px ${MONO}`;
    x.fillText('365 DAYS', 40, 664);
  }

  if (u.id === 'paper') {
    x.fillStyle = '#c9c2b2'; x.fillRect(40, 62, 432, 4);
    x.fillStyle = '#1b1a17'; x.font = `400 ${fit('PAPER', SERIF, 150, 432)}px ${SERIF}`;
    x.fillText('PAPER', 36, 212);
    // a column of text, abstracted to what actually survives the mip chain
    x.fillStyle = '#6d6759';
    [432, 432, 404, 432, 356].forEach((w, i) => x.fillRect(40, 264 + i * 30, w, 13));
    // the figure
    x.strokeStyle = '#c9c2b2'; x.lineWidth = 4; x.strokeRect(42, 440, 428, 190);
    x.strokeStyle = '#8a2b1e'; x.lineWidth = 9; x.beginPath();
    for (let i = 0; i <= 48; i++) {
      const t = i / 48, yy = 570 - Math.sin(t * 5.4) * 52 - t * 46;
      i ? x.lineTo(62 + t * 388, yy) : x.moveTo(62 + t * 388, yy);
    }
    x.stroke();
    x.fillStyle = '#8f8a7e'; x.font = `400 30px ${SERIF}`; x.fillText('fig. 3', 42, 674);
  }

  if (u.id === 'raw') {
    x.fillStyle = '#000'; x.font = `400 ${fit('RAW', SANS, 230, 452)}px ${SANS}`;
    x.fillText('RAW', 28, 224);
    x.fillStyle = '#b81d13'; x.fillRect(32, 262, 300, 26);
    x.fillStyle = '#000';
    [452, 452, 340].forEach((w, i) => x.fillRect(32, 330 + i * 54, w, 30));
    x.fillStyle = '#767676'; x.fillRect(32, 512, 240, 18);
    x.fillStyle = '#000'; x.fillRect(32, 636, 452, 14);
  }

  if (u.id === 'system') {
    x.fillStyle = '#1f1f1f'; x.fillRect(0, 0, PLATE_W, 74);
    x.fillStyle = '#9a9a9a'; x.font = `600 30px ${MONO}`; x.fillText('SUBSYSTEM', 26, 48);
    x.fillStyle = '#72c26a'; x.beginPath(); x.arc(462, 38, 15, 0, 6.2832); x.fill();
    const tiles = [['12', '#c8c8c8'], ['OK', '#72c26a'], ['61%', '#d9a441'], ['1', '#8fb8d8']];
    tiles.forEach((t, i) => {
      const cx = 26 + (i % 2) * 234, cy = 108 + Math.floor(i / 2) * 150;
      x.strokeStyle = '#4a4a4a'; x.lineWidth = 4; x.strokeRect(cx, cy, 214, 126);
      x.fillStyle = '#6e6e6e'; x.fillRect(cx + 16, cy + 24, 92, 10);
      x.fillStyle = t[1]; x.font = `500 62px ${MONO}`; x.fillText(t[0], cx + 16, cy + 100);
    });
    x.fillStyle = '#c8c8c8'; x.font = `500 ${fit('SYSTEM', MONO, 96, 452)}px ${MONO}`;
    x.fillText('SYSTEM', 26, 496);
    x.fillStyle = '#262626'; x.fillRect(0, PLATE_H - 96, PLATE_W, 96);
    x.fillStyle = '#7a7a7a'; x.fillRect(26, PLATE_H - 62, 300, 12);
  }

  if (u.id === 'loud') {
    x.fillStyle = '#ff2d55'; x.fillRect(0, 300, PLATE_W, 116);
    x.fillStyle = '#00e5ff'; x.fillRect(0, 560, PLATE_W, 78);
    x.fillStyle = '#1d1bff'; x.fillRect(360, 0, 152, 300);
    x.save(); x.translate(22, 0); x.rotate(-0.04);
    x.fillStyle = '#0a0a0a'; x.font = `400 ${fit('LOUD', DISP, 230, 340)}px ${DISP}`;
    x.fillText('LOUD', 0, 232); x.restore();
    x.fillStyle = '#0a0a0a'; x.font = `400 92px ${DISP}`;
    x.fillText('SIDEWAYS', 24, 392);
    x.fillStyle = '#f2ff00'; x.font = `400 60px ${DISP}`;
    x.fillText('11 ROOMS', 24, 618);
    x.fillStyle = '#0a0a0a'; x.fillRect(24, 666, 464, 16);
  }

  return cv;
}

/* ── GL helpers ─────────────────────────────────────────────────────────── */
function prog(gl, vs, fs, name) {
  const mk = (t, src) => {
    const s = gl.createShader(t); gl.shaderSource(s, src); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(name + ' ' + (t === gl.VERTEX_SHADER ? 'vs' : 'fs') + ': ' + gl.getShaderInfoLog(s));
    return s;
  };
  const p = gl.createProgram();
  gl.attachShader(p, mk(gl.VERTEX_SHADER, vs));
  gl.attachShader(p, mk(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(name + ' link: ' + gl.getProgramInfoLog(p));
  const u = new Proxy({}, { get: (c, k) => k in c ? c[k] : (c[k] = gl.getUniformLocation(p, k)) });
  return { p, u };
}

const V = '#version 300 es\n';
const HEAD_F = '#version 300 es\nprecision highp float;\nprecision highp sampler2D;\n';

/* ── shaders ─────────────────────────────────────────────────────────────── */

/* fullscreen triangle, used for the gradient and the backdrop blit */
const VS_FULL = V + `
out vec2 vUv;
void main(){
  vec2 p = vec2((gl_VertexID<<1)&2, gl_VertexID&2);
  vUv = p; gl_Position = vec4(p*2.0-1.0, 0.0, 1.0);
}`;

const FS_SKY = HEAD_F + `
in vec2 vUv; out vec4 o;
uniform vec3 uTop, uBot; uniform float uT;
void main(){
  float y = vUv.y;
  vec3 c = mix(uBot, uTop, pow(y, 1.35));
  // a slow cold pool low in the frame, so the ring has something to sit in
  float d = distance(vUv, vec2(0.5, 0.30));
  c += vec3(0.016,0.028,0.050) * exp(-d*d*6.0) * (0.85 + 0.15*sin(uT*0.22));
  o = vec4(c, 1.0);
}`;

/* the one place linear light becomes pixels */
const ENCODE = `
vec3 srgb(vec3 c){ return pow(max(c, 0.0), vec3(0.4545454545)); }
`;

const FS_BLIT = HEAD_F + ENCODE + `
in vec2 vUv; out vec4 o;
uniform sampler2D uTex;
void main(){ o = vec4(srgb(textureLod(uTex, vUv, 0.0).rgb), 1.0); }`;

/* the ground: a measured grid, because this identity is an instrument */
const VS_GRID = V + `
layout(location=0) in vec3 aPos;
uniform mat4 uProj, uView; out vec3 vW;
void main(){ vW = aPos; gl_Position = uProj * uView * vec4(aPos, 1.0); }`;

const FS_GRID = HEAD_F + `
in vec3 vW; out vec4 o;
uniform vec3 uLine, uPool; uniform float uT;
float grid(vec2 g, float s){
  vec2 q = g*s; vec2 f = abs(fract(q)-0.5)/fwidth(q);
  return 1.0 - min(min(f.x,f.y), 1.0);
}
void main(){
  vec2 g = vW.xz;
  float fine = grid(g, 1.0), coarse = grid(g, 0.2);
  float d = length(g);
  float fade = exp(-d*0.20);
  vec3 c = uLine * (fine*0.13 + coarse*0.42) * fade;
  c += uPool * exp(-d*d*0.014) * 0.055;
  // a single ring sweeping outward: the only thing in the room that keeps time
  float w = fract(uT*0.055);
  c += uPool * smoothstep(0.40, 0.0, abs(d - w*26.0)) * 0.035 * (1.0-w);
  o = vec4(c, 1.0);
}`;

/* the plates. Front: the poster. Back: a slate with the ordinal. */
const VS_PLATE = V + `
layout(location=0) in vec2 aXY;
uniform mat4 uProj, uView, uModel; uniform vec2 uHalf;
out vec2 vUv;
void main(){
  vUv = aXY*0.5 + 0.5;
  gl_Position = uProj * uView * uModel * vec4(aXY*uHalf, 0.0, 1.0);
}`;

/* A plate is a lightbox, not a picture hung on a wall: the poster sits in the
   middle of a panel that glows in its own ink. Without that, a dark edition
   (FIELD is near-black) would be invisible behind frosted glass in a dark
   room — which is a real failure mode I only saw once it was rendered. */
const FS_PLATE = HEAD_F + `
in vec2 vUv; out vec4 o;
uniform sampler2D uTex; uniform vec3 uInk; uniform vec2 uInset;
uniform float uT, uGain, uFocus;
void main(){
  vec2 uv = vec2(vUv.x, 1.0 - vUv.y);
  vec2 q  = (uv*2.0 - 1.0) / uInset;          // +-1 across the poster itself
  float d = max(abs(q.x), abs(q.y));
  float inside = 1.0 - step(1.0, d);

  if (!gl_FrontFacing){
    float e = smoothstep(1.02, 0.86, d);
    o = vec4(vec3(0.016,0.020,0.030) + uInk*(1.0-e)*0.06, 1.0); return;
  }

  vec3 c = texture(uTex, clamp(q*0.5 + 0.5, 0.0, 1.0)).rgb * inside * uGain;
  // the bezel: a soft halo of the edition's own colour, so every plate reads
  float halo = exp(-(max(d, 1.0) - 1.0) * 16.0);
  c += uInk * halo * (1.0 - inside) * (0.22 + 0.42*uFocus);
  c += uInk * 0.016 * inside;                 // the poster is backlit too

  float scan = sin(uv.y*220.0 - uT*1.1)*0.5 + 0.5;
  c *= 1.0 - 0.05*scan*uFocus;
  float sweep = smoothstep(0.03, 0.0, abs(fract(uv.y*0.5 - uT*0.05) - 0.5));
  c += uInk * sweep * 0.30 * uFocus * inside;
  o = vec4(c, 1.0);
}`;

/* The slabs, drawn into the backdrop so the ones in front can refract the
   ones behind. This pass contributes ONLY a rim, additively, with depth
   writes off.

   It used to be opaque, and that was the single worst bug in this file: a
   slab sits directly in front of its own plate, so an opaque pass painted
   over the very thing the glass exists to reveal. Every slab rendered as a
   flat coloured pane with a bright edge and nothing inside, and it looked
   deliberate enough that I nearly shipped it. */
const VS_SLAB = V + `
layout(location=0) in vec3 aPos;
layout(location=1) in vec3 aNrm;
uniform mat4 uProj, uView, uModel; uniform mat3 uNV;
out vec3 vN; out vec3 vNV; out vec3 vW; out vec3 vL;
void main(){
  vec4 wp = uModel * vec4(aPos, 1.0);
  vW = wp.xyz; vL = aPos;
  vN = mat3(uModel) * aNrm;
  vNV = uNV * aNrm;
  gl_Position = uProj * uView * wp;
}`;

const FS_SLAB_FLAT = HEAD_F + `
in vec3 vN; in vec3 vNV; in vec3 vW; in vec3 vL; out vec4 o;
uniform vec3 uCam, uTint; uniform float uFocus;
void main(){
  vec3 N = normalize(vN); if(!gl_FrontFacing) N = -N;
  vec3 Vv = normalize(uCam - vW);
  float F = 0.04 + 0.96*pow(1.0-clamp(dot(N,Vv),0.0,1.0), 5.0);
  o = vec4(uTint * (F*0.55 + 0.02 + 0.03*uFocus), 1.0);   // additive: rim only
}`;

const FS_SLAB = HEAD_F + ENCODE + `
in vec3 vN; in vec3 vNV; in vec3 vW; in vec3 vL; out vec4 o;
uniform sampler2D uBack;
uniform vec3 uCam, uTint;
uniform vec2 uRes, uHalf;
uniform float uRough, uDisp, uThick, uFocus, uT, uMaxLod, uHover, uWarp;
uniform int uTaps;

float hash21(vec2 p){ p = fract(p*vec2(123.34,456.21)); p += dot(p,p+45.32); return fract(p.x*p.y); }
vec3 back(vec2 uv, float lod){ return textureLod(uBack, clamp(uv, vec2(0.0015), vec2(0.9985)), lod).rgb; }

void main(){
  vec3 N  = normalize(vN);
  vec3 NV = normalize(vNV);
  if (!gl_FrontFacing){ N = -N; NV = -NV; }
  vec3 Vv = normalize(uCam - vW);
  float NoV = clamp(dot(N, Vv), 0.0, 1.0);
  float F = 0.04 + 0.96*pow(1.0 - NoV, 5.0);

  vec2 uv  = gl_FragCoord.xy / uRes;
  float lod = uRough * uRough * uMaxLod;   // see note in hub.js: linear lod erases the plate

  // Screen-space refraction. The tangential part of the view-space normal is
  // the direction the ray bends; thickness and grazing angle set how far.
  vec2 dir = NV.xy * uThick * (0.40 + 0.60*(1.0 - NoV));

  // Passing through. On a flat face the view-space normal has almost no
  // tangential component, so refraction alone shows nothing in the middle of
  // the slab — the dispersion lives at the rim and the traversal reads flat.
  // A radial term, growing toward the corners, puts the moment on screen.
  vec2 rad = uv - 0.5;
  dir += rad * uWarp * (0.55 + 2.4 * dot(rad, rad));

  vec3 col;
  if (uTaps >= 3){
    float g = hash21(gl_FragCoord.xy + fract(uT)*37.0);
    vec2 jit = (vec2(g, hash21(gl_FragCoord.yx - g)) - 0.5) * uRough * 0.020;
    col = vec3(
      back(uv + dir*(1.0 + uDisp) + jit, lod).r,
      back(uv + dir                + jit, lod).g,
      back(uv + dir*(1.0 - uDisp) - jit, lod).b);
  } else if (uTaps == 2){
    col = vec3(back(uv + dir*(1.0 + uDisp*0.7), lod).r, 0.0, 0.0);
    vec3 b = back(uv + dir*(1.0 - uDisp*0.7), lod);
    col.g = back(uv + dir, lod).g; col.b = b.b;
  } else {
    col = back(uv + dir, lod);
  }

  // absorption: glass is not colourless, and thicker glass is less colourless
  col *= mix(vec3(1.0), vec3(0.82,0.90,1.0), clamp(uThick*2.4, 0.0, 0.55));

  // a machined groove set in from the rim — the one ornament in the room
  vec2 din = abs(vL.xy) - uHalf + 0.055;
  float groove = smoothstep(0.0060, 0.0, abs(max(din.x, din.y)));
  col += uTint * groove * (0.18 + 0.55*uFocus);

  // rim. The hovered slab lights its edge: the reaction to being pointed at
  // is optical, never geometric.
  col += uTint * F * (0.42 + 0.30*uRough + 0.45*uHover);
  col += uTint * groove * 0.55 * uHover;

  // two lights, hard then soft
  vec3 L1 = normalize(vec3(-0.45, 0.78, 0.62));
  vec3 L2 = normalize(vec3( 0.70, 0.22,-0.35));
  vec3 H1 = normalize(L1 + Vv), H2 = normalize(L2 + Vv);
  float gloss = mix(18.0, 190.0, 1.0-uRough);
  col += vec3(1.0)      * pow(max(dot(N,H1),0.0), gloss) * (0.55 + 0.9*(1.0-uRough));
  col += uTint*0.9      * pow(max(dot(N,H2),0.0), gloss*0.45) * 0.30;

  o = vec4(srgb(col), 1.0);   // pass B targets the screen, which is not sRGB
}`;

/* ═══════════════════════════════════════════════════════════════════════════ */

export async function start() {
  const canvas = document.getElementById('stage');
  const html = document.documentElement;

  const gl = canvas.getContext('webgl2', {
    antialias: false, depth: true, stencil: false, alpha: false, premultipliedAlpha: false,
    powerPreference: 'high-performance', failIfMajorPerformanceCaveat: true,
    preserveDrawingBuffer: false
  });
  if (!gl) throw new Error('webgl2 unavailable at init');

  const content = await fetch('./content.json', { cache: 'no-cache' }).then(r => r.json());
  const U = content.universes;
  const N_U = U.length;
  if (!N_U) throw new Error('no universes in content.json');

  /* ── quality tiers. Every one of these is a real reduction, and the tier is
        printed in the corner rather than hidden. ─────────────────────────── */
  const TIERS = [
    { fbo: 0.34, taps: 1, dpr: 1.0, name: 'T0' },
    { fbo: 0.42, taps: 1, dpr: 1.5, name: 'T1' },
    { fbo: 0.52, taps: 2, dpr: 2.0, name: 'T2' },
    { fbo: 0.62, taps: 3, dpr: 2.0, name: 'T3' }
  ];
  let tier = 3, downgrades = 0, upgrades = 0;

  /* ── programs ─────────────────────────────────────────────────────────── */
  const pSky = prog(gl, VS_FULL, FS_SKY, 'sky');
  const pBlit = prog(gl, VS_FULL, FS_BLIT, 'blit');
  const pGrid = prog(gl, VS_GRID, FS_GRID, 'grid');
  const pPlate = prog(gl, VS_PLATE, FS_PLATE, 'plate');
  const pFlat = prog(gl, VS_SLAB, FS_SLAB_FLAT, 'slabflat');
  const pSlab = prog(gl, VS_SLAB, FS_SLAB, 'slab');

  const emptyVAO = gl.createVertexArray();

  /* slab mesh */
  const HX = 0.74, HY = 0.99, HZ = 0.075;
  const mesh = roundedBox(HX, HY, HZ, 0.052, 16);
  const slabVAO = gl.createVertexArray();
  gl.bindVertexArray(slabVAO);
  const vbP = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vbP);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.pos, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  const vbN = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vbN);
  gl.bufferData(gl.ARRAY_BUFFER, mesh.nrm, gl.STATIC_DRAW);
  gl.enableVertexAttribArray(1); gl.vertexAttribPointer(1, 3, gl.FLOAT, false, 0, 0);
  const ib = gl.createBuffer(); gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ib);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, mesh.idx, gl.STATIC_DRAW);
  gl.bindVertexArray(null);

  /* quad for plates */
  const quadVAO = gl.createVertexArray();
  gl.bindVertexArray(quadVAO);
  const vbQ = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vbQ);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  /* ground */
  const G = 46;
  const gridVAO = gl.createVertexArray();
  gl.bindVertexArray(gridVAO);
  const vbG = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vbG);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -G, -1.42, -G, G, -1.42, -G, -G, -1.42, G, G, -1.42, G]), gl.STATIC_DRAW);
  gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0, 3, gl.FLOAT, false, 0, 0);
  gl.bindVertexArray(null);

  /* ── the universes, placed ────────────────────────────────────────────── */
  const hex = h => {
    const n = parseInt(h.slice(1), 16);
    const s = v => { v /= 255; return v <= 0.04045 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return [s((n >> 16) & 255), s((n >> 8) & 255), s(n & 255)];
  };
  const R = 5.0, YOFF = [0, 0.17, -0.13, 0.10, -0.07, 0.05];
  const ROLL = [0, -0.028, 0.022, -0.016, 0.030, -0.012];

  const slabs = U.map((u, i) => {
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.SRGB8_ALPHA8, gl.RGBA, gl.UNSIGNED_BYTE, plateCanvas(u));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.generateMipmap(gl.TEXTURE_2D);
    const a = (i / N_U) * TAU;
    return {
      u, i, a, tex,
      ink: hex(u.ink), ground: hex(u.ground),
      y: YOFF[i % YOFF.length], roll: ROLL[i % ROLL.length],
      px: Math.sin(a) * R, pz: Math.cos(a) * R,
      qx: Math.sin(a) * (R - 0.62), qz: Math.cos(a) * (R - 0.62),
      focus: 0, model: new Float32Array(16), pmodel: new Float32Array(16)
    };
  });

  /* ── camera state ─────────────────────────────────────────────────────── */
  const DIST_NEAR = 7.55, DIST_FAR = 13.6;
  const cam = { theta: 0, thetaV: 0, phi: 0.085, dist: 9.4, distT: 9.4, ex: 0, ey: 0, ez: 0 };
  let focusIdx = 0, wantIdx = 0, snapping = true;
  let mode = 'idle', enterT0 = 0, enterFrom = 0, target = null;

  /* Coming back through the glass. The universe you left wrote down which slab
     it was; the camera resumes on the far side of it and withdraws, so the
     return is the traversal run backwards rather than a fresh page load. */
  let returning = null;
  try {
    const raw = JSON.parse(sessionStorage.getItem('mao:portal') || 'null');
    if (raw && raw.back && Date.now() - raw.t < 6000) {
      const j = U.findIndex(u => u.id === raw.id);
      if (j >= 0) { returning = { j, ground: raw.ground }; }
      sessionStorage.removeItem('mao:portal');
    }
  } catch { /* nothing to resume */ }
  if (returning) {
    wantIdx = focusIdx = returning.j;
    cam.theta = cam.thetaV = (returning.j / N_U) * TAU;
    cam.dist = R - 0.40; cam.distT = DIST_NEAR + 0.25; cam.phi = 0.06;
  }

  const proj = new Float32Array(16), view = new Float32Array(16);
  const mv = new Float32Array(16), nv = new Float32Array(9), tmp = new Float32Array(16);

  /* ── framebuffer ──────────────────────────────────────────────────────── */
  let fbo = null, fboTex = null, fboDepth = null, fw = 0, fh = 0, maxLod = 6;
  function makeFBO(w, h) {
    if (fbo) { gl.deleteFramebuffer(fbo); gl.deleteTexture(fboTex); gl.deleteRenderbuffer(fboDepth); }
    fw = Math.max(2, w); fh = Math.max(2, h);
    fboTex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, fboTex);
    // sRGB attachment: WebGL2 encodes on write and decodes on sample, so the
    // whole backdrop survives an 8-bit round trip without crushing the darks
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.SRGB8_ALPHA8, fw, fh, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    fboDepth = gl.createRenderbuffer();
    gl.bindRenderbuffer(gl.RENDERBUFFER, fboDepth);
    gl.renderbufferStorage(gl.RENDERBUFFER, gl.DEPTH_COMPONENT24, fw, fh);
    fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, fboTex, 0);
    gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.DEPTH_ATTACHMENT, gl.RENDERBUFFER, fboDepth);
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    maxLod = Math.min(7, Math.floor(Math.log2(Math.max(fw, fh))));
  }

  let cw = 0, ch = 0;
  function resize() {
    const t = TIERS[tier];
    const dpr = Math.min(window.devicePixelRatio || 1, t.dpr);
    const w = Math.max(2, Math.round(canvas.clientWidth * dpr));
    const h = Math.max(2, Math.round(canvas.clientHeight * dpr));
    if (w !== cw || h !== ch) { cw = w; ch = h; canvas.width = w; canvas.height = h; }
    const nw = Math.round(w * t.fbo), nh = Math.round(h * t.fbo);
    if (nw !== fw || nh !== fh) makeFBO(nw, nh);
  }

  /* ── HUD ──────────────────────────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  const elIdx = $('idx'), elNm = $('nm'), elDs = $('ds'), elLn = $('ln');
  const elFill = $('mfill'), elPct = $('mpct'), elEnter = $('enter'),
        elRing = $('ring'), elTier = $('tier'), elFps = $('fps'), elHint = $('hint'), elWash = $('wash');

  elRing.innerHTML = U.map((u, i) =>
    `<button type="button" data-i="${i}" aria-current="false"><span>${u.name}</span><s></s></button>`).join('');
  elRing.addEventListener('click', e => {
    const b = e.target.closest('button'); if (!b) return;
    const i = +b.dataset.i;
    if (i === focusIdx && cam.distT < DIST_NEAR + 1.4) enter(i); else goTo(i);
  });

  /* a way back to the flat index that does not require reloading */
  const flatBtn = document.createElement('button');
  flatBtn.type = 'button'; flatBtn.id = 'flatbtn'; flatBtn.className = 'pt';
  flatBtn.textContent = 'FLAT INDEX';
  flatBtn.style.cssText = 'appearance:none;background:none;border:1px solid #1a2131;color:inherit;' +
    'font:inherit;letter-spacing:.14em;padding:6px 10px;cursor:pointer;margin-right:14px';
  flatBtn.addEventListener('mouseenter', () => flatBtn.style.borderColor = '#5ad2e8');
  flatBtn.addEventListener('mouseleave', () => flatBtn.style.borderColor = '#1a2131');
  elTier.parentNode.insertBefore(flatBtn, elTier);
  flatBtn.addEventListener('click', () => { running = false; html.classList.remove('gl'); });
  document.querySelector('.skip').addEventListener('click', () => {
    running = false; html.classList.remove('gl');
  });

  let hintGone = !!returning;
  const retireHint = () => { if (!hintGone) { hintGone = true; elHint.classList.add('gone'); } };
  if (returning) elHint.classList.add('gone');

  const prefetched = new Set();
  function prefetch(i) {
    const u = U[i]; if (prefetched.has(u.id)) return; prefetched.add(u.id);
    const l = document.createElement('link');
    l.rel = 'prefetch'; l.href = u.path.replace(/^\.\//, './'); l.as = 'document';
    document.head.appendChild(l);
  }
  let prefTimer = 0;

  let shownIdx = -1;
  function paintHUD(i) {
    if (i === shownIdx) return;
    shownIdx = i;
    const u = U[i];
    elIdx.textContent = String(i + 1).padStart(2, '0') + ' / ' + String(N_U).padStart(2, '0');
    elNm.textContent = u.name;
    elNm.style.color = u.ink;
    elDs.textContent = u.desc.charAt(0).toUpperCase() + u.desc.slice(1) + '.';
    elLn.textContent = u.line || '';
    elEnter.setAttribute('href', u.path);
    elFill.style.background = u.ink;
    for (const b of elRing.children) b.setAttribute('aria-current', String(+b.dataset.i === i));
    clearTimeout(prefTimer);
    prefTimer = setTimeout(() => prefetch(i), 420);
  }
  paintHUD(focusIdx);

  /* ── interaction ──────────────────────────────────────────────────────── */
  function goTo(i) {
    wantIdx = ((i % N_U) + N_U) % N_U;
    snapping = true;
    cam.distT = clamp(cam.distT, DIST_NEAR, DIST_NEAR + 2.6);
    retireHint();
  }

  function enter(i) {
    if (mode === 'enter') return;
    mode = 'enter'; enterT0 = performance.now(); enterFrom = cam.dist; target = slabs[i];
    wantIdx = i; snapping = true;
    paintHUD(i);          // the readout names the destination during the flight
    elEnter.blur();
    try {
      sessionStorage.setItem('mao:portal', JSON.stringify(
        { id: target.u.id, ground: target.u.ground, ink: target.u.ink, t: Date.now() }));
    } catch { /* private mode; the arrival simply will not animate */ }
  }

  elEnter.addEventListener('click', e => { e.preventDefault(); enter(focusIdx); });

  let down = false, lastX = 0, lastY = 0, moved = 0, pid = null;
  canvas.addEventListener('pointerdown', e => {
    if (mode === 'enter') return;
    down = true; moved = 0; pid = e.pointerId; lastX = e.clientX; lastY = e.clientY;
    canvas.setPointerCapture(pid); canvas.classList.add('drag'); snapping = false; retireHint();
  });
  canvas.addEventListener('pointermove', e => {
    if (!down) { hoverX = e.clientX; hoverY = e.clientY; return; }
    const dx = e.clientX - lastX, dy = e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY; moved += Math.abs(dx) + Math.abs(dy);
    cam.thetaV -= dx * 0.0042;
    cam.phi = clamp(cam.phi - dy * 0.0022, -0.26, 0.40);
  });
  const release = () => {
    if (!down) return;
    down = false; canvas.classList.remove('drag');
    if (pid !== null) { try { canvas.releasePointerCapture(pid); } catch {} pid = null; }
    snapping = true;
  };
  canvas.addEventListener('pointerup', e => {
    const wasTap = moved < 8;
    release();
    if (wasTap && mode === 'idle') {
      const hit = pick(e.clientX, e.clientY);
      if (hit === focusIdx && cam.distT < DIST_NEAR + 1.4) enter(hit);
      else if (hit >= 0) goTo(hit);
    }
  });
  canvas.addEventListener('pointercancel', release);
  canvas.addEventListener('wheel', e => {
    if (mode === 'enter') return;
    e.preventDefault(); retireHint();
    cam.distT = clamp(cam.distT + Math.sign(e.deltaY) * Math.min(Math.abs(e.deltaY), 60) * 0.011,
      DIST_NEAR, DIST_FAR);
  }, { passive: false });

  let hoverX = -1, hoverY = -1, hoverIdx = -1;
  canvas.addEventListener('pointerleave', () => { hoverX = hoverY = -1; hoverIdx = -1; });

  addEventListener('keydown', e => {
    if (mode === 'enter') return;
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') { goTo(focusIdx + 1); e.preventDefault(); }
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') { goTo(focusIdx - 1); e.preventDefault(); }
    else if (e.key === 'Enter' && !e.target.closest('a,button')) { enter(focusIdx); e.preventDefault(); }
    else if (e.key === '+' || e.key === '=') { cam.distT = clamp(cam.distT - 0.7, DIST_NEAR, DIST_FAR); }
    else if (e.key === '-') { cam.distT = clamp(cam.distT + 0.7, DIST_NEAR, DIST_FAR); }
    else if (/^[1-9]$/.test(e.key) && +e.key <= N_U) { goTo(+e.key - 1); }
  });

  /* screen-space pick: project each slab centre, take the nearest within reach */
  const px4 = new Float32Array(4);
  function projectCentre(s) {
    // world → clip
    const wx = s.px, wy = s.y, wz = s.pz;
    const vx = view[0] * wx + view[4] * wy + view[8] * wz + view[12];
    const vy = view[1] * wx + view[5] * wy + view[9] * wz + view[13];
    const vz = view[2] * wx + view[6] * wy + view[10] * wz + view[14];
    const cxx = proj[0] * vx, cyy = proj[5] * vy, cww = -vz;
    if (cww <= 0.02) return null;
    px4[0] = (cxx / cww * 0.5 + 0.5) * canvas.clientWidth;
    px4[1] = (0.5 - cyy / cww * 0.5) * canvas.clientHeight;
    px4[2] = cww;
    return px4;
  }
  function pick(sx, sy) {
    const r = canvas.getBoundingClientRect();
    const x = sx - r.left, y = sy - r.top;
    let best = -1, bestD = Math.min(r.width, r.height) * 0.30, bestZ = 1e9;
    for (const s of slabs) {
      const p = projectCentre(s); if (!p) continue;
      const d = Math.hypot(p[0] - x, p[1] - y);
      if (d < bestD && p[2] < bestZ + 0.6) { best = s.i; bestD = Math.max(d, 1); bestZ = p[2]; }
    }
    return best;
  }

  /* ── adaptive quality ─────────────────────────────────────────────────── */
  const ftimes = new Float64Array(48); let fi = 0, fn = 0, sinceTier = 0;
  function accountFrame(dt) {
    ftimes[fi] = dt; fi = (fi + 1) % ftimes.length; if (fn < ftimes.length) fn++;
    sinceTier++;
    if (fn < ftimes.length || sinceTier < 60) return;
    const a = Array.prototype.slice.call(ftimes, 0, fn).sort((p, q) => p - q);
    const med = a[a.length >> 1];
    if (med > 21 && tier > 0 && downgrades < 4) {
      tier--; downgrades++; sinceTier = 0; cw = ch = 0; fw = fh = 0; resize(); paintTier();
    } else if (med < 10.5 && tier < 3 && upgrades < 2 && downgrades === 0) {
      tier++; upgrades++; sinceTier = 0; cw = ch = 0; fw = fh = 0; resize(); paintTier();
    }
  }
  function paintTier() {
    const t = TIERS[tier];
    elTier.textContent = `GLASS ${t.name} · ${t.fbo.toFixed(2)}× · ${t.taps} TAP${t.taps > 1 ? 'S' : ''}`;
  }

  /* ── context loss: fall back rather than freeze ───────────────────────── */
  canvas.addEventListener('webglcontextlost', e => {
    e.preventDefault(); running = false; html.classList.remove('gl');
    const n = document.getElementById('flatnote');
    if (n) n.textContent = 'graphics context lost · flat index';
  });

  /* ── draw ─────────────────────────────────────────────────────────────── */
  const SKY_TOP = hex('#0a0e1a'), SKY_BOT = hex('#030409');
  const GRID_LINE = hex('#2b3b56'), GRID_POOL = hex('#2c5f73');

  gl.enable(gl.DEPTH_TEST);
  gl.depthFunc(gl.LEQUAL);
  gl.disable(gl.CULL_FACE);   // the camera passes through the glass; both sides must draw
  gl.clearColor(0.008, 0.012, 0.027, 1);

  let running = true, t0 = performance.now(), last = t0, fpsAcc = 0, fpsN = 0, started = false;

  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    let dt = now - last; last = now;
    if (dt > 120) dt = 120;                 // a tab that was in the background
    const t = (now - t0) / 1000;
    const step = Math.min(dt / 16.6667, 3); // frame-rate independent damping

    resize();

    /* camera ------------------------------------------------------------ */
    if (mode === 'enter') {
      const k = clamp((now - enterT0) / 880, 0, 1), e = easeIO(k);
      cam.dist = lerp(enterFrom, R - 0.40, e);
      cam.thetaV += angDiff(target.a, cam.thetaV) * Math.min(1, 0.20 * step);
      cam.phi = lerp(cam.phi, target.y * 0.16, Math.min(1, 0.10 * step));
      if (k >= 1) { mode = 'gone'; depart(); }
    } else {
      if (snapping) {
        const want = slabs[wantIdx].a;
        cam.thetaV += angDiff(want, cam.thetaV) * Math.min(1, 0.085 * step);
      } else {
        cam.thetaV += 0.00022 * step;       // the ring never quite stands still
      }
      cam.dist += (cam.distT - cam.dist) * Math.min(1, 0.085 * step);
    }
    cam.theta = cam.thetaV;

    const hr = cam.dist * Math.cos(cam.phi);
    cam.ex = Math.sin(cam.theta) * hr;
    cam.ey = cam.dist * Math.sin(cam.phi) + 0.06;
    cam.ez = Math.cos(cam.theta) * hr;

    const aspect = canvas.width / canvas.height;
    M.persp(proj, 0.733, aspect, 0.05, 90);
    M.look(view, cam.ex, cam.ey, cam.ez, 0, 0.05, 0);

    /* focus: angular alignment × proximity. This number is the whole site. */
    const prox = 1 - clamp((cam.dist - DIST_NEAR) / (DIST_FAR - DIST_NEAR), 0, 1);
    if (mode === 'idle' && !down && hoverX >= 0) {
      const h = pick(hoverX, hoverY);
      if (h !== hoverIdx) {
        hoverIdx = h;
        canvas.style.cursor = h >= 0 ? 'pointer' : '';
      }
    } else if (down || mode !== 'idle') { hoverIdx = -1; }

    let bestF = -1, bestI = 0;
    for (const s of slabs) {
      const ad = Math.abs(angDiff(s.a, cam.theta));
      const align = clamp(1 - ad / (TAU / N_U * 0.86), 0, 1);
      let f = Math.pow(align, 1.5) * (0.20 + 0.80 * prox);
      if (s.i === hoverIdx) f = Math.min(1, f + 0.16);
      if (mode === 'enter' && s === target) f = Math.max(f, easeIO(clamp((performance.now() - enterT0) / 700, 0, 1)));
      s.focus += (f - s.focus) * Math.min(1, 0.12 * step);
      if (s.focus > bestF) { bestF = s.focus; bestI = s.i; }
      M.trs(s.model, s.px, s.y, s.pz, s.a, s.roll);
      M.trs(s.pmodel, s.qx, s.y, s.qz, s.a, s.roll);
    }
    if (!snapping || mode === 'enter') { /* free look follows the camera */ }
    focusIdx = bestI;
    if (mode !== 'enter') paintHUD(focusIdx);

    const fFocus = slabs[focusIdx].focus;
    elFill.style.width = (fFocus * 100).toFixed(0) + '%';
    elPct.textContent = Math.round(fFocus * 100) + '%';

    const T = TIERS[tier];

    /* ── PASS A: backdrop → FBO ─────────────────────────────────────── */
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.viewport(0, 0, fw, fh);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(pSky.p); gl.bindVertexArray(emptyVAO);
    gl.uniform3fv(pSky.u.uTop, SKY_TOP); gl.uniform3fv(pSky.u.uBot, SKY_BOT);
    gl.uniform1f(pSky.u.uT, t);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.DEPTH_TEST);

    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);   // no horizon seam
    gl.useProgram(pGrid.p); gl.bindVertexArray(gridVAO);
    gl.uniformMatrix4fv(pGrid.u.uProj, false, proj);
    gl.uniformMatrix4fv(pGrid.u.uView, false, view);
    gl.uniform3fv(pGrid.u.uLine, GRID_LINE); gl.uniform3fv(pGrid.u.uPool, GRID_POOL);
    gl.uniform1f(pGrid.u.uT, t);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    gl.disable(gl.BLEND);

    gl.useProgram(pPlate.p); gl.bindVertexArray(quadVAO);
    gl.uniformMatrix4fv(pPlate.u.uProj, false, proj);
    gl.uniformMatrix4fv(pPlate.u.uView, false, view);
    // Just large enough to back the slab at the closest approach, and no
    // larger: overhang reveals that the plate is a separate object.
    // needed half-height = 0.99 * (d - 4.38) / (d - 5.06), worst case d = 7.55
    gl.uniform2f(pPlate.u.uHalf, 0.90, 1.24);
    gl.uniform2f(pPlate.u.uInset, 0.828 / 0.90, 1.139 / 1.24);   // poster keeps 512:704
    gl.uniform1i(pPlate.u.uTex, 0);
    gl.activeTexture(gl.TEXTURE0);
    for (const s of slabs) {
      gl.bindTexture(gl.TEXTURE_2D, s.tex);
      gl.uniformMatrix4fv(pPlate.u.uModel, false, s.pmodel);
      gl.uniform3fv(pPlate.u.uInk, s.ink);
      gl.uniform1f(pPlate.u.uT, t);
      gl.uniform1f(pPlate.u.uFocus, s.focus);
      gl.uniform1f(pPlate.u.uGain, 0.80 + 1.10 * s.focus +
        (mode === 'enter' && s === target ? 0.9 * easeIO(clamp((performance.now() - enterT0) / 880, 0, 1)) : 0));
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    }

    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE); gl.depthMask(false);
    gl.useProgram(pFlat.p); gl.bindVertexArray(slabVAO);
    gl.uniformMatrix4fv(pFlat.u.uProj, false, proj);
    gl.uniformMatrix4fv(pFlat.u.uView, false, view);
    gl.uniform3f(pFlat.u.uCam, cam.ex, cam.ey, cam.ez);
    for (const s of slabs) {
      gl.uniformMatrix4fv(pFlat.u.uModel, false, s.model);
      gl.uniform3fv(pFlat.u.uTint, s.ink);
      gl.uniform1f(pFlat.u.uFocus, s.focus);
      gl.drawElements(gl.TRIANGLES, mesh.idx.length, gl.UNSIGNED_SHORT, 0);
    }
    gl.disable(gl.BLEND); gl.depthMask(true);

    gl.bindTexture(gl.TEXTURE_2D, fboTex);
    gl.generateMipmap(gl.TEXTURE_2D);

    /* ── PASS B: the glass ──────────────────────────────────────────── */
    gl.bindFramebuffer(gl.FRAMEBUFFER, null);
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);

    gl.disable(gl.DEPTH_TEST);
    gl.useProgram(pBlit.p); gl.bindVertexArray(emptyVAO);
    gl.uniform1i(pBlit.u.uTex, 0);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, fboTex);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    gl.enable(gl.DEPTH_TEST);

    gl.useProgram(pSlab.p); gl.bindVertexArray(slabVAO);
    gl.uniformMatrix4fv(pSlab.u.uProj, false, proj);
    gl.uniformMatrix4fv(pSlab.u.uView, false, view);
    gl.uniform3f(pSlab.u.uCam, cam.ex, cam.ey, cam.ez);
    gl.uniform2f(pSlab.u.uRes, canvas.width, canvas.height);
    gl.uniform2f(pSlab.u.uHalf, HX, HY);
    gl.uniform1f(pSlab.u.uMaxLod, maxLod);
    gl.uniform1f(pSlab.u.uT, t);
    gl.uniform1i(pSlab.u.uTaps, T.taps);
    gl.uniform1i(pSlab.u.uBack, 0);
    gl.uniform1f(pSlab.u.uWarp, 0);
    for (const s of slabs) {
      M.mul(mv, view, s.model); M.mat3(nv, mv);
      gl.uniformMatrix4fv(pSlab.u.uModel, false, s.model);
      gl.uniformMatrix3fv(pSlab.u.uNV, false, nv);
      gl.uniform3fv(pSlab.u.uTint, s.ink);
      const rough = lerp(0.70, 0.010, Math.pow(s.focus, 1.15));
      const push = (mode === 'enter' && s === target) ? easeIO(clamp((performance.now() - enterT0) / 880, 0, 1)) : 0;
      gl.uniform1f(pSlab.u.uRough, Math.max(0.0, rough * (1 - push)));
      gl.uniform1f(pSlab.u.uThick, 0.055 + 0.075 * s.focus + 0.34 * push);
      gl.uniform1f(pSlab.u.uDisp, 0.16 + 0.26 * s.focus + 2.6 * push);
      gl.uniform1f(pSlab.u.uFocus, s.focus);
      gl.uniform1f(pSlab.u.uHover, s.i === hoverIdx ? 1 : 0);
      gl.uniform1f(pSlab.u.uWarp, push * 0.075);
      // one slab, for under a second: the traversal always gets full
      // dispersion, whatever tier the rest of the scene has settled to
      gl.uniform1i(pSlab.u.uTaps, push > 0.02 ? 3 : T.taps);
      gl.drawElements(gl.TRIANGLES, mesh.idx.length, gl.UNSIGNED_SHORT, 0);
    }
    gl.bindVertexArray(null);

    if (!started) {
      started = true;
      html.classList.add('gl');
      canvas.classList.add('on');
      canvas.removeAttribute('aria-hidden');
      canvas.setAttribute('role', 'application');
      canvas.setAttribute('aria-label',
        'The atrium: five glass monoliths. Drag to orbit, scroll to approach, Enter to open one. ' +
        'Arrow keys step between them. Use the FLAT INDEX button for a plain list.');
      canvas.tabIndex = 0;
      document.getElementById('hud').removeAttribute('aria-hidden');
      paintTier();
      if (returning) {
        // the sheet is already the colour of the page we just left; open it
        elWash.style.background = returning.ground || '#04050a';
        elWash.classList.add('back');
        setTimeout(() => { elWash.classList.remove('back'); elWash.style.background = ''; }, 900);
      }
    }

    accountFrame(dt);
    fpsAcc += dt; fpsN++;
    if (fpsAcc > 500) { elFps.textContent = Math.round(1000 / (fpsAcc / fpsN)) + ' FPS'; fpsAcc = 0; fpsN = 0; }
  }

  /* ── the traversal out ────────────────────────────────────────────────── */
  function depart() {
    elWash.style.background = target.u.ground;
    elWash.classList.add('go');
    const go = () => { location.href = target.u.path; };
    setTimeout(go, 430);
  }

  /* coming back through the glass: reset, do not leave a white sheet */
  addEventListener('pageshow', e => {
    if (!e.persisted) return;
    elWash.classList.remove('go'); elWash.style.background = '';
    mode = 'idle'; target = null;
    if (!running) { running = true; last = performance.now(); requestAnimationFrame(frame); }
  });
  addEventListener('visibilitychange', () => {
    if (!document.hidden && running) last = performance.now();
  });

  requestAnimationFrame(frame);
  return {
    stop() { running = false; },
    enter(i) { enter(clamp(i | 0, 0, N_U - 1)); },
    get focus() { return focusIdx; }
  };
}

/* Exported for scripts/hub_math_test.mjs. Nothing imports these at runtime;
   they are here so the matrix, geometry and winding code can be tested in
   node without a browser. A sign error in M.trs shipped once and pointed
   every slab the wrong way round the ring — that test now exists. */
export { M, roundedBox, angDiff, clamp, lerp, easeIO };
