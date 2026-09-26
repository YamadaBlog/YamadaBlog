/* Matrix, geometry and winding tests for the atrium renderer.
   Run by scripts/check.py; no browser, no GL context, no dependency. */
import fs from 'node:fs';
import path from 'node:path';
import url from 'node:url';

/* hub.js ships as .js because the browser imports it as a module either way.
   Node decides CJS-vs-ESM from the extension, so copy it to .mjs to import
   it here rather than adding a package.json that would imply a build step
   this site does not have. */
const here = path.dirname(url.fileURLToPath(import.meta.url));
const tmp = path.join(here, '.hub_under_test.mjs');
fs.writeFileSync(tmp, fs.readFileSync(path.join(here, '..', 'site', 'hub', 'hub.js')));
let mod;
try { mod = await import(url.pathToFileURL(tmp).href); } finally { fs.unlinkSync(tmp); }
const { M, roundedBox, angDiff, easeIO } = mod;
let fail = 0;
const ok = (c, m) => { if (!c) { console.log('FAIL ' + m); fail++; } else console.log('  ok  ' + m); };
const close = (a, b, e = 1e-5) => Math.abs(a - b) < e;

// ── 1. projection + view: a point at the camera target must land at NDC (0,0)
const proj = new Float32Array(16), view = new Float32Array(16);
M.persp(proj, 0.733, 16 / 9, 0.05, 90);
M.look(view, 0, 2, 10, 0, 0.05, 0);
function xf(m, x, y, z, w = 1) {
  return [0, 1, 2, 3].map(r => m[r] * x + m[4 + r] * y + m[8 + r] * z + m[12 + r] * w);
}
let v = xf(view, 0, 0.05, 0);
ok(close(v[0], 0) && close(v[1], 0), 'look(): target maps to view-space (0,0,-d)');
ok(v[2] < 0, 'look(): target is in front of the camera (-z)');
ok(close(-v[2], Math.hypot(0 - 0, 2 - 0.05, 10 - 0)), 'look(): view depth equals eye-target distance');
// a point straight up from target should have positive view-space y
v = xf(view, 0, 1.05, 0); ok(v[1] > 0, 'look(): up is +y in view space');
// clip
let c = xf(proj, ...xf(view, 0, 0.05, 0).slice(0, 3));
ok(close(c[0] / -c[2] * 0, 0) && close(c[1], 0), 'persp(): target projects to NDC x=y=0');
// near/far mapping
const near = 0.05, far = 90;
for (const d of [near, far]) {
  const cc = xf(proj, 0, 0, -d);
  ok(close(cc[2] / cc[3], d === near ? -1 : 1, 1e-3), `persp(): z=${d} maps to NDC ${d === near ? -1 : 1}`);
}

// ── 2. mul associativity against a reference
function refMul(a, b) { const o = new Float32Array(16);
  for (let cc = 0; cc < 4; cc++) for (let r = 0; r < 4; r++) { let s = 0;
    for (let k = 0; k < 4; k++) s += a[k * 4 + r] * b[cc * 4 + k]; o[cc * 4 + r] = s; } return o; }
const A = new Float32Array(16); M.trs(A, 1, 2, 3, 0.7, -0.3);
const B = new Float32Array(16); M.look(B, 3, 1, 4, 0, 0, 0);
const got = M.mul(new Float32Array(16), B, A), want = refMul(B, A);
ok(want.every((x, i) => close(x, got[i], 1e-4)), 'mul(): matches reference');

// ── 3. trs(): rotY then rotZ, applied as translate*rotY*rotZ
const T = M.trs(new Float32Array(16), 5, 0, 0, Math.PI / 2, 0);
const p = xf(T, 0, 0, 1);   // local +Z under rotY(90°) becomes world +X
ok(close(p[0], 5 + 1, 1e-5) && close(p[2], 0, 1e-5), 'trs(): rotY(90) sends local +z to world +x');
const T2 = M.trs(new Float32Array(16), 0, 0, 0, 0, Math.PI / 2);
const p2 = xf(T2, 1, 0, 0); // local +X under rotZ(90°) becomes world +Y
ok(close(p2[0], 0, 1e-5) && close(p2[1], 1, 1e-5), 'trs(): rotZ(90) sends local +x to world +y');
// a slab at angle a, rotated by a about Y, must have its local +Z pointing outward
for (const a of [0, 1.2566, 2.5133, 3.7699, 5.0265]) {
  const Mt = M.trs(new Float32Array(16), Math.sin(a) * 5, 0, Math.cos(a) * 5, a, 0);
  const tip = xf(Mt, 0, 0, 1), ctr = xf(Mt, 0, 0, 0);
  const r0 = Math.hypot(ctr[0], ctr[2]), r1 = Math.hypot(tip[0], tip[2]);
  ok(r1 > r0 + 0.99, `ring: slab at a=${a.toFixed(3)} faces outward (r ${r0.toFixed(2)}->${r1.toFixed(2)})`);
}

