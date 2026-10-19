/* EMCC login gate — client-side page gate for a static site.
   It hides the dashboard until a valid username/password is entered. It does NOT protect
   the data itself: data/emergency_case.json and the ArcGIS layer stay reachable directly.

   Add or change an account: compute SHA-256 of  "<username>:<password>:emcc-dashboard"
   (see README) and put it in USERS below as  username: 'hash'. Usernames are lower-case. */
(() => {
  'use strict';

  const SALT = 'emcc-dashboard';
  const USERS = {
    emcc: '05de63eb18ae519513690224878d264ba8d2369d73924f7d3766b89b114f0476',
  };
  const KEY = 'emcc-auth';
  const REMEMBER_DAYS = 7;
  const root = document.documentElement;

  let unlock;
  window.emccReady = new Promise((res) => { unlock = res; });

  const store = (kind) => { try { return window[kind]; } catch (e) { return null; } };
  function readSession() {
    for (const s of [store('sessionStorage'), store('localStorage')]) {
      if (!s) continue;
      try {
        const t = JSON.parse(s.getItem(KEY) || 'null');
        if (t && USERS[t.u] && USERS[t.u] === t.h && (!t.exp || t.exp > Date.now())) return t;
        if (t) s.removeItem(KEY);
      } catch (e) { /* ignore broken entries */ }
    }
    return null;
  }
  function saveSession(u, remember) {
    const t = { u, h: USERS[u] };
    try {
      if (remember) { t.exp = Date.now() + REMEMBER_DAYS * 864e5; store('localStorage').setItem(KEY, JSON.stringify(t)); }
      else store('sessionStorage').setItem(KEY, JSON.stringify(t));
    } catch (e) { /* storage blocked: stay logged in for this page only */ }
  }
  function clearSession() {
    for (const s of [store('sessionStorage'), store('localStorage')]) { try { s && s.removeItem(KEY); } catch (e) { /* ignore */ } }
  }

  // Decide before the body paints, so a logged-in visitor never sees the gate flash.
  const session = readSession();
  root.classList.add(session ? 'authed' : 'locked');
  if (session) unlock(session.u);

  async function sha256(text) {
    const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('');
  }

  document.addEventListener('DOMContentLoaded', () => {
    const $ = (id) => document.getElementById(id);
    const form = $('gate-form'), user = $('gate-user'), pass = $('gate-pass'), err = $('gate-err'), btn = $('gate-btn'), eye = $('gate-eye');

    const who = $('who');
    if (who && session) who.textContent = session.u;

    $('logout')?.addEventListener('click', () => { clearSession(); location.reload(); });

    if (session || !form) return;
    setTimeout(() => user.focus(), 0);

    eye.addEventListener('click', () => {
      const show = pass.type === 'password';
      pass.type = show ? 'text' : 'password';
      eye.textContent = show ? 'ซ่อน' : 'แสดง';
      eye.setAttribute('aria-label', show ? 'ซ่อนรหัสผ่าน' : 'แสดงรหัสผ่าน');
      pass.focus();
    });

    let fails = 0;
    const showErr = (msg) => { err.textContent = msg; err.hidden = false; form.classList.remove('shake'); void form.offsetWidth; form.classList.add('shake'); };
    [user, pass].forEach((i) => i.addEventListener('input', () => { err.hidden = true; }));

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const u = user.value.trim().toLowerCase();
      const p = pass.value;
      if (!u || !p) { showErr('กรุณากรอกชื่อผู้ใช้และรหัสผ่าน'); (u ? pass : user).focus(); return; }
      if (!window.crypto || !crypto.subtle) { showErr('เบราว์เซอร์นี้ไม่รองรับการเข้าสู่ระบบ กรุณาเปิดผ่าน https'); return; }

      btn.disabled = true; btn.textContent = 'กำลังตรวจสอบ…';
      const h = await sha256(`${u}:${p}:${SALT}`);
      // Slow down repeated guesses a little.
      await new Promise((r) => setTimeout(r, Math.min(300 * 2 ** fails, 4000)));
      btn.disabled = false; btn.textContent = 'เข้าสู่ระบบ';

      if (USERS[u] && USERS[u] === h) {
        saveSession(u, $('gate-remember').checked);
        if (who) who.textContent = u;
        pass.value = '';
        root.classList.remove('locked');
        root.classList.add('authed');
        unlock(u);
        document.querySelector('.nav-a')?.focus({ preventScroll: true });
      } else {
        fails++;
        showErr('ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง');
        pass.select();
      }
    });
  });
})();
