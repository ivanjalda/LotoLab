#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LotoLab - mantenimiento automatico. Solo libreria estandar de Python.

Uso:
  python scripts/mantenimiento.py noticias   -> actualiza data/news.json
  python scripts/mantenimiento.py revisar    -> revision diaria de la web

NOTICIAS: solo guarda titular, medio, fecha y enlace (y la foto si el propio
medio la ofrece). Nunca copia el texto de las noticias.

REVISOR: comprueba datos, noticias, la web publicada y los enlaces. Corrige
lo que puede (volver a descargar los datos) y, si algo sigue mal, abre un
aviso (Issue) en GitHub, que te llega al correo.
"""

import email.utils
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from loteria_engine import DATA_DIR, BASE_DIR, save_json_if_changed  # noqa: E402

UA = "Mozilla/5.0 (compatible; LotoLabBot/1.0; +https://github.com/ivanjalda/LotoLab)"
MEDIA_NS = "{http://search.yahoo.com/mrss/}"
MAX_AGE_DAYS = 14
MAX_ITEMS = 30


# ---------------------------------------------------------------------------
# Utilidades de red
# ---------------------------------------------------------------------------
def http_get(url, timeout=30, headers=None):
    """Devuelve (codigo, bytes). Codigo 0 si no hay conexion."""
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    try:
        req = urllib.request.Request(url, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception:  # noqa: BLE001
        return 0, b""


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# NOTICIAS
# ---------------------------------------------------------------------------
def gnews(query, lang):
    q = urllib.parse.quote(query + " when:7d")
    if lang == "es":
        return "https://news.google.com/rss/search?q=%s&hl=es&gl=ES&ceid=ES:es" % q
    return "https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en" % q


FEEDS = [
    {"lang": "es", "kind": "news",
     "url": gnews('Euromillones OR Primitiva OR Bonoloto OR "El Gordo" OR "Lotería Nacional" OR EuroDreams', "es")},
    {"lang": "es", "kind": "winners",
     "url": gnews("lotería acertante OR premiado OR millonario", "es")},
    {"lang": "en", "kind": "news",
     "url": gnews('Powerball OR "Mega Millions" OR "lottery jackpot"', "en")},
    {"lang": "en", "kind": "winners",
     "url": gnews('"lottery winner" OR "claimed" Powerball OR "Mega Millions" winner', "en")},
]


def clean_title(title, source):
    t = (title or "").strip()
    if source and t.endswith(" - " + source):
        t = t[: -(len(source) + 3)].strip()
    return t


def parse_feed(xml_bytes):
    """Lee un RSS y devuelve una lista de noticias (titular, enlace, medio, fecha, foto)."""
    items = []
    root = ET.fromstring(xml_bytes)
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None and src_el.text else ""
        if not source and " - " in title:
            source = title.rsplit(" - ", 1)[1].strip()
        title = clean_title(title, source)
        try:
            dt = email.utils.parsedate_to_datetime(it.findtext("pubDate") or "")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            continue
        image = ""
        for tag in ("content", "thumbnail"):
            el = it.find(MEDIA_NS + tag)
            if el is not None and el.get("url", "").startswith("http"):
                image = el.get("url")
                break
        if not image:
            enc = it.find("enclosure")
            if enc is not None and (enc.get("type") or "").startswith("image") and enc.get("url", "").startswith("http"):
                image = enc.get("url")
        if title and link.startswith("http"):
            items.append({"title": title, "url": link, "source": source,
                          "date": iso(dt.astimezone(timezone.utc)), "image": image})
    return items


def build_news(fetch=http_get):
    out = {"es": {"news": [], "winners": []}, "en": {"news": [], "winners": []}}
    errors = []
    limit = iso(now_utc() - timedelta(days=MAX_AGE_DAYS))
    for f in FEEDS:
        code, body = fetch(f["url"])
        if code != 200 or not body:
            errors.append("%s/%s: HTTP %s" % (f["lang"], f["kind"], code))
            continue
        try:
            items = parse_feed(body)
        except Exception as e:  # noqa: BLE001
            errors.append("%s/%s: no se pudo leer (%s)" % (f["lang"], f["kind"], e))
            continue
        seen = {i["title"].lower() for i in out[f["lang"]][f["kind"]]}
        for i in sorted(items, key=lambda x: x["date"], reverse=True):
            if i["date"] < limit or i["title"].lower() in seen:
                continue
            seen.add(i["title"].lower())
            out[f["lang"]][f["kind"]].append(i)
        out[f["lang"]][f["kind"]] = out[f["lang"]][f["kind"]][:MAX_ITEMS]
    return out, errors


def actualizar_noticias():
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "news.json")
    nuevo, errors = build_news()
    total = sum(len(nuevo[l][k]) for l in nuevo for k in nuevo[l])
    for e in errors:
        print("AVISO noticias:", e, file=sys.stderr)
    if total == 0:
        print("Sin noticias nuevas: se conserva el archivo anterior.")
        return 0
    nuevo["updated_utc"] = iso(now_utc())
    cambio = save_json_if_changed(path, nuevo, ignore_keys=("updated_utc",))
    print("OK noticias: %d titulares%s" % (total, "" if cambio else " (sin cambios)"))
    return 0


# ---------------------------------------------------------------------------
# REVISOR DIARIO
# ---------------------------------------------------------------------------
LOT_KEYS = ["lottery", "name", "last_draw", "main", "bonus", "last_draws", "draws", "rules", "sum"]


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def check_data(add):
    """Comprueba los archivos de datos. Devuelve True si todo esta bien."""
    ok = True
    try:
        idx = read_json(os.path.join(DATA_DIR, "index.json"))
    except Exception as e:  # noqa: BLE001
        add("datos", "error", "No se puede leer data/index.json (%s)" % e)
        return False
    if not idx.get("lotteries"):
        add("datos", "error", "data/index.json no tiene loterias")
        return False
    today = now_utc().date()
    for it in idx["lotteries"]:
        try:
            j = read_json(os.path.join(DATA_DIR, it["file"]))
        except Exception as e:  # noqa: BLE001
            add("datos", "error", "%s: archivo ilegible (%s)" % (it["id"], e))
            ok = False
            continue
        missing = [k for k in LOT_KEYS if k not in j]
        if missing:
            add("datos", "error", "%s: faltan campos %s" % (it["id"], ", ".join(missing)))
            ok = False
            continue
        age = (today - datetime.strptime(j["last_draw"], "%Y-%m-%d").date()).days
        if age > 14:
            add("datos", "error", "%s: ultimo sorteo hace %d dias" % (j["name"], age))
            ok = False
        elif age > 8:
            add("datos", "warn", "%s: ultimo sorteo hace %d dias" % (j["name"], age))
            ok = False
        else:
            add("datos", "ok", "%s: ultimo sorteo %s (hace %d dias)" % (j["name"], j["last_draw"], age))
    try:
        st = read_json(os.path.join(DATA_DIR, "status.json"))
        for k, v in st.items():
            if isinstance(v, dict) and v.get("ok") is False:
                add("datos", "error", "%s fallo en la ultima descarga: %s" % (k, v.get("error", "")))
                ok = False
    except Exception:  # noqa: BLE001
        add("datos", "warn", "No se puede leer data/status.json")
    return ok


def check_news(add):
    try:
        n = read_json(os.path.join(DATA_DIR, "news.json"))
    except Exception:  # noqa: BLE001
        add("noticias", "warn", "Todavia no existe data/news.json")
        return
    total = sum(len(n.get(l, {}).get(k, [])) for l in ("es", "en") for k in ("news", "winners"))
    try:
        upd = datetime.strptime(n["updated_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        hours = (now_utc() - upd).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        hours = 999
    if total == 0:
        add("noticias", "warn", "No hay noticias cargadas")
    elif hours > 30:
        add("noticias", "warn", "Las noticias llevan %d horas sin actualizarse" % hours)
    else:
        add("noticias", "ok", "%d titulares, actualizados hace %d horas" % (total, hours))


def site_url():
    if os.environ.get("SITE_URL"):
        return os.environ["SITE_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repo:
        owner, name = repo.split("/", 1)
        return "https://%s.github.io/%s/" % (owner.lower(), name)
    return ""


def check_site(add, fetch=http_get):
    base = site_url()
    if not base:
        add("web", "warn", "No se conoce la direccion de la web (se omite la comprobacion)")
        return
    code, body = fetch(base)
    html = body.decode("utf-8", "ignore")
    if code != 200:
        add("web", "error", "La web no responde (codigo %s)" % code)
        return
    if "LotoLab" not in html or 'id="v-home"' not in html:
        add("web", "error", "La web carga pero le falta contenido esencial")
        return
    add("web", "ok", "La web responde y tiene su contenido principal")
    code, body = fetch(base + "data/index.json")
    if code != 200:
        add("web", "error", "La web publicada no encuentra data/index.json (codigo %s)" % code)
        return
    try:
        pub = json.loads(body.decode("utf-8"))
        loc = read_json(os.path.join(DATA_DIR, "index.json"))
        pubd = {i["id"]: i.get("last_draw") for i in pub["lotteries"]}
        for i in loc["lotteries"]:
            if pubd.get(i["id"]) and pubd[i["id"]] < i["last_draw"]:
                add("web", "warn", "%s: la web publicada va un poco por detras del repositorio" % i["id"])
        add("web", "ok", "Los datos publicados se pueden leer")
    except Exception as e:  # noqa: BLE001
        add("web", "error", "Los datos publicados no son validos (%s)" % e)


def check_links(add, fetch=http_get):
    path = os.path.join(BASE_DIR, "index.html")
    try:
        with open(path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:  # noqa: BLE001
        add("enlaces", "error", "No se puede leer index.html (%s)" % e)
        return
    urls = sorted(set(re.findall(r'u:"(https?://[^"]+)"', html)))
    if not urls:
        add("enlaces", "warn", "No se encontraron enlaces de publicidad en index.html")
    for u in urls:
        code, _ = fetch(u, headers={"User-Agent": "Mozilla/5.0"})
        if 200 <= code < 400:
            add("enlaces", "ok", "%s responde" % u)
        elif code in (403, 429, 999):
            add("enlaces", "warn", "%s bloquea a los robots (codigo %s), no se puede comprobar" % (u, code))
        else:
            add("enlaces", "error", "%s no responde bien (codigo %s)" % (u, code))
    size = len(html.encode("utf-8"))
    if size > 1_000_000:
        add("web", "warn", "index.html pesa %d KB, conviene aligerarlo" % (size // 1024))


def run_checks(fetch=http_get):
    checks = []

    def add(area, level, detail):
        checks.append({"area": area, "level": level, "detail": detail})

    data_ok = check_data(add)
    check_news(add)
    check_site(add, fetch)
    check_links(add, fetch)
    return checks, data_ok


def gh_api(method, path, token, body=None):
    url = "https://api.github.com" + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        txt = r.read().decode("utf-8")
        return json.loads(txt) if txt else {}


ISSUE_TITLE = "Revisión automática de LotoLab: hay problemas"


def gestionar_aviso(errors, warnings, fixed):
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        print("Sin token de GitHub: no se abre aviso.")
        return
    try:
        abiertos = gh_api("GET", "/repos/%s/issues?state=open&per_page=50" % repo, token)
        existente = next((i for i in abiertos if i.get("title") == ISSUE_TITLE and "pull_request" not in i), None)
        if errors:
            cuerpo = ["La revisión automática de hoy ha encontrado problemas que no ha podido corregir sola.", ""]
            cuerpo += ["**Errores**"] + ["- " + e for e in errors]
            if warnings:
                cuerpo += ["", "**Avisos**"] + ["- " + w for w in warnings]
            if fixed:
                cuerpo += ["", "Ya se intentó volver a descargar los datos automáticamente."]
            cuerpo += ["", "Cópiale este aviso a Claude y te dice qué hacer."]
            texto = "\n".join(cuerpo)
            if existente:
                gh_api("POST", "/repos/%s/issues/%d/comments" % (repo, existente["number"]), token, {"body": texto})
            else:
                gh_api("POST", "/repos/%s/issues" % repo, token, {"title": ISSUE_TITLE, "body": texto})
            print("Aviso creado/actualizado en GitHub.")
        elif existente:
            gh_api("POST", "/repos/%s/issues/%d/comments" % (repo, existente["number"]), token,
                   {"body": "Todo en orden en la revisión de hoy. Se cierra el aviso."})
            gh_api("PATCH", "/repos/%s/issues/%d" % (repo, existente["number"]), token, {"state": "closed"})
            print("Aviso anterior cerrado: todo en orden.")
    except Exception as e:  # noqa: BLE001
        print("No se pudo gestionar el aviso en GitHub:", e, file=sys.stderr)


def revisar():
    os.makedirs(DATA_DIR, exist_ok=True)
    checks, data_ok = run_checks()
    fixed = False
    if not data_ok:
        print("Problema con los datos: se intenta volver a descargarlos.")
        engine = os.path.join(os.path.dirname(os.path.abspath(__file__)), "loteria_engine.py")
        subprocess.run([sys.executable, engine], check=False)
        checks, data_ok = run_checks()
        fixed = True
        if data_ok:
            checks.append({"area": "datos", "level": "ok",
                           "detail": "Corregido automaticamente: se volvieron a descargar los datos"})
    errors = ["[%s] %s" % (c["area"], c["detail"]) for c in checks if c["level"] == "error"]
    warnings = ["[%s] %s" % (c["area"], c["detail"]) for c in checks if c["level"] == "warn"]
    informe = {"checked_utc": iso(now_utc()), "ok": not errors, "errors": errors,
               "warnings": warnings, "auto_fixed": fixed and data_ok, "checks": checks}
    save_json_if_changed(os.path.join(DATA_DIR, "revision.json"), informe, ignore_keys=("checked_utc",))
    for c in checks:
        print("%-7s %-9s %s" % (c["level"].upper(), c["area"], c["detail"]))
    gestionar_aviso(errors, warnings, fixed)
    print("RESULTADO:", "TODO BIEN" if not errors else "HAY %d ERRORES" % len(errors))
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "noticias":
        return actualizar_noticias()
    if cmd == "revisar":
        return revisar()
    print("Uso: python scripts/mantenimiento.py [noticias|revisar]")
    return 2


if __name__ == "__main__":
    sys.exit(main())
