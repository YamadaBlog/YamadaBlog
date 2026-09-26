/* ═══════════════════════════════════════════════════════════════════════════
   PORTAL — the arrival side of the traversal.

   The atrium pushes the camera through a slab, the screen fills with that
   universe's own ground colour, and only then does it navigate. This file runs
   render-blocking in <head>, so the destination page opens already covered by
   exactly that colour and irises it away. The cut across the navigation is on a
   flat field of one colour, which is the one thing two documents can agree on.

   It also hangs a way back on the page, and animates the return the same way.

   Usage, in each universe's <head>, BEFORE the stylesheet:
     <script src="../portal.js" data-id="paper"
             data-ink="#8a2b1e" data-ground="#f4f1ea" data-corner="tr"></script>

   With JavaScript off, nothing here happens and nothing here is needed: the
   page is already the page.
   ═══════════════════════════════════════════════════════════════════════════ */
(function () {
  'use strict';
  var s = document.currentScript;
  if (!s) return;
  var D = s.dataset;
  var ID = D.id || '', INK = D.ink || '#000', GROUND = D.ground || '#fff';
  var HOME = D.home || '../';
  var KEY = 'mao:portal';
  var reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

  function read() {
    try { var v = JSON.parse(sessionStorage.getItem(KEY) || 'null');
          return (v && Date.now() - v.t < 6000) ? v : null; } catch (e) { return null; }
  }
  function write(v) { try { sessionStorage.setItem(KEY, JSON.stringify(v)); } catch (e) {} }
  function clear() { try { sessionStorage.removeItem(KEY); } catch (e) {} }

  /* ── the sheet. Created in <head>, so it is painted before the page is. ── */
  var sheet = document.createElement('div');
  sheet.id = 'mao-portal';
  sheet.setAttribute('aria-hidden', 'true');
  sheet.style.cssText =
    'position:fixed;inset:0;z-index:2147483000;pointer-events:none;background:' + GROUND +
    ';opacity:0;will-change:clip-path,opacity';

  var style = document.createElement('style');
  style.textContent =
    '#mao-portal.in{opacity:1;clip-path:circle(150% at 50% 50%);' +
      'animation:mao-in .62s cubic-bezier(.22,1,.36,1) forwards}' +
    '#mao-portal.out{opacity:1;clip-path:circle(0% at 50% 50%);' +
      'animation:mao-out .40s cubic-bezier(.65,0,.35,1) forwards}' +
    '@keyframes mao-in{from{clip-path:circle(150% at 50% 50%)}' +
      'to{clip-path:circle(0% at 50% 50%)}}' +
    '@keyframes mao-out{from{clip-path:circle(0% at 50% 50%)}' +
      'to{clip-path:circle(150% at 50% 50%)}}' +
    '#mao-back{position:fixed;z-index:2147482999;display:inline-flex;align-items:center;gap:9px;' +
      'font:600 10.5px/1 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:.17em;' +
      'text-transform:uppercase;text-decoration:none;padding:9px 13px;cursor:pointer;' +
      'border:1px solid currentColor;background:transparent;appearance:none;' +
      'transition:background .18s ease,color .18s ease;' +
      // The resting state is visible. The animation only adds the fade-in, via
      // `backwards`, so a browser that never runs it still shows the control.
      // A chip with opacity:0 waiting on a keyframe is a chip that disappears.
      'animation:mao-fade .5s .35s backwards}' +
    '@keyframes mao-fade{from{opacity:0}}' +
    '#mao-back span{font-size:12px;line-height:1}' +
    '@media (prefers-reduced-motion:reduce){' +
      '#mao-portal.in{animation:none;opacity:0}' +
      '#mao-portal.out{animation:none;opacity:1;clip-path:circle(150% at 50% 50%)}' +
      '#mao-back{animation:none}}';

  (document.head || document.documentElement).appendChild(style);
  (document.head || document.documentElement).appendChild(sheet);

  /* ── arrival ─────────────────────────────────────────────────────────── */
  var p = read();
  var arrived = !!(p && !p.back && p.id === ID);
  if (arrived) {
    sheet.style.background = p.ground || GROUND;
    sheet.style.opacity = '1';
    sheet.style.clipPath = 'circle(150% at 50% 50%)';
    clear();
    var fire = function () {
      requestAnimationFrame(function () { requestAnimationFrame(function () { sheet.className = 'in'; }); });
    };
    if (document.readyState === 'loading') addEventListener('DOMContentLoaded', fire, { once: true });
    else fire();
    // never let a failed animation leave the page behind a coloured sheet
    setTimeout(function () { sheet.className = ''; sheet.style.opacity = '0'; sheet.style.clipPath = ''; }, 1600);
  }

  /* ── the way back ────────────────────────────────────────────────────── */
  function leave(href) {
    write({ back: true, id: ID, ground: GROUND, ink: INK, t: Date.now() });
    sheet.style.background = GROUND;
    if (reduce) { location.href = href; return; }
    sheet.className = 'out';
    setTimeout(function () { location.href = href; }, 330);
  }

  function bindHomeLinks() {
    // Any link the page already has back to the atrium becomes the departure.
    // A page that shows one in its own design does not also need a chip.
    var found = 0, links = document.querySelectorAll('a[href]');
    for (var i = 0; i < links.length; i++) {
      var a = links[i];
      if (a.id === 'mao-back') continue;
      if (a.href !== new URL(HOME, location.href).href) continue;
      found++;
      a.addEventListener('click', function (e) {
        if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
        e.preventDefault(); leave(this.getAttribute('href'));
      });
    }
    return found;
  }

  function mount() {
    var found = bindHomeLinks();
    // data-corner is the page saying "I have nowhere of my own to put this".
    if (!D.corner) {
      if (!found) console.warn('portal: no link home on this page and no data-corner');
      return;
    }
    var corner = D.corner;
    var b = document.createElement('a');
    b.id = 'mao-back';
    b.href = HOME;
    b.innerHTML = '<span>&#8598;</span>ATRIUM';
    b.style.color = INK;
    var pad = 'max(14px, env(safe-area-inset-' ;
    b.style.top    = corner[0] === 't' ? 'calc(14px + env(safe-area-inset-top,0px))' : 'auto';
    b.style.bottom = corner[0] === 'b' ? 'calc(14px + env(safe-area-inset-bottom,0px))' : 'auto';
    b.style.left   = corner[1] === 'l' ? '14px' : 'auto';
    b.style.right  = corner[1] === 'r' ? '14px' : 'auto';
    b.addEventListener('mouseenter', function () { b.style.background = INK; b.style.color = GROUND; });
    b.addEventListener('mouseleave', function () { b.style.background = 'transparent'; b.style.color = INK; });
    b.addEventListener('focus', function () { b.style.background = INK; b.style.color = GROUND; });
    b.addEventListener('blur', function () { b.style.background = 'transparent'; b.style.color = INK; });
    b.addEventListener('click', function (e) {
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault(); leave(b.getAttribute('href'));
    });
    document.body.appendChild(b);
    void pad;
  }
  if (document.readyState === 'loading') addEventListener('DOMContentLoaded', mount, { once: true });
  else mount();

  /* back/forward cache: the sheet must not survive the trip */
  addEventListener('pageshow', function (e) {
    if (!e.persisted) return;
    sheet.className = ''; sheet.style.opacity = '0'; sheet.style.clipPath = '';
  });

  window.MAOPortal = { leave: leave, id: ID };
})();
