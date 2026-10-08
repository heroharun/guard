#!/usr/bin/env python3
"""PC Guard (macOS) - kendi cihaziniz icin mudahale koruması.

Ekran KAPALI veya KILITLI iken:
  - klavyeye dokunulursa      -> ":keyboard: klavyeye dokunuldu" mesaji
  - fare oynatilirsa          -> ":mouse: mouse oynadi" mesaji
  - ekran acilirsa (uyanirsa) -> ":shield: ekran acildi" mesaji + kamera fotosu

ONEMLI: Ne yazildigini KAYDETMEZ. Sadece "giris oldu" olgusunu bildirir
(tus icerigi yok). Icerik yerine, sistemin "son giristen beri gecen sure"
degerini okur (Quartz CGEventSourceSecondsSinceLastEventType).

Ortam degiskenleri (plist icinde):
  SLACK_BOT_TOKEN   xoxb-...   (foto yuklemek + metin icin)
  SLACK_CHANNEL     C...       (hedef kanal)
  SLACK_WEBHOOK_URL ...        (yedek metin yolu)
"""
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

try:
    import Quartz  # pyobjc-framework-Quartz
except Exception as _e:  # noqa: BLE001
    Quartz = None
    _QUARTZ_ERR = _e

BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
CHANNEL = os.environ.get("SLACK_CHANNEL", "")
WEBHOOK = os.environ.get("SLACK_WEBHOOK_URL", "")
CAMERA_INDEX = os.environ.get("CAMERA_INDEX", "0")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "1"))
COOLDOWN = float(os.environ.get("COOLDOWN", "20"))          # ayni olay icin spam engeli
WAKE_COOLDOWN = float(os.environ.get("WAKE_COOLDOWN", "10"))  # cift uyanma tetigini birlestir
SETTLE_SECONDS = float(os.environ.get("SETTLE_SECONDS", "0.2"))  # uyanma sonrasi kisa bekleme
WARMUP_FRAMES = int(os.environ.get("WARMUP_FRAMES", "8"))   # az kare = hizli (biraz daha karanlik)
# Parlaklik 0.0..1.0 olcegi (DisplayServicesGetBrightness)
DARK_LEVEL = float(os.environ.get("DARK_LEVEL", "0.15"))   # bu orandan dusuk = "karanlik"
MIN_DARK_SECONDS = float(os.environ.get("MIN_DARK_SECONDS", "3"))  # bu kadar karanlik kaldiysa uyanma tetikler
RISE_DELTA = float(os.environ.get("RISE_DELTA", "0.15"))   # parlaklikta bu kadar ani artis = tetik
DEBUG_BRIGHT = os.environ.get("DEBUG_BRIGHT", "1") == "1"  # parlaklik degisimlerini logla
CAPTURE_DIR = Path(os.environ.get("CAPTURE_DIR", Path(__file__).parent / "captures"))

DISP_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4})\s+Notification\s+Display is turned (on|off)"
)


def log(msg: str) -> None:
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------- durum okuma (iceriksiz) ----------

def last_display_event() -> tuple[str, str] | None:
    """pmset gunlugundeki en son ekran olayi: ('on'|'off', zaman damgasi)."""
    out = subprocess.run(["/usr/bin/pmset", "-g", "log"], capture_output=True, text=True).stdout
    last = None
    for line in out.splitlines():
        m = DISP_RE.match(line)
        if m:
            last = (m.group(2), m.group(1))
    return last


import ctypes as _ctypes

try:
    _DS = _ctypes.CDLL(
        "/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices")
    _DS.DisplayServicesGetBrightness.restype = _ctypes.c_int
    _DS.DisplayServicesGetBrightness.argtypes = [
        _ctypes.c_uint32, _ctypes.POINTER(_ctypes.c_float)]
except Exception:  # noqa: BLE001
    _DS = None


