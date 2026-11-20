#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LotoLab - motor de datos de ESPANA (Primitiva, Bonoloto, Euromillones, El Gordo,
EuroDreams y Lototurf).

Solo usa la libreria estandar de Python. Nunca inventa datos:
- Para cada loteria prueba varias fuentes en orden (primero la oficial). Se usa la primera
  que devuelve datos validos y recientes. Si ninguna responde, se conserva el ultimo JSON bueno.
- Cada sorteo se valida (cantidad de numeros, rangos, sin repetidos) antes de usarse.
- El motor APRENDE de si mismo: cada vez que corre, compara 5 estrategias con sorteos pasados
  (sin mirar el futuro), elige la que mejor ha funcionado, genera con ella las combinaciones del
  proximo sorteo y, cuando llega el resultado real, anota como le fue frente a una combinacion al azar.

IMPORTANTE (honestidad): cada sorteo es independiente. Ninguna estrategia puede mejorar la
probabilidad de ganar; las diferencias entre estrategias son ruido estadistico y asi se avisa.
"""
import csv
import html as htmllib
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loteria_engine import DATA_DIR, save_json_if_changed, percentile  # noqa: E402

UA = "LotoLab/1.0 (+https://github.com/ivanjalda/LotoLab)"
UA_BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
API = "https://www.loteriasyapuestas.es/servicios/buscadorSorteos"
N_COMBOS = 5
BACKTEST_DRAWS = 300
MIN_HISTORY = 150
KEEP_DRAWS = 400
KEEP_LIVE = 300


def grp(es, en, lo, hi, idx):
    return {"es": es, "en": en, "lo": lo, "hi": hi, "idx": idx}


ES = [
    {"id": "es_primitiva", "name": "La Primitiva", "api": "LAPR", "k": 6, "n": 49, "era": "2013-01-01", "min": 300,
     "groups": [grp("Complementario", "Bonus", 1, 49, [0]), grp("Reintegro", "Refund", 0, 9, [1])], "days": [0, 3, 5]},
    {"id": "es_bonoloto", "name": "Bonoloto", "api": "BONO", "k": 6, "n": 49, "era": "2013-01-01", "min": 300,
     "groups": [grp("Complementario", "Bonus", 1, 49, [0]), grp("Reintegro", "Refund", 0, 9, [1])], "days": [0, 1, 2, 3, 4, 5]},
    {"id": "es_euromillones", "name": "Euromillones", "api": "EMIL", "k": 5, "n": 50, "era": "2016-09-27", "min": 300,
     "groups": [grp("Estrellas", "Stars", 1, 12, [0, 1])], "days": [1, 4]},
    {"id": "es_elgordo", "name": "El Gordo de la Primitiva", "api": "ELGR", "k": 5, "n": 54, "era": "2013-01-01", "min": 300,
     "groups": [grp("Número clave", "Key number", 0, 9, [0])], "days": [6]},
    {"id": "es_eurodreams", "name": "EuroDreams", "api": "EDMS", "k": 6, "n": 40, "era": "2023-01-01", "min": 60,
     "groups": [grp("Número Sueño", "Dream number", 1, 5, [0])], "days": [0, 3]},
    {"id": "es_lototurf", "name": "Lototurf", "api": "LOTU", "k": 6, "n": 31, "era": "2013-01-01", "min": 300,
     "groups": [grp("Caballo", "Horse", 1, 12, [0]), grp("Reintegro", "Refund", 0, 9, [1])], "days": None},
]


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------
def fetch(url, timeout=60, ua=None, referer=None):
    ultimo = None
    for intento in range(2):
        try:
            hdr = {"User-Agent": ua or UA, "Accept": "*/*", "Accept-Language": "es,en;q=0.8"}
            if referer:
                hdr["Referer"] = referer
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8-sig", errors="replace")
        except Exception as e:  # noqa: BLE001
            ultimo = e
            time.sleep(3)
    raise RuntimeError(str(ultimo))


def to_num(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return x
    s = str(x).strip().replace("€", "").replace(" ", "")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------
def parse_api_item(cfg, item):
    """Un sorteo del servicio oficial de Loterias y Apuestas del Estado."""
    f = str(item.get("fecha_sorteo") or "")[:10]
    d = datetime.strptime(f, "%Y-%m-%d").date().isoformat()
    ints = [int(x) for x in re.findall(r"\d+", str(item.get("combinacion") or ""))]
    ne = sum(len(g["idx"]) for g in cfg["groups"])
    if len(ints) != cfg["k"] + ne:
        raise ValueError("combinacion con %d numeros" % len(ints))
    out = {"date": d, "main": sorted(ints[:cfg["k"]]), "extras": ints[cfg["k"]:]}
    if cfg["id"] == "es_euromillones":
        out["extras"] = sorted(out["extras"])
    bote = to_num(item.get("premio_bote"))
    if bote:
        out["bote"] = bote
    esc = []
    for e in (item.get("escrutinio") or [])[:20]:
        if not isinstance(e, dict):
            continue
        tipo = e.get("tipo") or e.get("categoria") or e.get("nombre")
        win = None
        for key in ("numero", "acertantes", "ganadores", "numeroGanadores"):
            if e.get(key) is not None:
                win = to_num(e.get(key))
                break
        prem = to_num(e.get("premio") if e.get("premio") is not None else e.get("importe"))
        if tipo and (win is not None or prem is not None):
            esc.append({"tipo": str(tipo), "winners": win, "prize": prem})
    if esc:
        out["escrutinio"] = esc
    return out


def src_api(cfg, today):
    draws, bad, total = [], 0, 0
    for y in range(int(cfg["era"][:4]), today.year + 1):
        fin = today.strftime("%Y%m%d") if y == today.year else "%d1231" % y
        url = "%s?game_id=%s&celebrados=true&fechaInicioInclusiva=%d0101&fechaFinInclusiva=%s" % (API, cfg["api"], y, fin)
        try:
            body = fetch(url)
        except RuntimeError:
            if y != int(cfg["era"][:4]):
                raise
            body = fetch(url, ua=UA_BROWSER, referer="https://www.loteriasyapuestas.es/es/resultados")
        try:
            data = json.loads(body)
        except ValueError:
            raise RuntimeError("respuesta no es JSON: %r" % body[:120])
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        for item in data:
            total += 1
            try:
                draws.append(parse_api_item(cfg, item))
            except Exception:  # noqa: BLE001
                bad += 1
    if total and bad > total * 0.2:
        raise RuntimeError("demasiados sorteos ilegibles (%d de %d)" % (bad, total))
    return draws, {"name": "Loterías y Apuestas del Estado (oficial)", "page": "https://www.loteriasyapuestas.es/", "official": True}


def src_azar_primitiva(cfg, today):
    draws = {}
    txt = fetch("https://www.azarysuerte.es/downloads/Azar_y_Suerte_Hist_Primitiva.csv")
    for row in csv.reader(io.StringIO(txt), delimiter=";"):
        if len(row) < 9 or not re.match(r"\d\d/\d\d/\d{4}", row[0].strip()):
            continue
        try:
            d = datetime.strptime(row[0].strip(), "%d/%m/%Y").date().isoformat()
            draws[d] = {"date": d, "main": sorted(int(x) for x in row[1:7]), "extras": [int(row[7]), int(row[8])]}
        except ValueError:
            continue
    try:  # ultimos 50 sorteos de la pagina (mas al dia que el CSV)
        page = fetch("https://www.azarysuerte.es/HistoricoPR.php")
        text = htmllib.unescape(re.sub(r"<[^>]+>", " ", page))
        for m in re.finditer(r"(\d\d)/(\d\d)/(\d{4})\s+((?:\d{1,2}\s+){6})C\s*(\d{1,2})\s*R\s*(\d)", text):
            d = "%s-%s-%s" % (m.group(3), m.group(2), m.group(1))
            draws[d] = {"date": d, "main": sorted(int(x) for x in m.group(4).split()), "extras": [int(m.group(5)), int(m.group(6))]}
    except Exception:  # noqa: BLE001
        pass
    if not draws:
        raise RuntimeError("sin datos legibles")
    return list(draws.values()), {"name": "Azar y Suerte (copia de los resultados oficiales)", "page": "https://www.azarysuerte.es/", "official": False}


def parse_uk_xml(txt):
    """El archivo de la loteria britanica viene en XML: <draw> con fecha, bolas y estrellas (lectura tolerante)."""
    import xml.etree.ElementTree as ET
    out = []
    # Lectura por texto (no depende de como esten anidadas las etiquetas): cada sorteo empieza en <draw-number>
    parts = re.split(r"(?=<draw-number>)", txt)
    for p in parts[1:]:
        md = re.search(r"<draw-date>\s*([^<]+?)\s*</draw-date>", p)
        mb = re.search(r"<balls>(.*?)</balls>", p, re.S)
        if not (md and mb):
            continue
        dd = parse_any_date(md.group(1)) or (md.group(1)[:10] if re.match(r"\d{4}-\d{2}-\d{2}", md.group(1)) else None)
        nums = [int(x) for t, x in re.findall(r"<([a-zA-Z-]+)[^>]*>\s*(\d+)\s*</", mb.group(1)) if t.lower() != "set"]
        sts = []
        mbb = re.search(r"<bonus-ball[^>]*>(.*?)</bonus-ball>", p, re.S)
        if mbb:
            sts = [int(x) for x in re.findall(r">\s*(\d+)\s*<", ">" + mbb.group(1) + "<")] or [int(x) for x in re.findall(r"\d+", mbb.group(1))]
        if len(nums) == 7 and not sts:
            nums, sts = nums[:5], nums[5:]
        if dd and len(nums) == 5 and len(sts) == 2:
            out.append({"date": dd, "main": sorted(nums), "extras": sorted(sts)})
    if out:
        return out
    try:
        root = ET.fromstring(txt)
    except ET.ParseError:
        return out
    def leaf_digits(el):
        r = []
        for x in el.iter():
            if len(x) == 0 and (x.text or "").strip().isdigit():
                r.append(int((x.text or "").strip()))
        return r
    for dr in root.iter("draw"):
        dd = None
        for el in dr.iter():
            if "date" in el.tag.lower() and (el.text or "").strip():
                dd = parse_any_date(el.text.strip()) or (el.text.strip()[:10] if re.match(r"\d{4}-\d{2}-\d{2}", el.text.strip()) else None)
                break
        gb = [e for e in dr.iter() if e.tag.lower() == "balls"]
        bb = [e for e in dr.iter() if e.tag.lower() == "bonus-ball"]
        if dd and gb:
            mains = leaf_digits(gb[0])
            sts = []
            for e in bb:
                sts += leaf_digits(e)
            if len(mains) == 7 and not sts:
                mains, sts = mains[:5], mains[5:]
            if len(mains) == 5 and len(sts) == 2:
                out.append({"date": dd, "main": sorted(mains), "extras": sorted(sts)})
                continue
        d, balls, stars = None, [], []
        for el in dr.iter():
            tag = el.tag.lower()
            val = (el.text or "").strip()
            if not val:
                continue
            if d is None and "date" in tag:
                d = parse_any_date(val) or (val[:10] if re.match(r"\d{4}-\d{2}-\d{2}", val) else None)
            elif "number" in tag and "draw" in tag:
                continue
            elif re.search(r"star|lucky", tag) and val.isdigit():
                stars.append(int(val))
            elif re.search(r"ball|main", tag) and val.isdigit():
                balls.append(int(val))
            elif tag in ("numbers", "results", "balls") and re.fullmatch(r"[\d,\s-]+", val):
                balls += [int(x) for x in re.findall(r"\d+", val)]
        if d and len(balls) == 5 and len(stars) == 2:
            out.append({"date": d, "main": sorted(balls), "extras": sorted(stars)})
    return out


def src_uk_euromillions(cfg, today):
    txt = fetch("https://www.national-lottery.co.uk/results/euromillions/draw-history/csv")
    if txt.lstrip().startswith("<?xml") or "<draw-results" in txt[:300]:
        out = parse_uk_xml(txt)
        if not out:
            print("DIAGNOSTICO archivo Euromillones UK (%d caracteres): %s" % (len(txt), re.sub(r"\s+", " ", txt)[:3600]))
            i = txt.find("<draw-number>")
            raise RuntimeError("XML sin sorteos legibles; largo=%d; ejemplo: %r" % (len(txt), re.sub(r"\s+", " ", txt[i:i + 1200])))
        return out, {"name": "UK National Lottery (resultados oficiales de EuroMillions)", "page": "https://www.national-lottery.co.uk/results/euromillions/draw-history", "official": True}
    rd = csv.DictReader(io.StringIO(txt))
    out = []
    for row in rd:
        r = {re.sub(r"\s+", " ", (k or "").strip().lower()): (v or "").strip() for k, v in row.items()}
        ds = r.get("drawdate") or r.get("draw date") or ""
        d = None
        for fm in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d %b %Y"):
            try:
                d = datetime.strptime(ds, fm).date().isoformat()
                break
            except ValueError:
                pass
        try:
            main = sorted(int(r["ball %d" % i]) for i in range(1, 6))
            stars = sorted(int(r["lucky star %d" % i]) for i in (1, 2))
        except (KeyError, ValueError):
            continue
        if d:
            out.append({"date": d, "main": main, "extras": stars})
    if not out:
        out = parse_generic_csv(cfg, txt)
    if not out:
        raise RuntimeError("sin datos legibles; empieza por: %r" % txt[:140])
    return out, {"name": "UK National Lottery (resultados oficiales de EuroMillions)", "page": "https://www.national-lottery.co.uk/results/euromillions/draw-history", "official": True}


DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d/%m/%y", "%Y/%m/%d", "%a %d %b %Y")


def parse_any_date(s):
    s = (s or "").strip()
    for fm in DATE_FORMATS:
        try:
            return datetime.strptime(s, fm).date().isoformat()
        except ValueError:
            pass
    return None


def parse_generic_csv(cfg, txt):
    """CSV de cualquier forma: busca la fecha y los numeros (por nombre de columna o por orden)."""
    k = cfg["k"]
    ne = sum(len(g["idx"]) for g in cfg["groups"])
    sample = txt[:2000]
    delim = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(txt), delimiter=delim))
    head = [c.strip().lower() for c in rows[0]] if rows else []
    main_cols = [i for i, c in enumerate(head) if re.match(r"^(n|b|bola|num|numero|número|ball)[ _]?\d{1,2}$", c)]
    ext_cols = [i for i, c in enumerate(head) if re.search(r"comp|reint|clave|estrella|star|sue[ñn]o|caballo|^e\d$|^s\d$", c)]
    by_name = len(main_cols) == k and len(ext_cols) == ne
    out = []
    for row in rows[1:]:
        d = None
        di = None
        for i, c in enumerate(row):
            d = parse_any_date(c)
            if d:
                di = i
                break
        if not d:
            continue
        try:
            if by_name:
                main = [int(row[i]) for i in main_cols]
                ex = [int(row[i]) for i in ext_cols]
            else:
                nums = [int(c) for c in row[di + 1:] if re.fullmatch(r"\s*\d{1,2}\s*", c or "")]
                if len(nums) < k + ne:
                    continue
                main, ex = nums[:k], nums[k:k + ne]
        except (ValueError, IndexError):
            continue
        out.append({"date": d, "main": sorted(main), "extras": sorted(ex) if cfg["id"] == "es_euromillones" else ex})
    return out


def _gh_source(path, label):
    def fn(cfg, today):
        url = "https://raw.githubusercontent.com/Ana-2006-1967/loteria-estadisticas/main/" + path
        txt = fetch(url)
        draws = parse_generic_csv(cfg, txt)
        if not draws:
            raise RuntimeError("CSV sin sorteos legibles; cabecera: %r" % txt[:120])
        if cfg["id"] == "es_primitiva":
            try:
                extra, _ = src_azar_primitiva(cfg, today)
                have = {d["date"] for d in draws}
                draws += [d for d in extra if d["date"] not in have]
            except Exception:  # noqa: BLE001
                pass
        return draws, {"name": label, "page": "https://github.com/Ana-2006-1967/loteria-estadisticas", "official": False}
    fn.__name__ = "src_github_" + path
    return fn


SOURCES = {
    "es_primitiva": [src_api, _gh_source("primitiva_historico.csv", "Archivo público en GitHub (copia de los resultados oficiales)")],
    "es_bonoloto": [src_api],
    "es_euromillones": [src_api, src_uk_euromillions, _gh_source("euro_historico.csv", "Archivo público en GitHub (copia de los resultados oficiales)")],
    "es_elgordo": [src_api, _gh_source("gordo_historico.csv", "Archivo público en GitHub (copia de los resultados oficiales)")],
    "es_eurodreams": [src_api],
    "es_lototurf": [src_api],
}


# ---------------------------------------------------------------------------
# Validacion
# ---------------------------------------------------------------------------
def validate(cfg, draws):
    ok, bad = {}, 0
    for d in draws:
        try:
            m, ex = d["main"], d["extras"]
            if d["date"] < cfg["era"]:
                continue
            if len(m) != cfg["k"] or len(set(m)) != cfg["k"] or min(m) < 1 or max(m) > cfg["n"]:
                raise ValueError("principales")
            for g in cfg["groups"]:
                vals = [ex[i] for i in g["idx"]]
                if any(v < g["lo"] or v > g["hi"] for v in vals) or len(set(vals)) != len(vals):
                    raise ValueError("extra")
            ok[d["date"]] = d
        except Exception:  # noqa: BLE001
            bad += 1
    out = [ok[k] for k in sorted(ok)]
    if len(out) < cfg["min"]:
        raise RuntimeError("pocos sorteos validos (%d)" % len(out))
    return out, bad


# ---------------------------------------------------------------------------
# Estadisticas
# ---------------------------------------------------------------------------
def stats(lists, lo, hi, picks):
    n = len(lists)
    counts, last, gap = Counter(), {}, {}
    for i, nums in enumerate(lists):
        for x in nums:
            g = i - last.get(x, -1) - 1
            if g > gap.get(x, 0):
                gap[x] = g
            last[x] = i
            counts[x] += 1
    rows = []
    for x in range(lo, hi + 1):
        c = counts.get(x, 0)
        delay = (n - 1 - last[x]) if x in last else n
        rows.append({"n": x, "count": c, "pct_draws": round(100.0 * c / n, 2) if n else 0,
                     "delay": delay, "max_delay": max(gap.get(x, 0), delay)})
    return {"draws": n, "expected_pct_draws": round(100.0 * picks / (hi - lo + 1), 2), "numbers": rows}


def profile_tables(draws, k, n):
    nd = len(draws)
    odd = Counter(sum(x % 2 for x in d["main"]) for d in draws)
    low = Counter(sum(1 for x in d["main"] if x <= n // 2) for d in draws)
    sums = sorted(sum(d["main"]) for d in draws)
    return {
        "odd_even": [{"odd": i, "even": k - i, "draws": odd.get(i, 0), "pct": round(100.0 * odd.get(i, 0) / nd, 2)} for i in range(k + 1)],
        "low_high": [{"low": i, "high": k - i, "draws": low.get(i, 0), "pct": round(100.0 * low.get(i, 0) / nd, 2)} for i in range(k + 1)],
        "sum": {"mean": round(sum(sums) / nd, 1), "min": sums[0], "max": sums[-1], "p10": percentile(sums, 10), "p90": percentile(sums, 90)},
    }


# ---------------------------------------------------------------------------
# Aprendizaje: torneo de estrategias (sin mirar el futuro)
# ---------------------------------------------------------------------------
STRATS = [
    ("azar", "Al azar", "Random"),
    ("equilibrada", "Equilibrada", "Balanced"),
    ("frecuentes", "Más frecuentes", "Most frequent"),
    ("retrasados", "Más retrasados", "Most overdue"),
    ("mixta", "Mixta (frecuencia + retraso)", "Mixed (frequency + delay)"),
]


def weighted_pick(weights, k, rnd):
    pool = dict(weights)
    out = []
    for _ in range(k):
        tot = sum(pool.values())
        r = rnd.random() * tot
        acc = 0.0
        pick = None
        for x, w in pool.items():
            acc += w
            if acc >= r:
                pick = x
                break
        if pick is None:
            pick = next(iter(pool))
        out.append(pick)
        del pool[pick]
    return sorted(out)


def balanced_ok(m, n):
    k = len(m)
    odd = sum(x % 2 for x in m)
    low = sum(1 for x in m if x <= n / 2)
    mean, sd = k * (n + 1) / 2.0, (k * (n - k) * (n + 1) / 12.0) ** 0.5
    run = mx = 1
    for a, b in zip(m, m[1:]):
        run = run + 1 if b == a + 1 else 1
        mx = max(mx, run)
    if abs(odd - k / 2.0) > 1.01 or abs(low - k / 2.0) > 1.01:
        return False
    if abs(sum(m) - mean) > 1.3 * sd or mx >= 3:
        return False
    if n > 31 and sum(1 for x in m if x > 31) < -(-k // 3):
        return False
    return True


def make_combo(strat, k, n, counts, last_seen, i, rnd):
    if strat == "azar":
        return sorted(rnd.sample(range(1, n + 1), k))
    if strat == "equilibrada":
        m = None
        for _ in range(3000):
            m = sorted(rnd.sample(range(1, n + 1), k))
            if balanced_ok(m, n):
                break
        return m
    avg_c = (sum(counts.values()) / float(n)) or 1.0
    avg_d = (i / float(n)) or 1.0
    w = {}
    for x in range(1, n + 1):
        fc = counts.get(x, 0) / avg_c
        dl = (i - 1 - last_seen[x] if x in last_seen else i) / avg_d
        w[x] = {"frecuentes": 0.2 + fc, "retrasados": 0.2 + dl, "mixta": (0.2 + fc) * (0.2 + dl)}[strat]
    return weighted_pick(w, k, rnd)


def tournament(draws, cfg, seed):
    k, n = cfg["k"], cfg["n"]
    N = len(draws)
    start = max(MIN_HISTORY, N - BACKTEST_DRAWS)
    if N <= start:
        return None
    rnd = random.Random(seed)
    counts, last_seen = Counter(), {}
    for i in range(start):
        for x in draws[i]["main"]:
            counts[x] += 1
            last_seen[x] = i
    thr = 3 if k == 6 else 2
    tot = {s[0]: {"matches": 0, "prize_draws": 0} for s in STRATS}
    tested = 0
    for i in range(start, N):
        real = set(draws[i]["main"])
        for sid, _, _ in STRATS:
            best = 0
            for _c in range(N_COMBOS):
                m = make_combo(sid, k, n, counts, last_seen, i, rnd)
                hit = len(real & set(m))
                tot[sid]["matches"] += hit
                best = max(best, hit)
            if best >= thr:
                tot[sid]["prize_draws"] += 1
        tested += 1
        for x in draws[i]["main"]:
            counts[x] += 1
            last_seen[x] = i
    rows = []
    for sid, es, en in STRATS:
        t = tot[sid]
        rows.append({"id": sid, "es": es, "en": en, "avg_matches": round(t["matches"] / float(tested * N_COMBOS), 4),
                     "prize_rate": round(100.0 * t["prize_draws"] / tested, 1)})
    # ganadora: mayor media de aciertos; con empate o diferencia minima se prefiere "equilibrada"
    best = max(rows, key=lambda r: (r["avg_matches"], r["id"] == "equilibrada"))
    return {"draws_tested": tested, "combos_per_draw": N_COMBOS, "threshold": thr, "strategies": rows, "best": best["id"]}


def next_draw_date(draws, today):
    """Dias de sorteo deducidos de los ultimos sorteos reales (no se inventan)."""
    wd = Counter(date.fromisoformat(d["date"]).weekday() for d in draws[-30:])
    days = sorted(w for w, c in wd.items() if c >= 0.2 * min(30, len(draws)))
    if not days:
        return None, []
    for add in range(0, 9):
        dd = today + timedelta(days=add)
        if dd.weekday() in days:
            return dd.isoformat(), days
    return None, days


def build(cfg, draws, src, bad, today, old):
    k, n = cfg["k"], cfg["n"]
    last = draws[-1]["date"]
    seed = "%s|%s" % (cfg["id"], last)
    warns = []
    if len(draws) < 300:
        warns.append("Muestra pequeña (%d sorteos): usar con cautela." % len(draws))
    if (today - date.fromisoformat(last)).days > 14:
        warns.append("El último sorteo disponible es del %s: la fuente puede ir con retraso." % last)
    if not src.get("official"):
        warns.append("Fuente no oficial: copia de los resultados oficiales. Comprueba siempre en la web oficial.")
    if bad:
        warns.append("%d sorteos de la fuente se ignoraron por no pasar la validación." % bad)

    main_lists = [d["main"] for d in draws]
    gstats = []
    for g in cfg["groups"]:
        lists = [[d["extras"][i] for i in g["idx"]] for d in draws]
        gstats.append({"es": g["es"], "en": g["en"], "lo": g["lo"], "hi": g["hi"], "picks": len(g["idx"]),
                       "stats": stats(lists, g["lo"], g["hi"], len(g["idx"]))})
    prof = profile_tables(draws, k, n)

    nd, ds = next_draw_date(draws, today)
    learn = tournament(draws, cfg, seed)
    suggest = None
    if learn:
        rnd = random.Random(seed + "|sug")
        counts, last_seen = Counter(), {}
        for i, d in enumerate(draws):
            for x in d["main"]:
                counts[x] += 1
                last_seen[x] = i
        best = learn["best"]
        combos = []
        for _ in range(N_COMBOS):
            m = make_combo(best, k, n, counts, last_seen, len(draws), rnd)
            ex = []
            for g in cfg["groups"]:
                ex += [(i, v) for i, v in zip(g["idx"], sorted(rnd.sample(range(g["lo"], g["hi"] + 1), len(g["idx"]))))]
            combos.append({"main": m, "extras": [v for _, v in sorted(ex)]})
        ctrl_m = sorted(rnd.sample(range(1, n + 1), k))
        suggest = {"for_draw": nd, "strategy": best, "combos": combos, "control": ctrl_m,
                   "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    # aprendizaje en vivo: la sugerencia guardada antes vs. el resultado real
    live = list((old or {}).get("live") or [])
    prev = (old or {}).get("suggest")
    if prev and prev.get("for_draw"):
        real = next((d for d in draws if d["date"] >= prev["for_draw"]), None)
        if real and not any(x["for_draw"] == prev["for_draw"] for x in live):
            rs = set(real["main"])
            live.append({"for_draw": prev["for_draw"], "draw_date": real["date"], "strategy": prev.get("strategy"),
                         "best_main": max(len(rs & set(c["main"])) for c in prev["combos"]),
                         "random_main": len(rs & set(prev.get("control") or []))})
    live = live[-KEEP_LIVE:]
    changes = (old or {}).get("learn", {}).get("changes", 0) if old else 0
    if old and old.get("learn") and old["learn"].get("best") and learn and old["learn"]["best"] != learn["best"]:
        changes += 1
    if learn:
        learn["changes"] = changes
        learn["note"] = "Las diferencias entre estrategias son ruido estadístico: ninguna mejora la probabilidad de ganar."

    return {
        "lottery": cfg["id"], "country": "es", "name": cfg["name"],
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": {"name": src["name"], "page": src["page"], "official": bool(src.get("official"))},
        "rules": {"main_count": k, "main_max": n, "era_from": cfg["era"], "days": ds,
                  "groups": [{"es": g["es"], "en": g["en"], "lo": g["lo"], "hi": g["hi"], "idx": g["idx"]} for g in cfg["groups"]]},
        "total_draws": len(draws), "first_draw": draws[0]["date"], "last_draw": last, "draws_used": len(draws),
        "last_draws": list(reversed(draws[-10:])),
        "main": stats(main_lists, 1, n, k), "groups": gstats,
        "odd_even": prof["odd_even"], "low_high": prof["low_high"], "sum": prof["sum"],
        "warnings": warns, "learn": learn, "suggest": suggest, "live": live,
        "next_draw": nd,
        "draws": [[d["date"], d["main"], d["extras"]] for d in draws[-KEEP_DRAWS:]],
    }


def load_old(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def main(today=None, sources=None):
    today = today or datetime.now(timezone.utc).date()
    sources = sources or SOURCES
    os.makedirs(DATA_DIR, exist_ok=True)
    status, ok_count = {}, 0
    for cfg in ES:
        path = os.path.join(DATA_DIR, cfg["id"] + ".json")
        old = load_old(path)
        intentos = []
        elegido = None
        for fn in sources[cfg["id"]]:
            try:
                raw, src = fn(cfg, today)
                draws, bad = validate(cfg, raw)
                fresh = (today - date.fromisoformat(draws[-1]["date"])).days <= 14
                intentos.append({"fuente": src["name"], "ok": True, "ultimo": draws[-1]["date"], "sorteos": len(draws)})
                if elegido is None or (fresh and not elegido[3]):
                    elegido = (draws, src, bad, fresh)
                if fresh:
                    break
            except Exception as e:  # noqa: BLE001
                intentos.append({"fuente": fn.__name__, "ok": False, "error": str(e)[:160]})
        st = {"intentos": intentos}
        if elegido and not (old and old.get("last_draw", "") > elegido[0][-1]["date"]):
            draws, src, bad, _ = elegido
            res = build(cfg, draws, src, bad, today, old)
            cambio = save_json_if_changed(path, res)
            st.update({"ok": True, "last_draw": res["last_draw"], "source": src["name"]})
            ok_count += 1
            print("OK    %s: %d sorteos, ultimo %s, fuente: %s%s" % (cfg["id"], len(draws), res["last_draw"], src["name"], "" if cambio else " (sin cambios)"))
            for it in intentos:
                print("      intento: " + json.dumps(it, ensure_ascii=False))
        else:
            st.update({"ok": False, "kept_old": bool(old)})
            print("SIN DATOS %s: %s" % (cfg["id"], json.dumps(intentos, ensure_ascii=False)), file=sys.stderr)
        status[cfg["id"]] = st

    items = []
    for cfg in ES:
        j = load_old(os.path.join(DATA_DIR, cfg["id"] + ".json"))
        if j:
            items.append({"id": cfg["id"], "last_draw": j.get("last_draw"), "file": cfg["id"] + ".json", "official": j.get("source", {}).get("official")})
    save_json_if_changed(os.path.join(DATA_DIR, "es_index.json"),
                         {"lotteries": items, "status": status, "checked_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")})
    return 0


if __name__ == "__main__":
    sys.exit(main())
