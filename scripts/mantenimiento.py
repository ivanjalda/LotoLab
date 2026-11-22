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
# Premiados por loteria: una busqueda por cada loteria para que ninguna se quede sin noticias
for _q in ('Euromillones acertante OR ganador OR premio', 'Primitiva acertante OR premiado OR bote',
           'Bonoloto acertante OR premiado', '"El Gordo de la Primitiva" acertante OR premiado',
           'EuroDreams acertante OR ganador', 'Lototurf acertante', '"Lotería Nacional" premiado OR "primer premio"',
           'Lotería de Navidad premiado OR administración', '"Lotería del Niño" premiado OR administración'):
    FEEDS.append({"lang": "es", "kind": "winners", "url": gnews(_q, "es")})
for _q in ('Powerball winner OR jackpot winner', '"Mega Millions" winner OR jackpot winner',
           '"Millionaire for Life" winner OR "Cash4Life" winner'):
    FEEDS.append({"lang": "en", "kind": "winners", "url": gnews(_q, "en")})

# Periodicos y medios de actualidad (con foto en el propio RSS). Si alguno falla, se ignora y se avisa en el registro.
GENERAL = [
    ("es", "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"),
    ("es", "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml"),
    ("es", "https://www.20minutos.es/rss/"),
    ("es", "https://www.lavanguardia.com/rss/home.xml"),
    ("es", "https://www.abc.es/rss/2.0/portada/"),
    ("es", "https://www.europapress.es/rss/rss.aspx"),
    ("en", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    ("en", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("en", "https://feeds.npr.org/1001/rss.xml"),
    ("en", "https://www.theguardian.com/world/rss"),
]
for _l, _u in GENERAL:
    FEEDS.append({"lang": _l, "kind": "general", "url": _u})

# Titulares que no queremos mostrar (contenido sexual o violencia grafica). Se descartan antes de guardar.
BLOCK_RE = re.compile(
    r"sexual|sexo|porn|er[oó]tic|desnud|nude|naked|violaci|violad|\brape|asesin|murder|killed|apu[ñn]al|"
    r"suicid|atentado|masacre|massacre|pedofil|abuso de menores|child abuse|cad[aá]ver|decapit|tortur", re.I)


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
    out = {"es": {"news": [], "winners": [], "general": []}, "en": {"news": [], "winners": [], "general": []}}
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
        general = f["kind"] == "general"
        taken = 0
        for i in sorted(items, key=lambda x: x["date"], reverse=True):
            if i["date"] < limit or i["title"].lower() in seen or BLOCK_RE.search(i["title"]):
                continue
            if general:
                if taken >= 12:
                    break
                if not i["source"]:
                    i["source"] = urllib.parse.urlparse(f["url"]).netloc.replace("www.", "")
                taken += 1
            seen.add(i["title"].lower())
            out[f["lang"]][f["kind"]].append(i)
        lst = sorted(out[f["lang"]][f["kind"]], key=lambda x: x["date"], reverse=True)
        out[f["lang"]][f["kind"]] = lst[:(120 if f["kind"] == "winners" else (60 if general else MAX_ITEMS))]
    return out, errors



OG_RE = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']|<meta[^>]+content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', re.I)


def og_image(url):
    """Foto de portada que el propio medio declara en su pagina (og:image). Vacio si no hay."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=8) as r:
            final = r.geturl()
            if "google." in urllib.parse.urlparse(final).netloc:
                return ""          # Google no ha redirigido: esa imagen no es del medio
            html = r.read(300000).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""
    m = OG_RE.search(html)
    img = (m.group(1) or m.group(2)) if m else ""
    return img if img.startswith("https://") else ""


def enrich_images(out, max_calls=40, max_seconds=120):
    """Intenta poner foto a las noticias que no la traen. Si falla, se queda sin foto."""
    import time
    t0 = time.time()
    calls = got = 0
    for lang in out:
        for kind in out[lang]:
            for it in out[lang][kind]:
                if it.get("image") or calls >= max_calls or time.time() - t0 > max_seconds:
                    continue
                calls += 1
                img = og_image(it["url"])
                if img:
                    it["image"] = img
                    got += 1
    print("Fotos: %d de %d intentos" % (got, calls))


def actualizar_noticias():
    os.makedirs(DATA_DIR, exist_ok=True)
    path = os.path.join(DATA_DIR, "news.json")
    nuevo, errors = build_news()
    try:
        enrich_images(nuevo)
    except Exception as e:  # noqa: BLE001
        print("AVISO fotos:", e, file=sys.stderr)
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
    for sec in ("v-sug", "v-calc", "v-guide", "special-es", "rail-left", "rail-right", "bottom-mix"):
        if 'id="%s"' % sec not in html:
            add("web", "error", "A la web publicada le falta la seccion %s" % sec)
    if ("LotoPulse" not in html and "LotoLab" not in html) or 'id="v-home"' not in html:
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


def check_content(add):
    """Comprueba que los datos traen todo lo que la web necesita (calendario y premios)."""
    try:
        idx = read_json(os.path.join(DATA_DIR, "index.json"))
    except Exception:  # noqa: BLE001
        return
    for it in idx.get("lotteries", []):
        try:
            j = read_json(os.path.join(DATA_DIR, it["file"]))
        except Exception:  # noqa: BLE001
            continue
        r = j.get("rules", {})
        miss = [k for k in ("draw_days", "draw_time_et", "prizes", "prize_min_main") if not r.get(k) and r.get(k) != 0]
        if miss:
            add("contenido", "error", "%s: faltan datos nuevos en el JSON (%s). Hay que ejecutar 'Actualizar datos LotoLab'." % (it["id"], ", ".join(miss)))
        else:
            add("contenido", "ok", "%s: calendario y premios presentes" % it["id"])


def check_botes(add):
    """El bote de Mega Millions debe existir, ser coherente y estar al dia."""
    try:
        b = read_json(os.path.join(DATA_DIR, "botes.json"))
    except Exception:  # noqa: BLE001
        add("botes", "error", "No existe data/botes.json (botes de EE. UU.)")
        return
    mm = b.get("lotteries", {}).get("us_megamillions")
    if not mm:
        add("botes", "error", "Mega Millions: no hay bote guardado")
        return
    today = now_utc().date()
    try:
        nxt = datetime.strptime(mm["next"]["date"], "%Y-%m-%d").date()
        last = datetime.strptime(mm["last"]["date"], "%Y-%m-%d").date()
    except Exception:  # noqa: BLE001
        add("botes", "error", "Mega Millions: fechas del bote ilegibles")
        return
    if mm["next"]["jackpot"] <= 0 or mm["next"]["cash"] <= 0 or mm["next"]["cash"] > mm["next"]["jackpot"]:
        add("botes", "error", "Mega Millions: el bote tiene cifras incoherentes")
    elif nxt < today - timedelta(days=1):
        add("botes", "error", "Mega Millions: el bote guardado es de un sorteo ya pasado (%s)" % mm["next"]["date"])
    elif (today - last).days > 8:
        add("botes", "warn", "Mega Millions: el ultimo sorteo guardado es del %s" % mm["last"]["date"])
    else:
        add("botes", "ok", "Mega Millions: bote del proximo sorteo (%s) guardado" % mm["next"]["date"])


def check_espana(add):
    """Cada loteria de Espana debe tener datos reales al dia; si la fuente falla se avisa (no se inventa nada)."""
    try:
        idx = read_json(os.path.join(DATA_DIR, "es_index.json"))
    except Exception:  # noqa: BLE001
        add("espana", "warn", "Todavia no existe data/es_index.json (motor de Espana sin ejecutar)")
        return
    today = now_utc().date()
    have = {x["id"]: x for x in idx.get("lotteries", [])}
    for cid in ("es_primitiva", "es_bonoloto", "es_euromillones", "es_elgordo", "es_eurodreams", "es_lototurf"):
        it = have.get(cid)
        if not it:
            add("espana", "warn", "%s: sin datos (todas las fuentes fallaron)" % cid)
            continue
        try:
            last = datetime.strptime(it["last_draw"], "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            add("espana", "error", "%s: fecha del ultimo sorteo ilegible" % cid)
            continue
        if (today - last).days > 10:
            add("espana", "warn", "%s: el ultimo sorteo guardado es del %s" % (cid, it["last_draw"]))
        else:
            add("espana", "ok", "%s: datos al dia (%s)" % (cid, it["last_draw"]))


def check_predictions(add):
    """Las combinaciones sugeridas deben existir, ser de sorteos futuros y evaluarse al terminar."""
    today = now_utc().date()
    try:
        sug = read_json(os.path.join(DATA_DIR, "sugerencias.json"))
        hist = read_json(os.path.join(DATA_DIR, "historial.json"))
    except Exception as e:  # noqa: BLE001
        add("predicciones", "error", "No se pueden leer sugerencias/historial (%s)" % e)
        return
    lots = sug.get("lotteries", {})
    if not lots:
        add("predicciones", "error", "No hay combinaciones sugeridas guardadas")
        return
    for k, v in lots.items():
        try:
            d = datetime.strptime(v["for_draw"], "%Y-%m-%d").date()
        except Exception:  # noqa: BLE001
            add("predicciones", "error", "%s: fecha de sorteo ilegible" % k)
            continue
        if d < today - timedelta(days=1):
            add("predicciones", "error", "%s: las combinaciones son de un sorteo ya pasado (%s)" % (k, v["for_draw"]))
        elif len(v.get("combos", [])) < 5:
            add("predicciones", "error", "%s: faltan combinaciones" % k)
        else:
            add("predicciones", "ok", "%s: combinaciones guardadas para el %s" % (k, v["for_draw"]))
    stuck = [e for e in hist.get("live", []) if e.get("result") is None
             and datetime.strptime(e["for_draw"], "%Y-%m-%d").date() < today - timedelta(days=4)]
    if stuck:
        add("predicciones", "error", "%d sorteos pasados siguen sin evaluarse en el historial" % len(stuck))
    else:
        add("predicciones", "ok", "El historial evalua los sorteos que ya han pasado")
    # Las combinaciones guardadas antes del sorteo no se pueden cambiar: se comprueba que se guardaron antes
    def _late(e):
        try:
            c = datetime.strptime(e["created_utc"][:10], "%Y-%m-%d").date()
            d = datetime.strptime(e["for_draw"], "%Y-%m-%d").date()
            return c > d + timedelta(days=1)     # margen de un dia por la diferencia horaria con Nueva York
        except Exception:  # noqa: BLE001
            return True
    late = [e for e in hist.get("live", []) if _late(e)]
    if late:
        add("predicciones", "error", "%d combinaciones se guardaron DESPUES de su sorteo (no valen para el historial)" % len(late))


def check_photos(add, fetch=http_get):
    """Noticias y anuncios deben tener foto. Las fotos de los anuncios deben cargar."""
    try:
        n = read_json(os.path.join(DATA_DIR, "news.json"))
        tot = img = 0
        for l in ("es", "en"):
            for k in ("news", "winners", "general"):
                for it in n.get(l, {}).get(k, []):
                    tot += 1
                    img += 1 if it.get("image") else 0
        if tot:
            pct = 100 * img // tot
            add("fotos", "ok" if pct >= 40 else "warn", "%d%% de las noticias traen foto (%d de %d)" % (pct, img, tot))
    except Exception:  # noqa: BLE001
        pass
    try:
        with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
    except Exception:  # noqa: BLE001
        return
    for name, src in re.findall(r'n:"([^"]+)"[^\n]*?img:"([^"]+)"', html):
        if src.startswith("http"):
            code, body = fetch(src, headers={"User-Agent": "Mozilla/5.0"})
            if code == 200 and len(body) > 1000:
                add("fotos", "ok", "Foto del anuncio %s carga bien" % name)
            else:
                add("fotos", "warn", "La foto del anuncio %s no carga (codigo %s): el anuncio no se muestra" % (name, code))
        elif os.path.exists(os.path.join(BASE_DIR, src)):
            add("fotos", "ok", "Foto del anuncio %s presente (%s)" % (name, src))
        else:
            add("fotos", "warn", "Falta la foto %s del anuncio %s: no se muestra hasta que la subas" % (src, name))


def check_script(add):
    """Revisa que el codigo de la web no tenga errores de sintaxis (si hay Node disponible)."""
    import shutil
    import tempfile
    node = shutil.which("node")
    if not node:
        add("codigo", "ok", "Sin Node en este equipo: no se revisa la sintaxis")
        return
    try:
        with open(os.path.join(BASE_DIR, "index.html"), "r", encoding="utf-8") as f:
            html = f.read()
        js = html.split("<script>", 1)[1].rsplit("</script>", 1)[0]
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as t:
            t.write(js)
        r = subprocess.run([node, "--check", t.name], capture_output=True, text=True)
        os.unlink(t.name)
        if r.returncode == 0:
            add("codigo", "ok", "El codigo de la web no tiene errores de sintaxis")
        else:
            add("codigo", "error", "Error de sintaxis en index.html: %s" % r.stderr.strip()[:200])
    except Exception as e:  # noqa: BLE001
        add("codigo", "warn", "No se pudo revisar el codigo (%s)" % e)


def run_checks(fetch=http_get):
    checks = []

    def add(area, level, detail):
        checks.append({"area": area, "level": level, "detail": detail})

    data_ok = check_data(add)
    check_news(add)
    check_site(add, fetch)
    check_links(add, fetch)
    check_content(add)
    check_predictions(add)
    check_botes(add)
    check_espana(add)
    check_photos(add, fetch)
    check_script(add)
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
    if any(c["area"] == "predicciones" and c["level"] == "error" for c in checks):
        print("Problema con las combinaciones: se vuelven a calcular.")
        sug = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sugerencias.py")
        subprocess.run([sys.executable, sug], check=False)
        checks, data_ok = run_checks()
        fixed = True
        if not any(c["area"] == "predicciones" and c["level"] == "error" for c in checks):
            checks.append({"area": "predicciones", "level": "ok",
                           "detail": "Corregido automaticamente: se recalcularon las combinaciones"})
    if any(c["area"] == "botes" and c["level"] == "error" for c in checks):
        print("Problema con los botes: se vuelven a descargar.")
        bot = os.path.join(os.path.dirname(os.path.abspath(__file__)), "botes.py")
        subprocess.run([sys.executable, bot], check=False)
        checks, data_ok = run_checks()
        fixed = True
        if not any(c["area"] == "botes" and c["level"] == "error" for c in checks):
            checks.append({"area": "botes", "level": "ok", "detail": "Corregido automaticamente: se volvieron a descargar los botes"})
    errors = ["[%s] %s" % (c["area"], c["detail"]) for c in checks if c["level"] == "error"]
    warnings = ["[%s] %s" % (c["area"], c["detail"]) for c in checks if c["level"] == "warn"]
    informe = {"checked_utc": iso(now_utc()), "ok": not errors, "errors": errors,
               "warnings": warnings, "auto_fixed": bool(fixed and not errors), "checks": checks}
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