def read_brightness() -> float | None:
    """Gercek ekran parlakligi 0.0..1.0 (DisplayServicesGetBrightness).

    ioreg AppleARMBacklight bu Mac'te donuk deger veriyordu; bu API canli.
    Okunamazsa None.
    """
    if _DS is None or Quartz is None:
        return None
    try:
        did = Quartz.CGMainDisplayID()
        val = _ctypes.c_float(-1.0)
        if _DS.DisplayServicesGetBrightness(did, _ctypes.byref(val)) != 0:
            return None
        v = float(val.value)
        return v if 0.0 <= v <= 1.0 else None
    except Exception:  # noqa: BLE001
        return None


def is_locked() -> bool:
    if Quartz is None:
        return False
    try:
        d = Quartz.CGSessionCopyCurrentDictionary()
        return bool(d.get("CGSSessionScreenIsLocked", 0)) if d else False
    except Exception:  # noqa: BLE001
        return False


def display_asleep() -> bool:
    """Ekran (panel) uykuda/kapali mi? ioreg parlakliktan bagimsiz, guvenilir."""
    if Quartz is None:
        return False
    try:
        return bool(Quartz.CGDisplayIsAsleep(Quartz.CGMainDisplayID()))
    except Exception:  # noqa: BLE001
        return False


def _idle(*types) -> float:
    """Verilen olay tiplerinden herhangi birinin son olusundan beri gecen saniye."""
    if Quartz is None:
        return 1e9
    vals = [Quartz.CGEventSourceSecondsSinceLastEventType(0, t) for t in types]
    return min(vals) if vals else 1e9


def keyboard_idle() -> float:
    return _idle(Quartz.kCGEventKeyDown, Quartz.kCGEventFlagsChanged)


def mouse_idle() -> float:
    return _idle(
        Quartz.kCGEventMouseMoved,
        Quartz.kCGEventLeftMouseDown,
        Quartz.kCGEventRightMouseDown,
        Quartz.kCGEventScrollWheel,
    )


# ---------- Slack ----------

def post_webhook(text: str) -> bool:
    if not WEBHOOK:
        return False
    try:
        requests.post(WEBHOOK, json={"text": text}, timeout=15).raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log(f"webhook hatasi: {e}")
        return False


def send_text(text: str) -> None:
    """Once chat.postMessage (kanala), olmazsa webhook."""
    if BOT_TOKEN and CHANNEL:
        try:
            r = requests.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {BOT_TOKEN}"},
                json={"channel": CHANNEL, "text": text},
                timeout=20,
            ).json()
            if r.get("ok"):
                return
            log(f"chat.postMessage hata: {r.get('error')}; webhook'a dusuluyor")
        except Exception as e:  # noqa: BLE001
            log(f"chat.postMessage istisna: {e}; webhook'a dusuluyor")
    if not post_webhook(text):
        log(f"UYARI: metin gonderilemedi: {text}")


IMAGESNAP = "/opt/homebrew/bin/imagesnap"
CAPTURE_LABEL = os.environ.get("CAPTURE_LABEL", "com.pcguard.capture")
SHOT_PATH = CAPTURE_DIR / "_shot.jpg"


