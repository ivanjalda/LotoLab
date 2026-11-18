#!/usr/bin/env python3
"""LotoLab - prueba de fuentes oficiales (bote, ganadores, premios). Solo libreria estandar.
No inventa nada: solo descarga y guarda lo que responde cada direccion oficial."""
import json, os, urllib.request, urllib.error
from datetime import datetime, timedelta, timezone

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36",
      "Accept": "application/json,text/csv,text/html,*/*"}
hoy = datetime.now(timezone.utc)
f1 = (hoy - timedelta(days=21)).strftime("%Y%m%d")
f2 = hoy.strftime("%Y%m%d")
ES = "https://www.loteriasyapuestas.es/servicios/"
PRUEBAS = []
for gid, nombre in [("LAPR", "La Primitiva"), ("EMIL", "Euromillones"), ("BONO", "Bonoloto"),
                    ("ELGR", "El Gordo"), ("EDMS", "EuroDreams"), ("LOTU", "Lototurf")]:
    PRUEBAS.append(("ES " + nombre + " (buscador)",
                    ES + "buscadorSorteos?game_id=%s&celebrados=true&fechaInicioInclusiva=%s&fechaFinInclusiva=%s" % (gid, f1, f2)))
PRUEBAS += [
    ("ES Euromillones (resultados por fecha)", ES + "resultados?game_id=EMIL&fecha_sorteo=" + f2),
    ("ES ultimos sorteos (pagina)", "https://www.loteriasyapuestas.es/es/resultados-ultimos-sorteos"),
    ("US Powerball bote (powerball.com)", "https://www.powerball.com/api/v1/estimates/powerball?_format=json"),
    ("US Powerball sorteos (powerball.com)", "https://www.powerball.com/api/v1/numbers/powerball/recent?_format=json"),
    ("US Mega Millions (megamillions.com)", "https://www.megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData"),
    ("US Texas Mega Millions CSV", "https://www.texaslottery.com/export/sites/lottery/Games/Mega_Millions/Winning_Numbers/megamillions.csv"),
    ("US Texas Powerball CSV", "https://www.texaslottery.com/export/sites/lottery/Games/Powerball/Winning_Numbers/powerball.csv"),
    ("US Texas Mega Millions (pagina)", "https://www.texaslottery.com/export/sites/lottery/Games/Mega_Millions/Winning_Numbers/index.html_98045118.html"),
]
out = []
for nombre, url in PRUEBAS:
    r = {"nombre": nombre, "url": url}
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=30) as resp:
            cuerpo = resp.read(400000).decode("utf-8", "replace")
            r.update(estado=resp.status, tipo=resp.headers.get("Content-Type", ""),
                     bytes=len(cuerpo), muestra=cuerpo[:1500])
    except urllib.error.HTTPError as e:
        r.update(estado=e.code, error=str(e))
    except Exception as e:  # noqa: BLE001
        r.update(estado=0, error=str(e))
    print("%s -> %s %s" % (nombre, r.get("estado"), r.get("tipo", r.get("error", ""))))
    out.append(r)
os.makedirs("data", exist_ok=True)
with open("data/pruebas_fuentes.json", "w", encoding="utf-8") as f:
    json.dump({"generado_utc": hoy.strftime("%Y-%m-%dT%H:%M:%SZ"), "pruebas": out}, f, ensure_ascii=False, indent=1)
