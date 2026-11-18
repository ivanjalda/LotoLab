#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LotoLab - motor de datos.

Descarga los resultados oficiales publicados como datos abiertos,
calcula estadisticas reales (sin inventar nada) y guarda archivos JSON
en la carpeta data/. Usa SOLO la libreria estandar de Python.

Reglas del motor:
- Si una fuente falla, se conserva el ultimo JSON bueno (no se borra nada).
- Las estadisticas solo usan sorteos del formato de bolas actual, porque
  los rangos de numeros cambian con los anos y mezclarlos falsea la cuenta.
- Todo aviso de fiabilidad se guarda en el campo "warnings" de cada JSON.
"""

import csv
import io
import json
import os
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
USER_AGENT = "LotoLab/1.0 (+https://github.com/ivanjalda/LotoLab)"

# ---------------------------------------------------------------------------
# Loterias configuradas.
# main_from / bonus_from = fecha desde la que rige el formato de bolas actual.
# Si un numero supera el maximo esperado, el motor lo avisa en "warnings".
# ---------------------------------------------------------------------------
LOTTERIES = [
    {
        "id": "us_powerball",
        "country": "us",
        "name": "Powerball",
        "source_name": "New York State Open Data (data.ny.gov)",
        "source_page": "https://data.ny.gov/d/d6yy-54nr",
        "url": "https://data.ny.gov/api/views/d6yy-54nr/rows.csv?accessType=DOWNLOAD",
        "main_count": 5,
        "main_max": 69,
        "bonus_name": "Powerball",
        "bonus_max": 26,
        "bonus_column": None,
        "main_from": "2015-10-07",
        "bonus_from": "2015-10-07",
        "prize_min_main": 3, "prize_min_main_with_bonus": 0,   # premio con 3+ numeros, o solo con la bola extra
        "draw_days": [0, 2, 5], "draw_time_et": "22:59",       # lunes, miercoles, sabado (powerball.com)
        "price_usd": 2,
        "prize_source": "powerball.com (tabla de premios)",
        "prize_note": "Premios en dolares. El premio gordo cambia en cada sorteo.",
        "prizes": {"5-0": 1000000, "4-1": 50000, "4-0": 100, "3-1": 100, "3-0": 7, "2-1": 7, "1-1": 4, "0-1": 4},
    },
    {
        "id": "us_megamillions",
        "country": "us",
        "name": "Mega Millions",
        "source_name": "New York State Open Data (data.ny.gov)",
        "source_page": "https://data.ny.gov/d/5xaw-6ayf",
        "url": "https://data.ny.gov/api/views/5xaw-6ayf/rows.csv?accessType=DOWNLOAD",
        "main_count": 5,
        "main_max": 70,
        "bonus_name": "Mega Ball",
        "bonus_max": 24,
        "bonus_column": "mega_ball",
        "main_from": "2017-10-31",
        "bonus_from": "2025-04-08",
        "prize_min_main": 3, "prize_min_main_with_bonus": 0,
        "draw_days": [1, 4], "draw_time_et": "23:00",        # martes y viernes, 11 p.m. ET (NBC New York)
        "price_usd": 5,
        "prize_source": "North Dakota Lottery (lottery.nd.gov)",
        "prize_note": "Premio base: cada jugada de 5 $ lleva un multiplicador de x2 a x10 en los premios menores. El premio gordo cambia en cada sorteo.",
        "prizes": {"5-0": 1000000, "4-1": 10000, "4-0": 500, "3-1": 200, "3-0": 10, "2-1": 10, "1-1": 7, "0-1": 5},
    },
    {
        # Sustituye a Cash4Life (retirado en 2026). Primer sorteo: 22/02/2026. Sorteo todas las noches.
        "id": "us_millionaire",
        "country": "us",
        "name": "Millionaire for Life",
        "source_name": "New York State Open Data (data.ny.gov)",
        "source_page": "https://data.ny.gov/d/a4w9-a3tp",
        "url": "https://data.ny.gov/api/views/a4w9-a3tp/rows.csv?accessType=DOWNLOAD",
        "main_count": 5,
        "main_max": 58,
        "bonus_name": "Millionaire Ball",
        "bonus_max": 5,
        "bonus_column": None,
        "main_from": "2026-02-22",
        "bonus_from": "2026-02-22",
        "prize_min_main": 2, "prize_min_main_with_bonus": 1,   # con 2 numeros, o 1 numero + bola
        "draw_days": [0, 1, 2, 3, 4, 5, 6], "draw_time_et": "23:15",   # todas las noches
        "price_usd": 5,
        "prize_source": "Wikipedia (Millionaire for Life)",
        "prize_note": "Los dos premios mayores se pagan por vida; aqui va la opcion de pago unico (18.000.000 $ y 2.200.000 $).",
        "prizes": {"5-1": 18000000, "5-0": 2200000, "4-1": 7500, "4-0": 500, "3-1": 250, "3-0": 50, "2-1": 25, "2-0": 8, "1-1": 8},
    },
]


# ---------------------------------------------------------------------------
# Descarga y lectura
# ---------------------------------------------------------------------------
def download(url):
    ultimo_error = None
    for intento in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read().decode("utf-8-sig")
        except Exception as e:  # noqa: BLE001
            ultimo_error = e
            time.sleep(5 * (intento + 1))
    raise RuntimeError("No se pudo descargar la fuente: %s" % ultimo_error)


def norm_key(k):
    return k.strip().lower().replace(" ", "_")


def parse_date(s):
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError("fecha no reconocida: " + s)


def parse_rows(text, cfg):
    reader = csv.DictReader(io.StringIO(text))
    draws = []
    bad = 0
    for raw in reader:
        row = {norm_key(k): (v or "").strip() for k, v in raw.items() if k}
        try:
            d = parse_date(row.get("draw_date", ""))
            nums = [int(x) for x in row.get("winning_numbers", "").replace(",", " ").split()]
            col = cfg.get("bonus_column")
            if not col and len(nums) == cfg["main_count"]:
                # Si la fuente trae la bola extra en una columna aparte, la buscamos por su nombre
                for k in row:
                    if "ball" in k and row[k]:
                        col = k
                        break
            if col and row.get(col):
                bonus = int(row[col])
                main = nums
            elif len(nums) == cfg["main_count"] + 1:
                main, bonus = nums[:-1], nums[-1]
            else:
                raise ValueError("cantidad de numeros inesperada")
            if len(main) != cfg["main_count"]:
                raise ValueError("cantidad de numeros principales inesperada")
            draws.append({"date": d.isoformat(), "main": sorted(main), "bonus": bonus})
        except Exception:  # noqa: BLE001
            bad += 1
    draws.sort(key=lambda x: x["date"])
    return draws, bad


def check_source(draws, bad):
    total = len(draws) + bad
    if len(draws) < 100:
        raise RuntimeError("La fuente trae muy pocos sorteos (%d). No se actualiza." % len(draws))
    if total and bad / total > 0.05:
        raise RuntimeError("Demasiadas filas ilegibles (%d de %d). No se actualiza." % (bad, total))


# ---------------------------------------------------------------------------
# Estadisticas
# ---------------------------------------------------------------------------
def percentile(sorted_vals, p):
    idx = int(round((len(sorted_vals) - 1) * p / 100.0))
    return sorted_vals[idx]


def number_stats(lists, expected_max, picks):
    """Frecuencia y retraso de cada numero en una lista de sorteos."""
    n = len(lists)
    counts = Counter()
    last_seen = {}
    max_gap = {}
    for i, nums in enumerate(lists):
        for x in nums:
            prev = last_seen.get(x, -1)
            gap = i - prev - 1
            if gap > max_gap.get(x, 0):
                max_gap[x] = gap
            last_seen[x] = i
            counts[x] += 1
    top = max([expected_max] + list(counts))
    esperado_pct = round(100.0 * picks / expected_max, 2)
    rows = []
    for x in range(1, top + 1):
        c = counts.get(x, 0)
        delay = (n - 1 - last_seen[x]) if x in last_seen else n
        rows.append({
            "n": x,
            "count": c,
            "pct_draws": round(100.0 * c / n, 2) if n else 0,
            "delay": delay,
            "max_delay": max(max_gap.get(x, 0), delay),
        })
    return {"draws": n, "expected_pct_draws": esperado_pct, "numbers": rows}


def build(cfg, draws, bad):
    main_draws = [d for d in draws if d["date"] >= cfg["main_from"]]
    bonus_draws = [d for d in draws if d["date"] >= cfg["bonus_from"]]
    if not main_draws or not bonus_draws:
        raise RuntimeError("No hay sorteos del formato actual para calcular.")

    warnings = []
    if len(main_draws) < 300:
        warnings.append("Muestra pequena para los numeros principales (%d sorteos): usar con cautela." % len(main_draws))
    if len(bonus_draws) < 300:
        warnings.append("Muestra pequena para %s (%d sorteos): usar con cautela." % (cfg["bonus_name"], len(bonus_draws)))
    if max(max(d["main"]) for d in main_draws) > cfg["main_max"]:
        warnings.append("Hay numeros principales por encima de %d: revisar la fecha del cambio de reglas." % cfg["main_max"])
    if max(d["bonus"] for d in bonus_draws) > cfg["bonus_max"]:
        warnings.append("Hay %s por encima de %d: revisar la fecha del cambio de reglas." % (cfg["bonus_name"], cfg["bonus_max"]))
    if bad:
        warnings.append("%d filas de la fuente no se pudieron leer y se ignoraron." % bad)

    main_lists = [d["main"] for d in main_draws]
    bonus_lists = [[d["bonus"]] for d in bonus_draws]
    k = cfg["main_count"]
    nd = len(main_draws)

    odd_counter = Counter(sum(x % 2 for x in d["main"]) for d in main_draws)
    odd_even = [{"odd": i, "even": k - i, "draws": odd_counter.get(i, 0),
                 "pct": round(100.0 * odd_counter.get(i, 0) / nd, 2)} for i in range(k + 1)]

    mitad = cfg["main_max"] // 2
    low_counter = Counter(sum(1 for x in d["main"] if x <= mitad) for d in main_draws)
    low_high = [{"low": i, "high": k - i, "draws": low_counter.get(i, 0),
                 "pct": round(100.0 * low_counter.get(i, 0) / nd, 2)} for i in range(k + 1)]

    digit_counter = Counter(x % 10 for d in main_draws for x in d["main"])
    total_nums = nd * k
    last_digit = [{"digit": i, "count": digit_counter.get(i, 0),
                   "pct": round(100.0 * digit_counter.get(i, 0) / total_nums, 2)} for i in range(10)]

    sums = sorted(sum(d["main"]) for d in main_draws)
    sum_stats = {
        "mean": round(sum(sums) / len(sums), 1),
        "min": sums[0],
        "max": sums[-1],
        "p10": percentile(sums, 10),
        "p90": percentile(sums, 90),
    }

    ultimos = [{"date": d["date"], "main": d["main"], "bonus": d["bonus"]} for d in reversed(draws[-10:])]

    return {
        "lottery": cfg["id"],
        "country": cfg["country"],
        "name": cfg["name"],
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"name": cfg["source_name"], "page": cfg["source_page"]},
        "rules": {
            "main_count": k,
            "main_max": cfg["main_max"],
            "main_from": cfg["main_from"],
            "bonus_name": cfg["bonus_name"],
            "bonus_max": cfg["bonus_max"],
            "bonus_from": cfg["bonus_from"],
            "prize_min_main": cfg["prize_min_main"],
            "prize_min_main_with_bonus": cfg["prize_min_main_with_bonus"],
            "draw_days": cfg["draw_days"],
            "draw_time_et": cfg["draw_time_et"],
            "price_usd": cfg.get("price_usd"),
            "prizes": cfg.get("prizes"),
            "prize_source": cfg.get("prize_source"),
            "prize_note": cfg.get("prize_note"),
        },
        "total_draws_in_source": len(draws),
        "first_draw": draws[0]["date"],
        "last_draw": draws[-1]["date"],
        "draws_used_main": len(main_draws),
        "draws_used_bonus": len(bonus_draws),
        "last_draws": ultimos,
        "main": number_stats(main_lists, cfg["main_max"], k),
        "bonus": number_stats(bonus_lists, cfg["bonus_max"], 1),
        "odd_even": odd_even,
        "low_high": low_high,
        "last_digit": last_digit,
        "sum": sum_stats,
        "warnings": warnings,
        "note": "Cada sorteo es independiente. Estas cifras describen el pasado y no predicen resultados.",
        "draws": [[d["date"], d["main"], d["bonus"]] for d in draws],
    }


# ---------------------------------------------------------------------------
# Guardado
# ---------------------------------------------------------------------------
def save_json_if_changed(path, obj, ignore_keys=("generated_utc", "checked_utc")):
    """Guarda el JSON solo si cambia algo mas que la hora."""
    def limpio(o):
        return {k: v for k, v in o.items() if k not in ignore_keys}

    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                viejo = json.load(f)
            if limpio(viejo) == limpio(obj):
                return False
        except Exception:  # noqa: BLE001
            pass
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)
    return True


def build_index():
    items = []
    for cfg in LOTTERIES:
        path = os.path.join(DATA_DIR, cfg["id"] + ".json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
            items.append({
                "id": cfg["id"],
                "country": cfg["country"],
                "name": cfg["name"],
                "last_draw": j.get("last_draw"),
                "draws_used_main": j.get("draws_used_main"),
                "file": cfg["id"] + ".json",
            })
        except Exception:  # noqa: BLE001
            pass
    return {"lotteries": items}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    status = {}
    ok_count = 0
    for cfg in LOTTERIES:
        try:
            texto = download(cfg["url"])
            draws, bad = parse_rows(texto, cfg)
            check_source(draws, bad)
            resultado = build(cfg, draws, bad)
            cambio = save_json_if_changed(os.path.join(DATA_DIR, cfg["id"] + ".json"), resultado)
            status[cfg["id"]] = {"ok": True, "last_draw": draws[-1]["date"],
                                 "draws": len(draws), "warnings": resultado["warnings"]}
            ok_count += 1
            print("OK  %s: %d sorteos, ultimo %s%s" % (
                cfg["id"], len(draws), draws[-1]["date"], "" if cambio else " (sin cambios)"))
        except Exception as e:  # noqa: BLE001
            status[cfg["id"]] = {"ok": False, "error": str(e)}
            print("ERROR %s: %s" % (cfg["id"], e), file=sys.stderr)

    status["checked_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    save_json_if_changed(os.path.join(DATA_DIR, "status.json"), status)
    save_json_if_changed(os.path.join(DATA_DIR, "index.json"), build_index())
    return 0 if ok_count else 1


if __name__ == "__main__":
    sys.exit(main())