// ── 4. normal matrix from a rigid transform must be orthonormal
const mv = M.mul(new Float32Array(16), B, A), n3 = M.mat3(new Float32Array(9), mv);
const col = i => [n3[i * 3], n3[i * 3 + 1], n3[i * 3 + 2]];
for (let i = 0; i < 3; i++) ok(close(Math.hypot(...col(i)), 1, 1e-4), `mat3(): column ${i} is unit length`);
const dot = (u, w) => u[0] * w[0] + u[1] * w[1] + u[2] * w[2];
ok(close(dot(col(0), col(1)), 0, 1e-4) && close(dot(col(0), col(2)), 0, 1e-4), 'mat3(): columns orthogonal');

// ── 5. rounded box: every vertex on the surface, every normal the SDF gradient
const HX = 0.74, HY = 0.99, HZ = 0.075, r = 0.052, seg = 16;
const g = roundedBox(HX, HY, HZ, r, seg);
const rr = Math.min(r, Math.min(HX, HY, HZ) * 0.92);
const core = [HX - rr, HY - rr, HZ - rr];
ok(core.every(x => x > 0), 'roundedBox(): core half-extents positive (r clamped for a thin slab)');
let maxSdf = 0, minDot = 2, nUnit = 0;
for (let i = 0; i < g.pos.length; i += 3) {
  const P = [g.pos[i], g.pos[i + 1], g.pos[i + 2]];
  const q = [Math.abs(P[0]) - core[0], Math.abs(P[1]) - core[1], Math.abs(P[2]) - core[2]];
  const m = [Math.max(q[0], 0), Math.max(q[1], 0), Math.max(q[2], 0)];
  const sdf = Math.hypot(...m) + Math.min(Math.max(q[0], q[1], q[2]), 0) - rr;
  maxSdf = Math.max(maxSdf, Math.abs(sdf));
  const N = [g.nrm[i], g.nrm[i + 1], g.nrm[i + 2]];
  if (!close(Math.hypot(...N), 1, 1e-4)) nUnit++;
  // outward test: the normal must point away from the core
  const grad = [Math.sign(P[0]) * m[0], Math.sign(P[1]) * m[1], Math.sign(P[2]) * m[2]];
  const gl = Math.hypot(...grad);
  if (gl > 1e-6) minDot = Math.min(minDot, dot(N, grad.map(x => x / gl)));
}
ok(maxSdf < 1e-5, `roundedBox(): all ${g.pos.length / 3} vertices on the surface (max |sdf| ${maxSdf.toExponential(1)})`);
ok(nUnit === 0, 'roundedBox(): every normal is unit length');
ok(minDot > 0.999, `roundedBox(): every normal matches the SDF gradient (min dot ${minDot.toFixed(4)})`);

// ── 6. winding: every triangle must face outward
let backwards = 0;
for (let t = 0; t < g.idx.length; t += 3) {
  const [a, b, cI] = [g.idx[t], g.idx[t + 1], g.idx[t + 2]];
  const P = j => [g.pos[j * 3], g.pos[j * 3 + 1], g.pos[j * 3 + 2]];
  const [p0, p1, p2] = [P(a), P(b), P(cI)];
  const u = p1.map((x, i) => x - p0[i]), w = p2.map((x, i) => x - p0[i]);
  const fn = [u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0]];
  const L = Math.hypot(...fn); if (L < 1e-12) continue;      // degenerate at a pole
  const vn = [g.nrm[a * 3], g.nrm[a * 3 + 1], g.nrm[a * 3 + 2]];
  if (dot(fn.map(x => x / L), vn) < 0) backwards++;
}
ok(backwards === 0, `winding: ${g.idx.length / 3} triangles, ${backwards} wound inward`);
ok(g.idx.length / 3 === 6 * seg * seg * 2, `geometry: ${g.idx.length / 3} tris, ${g.pos.length / 3} verts`);
ok(g.pos.length / 3 < 65536, 'geometry: vertex count fits Uint16 indices');

// ── 7. angDiff
for (const [a, b, e] of [[0, 0, 0], [0.1, 6.2, 0.1 - 6.2 + 2 * Math.PI], [Math.PI, 0, Math.PI], [-3.2, 3.2, -6.4 + 2 * Math.PI]])
  ok(close(angDiff(a, b), e, 1e-9), `angDiff(${a}, ${b}) = ${angDiff(a, b).toFixed(4)}`);
ok(Math.abs(angDiff(0, 6.283185307)) < 1e-6, 'angDiff(): a full turn is zero');
let worst = 0;
for (let i = 0; i < 20000; i++) { const a = (Math.random() - 0.5) * 200, b = (Math.random() - 0.5) * 200;
  const d = angDiff(a, b); worst = Math.max(worst, Math.abs(d)); }
ok(worst <= Math.PI + 1e-9, `angDiff(): always within [-pi,pi] over 20k random pairs (worst ${worst.toFixed(4)})`);

ok(close(easeIO(0), 0) && close(easeIO(1), 1) && close(easeIO(0.5), 0.5), 'easeIO(): endpoints and midpoint');

console.log(fail ? `\n${fail} FAILURE(S)` : '\nall passed');
process.exit(fail ? 1 : 0);
