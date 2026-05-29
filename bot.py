# /// script
# requires-python = "==3.11.*"
# dependencies = [
#   "codewords-client==0.4.6",
#   "fastapi==0.116.1"
# ]
# [tool.env-checker]
# env_vars = [
#   "PORT=8000",
#   "LOGLEVEL=INFO",
#   "CODEWORDS_API_KEY",
#   "CODEWORDS_RUNTIME_URI"
# ]
# ///

# Outlaw Putt Putt Bot - Local Version
# Run on your laptop to use your residential IP for hCaptcha PoW auto-solve.
#
# Setup (one time):
#   pip install playwright httpx httpx[socks]
#   playwright install chromium
#
# Run:
#   python bot.py

import asyncio
import base64
import json
import random
import time
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Page

GAME_URL = "https://app.outieputt.com/"
API_BASE = "https://api.outieputt.com"

BASE_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "content-type": "application/json",
    "origin": "https://app.outieputt.com",
    "x-requested-with": "com.outlawgame.android",
    "referer": "https://app.outieputt.com/",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 12; SM-G9980 Build/V417IR; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/110.0.5481.154 Mobile Safari/537.36"
    ),
    "accept-language": "en,en-US;q=0.9",
    "accept-encoding": "gzip, deflate",
    "sec-fetch-site": "same-site",
    "sec-fetch-mode": "cors",
    "sec-fetch-dest": "empty",
}

HCAPTCHA_JS = (
    "() => new Promise(function(resolve, reject) {"
    "  var SK = '1adc4ffa-1388-4706-858a-f7183c42aafc';"
    "  var timer = setTimeout(function() { reject(new Error('hcaptcha timeout')); }, 45000);"
    "  function extractToken(r) {"
    "    if (!r) return null;"
    "    if (typeof r === 'string' && r.length > 50) return r;"
    "    if (typeof r === 'object' && r.response && r.response.length > 50) return r.response;"
    "    return null;"
    "  }"
    "  function doSolve() {"
    "    try {"
    "      var el = document.createElement('div');"
    "      el.id = 'hc-auto-' + Date.now();"
    "      el.setAttribute('style', 'display:none');"
    "      document.body.appendChild(el);"
    "      var wid = hcaptcha.render(el, {"
    "        sitekey: SK,"
    "        size: 'invisible',"
    "        callback: function(t) { var tok = extractToken(t); if (tok) { clearTimeout(timer); resolve(tok); } },"
    "        'error-callback': function(e) { clearTimeout(timer); reject(new Error('hc error')); },"
    "        'expired-callback': function() { clearTimeout(timer); reject(new Error('hc expired')); }"
    "      });"
    "      hcaptcha.execute(wid, {async: true})"
    "        .then(function(r) { var tok = extractToken(r); if (tok) { clearTimeout(timer); resolve(tok); } })"
    "        .catch(function(e) { clearTimeout(timer); reject(e); });"
    "    } catch(e) { clearTimeout(timer); reject(e); }"
    "  }"
    "  if (typeof hcaptcha !== 'undefined') { doSolve(); return; }"
    "  var s = document.createElement('script');"
    "  s.src = 'https://js.hcaptcha.com/1/api.js?render=explicit&recaptchacompat=off';"
    "  s.async = true;"
    "  s.onload = function() { setTimeout(doSolve, 2000); };"
    "  s.onerror = function() { clearTimeout(timer); reject(new Error('script load failed')); };"
    "  document.head.appendChild(s);"
    "})"
)

RECAPTCHA_JS = """
() => new Promise((resolve) => {
    if (typeof grecaptcha === 'undefined') { resolve(null); return; }
    const timer = setTimeout(() => resolve(null), 12000);
    grecaptcha.ready(() => {
        grecaptcha
            .execute('6Ldq2lQsAAAAAHHpQj1NbC1oGK419Tpa1XsSZfIW', { action: 'complete_level_gamer' })
            .then(tok => { clearTimeout(timer); resolve(tok); })
            .catch(() => { clearTimeout(timer); resolve(null); });
    });
})
"""

