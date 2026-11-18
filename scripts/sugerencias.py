#!/usr/bin/env python3
"""LotoLab - combinaciones sugeridas, historial de aciertos y comparacion con el azar.

Solo usa la libreria estandar de Python y los datos reales que ya genera loteria_engine.py.

IMPORTANTE (honestidad): cada sorteo es independiente. Estas combinaciones NO tienen mas
probabilidad de ganar que cualquier otra. Lo unico que hacen es evitar combinaciones muy
raras o muy repetidas por la gente (como todos los numeros del 1 al 31 o numeros seguidos),
para que, si se gana, haya menos riesgo de compartir el premio. Para demostrarlo con datos,
cada semana se guarda una combinacion de control hecha completamente al azar y se compara.
"""
import json
import os
import random
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loteria_engine import DATA_DIR, LOTTERIES, save_json_if_changed  # noqa: E402

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001
    ET = timezone(timedelta(hours=-5))

N_COMBOS = 5            # combinaciones sugeridas por sorteo
BACKTEST_DRAWS = 150    # sorteos pasados usados en la simulacion
MIN_HISTORY = 100       # sorteos minimos antes de poder sugerir
KEEP_LIVE = 400         # cuantos sorteos en vivo se guardan en el historial


# ---------------------------------------------------------------------------
# Reglas
# ---------------------------------------------------------------------------
def is_prize(cfg, m, b):
    return m >= cfg["prize_min_main"] or (b and m >= cfg["prize_min_main_with_bonus"])


def matches(main, bonus, draw_main, draw_bonus):
    return len(set(main) & set(draw_main)), bonus == draw_bonus


# ---------------------------------------------------------------------------
# Perfil del sorteo tipico (solo con sorteos anteriores)
# ---------------------------------------------------------------------------
def profile(draws, cfg):
    k = cfg["main_count"]
    era = [d for d in draws if d[0] >= cfg["main_from"]]
    n = len(era)
    if n < MIN_HISTORY:
        return None
    mitad = cfg["main_max"] // 2
    odd = Counter(sum(x % 2 for x in d[1]) for d in era)
    low = Counter(sum(1 for x in d[1] if x <= mitad) for d in era)
    sums = sorted(sum(d[1]) for d in era)
    return {
        "odd_ok": {i for i in range(k + 1) if odd.get(i, 0) / n >= 0.15},
        "low_ok": {i for i in range(k + 1) if low.get(i, 0) / n >= 0.15},
        "sum_lo": sums[int(round((n - 1) * 0.10))],
        "sum_hi": sums[int(round((n - 1) * 0.90))],
        "mitad": mitad,
        "draws": n,
    }


def too_popular(main, cfg):
    """Patrones que mucha gente juega: todo fechas (<=31), tres seguidos, progresion."""
    s = sorted(main)
    if cfg["main_max"] > 31 and s[-1] <= 31:
        return True
    run = 1
    for a, b in zip(s, s[1:]):
        run = run + 1 if b == a + 1 else 1
        if run >= 3:
            return True
    diffs = {b - a for a, b in zip(s, s[1:])}
    if len(diffs) == 1:
        return True
    return False


def balanced(main, prof, cfg):
    if sum(x % 2 for x in main) not in prof["odd_ok"]:
        return False
    if sum(1 for x in main if x <= prof["mitad"]) not in prof["low_ok"]:
        return False
    return prof["sum_lo"] <= sum(main) <= prof["sum_hi"]


def random_combo(rng, cfg):
    return sorted(rng.sample(range(1, cfg["main_max"] + 1), cfg["main_count"])), rng.randint(1, cfg["bonus_max"])


def suggest(prof, cfg, seed, n=N_COMBOS):
    """n combinaciones equilibradas. Los numeros se eligen al azar dentro de los filtros."""
    rng = random.Random("sug|%s|%s" % (cfg["id"], seed))
    out, tries = [], 0
    while len(out) < n and tries < 20000:
        tries += 1
        main, bonus = random_combo(rng, cfg)
        if too_popular(main, cfg) or not balanced(main, prof, cfg):
            continue
        if any(main == o["main"] for o in out):
            continue
        out.append({"main": main, "bonus": bonus})
    return out


def control(cfg, seed, n=N_COMBOS):
    """n combinaciones totalmente al azar, para comparar."""
    rng = random.Random("ctl|%s|%s" % (cfg["id"], seed))
    out = []
    while len(out) < n:
        main, bonus = random_combo(rng, cfg)
        out.append({"main": main, "bonus": bonus})
    return out


# ---------------------------------------------------------------------------
# Evaluacion
# ---------------------------------------------------------------------------
def score(combos, draw_main, draw_bonus, cfg):
    prizes, tot = 0, 0
    best = 0
    for c in combos:
        m, b = matches(c["main"], c["bonus"], draw_main, draw_bonus)
        tot += m
        best = max(best, m)
        if is_prize(cfg, m, b):
            prizes += 1
    return {"prizes": prizes, "main_matches": tot, "best_main": best}


