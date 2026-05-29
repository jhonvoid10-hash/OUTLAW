#!/usr/bin/env python3
"""
Outlaw Putt Putt Bot - Local Version
=====================================
Install:
  pip install httpx playwright
  playwright install chromium

Cara pakai:
  python bot.py --jwt "eyJhbGci..." --sessions 5
  python bot.py --jwt "eyJhbGci..." --sessions 10 --delay 5 --show-browser

JWT didapat dari HTTP Toolkit / mitmproxy saat buka app Android.
JWT expire tiap 1 jam - tangkap ulang jika expired.
"""

import argparse
import asyncio
import base64
import json
import random
import time
from typing import Optional

import httpx
from playwright.async_api import async_playwright, Page

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
GAME_URL = "https://app.outieputt.com/"
API_BASE = "https://api.outieputt.com"

BASE_HEADERS = {
    "accept":           "application/json, text/plain, */*",
    "content-type":     "application/json",
    "origin":           "https://app.outieputt.com",
    "x-requested-with": "com.outlawgame.android",
    "referer":          "https://app.outieputt.com/",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 12; SM-G9980 Build/V417IR; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
        "Chrome/110.0.5481.154 Mobile Safari/537.36"
    ),
    "accept-language":  "en,en-US;q=0.9",
    "accept-encoding":  "gzip, deflate",
    "sec-fetch-site":   "same-site",
    "sec-fetch-mode":   "cors",
    "sec-fetch-dest":   "empty",
}

# hCaptcha auto-solve (hsw proof-of-work).
# Game menamai headernya X-Turnstile-Token tapi isinya hCaptcha token.
# Sitekey 1adc4ffa-1388-4706-858a-f7183c42aafc dipakai untuk semua captcha.
HCAPTCHA_JS = (
    "() => new Promise(function(resolve) {"
    "  var SK = '1adc4ffa-1388-4706-858a-f7183c42aafc';"
    "  var timer = setTimeout(function() { resolve(null); }, 35000);"
    "  function doSolve() {"
    "    try {"
    "      var el = document.createElement('div');"
    "      el.id = 'hc-' + Date.now();"
    "      el.setAttribute('style', 'display:none');"
    "      document.body.appendChild(el);"
    "      var wid = hcaptcha.render(el, {"
    "        sitekey: SK, size: 'invisible',"
    "        callback: function(t) { clearTimeout(timer); resolve(t); },"
    "        'error-callback': function() { clearTimeout(timer); resolve(null); }"
    "      });"
    "      hcaptcha.execute(wid, {async: true})"
    "        .then(function(t) { if(t) { clearTimeout(timer); resolve(t); } })"
    "        .catch(function() { clearTimeout(timer); resolve(null); });"
    "    } catch(e) { clearTimeout(timer); resolve(null); }"
    "  }"
    "  if (typeof hcaptcha !== 'undefined') { doSolve(); return; }"
    "  var s = document.createElement('script');"
    "  s.src = 'https://js.hcaptcha.com/1/api.js?render=explicit&recaptchacompat=off';"
    "  s.async = true;"
    "  s.onload = function() { setTimeout(doSolve, 2500); };"
    "  s.onerror = function() { clearTimeout(timer); resolve(null); };"
    "  document.head.appendChild(s);"
    "})"
)


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def decode_jwt(token: str) -> dict:
    try:
        seg = token.split(".")[1]
        seg += "=" * (4 - len(seg) % 4)
        return json.loads(base64.b64decode(seg))
    except Exception:
        return {}


def jwt_valid(token: str, buffer: int = 300) -> bool:
    return time.time() < decode_jwt(token).get("exp", 0) - buffer


def validate_jwt(jwt: str) -> str:
    if not jwt or not jwt.startswith("eyJ"):
        raise SystemExit("ERROR: JWT tidak valid. Harus dimulai dengan 'eyJ'.")
    payload = decode_jwt(jwt)
    if not payload:
        raise SystemExit("ERROR: Gagal decode JWT.")
    if not jwt_valid(jwt):
        raise SystemExit("ERROR: JWT sudah expired. Tangkap JWT baru dari app.")
    user_id = payload.get("sub", "")
    if not user_id:
        raise SystemExit("ERROR: JWT tidak mengandung user_id (sub).")
    return user_id