def _capture_via_helper() -> None:
    """Kamerayi ayri bir launchd isi (imagesnap dogrudan program) tetikler.

    macOS kamera iznini sorumlu surece atar; imagesnap dogrudan calisinca
    izinli olur. python alt-sureci olarak calisinca 'access not granted' olur,
    o yuzden helper'i kickstart ile tetikleyip ciktisini bekliyoruz.
    """
    uid = os.getuid()
    if SHOT_PATH.exists():
        SHOT_PATH.unlink()
    subprocess.run(["/bin/launchctl", "kickstart", "-k",
                    f"gui/{uid}/{CAPTURE_LABEL}"],
                   check=True, timeout=10,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # dosyanin olusup boyutunun oturmasini bekle (hizli: 0.1 sn araliklarla)
    deadline = time.time() + 8
    last_size = -1
    while time.time() < deadline:
        time.sleep(0.1)
        if SHOT_PATH.exists():
            size = SHOT_PATH.stat().st_size
            if size > 0 and size == last_size:  # iki okumada ayni = tamam
                return
            last_size = size
    raise RuntimeError("helper zaman asimi (foto olusmadi)")


def _capture_direct(path: Path) -> None:
    """Yedek: python'un kendisi kameraya erisebiliyorsa (ornek: terminalden --test)."""
    subprocess.run([IMAGESNAP, "-w", "1.0", str(path)],
                   check=True, timeout=20,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def capture_photo() -> Path:
    CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTURE_DIR / f"guard_{datetime.now():%Y%m%d_%H%M%S}.jpg"
    last_err: Exception | None = None
    for attempt in range(1, 4):
        # 1) helper (launchd servisi altinda calisan yol)
        try:
            _capture_via_helper()
            SHOT_PATH.replace(path)
            if path.exists() and path.stat().st_size > 0:
                return path
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"yakalama (helper) deneme {attempt} basarisiz: {e}")
        # 2) dogrudan (terminalden calisirken)
        try:
            _capture_direct(path)
            if path.exists() and path.stat().st_size > 0:
                return path
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"yakalama (dogrudan) deneme {attempt} basarisiz: {e}")
        time.sleep(2)
    raise RuntimeError(f"kamera yakalanamadi: {last_err}")


def upload_photo(photo: Path, text: str) -> None:
    if not (BOT_TOKEN and CHANNEL):
        post_webhook(text + " (foto yerelde)")
        return
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    size = photo.stat().st_size
    r = requests.post("https://slack.com/api/files.getUploadURLExternal", headers=headers,
                      data={"filename": photo.name, "length": size}, timeout=30).json()
    if not r.get("ok"):
        log(f"getUploadURL hata: {r}")
        send_text(text + f" (foto yuklenemedi: {r.get('error')})")
        return
    with photo.open("rb") as f:
        requests.post(r["upload_url"], files={"file": (photo.name, f, "image/jpeg")},
                      timeout=60).raise_for_status()
    r2 = requests.post("https://slack.com/api/files.completeUploadExternal", headers=headers, json={
        "files": [{"id": r["file_id"], "title": photo.name}],
        "channel_id": CHANNEL,
        "initial_comment": text,
    }, timeout=30).json()
    if not r2.get("ok"):
        log(f"completeUpload hata: {r2}")
        send_text(text + f" (foto yuklenemedi: {r2.get('error')})")


def handle_wake(ts: str, reason: str = "ekran acildi") -> None:
    text = f":shield: PC Guard: {reason} — {ts}"
    log(f"{reason} ({ts}); foto cekiliyor...")
    time.sleep(SETTLE_SECONDS)
    try:
        photo = capture_photo()
        log(f"Foto kaydedildi: {photo}")
        upload_photo(photo, text)
        log("Slack'e gonderildi.")
    except Exception as e:  # noqa: BLE001
        log(f"HATA: {e}")
        send_text(text + f" (foto cekilemedi: {e})")


# ---------- ana dongu ----------