TURNSTILE_JS = """
() => new Promise((resolve) => {
    const maxWait = 20000; const start = Date.now();
    function poll() {
        try {
            const inputs = document.querySelectorAll('input[name="cf-turnstile-response"]');
            for (const el of inputs) { if (el.value && el.value.length > 50) { resolve(el.value); return; } }
            if (window.turnstile) { try { const t = window.turnstile.getResponse(); if (t && t.length > 50) { resolve(t); return; } } catch(e) {} }
        } catch(e) {}
        if (Date.now() - start > maxWait) { resolve(null); return; }
        setTimeout(poll, 500);
    }
    setTimeout(poll, 1500);
})
"""

def decode_jwt(token):
    try:
        seg = token.split(".")[1]
        seg += "=" * (4 - len(seg) % 4)
        return json.loads(base64.b64decode(seg))
    except Exception:
        return {}

def jwt_still_valid(token, buffer=300):
    return time.time() < decode_jwt(token).get("exp", 0) - buffer

def random_ball_pos():
    return {"ballPos": {"x": round(150.0 + random.uniform(-40, 40), 6),
                        "y": round(400.0 + random.uniform(-60, 60), 6)}}

async def auto_solve_hcaptcha(page):
    print("  [hCaptcha] Auto-solving (hsw PoW)...")
    try:
        page.set_default_timeout(50000)
        token = await page.evaluate(HCAPTCHA_JS)
        page.set_default_timeout(15000)
        if isinstance(token, str) and len(token) > 50:
            print(f"  [hCaptcha] Solved! len={len(token)}")
            return token
        print(f"  [hCaptcha] Bad token: {type(token).__name__}={str(token)[:80]}")
        return None
    except Exception as e:
        page.set_default_timeout(15000)
        print(f"  [hCaptcha] Failed: {e}")
        return None

async def play_one_level(http, page, session_id, level_id, headers, local_ip):
    await http.post(
        f"{API_BASE}/api/solo/session/{session_id}/level/{level_id}/hit",
        headers=headers, json={"strokes": 1, "game_state": random_ball_pos()},
    )
    recap = await page.evaluate(RECAPTCHA_JS)
    if not recap:
        raise ValueError("reCAPTCHA token kosong")
    compl_h = {**headers, "x-turnstile-token": recap, "x-turnstile-remote-ip": local_ip}
    rc = await http.post(
        f"{API_BASE}/api/solo/session/{session_id}/level/{level_id}/complete",
        headers=compl_h, json={},
    )
    rc.raise_for_status()
    return rc.json()

async def play_session(http, page, jwt, user_id, local_ip, session_num):
    print(f"\n=== Sesi {session_num} ===")
    h = {**BASE_HEADERS, "authorization": f"Bearer {jwt}"}
    await page.goto(GAME_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)
    turnstile = await page.evaluate(TURNSTILE_JS) or "token"
    r = await http.post(
        f"{API_BASE}/api/solo/session",
        params={"user_id": user_id},
        headers={**h, "x-turnstile-token": turnstile, "x-turnstile-remote-ip": local_ip},
        json={},
    )
    r.raise_for_status()
    sess = r.json()
    sid, cur_level, lvl_count = sess["id"], sess["level_id"], sess.get("level_count", 18)
    print(f"  Session {sid} | {lvl_count} levels")
    tokens, done = 0, 0
    for _ in range(lvl_count):
        if not cur_level:
            break
        try:
            cd = await play_one_level(http, page, sid, cur_level, h, local_ip)
            tokens = cd.get("total_earned_tokens", tokens)
            cur_level = cd.get("next_level_id")
            done += 1
            print(f"  Level {cd.get('level_id')} +{cd.get('earned_tokens_for_level', 0)} => {tokens}")
            await asyncio.sleep(0.4)
        except Exception as e:
            print(f"  Level {cur_level} error: {e}")
            break
    print(f"  {done}/{lvl_count} level. Reload + hCaptcha...")
    await page.goto(GAME_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)
    hcap = await auto_solve_hcaptcha(page)
    complete_h = {**h}
    if hcap:
        complete_h["X-HCaptcha-Token"] = hcap
        complete_h["X-HCaptcha-Remote-Ip"] = local_ip
    try:
        rc = await http.post(f"{API_BASE}/api/solo/session/{sid}/complete", headers=complete_h, json={})
        if rc.status_code in (200, 201):
            print(f"  Session complete! tokens={tokens}")
        else:
            print(f"  Session complete HTTP {rc.status_code}: {rc.text[:300]}")
    except Exception as e:
        print(f"  Session complete error: {e}")
    return {"session_id": sid, "levels": done, "tokens": tokens}