def backtest(draws, cfg):
    """Simulacion: para cada sorteo pasado, sugiere solo con lo anterior y compara con el azar."""
    era_idx = [i for i, d in enumerate(draws) if d[0] >= cfg["main_from"]]
    if len(era_idx) < MIN_HISTORY + 20:
        return None
    start = max(era_idx[0] + MIN_HISTORY, len(draws) - BACKTEST_DRAWS)
    s_p = s_m = c_p = c_m = n = 0
    for i in range(start, len(draws)):
        before = draws[:i]
        prof = profile(before, cfg)
        if not prof:
            continue
        d = draws[i]
        sug = suggest(prof, cfg, d[0])
        if len(sug) < N_COMBOS:
            continue
        ctl = control(cfg, d[0])
        a = score(sug, d[1], d[2], cfg)
        b = score(ctl, d[1], d[2], cfg)
        s_p += a["prizes"]; s_m += a["main_matches"]
        c_p += b["prizes"]; c_m += b["main_matches"]
        n += 1
    if not n:
        return None
    tickets = n * N_COMBOS
    return {
        "draws": n, "combos_per_draw": N_COMBOS, "tickets": tickets,
        "suggested": {"prizes": s_p, "avg_main_matches": round(s_m / tickets, 3)},
        "random": {"prizes": c_p, "avg_main_matches": round(c_m / tickets, 3)},
    }


# ---------------------------------------------------------------------------
# Calendario
# ---------------------------------------------------------------------------
def next_draw_date(cfg, now_et):
    """Proxima fecha de sorteo (hora de Nueva York) que aun no ha empezado."""
    hhmm = cfg.get("draw_time_et") or "22:00"      # si no se conoce la hora, se usa una cota segura
    h, m = [int(x) for x in hhmm.split(":")]
    for add in range(0, 9):
        day = (now_et + timedelta(days=add)).date()
        if day.weekday() not in cfg["draw_days"]:
            continue
        start = datetime(day.year, day.month, day.day, h, m, tzinfo=now_et.tzinfo)
        if start > now_et + timedelta(minutes=30):
            return day.isoformat()
    return None


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------
def read_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def run(now_utc=None):
    now_utc = now_utc or datetime.now(timezone.utc)
    now_et = now_utc.astimezone(ET)
    hist = read_json(os.path.join(DATA_DIR, "historial.json"), {"live": []})
    live = hist.get("live", [])
    sug_out = {"generated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), "lotteries": {}}
    summary = {}

    for cfg in LOTTERIES:
        j = read_json(os.path.join(DATA_DIR, cfg["id"] + ".json"), None)
        if not j or not j.get("draws"):
            continue
        draws = j["draws"]
        by_date = {d[0]: d for d in draws}

        # 1) Evaluar sugerencias guardadas antes de cada sorteo con el resultado real
        for e in live:
            if e["lottery"] == cfg["id"] and e.get("result") is None and e["for_draw"] in by_date:
                d = by_date[e["for_draw"]]
                e["result"] = {
                    "main": d[1], "bonus": d[2],
                    "suggested": score(e["combos"], d[1], d[2], cfg),
                    "random": score(e["control"], d[1], d[2], cfg),
                }

        # 2) Guardar las sugerencias del proximo sorteo (una sola vez, antes de que ocurra)
        target = next_draw_date(cfg, now_et)
        prof = profile(draws, cfg)
        if target and prof:
            ex = [e for e in live if e["lottery"] == cfg["id"] and e["for_draw"] == target]
            if not ex:
                sug = suggest(prof, cfg, target)
                if len(sug) == N_COMBOS:
                    live.append({
                        "lottery": cfg["id"], "for_draw": target,
                        "created_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "based_on_draws": prof["draws"],
                        "combos": sug, "control": control(cfg, target), "result": None,
                    })
            cur = [e for e in live if e["lottery"] == cfg["id"] and e["for_draw"] == target]
            if cur:
                sug_out["lotteries"][cfg["id"]] = {
                    "name": cfg["name"], "for_draw": target, "combos": cur[0]["combos"],
                    "created_utc": cur[0]["created_utc"], "based_on_draws": cur[0]["based_on_draws"],
                    "criteria": {"odd_ok": sorted(prof["odd_ok"]), "low_ok": sorted(prof["low_ok"]),
                                 "sum_lo": prof["sum_lo"], "sum_hi": prof["sum_hi"]},
                }

        # 3) Simulacion con sorteos pasados (no cambia salvo con nuevos sorteos)
        bt = backtest(draws, cfg)
        ev = [e for e in live if e["lottery"] == cfg["id"] and e.get("result")]
        lv = {"draws": len(ev), "tickets": len(ev) * N_COMBOS,
              "suggested_prizes": sum(e["result"]["suggested"]["prizes"] for e in ev),
              "random_prizes": sum(e["result"]["random"]["prizes"] for e in ev)}
        summary[cfg["id"]] = {"name": cfg["name"], "backtest": bt, "live": lv}

    live.sort(key=lambda e: (e["for_draw"], e["lottery"]))
    live = live[-KEEP_LIVE:]
    hist_out = {
        "generated_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "combos_per_draw": N_COMBOS,
        "method": ("Combinaciones equilibradas: numeros elegidos al azar dentro de filtros "
                   "(impares/pares y bajos/altos habituales, suma habitual, sin numeros seguidos ni todo fechas). "
                   "No aumentan la probabilidad de ganar."),
        "summary": summary,
        "live": live,
    }
    save_json_if_changed(os.path.join(DATA_DIR, "sugerencias.json"), sug_out)
    save_json_if_changed(os.path.join(DATA_DIR, "historial.json"), hist_out)
    return sug_out, hist_out


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    sug, hist = run()
    for k, v in hist["summary"].items():
        bt = v["backtest"]
        if bt:
            print("OK  %s: simulacion %d sorteos: sugeridas %d premios, azar %d premios" % (
                k, bt["draws"], bt["suggested"]["prizes"], bt["random"]["prizes"]))
    print("Sugerencias para:", ", ".join("%s %s" % (k, v["for_draw"]) for k, v in sug["lotteries"].items()))


if __name__ == "__main__":
    main()