def main() -> None:
    if Quartz is None:
        log(f"UYARI: Quartz yok ({_QUARTZ_ERR}); klavye/mouse algilama devre disi.")

    if "--test" in sys.argv:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S %z")
        send_text(f":keyboard: PC Guard testi: klavyeye dokunuldu — {now}")
        send_text(f":mouse: PC Guard testi: mouse oynadi — {now}")
        handle_wake(now)
        return

    ev = last_display_event()
    display_on = (ev is None) or (ev[0] == "on")
    last_seen_ts = ev[1] if ev else None
    last_kb_sent = 0.0
    last_mouse_sent = 0.0
    last_wake = 0.0
    prev_bright = read_brightness()
    if prev_bright is None:
        prev_bright = 1.0  # okunamadiysa "aydinlik" varsay, yanlis tetik olmasin
    prev_locked = is_locked()
    prev_asleep = display_asleep()
    asleep_since: float | None = time.time() if prev_asleep else None

    def take_photo(caption: str) -> bool:
        """Foto cek + yukle; WAKE_COOLDOWN ile spam/cakisma onlenir.

        Dondurur: foto gercekten cekildi mi (cooldown'a takildiysa False).
        """
        nonlocal last_wake
        now = time.time()
        if now - last_wake < WAKE_COOLDOWN:
            return False
        last_wake = now
        log(f"{caption}; foto cekiliyor...")
        try:
            time.sleep(SETTLE_SECONDS)
            photo = capture_photo()
            log(f"Foto kaydedildi: {photo}")
            upload_photo(photo, caption)
            log("Slack'e gonderildi.")
        except Exception as e:  # noqa: BLE001
            log(f"HATA: {e}")
            send_text(caption + f" (foto cekilemedi: {e})")
        return True

    def maybe_wake(ts: str, reason: str) -> None:
        take_photo(f":shield: PC Guard: {reason} — {ts}")

    log(f"PC Guard basladi. display_on={display_on}, kilitli={is_locked()}, parlaklik={prev_bright}")

    while True:
        time.sleep(POLL_SECONDS)

        # 1) ekran acilma olayini pmset'ten yakala
        ev = last_display_event()
        if ev:
            state, ts = ev
            if ts != last_seen_ts:
                last_seen_ts = ts
                if state == "on" and not display_on:
                    display_on = True
                    maybe_wake(ts, "ekran acildi")
                elif state == "off":
                    display_on = False

        # 2) parlaklik: karanlik esigini asar VEYA ani buyuk artis olursa tetikle
        bright = read_brightness()
        if bright is not None:
            if DEBUG_BRIGHT and abs(bright - prev_bright) >= 0.02:
                log(f"parlaklik {prev_bright:.2f} -> {bright:.2f}")
            crossed = (prev_bright < DARK_LEVEL and bright >= DARK_LEVEL)
            jumped = (bright - prev_bright >= RISE_DELTA)
            if crossed or jumped:
                display_on = True
                maybe_wake(datetime.now().strftime("%Y-%m-%d %H:%M:%S %z"), "ekran aydinlandi")
            prev_bright = bright

        # 3) kilit acilirsa (kilitli -> acik) tetikle
        locked_now = is_locked()
        if prev_locked and not locked_now:
            maybe_wake(datetime.now().strftime("%Y-%m-%d %H:%M:%S %z"), "ekran kilidi acildi")
        prev_locked = locked_now

        # 4) ekran uykuya girip (>=MIN_DARK_SECONDS) uyaninca tetikle
        asleep_now = display_asleep()
        if asleep_now and not prev_asleep:
            asleep_since = time.time()
        elif prev_asleep and not asleep_now:
            dur = (time.time() - asleep_since) if asleep_since else 999.0
            asleep_since = None
            if dur >= MIN_DARK_SECONDS:
                display_on = True
                maybe_wake(datetime.now().strftime("%Y-%m-%d %H:%M:%S %z"),
                           f"ekran uykudan uyandi ({int(dur)} sn karanlik)")
        prev_asleep = asleep_now

        dark = (prev_bright < DARK_LEVEL) or asleep_now
        armed = (not display_on) or dark or locked_now
        if not armed:
            continue

        now = time.time()
        now_str = datetime.now().strftime("%H:%M:%S")

        # karanlik veya uyku modunda (ekran kapali/dusuk) foto da cek
        dark_or_sleep = dark or (not display_on)

        if keyboard_idle() <= POLL_SECONDS + 0.5 and (now - last_kb_sent) >= COOLDOWN:
            last_kb_sent = now
            log("olay: klavye")
            msg = f":keyboard: PC Guard: ekran kapali/kilitliyken klavyeye dokunuldu — {now_str}"
            if not (dark_or_sleep and take_photo(msg)):
                send_text(msg)

        if mouse_idle() <= POLL_SECONDS + 0.5 and (now - last_mouse_sent) >= COOLDOWN:
            last_mouse_sent = now
            log("olay: mouse")
            msg = f":mouse: PC Guard: ekran kapali/kilitliyken mouse oynadi — {now_str}"
            if not (dark_or_sleep and take_photo(msg)):
                send_text(msg)


if __name__ == "__main__":
    main()