async def main():
    print("="*50)
    print("  Outlaw Putt Putt Bot - Local")
    print("="*50)
    jwt = input("\nJWT token (eyJ...): ").strip()
    if not jwt.startswith("eyJ"):
        print("JWT tidak valid")
        return
    if not jwt_still_valid(jwt):
        print("JWT expired. Tangkap JWT baru.")
        return
    payload = decode_jwt(jwt)
    user_id = payload.get("sub", "")
    print(f"JWT valid | user: {user_id}")
    try:
        num_sessions = int(input("Jumlah sesi [10]: ").strip() or "10")
        delay = float(input("Jeda detik [3]: ").strip() or "3")
    except ValueError:
        num_sessions, delay = 10, 3.0

    # --- Proxy setting ---
    print("\nProxy residential (kosongkan jika tidak pakai)")
    print("Format: socks5://user:pass@host:port  atau  http://user:pass@host:port")
    print("Contoh: socks5://AsepsundrfZ9:c2a99exB8@51.79.192.226:10069")
    proxy_input = input("Proxy [kosong=skip]: ").strip()
    proxy_url = proxy_input if proxy_input else None
    if proxy_url:
        print(f"Pakai proxy: {proxy_url}")
    else:
        print("Tidak pakai proxy (pakai IP lokal)")

    # Ambil IP publik (lewat proxy kalau ada)
    try:
        if proxy_url:
            # httpx support socks5 via httpx[socks]
            async with httpx.AsyncClient(proxy=proxy_url, timeout=10) as tmp:
                r = await tmp.get("https://api.ipify.org?format=json")
                local_ip = r.json().get("ip", "0.0.0.0")
        else:
            async with httpx.AsyncClient(timeout=5) as tmp:
                r = await tmp.get("https://api.ipify.org?format=json")
                local_ip = r.json().get("ip", "0.0.0.0")
    except Exception as e:
        print(f"Warning: Gagal ambil IP publik: {e}")
        local_ip = "0.0.0.0"
    print(f"IP publik: {local_ip}")

    results = []
    async with async_playwright() as pw:
        # Browser juga lewat proxy kalau ada
        launch_args = ["--disable-blink-features=AutomationControlled"]
        browser = await pw.chromium.launch(
            headless=True,
            args=launch_args,
            proxy={"server": proxy_url} if proxy_url else None,
        )
        ctx = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        page.set_default_timeout(15000)
        print("Memuat halaman game...")
        await page.goto(GAME_URL, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
        try:
            bb_ip = await page.evaluate("() => fetch('https://api.ipify.org?format=json').then(r=>r.json()).then(d=>d.ip)")
        except Exception:
            bb_ip = local_ip
        print(f"IP browser: {bb_ip}")

        # httpx juga lewat proxy
        http_kwargs = {"timeout": 30.0}
        if proxy_url:
            http_kwargs["proxy"] = proxy_url

        async with httpx.AsyncClient(**http_kwargs) as http:
            for snum in range(1, num_sessions + 1):
                res = await play_session(http, page, jwt, user_id, bb_ip, snum)
                results.append(res)
                if snum < num_sessions:
                    await asyncio.sleep(delay)
                if not jwt_still_valid(jwt, buffer=60):
                    print("JWT hampir expired! Stop.")
                    break
        await browser.close()
    total = sum(r["tokens"] for r in results)
    print(f"\n{'='*50}")
    print(f"SELESAI: {len(results)} sesi | Total token: {total}")
    for r in results:
        print(f"  Session {r['session_id']}: {r['levels']} levels, {r['tokens']} tokens")

if __name__ == "__main__":
    asyncio.run(main())
