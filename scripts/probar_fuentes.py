#!/usr/bin/env python3
"""LotoLab - prueba 2 de fuentes oficiales. Solo libreria estandar. No inventa nada."""
import gzip, json, os, re, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

hoy = datetime.now(timezone.utc)
f1 = (hoy - timedelta(days=21)).strftime("%Y%m%d")
f2 = hoy.strftime("%Y%m%d")
ES = "https://www.loteriasyapuestas.es/"
NAV = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
       "Accept": "application/json, text/plain, */*", "Accept-Language": "es-ES,es;q=0.9", "Accept-Encoding": "gzip",
       "Referer": ES + "es/resultados-ultimos-sorteos"}
PB = {"User-Agent": NAV["User-Agent"], "Accept": "application/json, text/html, */*", "Accept-Encoding": "gzip"}

def bajar(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read(1500000)
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return resp.status, resp.headers.get("Content-Type", ""), raw.decode("utf-8", "replace")

def texto(html):
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html))

PRUEBAS = [
    ("US Powerball bote (json)", "https://www.powerball.com/api/v1/estimates/powerball?_format=json", PB, "json"),
    ("US Powerball resultados (pagina, ganadores por categoria)", "https://www.powerball.com/previous-results?gc=powerball", PB, "texto:Match 5"),
    ("US Mega Millions (json completo)", "https://www.megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData", PB, "todo"),
    ("US Texas Mega Millions (tabla de bote)", "https://www.texaslottery.com/export/sites/lottery/Games/Mega_Millions/Winning_Numbers/index.html_98045118.html", PB, "texto:Draw Date"),
]
for gid, nombre in [("LAPR", "La Primitiva"), ("EMIL", "Euromillones"), ("BONO", "Bonoloto"), ("ELGR", "El Gordo")]:
    u = ES + "servicios/buscadorSorteos?game_id=%s&celebrados=true&fechaInicioInclusiva=%s&fechaFinInclusiva=%s" % (gid, f1, f2)
    PRUEBAS.append(("ES %s (navegador completo)" % nombre, u, NAV, "todo"))
PRUEBAS += [
    ("ES Euromillones (resultados por fecha, ultimo martes/viernes)", ES + "servicios/resultados?game_id=EMIL&fecha_sorteo=" + f2, NAV, "todo"),
    ("ES inicio", ES + "es", NAV, "texto:"),
    ("ES ficheros para webs (euromillones.html)", ES + "es/resultados-ultimos-sorteos", {**NAV, "Accept": "text/html"}, "texto:"),
    ("ES alternativo juegos.loteriasyapuestas", "https://juegos.loteriasyapuestas.es/", NAV, "texto:"),
]
out = []
for nombre, url, hdr, modo in PRUEBAS:
    r = {"nombre": nombre, "url": url}
    try:
        st, tipo, cuerpo = bajar(url, hdr)
        if modo.startswith("texto:"):
            t = texto(cuerpo); k = modo[6:]
            i = t.find(k) if k else 0
            muestra = t[max(i, 0):max(i, 0) + 3500]
        elif modo == "json":
            muestra = cuerpo[:6000]
        else:
            muestra = cuerpo[:6000]
        r.update(estado=st, tipo=tipo, bytes=len(cuerpo), muestra=muestra)
    except urllib.error.HTTPError as e:
        r.update(estado=e.code, error=str(e))
    except Exception as e:  # noqa: BLE001
        r.update(estado=0, error=str(e))
    print("%s -> %s %s" % (nombre, r.get("estado"), r.get("tipo", r.get("error", ""))))
    out.append(r)
os.makedirs("data", exist_ok=True)
with open("data/pruebas_fuentes.json", "w", encoding="utf-8") as f:
    json.dump({"generado_utc": hoy.strftime("%Y-%m-%dT%H:%M:%SZ"), "pruebas": out}, f, ensure_ascii=False, indent=1)