def random_ball_pos() -> dict:
    # FIX #6: game_state langsung berisi x/y, bukan ballPos nested
    return {
        "x": round(150.0 + random.uniform(-40, 40), 6),
        "y": round(400.0 + random.uniform(-60, 60), 6),
    }


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------
async def start_browser(headless: bool = True):
    pw      = await async_playwright().start()
    browser = await pw.chromium.launch(headless=headless)
    ctx     = await browser.new_context(user_agent=BASE_HEADERS["user-agent"])
    page    = await ctx.new_page()
    page.set_default_timeout(15000)

    print("[browser] Membuka app.outieputt.com ...")
    await page.goto(GAME_URL, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # FIX #7: abort jika IP gagal didapat, jangan pakai 0.0.0.0 diam-diam
    try:
        ip: str = await page.evaluate(
            "() => fetch('https://api.ipify.org?format=json')"
            ".then(r=>r.json()).then(d=>d.ip)"
        )
        if not ip or ip == "0.0.0.0":
            raise ValueError("IP tidak valid")
    except Exception as e:
        raise SystemExit(f"ERROR: Gagal mendapatkan public IP: {e}\nCek koneksi internet Anda.")

    print(f"[browser] Ready. Public IP: {ip}")
    return pw, browser, page, ip


async def solve_captcha(page: Page, tag: str = "") -> Optional[str]:
    label = f"[{tag}]" if tag else "[captcha]"
    print(f"  {label} Solving hCaptcha (maks 35 detik) ...")
    try:
        page.set_default_timeout(40000)
        token: str = await page.evaluate(HCAPTCHA_JS)
        page.set_default_timeout(15000)
        if token and len(token) > 50:
            print(f"  {label} OK (len={len(token)})")
            return token
        print(f"  {label} Returned empty/short token")
        return None
    except Exception as e:
        page.set_default_timeout(15000)
        print(f"  {label} Error: {e}")
        return None


# ---------------------------------------------------------------------------
# Game API
# ---------------------------------------------------------------------------
async def do_level(
    http: httpx.AsyncClient, page: Page,
    session_id: int, level_id: int, headers: dict, ip: str,
) -> dict:
    # Hit (1 stroke)
    hit = await http.post(
        f"{API_BASE}/api/solo/session/{session_id}/level/{level_id}/hit",
        headers=headers,
        # FIX #6: game_state langsung x/y tanpa nesting ballPos
        json={"strokes": 1, "game_state": random_ball_pos()},
    )
    if hit.status_code != 200:
        print(f"    Warning hit HTTP {hit.status_code}")

    # Solve hCaptcha -> X-Turnstile-Token
    tok = await solve_captcha(page, f"lvl{level_id}")
    if not tok:
        raise RuntimeError("hCaptcha gagal untuk level complete.")

    # Complete level
    rc = await http.post(
        f"{API_BASE}/api/solo/session/{session_id}/level/{level_id}/complete",
        headers={**headers, "x-turnstile-token": tok, "x-turnstile-remote-ip": ip},
        json={},
    )
    if rc.status_code != 200:
        raise RuntimeError(f"Level complete HTTP {rc.status_code}: {rc.text[:200]}")
    return rc.json()


async def do_session(
    http: httpx.AsyncClient, page: Page,
    jwt: str, user_id: str, ip: str, n: int,
) -> dict:
    h = {**BASE_HEADERS, "authorization": f"Bearer {jwt}"}
    print(f"\n--- Sesi {n} ---")

    # FIX #1: solve captcha nyata sebelum create session, bukan hardcode "token"
    print("  [sess_start] Solving hCaptcha untuk buat session ...")
    tok_start = await solve_captcha(page, "sess_start")
    if not tok_start:
        raise RuntimeError("hCaptcha gagal untuk membuat session.")

    # Create session
    cr = await http.post(
        f"{API_BASE}/api/solo/session",
        params={"user_id": user_id},
        headers={**h, "x-turnstile-token": tok_start, "x-turnstile-remote-ip": ip},
        json={},
    )
    cr.raise_for_status()
    sess   = cr.json()
    sid    = sess["id"]
    lvl    = sess["level_id"]
    count  = sess.get("level_count", 18)
    print(f"  Session {sid} | {count} levels")

    tokens = 0
    done   = 0
    for _ in range(count):
        if not lvl:
            break
        try:
            cd     = await do_level(http, page, sid, lvl, h, ip)
            tokens = cd.get("total_earned_tokens", tokens)
            lvl    = cd.get("next_level_id")
            done  += 1
            earned = cd.get("earned_tokens_for_level", 0)
            # FIX #3: pakai default '?' supaya tidak crash kalau level_id None
            print(f"  Level {cd.get('level_id', '?'):>4}: +{earned:>3} | total: {tokens}")
            await asyncio.sleep(0.5)
        except Exception as le:
            print(f"  Level {lvl} FAILED: {le}")
            break

    # Session complete
    print("  Session complete...")
    hct = await solve_captcha(page, "sess")
    try:
        ch = {**h}
        if hct:
            # FIX #2: header lowercase, konsisten dengan endpoint lain
            ch["x-hcaptcha-token"]     = hct
            ch["x-hcaptcha-remote-ip"] = ip
        sc = await http.post(
            f"{API_BASE}/api/solo/session/{sid}/complete",
            headers=ch, json={},
        )
        sc.raise_for_status()
        # FIX #4: pakai lvl is None (sinyal API) bukan done >= count (bisa salah)
        status = "completed" if lvl is None else "partial"
    except Exception as e:
        print(f"  Session complete error: {e}")
        status = "partial"

    print(f"  [{status}] {done}/{count} levels | {tokens} tokens")
    return {"n": n, "sid": sid, "levels": done, "tokens": tokens, "status": status}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    ap = argparse.ArgumentParser(description="Outlaw Putt Putt Bot (Local)")
    ap.add_argument("--jwt",          required=True,           help="JWT dari Android app")
    ap.add_argument("--sessions",     type=int,   default=5,   help="Jumlah sesi (default: 5)")
    ap.add_argument("--delay",        type=float, default=3.0, help="Detik antar sesi (default: 3)")
    ap.add_argument("--show-browser", action="store_true",     help="Tampilkan browser window")
    args = ap.parse_args()

    uid = validate_jwt(args.jwt)
    print(f"JWT valid  |  user_id: {uid}")
    print(f"Sesi: {args.sessions}  |  Delay: {args.delay}s\n")

    pw, browser, page, ip = await start_browser(headless=not args.show_browser)
    results, total = [], 0

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            for i in range(1, args.sessions + 1):
                try:
                    res   = await do_session(http, page, args.jwt, uid, ip, i)
                    total += res["tokens"]
                    results.append(res)
                except Exception as e:
                    print(f"Sesi {i} error: {e}")
                    results.append({"n": i, "status": "failed", "tokens": 0, "levels": 0})
                if i < args.sessions:
                    print(f"\nWait {args.delay}s ...")
                    await asyncio.sleep(args.delay)
    finally:
        await browser.close()
        await pw.stop()

    ok = sum(1 for r in results if r.get("status") in ("completed", "partial"))
    print(f"\n{'='*46}")
    print(f"SELESAI: {ok}/{len(results)} sesi | Total: {total} tokens")
    print("="*46)
    for r in results:
        s = r.get("status", "?")
        print(f"  [{'OK' if s != 'failed' else 'XX'}] "
              f"Sesi {r['n']:>2}: {r.get('levels', 0):>2}/18 | "
              f"{r.get('tokens', 0):>5} tokens | [{s}]")


if __name__ == "__main__":
    asyncio.run(main())
