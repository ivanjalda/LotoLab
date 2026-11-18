#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LotoLab - botes (premio gordo) y ganadores del bote. Solo libreria estandar.

Fuente real y oficial: Mega Millions (megamillions.com, archivo de datos de su propia web).
Cada vez que se ejecuta guarda el bote del ultimo sorteo, si hubo ganador o se acumulo,
y el bote del proximo sorteo. Con los dias se va formando el historial (empieza el dia
que se pone en marcha: no se inventa nada del pasado).

Powerball: powerball.com todavia no se ha podido leer de forma fiable, asi que no se
muestra ningun bote de Powerball hasta tener una fuente comprobada.
"""
import json
import os
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loteria_engine import DATA_DIR, save_json_if_changed  # noqa: E402

MM_URL = "https://www.megamillions.com/cmspages/utilservice.asmx/GetLatestDrawData"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/124 Safari/537.36", "Accept": "*/*",
      "Accept-Encoding": "identity"}
KEEP = 400


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_mm(body):
    """Devuelve (ultimo, proximo) a partir del archivo oficial de Mega Millions, o lanza error."""
    data = json.loads(ET.fromstring(body).text)
    jp = data["Jackpot"]
    last_date = jp["PlayDate"][:10]
    last = {"date": last_date, "jackpot": int(jp["CurrentPrizePool"]), "cash": int(jp["CurrentCashValue"]),
            "winners": int(jp["Winners"]), "verified": bool(jp.get("Verified"))}
    nxt = {"date": data["NextDrawingDate"][:10], "jackpot": int(jp["NextPrizePool"]), "cash": int(jp["NextCashValue"])}
    if last["jackpot"] <= 0 or nxt["jackpot"] <= 0 or nxt["date"] <= last["date"]:
        raise ValueError("datos de bote incoherentes")
    return last, nxt


def merge_history(hist, last, nxt):
    by = {h["date"]: h for h in hist}
    by[last["date"]] = dict(last)
    cur = by.get(nxt["date"], {})
    if "winners" not in cur:                       # el proximo sorteo aun no se ha jugado
        by[nxt["date"]] = {"date": nxt["date"], "jackpot": nxt["jackpot"], "cash": nxt["cash"],
                           "winners": None, "verified": False}
    out = sorted(by.values(), key=lambda h: h["date"])
    return out[-KEEP:]


def run(fetch_fn=fetch, now=None):
    now = now or datetime.now(timezone.utc)
    path = os.path.join(DATA_DIR, "botes.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            cur = json.load(f)
    except Exception:  # noqa: BLE001
        cur = {"lotteries": {}}
    out = {"generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "lotteries": cur.get("lotteries", {})}
    estado = {}
    try:
        last, nxt = parse_mm(fetch_fn(MM_URL))
        old = out["lotteries"].get("us_megamillions", {})
        out["lotteries"]["us_megamillions"] = {
            "source": "megamillions.com",
            "source_url": "https://www.megamillions.com/",
            "last": last, "next": nxt,
            "history": merge_history(old.get("history", []), last, nxt),
        }
        estado["us_megamillions"] = "ok"
    except Exception as e:  # noqa: BLE001
        estado["us_megamillions"] = "error: %s" % e
    os.makedirs(DATA_DIR, exist_ok=True)
    save_json_if_changed(path, out)
    return out, estado


def main():
    out, estado = run()
    for k, v in estado.items():
        print(("OK  " if v == "ok" else "ERR ") + k + ": " + v)
    return 0


if __name__ == "__main__":
    main()
