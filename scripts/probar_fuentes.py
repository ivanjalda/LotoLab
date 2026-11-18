#!/usr/bin/env python3
"""LotoLab - prueba 3 (Powerball sin compresion). Solo libreria estandar. No inventa nada."""
import json, os, re, urllib.request, urllib.error
from datetime import datetime, timezone

H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
     "Accept": "application/json, text/html, */*", "Accept-Encoding": "identity", "Accept-Language": "en-US,en;q=0.9"}
TX = "https://www.texaslottery.com/export/sites/lottery/Games/Powerball/Winning_Numbers/"
PRUEBAS = [
    ("Powerball bote (json)", "https://www.powerball.com/api/v1/estimates/powerball?_format=json", "todo"),
    ("Powerball ganadores por categoria (pagina)", "https://www.powerball.com/previous-results?gc=powerball", "texto:Match 5"),
    ("Powerball un sorteo concreto", "https://www.powerball.com/draw-result?gc=powerball&date=2026-11-16", "texto:Match 5"),
    ("Texas Powerball (tabla de bote)", TX + "index.html", "texto:Draw Date"),
    ("Texas Powerball (tabla de bote, otra)", TX, "texto:Draw Date"),
]
def texto(h):
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", h))
out = []
for nombre, url, modo in PRUEBAS:
    r = {"nombre": nombre, "url": url}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30) as resp:
            raw = resp.read(1500000)
            r.update(estado=resp.status, tipo=resp.headers.get("Content-Type", ""),
                     compresion=resp.headers.get("Content-Encoding", ""), bytes=len(raw))
            c = raw.decode("utf-8", "replace")
            if modo.startswith("texto:"):
                t = texto(c); i = max(t.find(modo[6:]), 0); r["muestra"] = t[i:i + 3500]
            else:
                r["muestra"] = c[:7000]
    except urllib.error.HTTPError as e:
        r.update(estado=e.code, error=str(e))
    except Exception as e:  # noqa: BLE001
        r.update(estado=0, error=str(e))
    print("%s -> %s %s %s" % (nombre, r.get("estado"), r.get("tipo", ""), r.get("error", "")))
    out.append(r)
os.makedirs("data", exist_ok=True)
with open("data/pruebas_fuentes.json", "w", encoding="utf-8") as f:
    json.dump({"generado_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "pruebas": out}, f, ensure_ascii=False, indent=1)
