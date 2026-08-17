#!/usr/bin/env python3
"""
Monitor de alturas hidrométricas - Ganadera Fortines S.A.
Fuente: Secretaría de Recursos Hídricos, Santa Fe
"""

import json
import csv
import os
import smtplib
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.image import MIMEImage
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import requests
from requests.exceptions import ConnectionError as ReqConnectionError, Timeout as ReqTimeout
from tenacity import (
    retry,
    stop_after_attempt,
    wait_fixed,
    retry_if_exception_type,
)

import nim_client

ARGENTINA_TZ = timezone(timedelta(hours=-3))

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

DESTINATARIOS = [
    "gabrielluna@ganaderafortines.com",
    "jorgeluna@ganaderafortines.com",
    "alejandroluna@ganaderafortines.com",
    "gonzaloluna@ganaderafortines.com",
]

# Orden geografico Norte -> Sur (Tostado hasta Santo Tome, a las puertas de Santa Fe).
# "clave" es substring (sin tildes, en minuscula) del nombre que devuelve la API.
# "curso" = curso de agua real. Tostado y del Bonete/Calchaqui hacia el sur son
# tributarios o el propio Salado; El Bonete es el Arroyo Golondrina y Calchaqui
# es el Rio Calchaqui (ambos alimentan al Salado, no son el Salado en si).
ESTACIONES = [
    {"nombre": "Tostado (Rio Salado, R.N. 95)",            "curso": "Rio Salado",    "clave": "tostado",      "archivo_ultimo": BASE_DIR / "ultimo_tostado.json",   "archivo_historico": BASE_DIR / "historico_tostado.csv"},
    {"nombre": "El Bonete (A° Golondrina)",              "curso": "A° Golondrina","clave": "bonete",       "archivo_ultimo": BASE_DIR / "ultimo_bonete.json",    "archivo_historico": BASE_DIR / "historico_bonete.csv"},
    {"nombre": "Calchaqui (Rio Calchaqui, R.P. 38)",       "curso": "Rio Calchaqui", "clave": "calchaqui",    "archivo_ultimo": BASE_DIR / "ultimo_calchaqui.json", "archivo_historico": BASE_DIR / "historico_calchaqui.csv"},
    {"nombre": "Paso de las Piedras (Rio Salado, La Penca)","curso": "Rio Salado",   "clave": "piedras",      "archivo_ultimo": BASE_DIR / "ultimo_piedras.json",   "archivo_historico": BASE_DIR / "historico_piedras.csv"},
    {"nombre": "San Justo (Rio Salado, R.P. 2)",           "curso": "Rio Salado",    "clave": "san justo",    "archivo_ultimo": BASE_DIR / "ultimo_sanjusto.json",  "archivo_historico": BASE_DIR / "historico_sanjusto.csv"},
    {"nombre": "Angeloni (RP 61)", "etiqueta": "Angeloni (RP 61)", "curso": "Rio Salado", "clave": "salado rp 61", "archivo_ultimo": BASE_DIR / "ultimo_saladorp61.json","archivo_historico": BASE_DIR / "historico_saladorp61.csv"},
    {"nombre": "Emilia (Rio Salado)",                      "curso": "Rio Salado",    "clave": "emilia",       "archivo_ultimo": BASE_DIR / "ultimo_emilia.json",    "archivo_historico": BASE_DIR / "historico_emilia.csv"},
    {"nombre": "Recreo (Rio Salado)",                      "curso": "Rio Salado",    "clave": "recreo",       "archivo_ultimo": BASE_DIR / "ultimo_recreo.json",    "archivo_historico": BASE_DIR / "historico_recreo.csv"},
    {"nombre": "Santo Tome (Rio Salado)",                  "curso": "Rio Salado",    "clave": "santo tome",   "archivo_ultimo": BASE_DIR / "ultimo_santotome.json", "archivo_historico": BASE_DIR / "historico_santotome.csv"},
]

# Estaciones nuevas del tramo aguas abajo (Paso de las Piedras -> Santo Tome), N->S.
# Se usan para que la IA integre el tramo hacia Santa Fe en la narrativa.
TRAMO_AGUAS_ABAJO = [
    ("san justo",       "San Justo"),
    ("angeloni",        "Angeloni (RP 61)"),
    ("emilia",          "Emilia"),
    ("recreo",          "Recreo"),
    ("santo tome",      "Santo Tome"),
]

FACEBOOK_PAGE_URL = "facebook.com/profile.php?id=1147087285146142"

LOCALIDADES_CLIMA = {
    "Vera":        (-29.47, -60.21),
    "Tostado":     (-29.23, -61.77),
    "Calchaqui":   (-29.89, -60.29),
    "Gob. Crespo": (-29.32, -61.00),
}

API_PAGE  = "https://www.santafe.gob.ar/idesf/vis-pre/?user=rec_hidricos_alturas"
API_PROXY = "https://www.santafe.gob.ar/idesf/vis-pre/proxyPTRxml.php?url="
API_WFS   = "https://aswe.santafe.gov.ar/idesf/geoserver/RecursosHidricos/wfs/wfs"


def _log_reintento(estado_retry):
    print(
        f"Santa Fe no responde (timeout/conexion), "
        f"reintento {estado_retry.attempt_number}/3 en 30s...",
        file=sys.stderr,
    )


@retry(
    # Solo reintentar ante timeout o error de conexion; nunca ante 4xx (HTTPError).
    retry=retry_if_exception_type((ReqConnectionError, ReqTimeout)),
    stop=stop_after_attempt(3),          # maximo 3 intentos
    wait=wait_fixed(30),                 # 30 segundos entre intentos
    before_sleep=_log_reintento,
    reraise=True,                        # tras el 3er fallo, re-lanza la excepcion
)
def fetch_datos(claves):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": API_PAGE,
    })
    session.get(API_PAGE, timeout=15)

    inner = (
        f"{API_WFS}?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName=diferencia_alturas&maxFeatures=200"
        f"&outputFormat=application/json"
    )
    url = API_PROXY + requests.utils.quote(inner, safe="")
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    # Buscar por nombre (substring) para evitar dependencia de IDs
    por_clave = {}
    for f in data.get("features", []):
        p = f["properties"]
        nombre_api = p.get("nombre", "").lower()
        # Normalizar: quitar tildes para comparar
        nombre_norm = nombre_api.replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
        for clave in claves:
            if clave in nombre_norm:
                por_clave[clave] = p
                break

    return por_clave


def cargar_ultimo(path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


def guardar_ultimo(path, datos):
    with open(path, "w") as f:
        json.dump(datos, f, indent=2, ensure_ascii=False)


def guardar_historico(path, datos):
    existe = path.exists()
    with open(path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not existe:
            writer.writerow(["fecha", "altura_m", "variacion_m", "estado", "estacion"])
        writer.writerow([datos["fecha"], datos["altura_m"], datos.get("variacion_m", ""), datos["estado"], datos["estacion"]])


def construir_bloque(datos):
    altura     = f"{datos['altura_m']:.2f} m"
    variacion  = f"{datos['variacion_m']:+.2f} m" if datos.get("variacion_m") is not None else "s/d"
    estado     = datos["estado"]
    alerta_tag = "  *** ALERTA ***" if estado == "ALERTA" else ""
    tendencia  = ""
    if datos.get("variacion_m") is not None:
        if datos["variacion_m"] > 0:
            tendencia = " - Sube"
        elif datos["variacion_m"] < 0:
            tendencia = " - Baja"
        else:
            tendencia = " - Sin cambios"
    brusca     = ""
    if datos.get("variacion_brusca") is not None:
        d = datos["variacion_brusca"]
        brusca = f"\n  !! VARIACION BRUSCA: {'SUBIO' if d > 0 else 'BAJO'} {abs(d):.2f} m desde ayer !!"

    return (
        f"{datos['estacion']}{alerta_tag}\n"
        f"  Altura:    {altura}\n"
        f"  Variacion: {variacion}\n"
        f"  Estado:    {estado}{tendencia}{brusca}\n"
    )


def _es_hora(s):
    """True si el string parece una hora HH:MM (columna 'hora' del formato antiguo)."""
    parts = s.strip().split(":")
    return len(parts) == 2 and all(p.isdigit() for p in parts)


def leer_historico(path, dias=7):
    """
    Retorna las últimas N variaciones diarias del CSV histórico como lista de floats.

    Maneja dos formatos que coexisten en los archivos:
      Antiguo (6 col): fecha, hora, altura_m, variacion_m, estado, estacion
      Nuevo   (5 col): fecha, altura_m, variacion_m, estado, estacion
    La detección se hace fila a fila mirando si la columna 1 parece una hora (HH:MM).
    """
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)          # saltar header
        rows = list(reader)

    variaciones = []
    for row in rows[-dias:]:
        if not row:
            continue
        try:
            if len(row) >= 2 and _es_hora(row[1]):
                # Formato antiguo: col[3] = variacion_m
                v = float(row[3]) if len(row) > 3 and row[3] else 0.0
            else:
                # Formato nuevo: col[2] = variacion_m
                v = float(row[2]) if len(row) > 2 and row[2] else 0.0
        except (ValueError, IndexError):
            v = 0.0
        variaciones.append(v)
    return variaciones


def dias_sin_subir(variaciones):
    """
    Cuenta registros consecutivos desde el más reciente donde la variación
    no superó el umbral de ruido (<=0.005 m). Un día con 0.0 cuenta como
    'sin subir' — resolución de medición, no un pico.
    """
    count = 0
    for v in reversed(variaciones):
        if v <= 0.005:
            count += 1
        else:
            break
    return count


def generar_comentario(resultados, precip=None, historicos=None):
    """
    Genera el comentario interpretativo siguiendo el orden geográfico norte→sur:
    Tostado (Río Salado) → Calchaquí → El Bonete (Golondrina) → Paso de las Piedras.

    Usa la tendencia histórica (últimos 7 registros) para no describir como
    novedades situaciones que ya llevan varios días consolidadas.

    Geografía clave:
    - Tostado: Río Salado, alimentado desde Santiago del Estero por el oeste.
    - El Bonete: Arroyo Golondrina (Ruta 98), tributario del Calchaquí.
      Si lleva 5+ registros sin subir, el Golondrina ya pasó su pico — el agua
      que ahora mueve el Calchaquí viene del propio cauce aguas arriba.
    - Paso de las Piedras: salida del sistema. Siempre 'drenando', nunca 'evacuando'.
    """
    if historicos is None:
        historicos = {}

    def get_est(clave):
        for r in resultados:
            if "error" not in r and clave.lower() in r["estacion"].lower():
                return r
        return None

    def trend(v):
        if v is None or abs(v) < 0.005:
            return "estable"
        return "sube" if v > 0 else "baja"

    T = get_est("tostado")
    B = get_est("bonete")
    C = get_est("calchaqui")
    P = get_est("piedras")

    at = T["altura_m"] if T else 0.0
    ab = B["altura_m"] if B else 0.0
    ac = C["altura_m"] if C else 0.0
    ap = P["altura_m"] if P else 0.0

    vt = T.get("variacion_m") if T else None
    vb = B.get("variacion_m") if B else None
    vc = C.get("variacion_m") if C else None
    vp = P.get("variacion_m") if P else None

    tt = trend(vt)
    tb = trend(vb)
    tc = trend(vc)
    tp = trend(vp)

    et = T["estado"] if T else "NORMAL"
    eb = B["estado"] if B else "NORMAL"
    ec = C["estado"] if C else "NORMAL"
    ep = P["estado"] if P else "NORMAL"

    hay_alerta = any(e == "ALERTA" for e in [et, eb, ec, ep])

    hist_t = historicos.get("tostado", [])
    hist_b = historicos.get("bonete", [])

    # Días consecutivos (registros) sin subir, desde el más reciente
    desc_t = dias_sin_subir(hist_t)
    plano_b = dias_sin_subir(hist_b)

    # Si el Bonete lleva 5+ registros sin subir → el Golondrina ya pasó su pico;
    # el agua que ahora mueve el Calchaquí viene del propio cauce aguas arriba.
    golondrina_ya_paso = plano_b >= 5

    partes = []

    # ── 1. Alertas ───────────────────────────────────────────────────────────
    alertas = [n for n, e in [("Tostado", et), ("Calchaqui", ec), ("El Bonete", eb), ("Paso de las Piedras", ep)] if e == "ALERTA"]
    if alertas:
        verbo = "esta" if len(alertas) == 1 else "estan"
        if len(alertas) <= 2:
            lista = " y ".join(alertas)
        else:
            lista = ", ".join(alertas[:-1]) + " y " + alertas[-1]
        partes.append(f"{lista} {verbo} en alerta")

    # ── 2. Tostado — Río Salado (agua desde Santiago del Estero) ─────────────
    if T:
        if tt == "sube":
            if et == "ALERTA":
                partes.append(
                    f"el Rio Salado en Tostado sigue subiendo ({at:.2f} m, en alerta) — "
                    f"hay nuevo aporte llegando desde el oeste"
                )
            else:
                partes.append(f"el Rio Salado en Tostado sube a {at:.2f} m")
        else:
            # baja o estable
            if et == "ALERTA":
                if desc_t >= 3:
                    partes.append(
                        f"el Rio Salado en Tostado mantiene una tendencia descendente sostenida — "
                        f"viene cediendo de forma gradual y hoy se ubica en {at:.2f} m, todavia en alerta"
                    )
                else:
                    partes.append(
                        f"el Rio Salado en Tostado empieza a ceder ({at:.2f} m) aunque sigue en zona de alerta"
                    )
            else:
                if desc_t >= 3:
                    partes.append(
                        f"el Rio Salado en Tostado ({at:.2f} m) viene bajando gradualmente"
                    )
                else:
                    partes.append(f"el Rio Salado en Tostado baja a {at:.2f} m")

    # ── 3. Calchaquí ─────────────────────────────────────────────────────────
    if C:
        if tc == "sube":
            if ec == "ALERTA":
                if golondrina_ya_paso:
                    partes.append(
                        f"el Calchaqui sigue subiendo ({ac:.2f} m, en alerta) — "
                        f"recibe el agua que el Arroyo Golondrina fue volcando en los dias previos, "
                        f"cuando alcanzo su pico en El Bonete"
                    )
                elif tb == "sube":
                    # Golondrina activo: ambos subiendo, aporte directo en curso
                    partes.append(
                        f"el Calchaqui sube y esta en alerta ({ac:.2f} m) — "
                        f"el Arroyo Golondrina sigue activo y le manda agua directamente"
                    )
                else:
                    # Golondrina recientemente pico (< 5 dias), agua propagandose
                    partes.append(
                        f"el Calchaqui sube y esta en alerta ({ac:.2f} m) — "
                        f"el agua del Golondrina que paso por El Bonete esta llegando"
                    )
            else:
                if golondrina_ya_paso:
                    partes.append(
                        f"el Calchaqui sube ({ac:.2f} m) — recibe el agua que el Arroyo Golondrina aporto en los dias previos"
                    )
                elif tb == "sube":
                    partes.append(
                        f"el Calchaqui sube ({ac:.2f} m) — el Arroyo Golondrina le aporta agua directamente"
                    )
                else:
                    partes.append(
                        f"el Calchaqui sube ({ac:.2f} m) — el agua del Golondrina avanza aguas abajo"
                    )
        elif tc == "baja":
            if ec == "ALERTA":
                partes.append(
                    f"el Calchaqui empieza a ceder ({ac:.2f} m), aunque todavia en alerta"
                )
            else:
                partes.append(f"el Calchaqui baja a {ac:.2f} m")
        else:
            if ec == "ALERTA":
                partes.append(f"el Calchaqui se mantiene en alerta ({ac:.2f} m) sin cambios por ahora")

    # ── 4. El Bonete / Arroyo Golondrina ─────────────────────────────────────
    if B:
        if golondrina_ya_paso:
            partes.append(
                f"el Arroyo Golondrina en El Bonete ({ab:.2f} m) lleva ya varios dias estable o cediendo — "
                f"el Golondrina alcanzo su pico en los dias previos y desde entonces drena gradualmente hacia el Calchaqui"
            )
        elif tb == "baja":
            partes.append(
                f"el Arroyo Golondrina en El Bonete empieza a ceder ({ab:.2f} m) — "
                f"el agua que bajo por el Golondrina esta llegando al Calchaqui"
            )
        elif tb == "sube":
            if eb == "ALERTA":
                partes.append(
                    f"el Arroyo Golondrina en El Bonete sigue subiendo ({ab:.2f} m, en alerta) — aporte activo hacia el Calchaqui"
                )
            else:
                partes.append(
                    f"el Arroyo Golondrina en El Bonete sube ({ab:.2f} m) y sigue aportando agua al Calchaqui"
                )
        else:
            partes.append(f"el Arroyo Golondrina en El Bonete se mantiene estable ({ab:.2f} m)")

    # ── 5. Paso de las Piedras — salida del sistema ───────────────────────────
    if P:
        calchaqui_presiona = (ec == "ALERTA" and tc == "sube")

        if tp == "sube":
            if ep == "ALERTA":
                partes.append(
                    f"Paso de las Piedras sube y esta en alerta ({ap:.2f} m) — "
                    f"el agua de los aportes del norte llego al punto de cierre"
                )
            else:
                partes.append(
                    f"Paso de las Piedras sube a {ap:.2f} m — el agua de aguas arriba esta llegando"
                )
        else:
            # baja o estable
            if ep == "ALERTA":
                partes.append(
                    f"Paso de las Piedras en {ap:.2f} m (en alerta) — el sistema esta drenando"
                )
            else:
                partes.append(
                    f"Paso de las Piedras baja a {ap:.2f} m — el sistema esta drenando"
                )
            if calchaqui_presiona:
                partes.append(
                    "ese descenso puede frenarse si el aporte del Calchaqui en alerta sigue llegando"
                )

    # ── 6. Cierre con contexto de lluvia (última frase, nada después) ─────────
    # El sistema está "subiendo" cuando la entrada principal (Tostado) crece,
    # o cuando el punto de cierre (Paso de las Piedras) sube en alerta.
    # El Calchaquí subiendo por efecto de demora del Golondrina no clasifica
    # como crecida activa si el Salado ya está cediendo.
    sistema_subiendo = tt == "sube" or (tp == "sube" and ep == "ALERTA")

    if precip:
        ll_tostado   = precip.get("Tostado",   {}).get("total", 0) or 0
        ll_calchaqui = precip.get("Calchaqui", {}).get("total", 0) or 0
        ll_vera      = precip.get("Vera",       {}).get("total", 0) or 0
        ll_max = max(ll_tostado, ll_calchaqui, ll_vera)

        if ll_max < 10:
            if sistema_subiendo:
                partes.append(
                    "sin lluvias locales significativas previstas — la presion sobre el sistema viene del agua que llega desde aguas arriba"
                )
            else:
                partes.append(
                    "sin lluvias significativas previstas en la cuenca, el sistema deberia seguir drenando gradualmente"
                )
        elif ll_max < 30:
            partes.append(
                "las lluvias previstas en la zona son leves y no deberian cambiar el comportamiento del sistema"
            )
        elif ll_max < 60:
            if hay_alerta:
                partes.append(
                    "hay lluvias moderadas previstas en la cuenca — con el sistema en alerta, podrian frenar el drenaje actual"
                )
            else:
                partes.append(
                    "hay lluvias moderadas previstas — a monitorear si los rios responden"
                )
        else:
            if hay_alerta:
                partes.append(
                    "se esperan lluvias importantes en la cuenca — podrian recargar el sistema y empeorar la situacion en las estaciones en alerta"
                )
            else:
                partes.append(
                    "se esperan lluvias importantes — el sistema podria volver a cargarse"
                )
    else:
        if hay_alerta:
            partes.append("situacion para seguir de cerca")

    if not partes:
        return "Sin novedades relevantes."

    texto = ". ".join(p[0].upper() + p[1:] for p in partes)
    if not texto.endswith("."):
        texto += "."
    return texto


def resumen_aguas_abajo(resultados):
    """
    Linea factual con las estaciones del tramo Paso de las Piedras -> Santo Tome,
    para que la IA la integre en la narrativa Norte->Sur. Devuelve "" si no hay datos.
    La logica hidrologica fina de las 4 estaciones originales sigue en generar_comentario;
    esto solo aporta los hechos del tramo nuevo hacia la ciudad de Santa Fe.
    """
    def get(sub):
        for r in resultados:
            if "error" not in r and sub in r["estacion"].lower():
                return r
        return None

    partes = []
    for sub, display in TRAMO_AGUAS_ABAJO:
        r = get(sub)
        if not r:
            continue
        estado = "en alerta" if r.get("estado") == "ALERTA" else "normal"
        partes.append(f"{display} {r['altura_m']:.2f} m ({estado})")
    if not partes:
        return ""
    return ("Aguas abajo, siguiendo el Salado hacia la ciudad de Santa Fe: "
            + ", ".join(partes) + ".")


def enviar_email(config, asunto, cuerpo_texto, img_path=None):
    remitente = config["gmail_usuario"]
    password  = config["gmail_password"]

    msg = MIMEMultipart()
    msg["Subject"] = asunto
    msg["From"]    = f"Ganadera Fortines <{remitente}>"
    msg["To"]      = ", ".join(DESTINATARIOS)

    cuerpo_completo = (
        "-Informe altura de los Rios-\n"
        "Fundacion Humedales y Pastizales.\n\n"
        + cuerpo_texto
        + "\n----------------------------------------\n"
        f"Fuente: Sec. Recursos Hidricos Santa Fe\n"
        f"Generado: {datetime.now(ARGENTINA_TZ).strftime('%d/%m/%Y %H:%M')}\n"
        f"{FACEBOOK_PAGE_URL}\n"
    )

    msg.attach(MIMEText(cuerpo_completo, "plain", "utf-8"))

    # Adjuntar la MISMA imagen que se publica en Facebook. Si falla, el mail
    # igual sale (solo texto) para no perder el envio.
    if img_path is not None:
        try:
            with open(img_path, "rb") as f:
                imagen = MIMEImage(f.read())
            imagen.add_header("Content-Disposition", "attachment", filename="informe_rios.png")
            msg.attach(imagen)
        except Exception as e:
            print(f"No se pudo adjuntar la imagen al mail: {e}", file=sys.stderr)

    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.ehlo()
        servidor.starttls()
        servidor.login(remitente, password)
        servidor.sendmail(remitente, DESTINATARIOS, msg.as_string())


def _font(size, bold=False):
    candidates = (
        ["/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
         "/Library/Fonts/Arial Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/Library/Fonts/Arial.ttf"]
    )
    for p in candidates:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def generar_imagen_rios(datos_validos, fecha_str):
    """
    Dibuja una tabla dinamica con TODAS las estaciones (Norte -> Sur), sin plantilla
    fija. Escala en alto segun la cantidad de estaciones (soporta 4 o 9 o las que sean).
    """
    AZUL     = (26,  58,  92)
    AZUL2    = (200, 214, 230)
    VERDE    = (30, 130,  76)
    ROJO     = (176,  0,  32)
    BLANCO   = (255, 255, 255)
    OSCURO   = (40,  40,  40)
    GRISFILA = (238, 241, 245)
    LINEA    = (210, 216, 224)

    W        = 1000
    MARGEN   = 40
    H_HEADER = 150
    H_COLHD  = 54
    ROW_H    = 74
    H_FOOTER = 66
    n = len(datos_validos)
    H = H_HEADER + H_COLHD + n * ROW_H + H_FOOTER

    img  = Image.new("RGB", (W, H), BLANCO)
    draw = ImageDraw.Draw(img)

    f_marca  = _font(32, bold=True)
    f_sub    = _font(19)
    f_fecha  = _font(23, bold=True)
    f_colh   = _font(18, bold=True)
    f_est    = _font(24, bold=True)
    f_alt    = _font(30, bold=True)
    f_var    = _font(19, bold=True)
    f_badge  = _font(16, bold=True)
    f_foot   = _font(15)

    # Centros X de las columnas de valores
    X_EST   = MARGEN
    X_ALT   = int(W * 0.55)
    X_VAR   = int(W * 0.71)
    X_BADGE = int(W * 0.88)

    # ── Banda superior ───────────────────────────────────────────────
    draw.rectangle([0, 0, W, H_HEADER], fill=AZUL)
    draw.text((MARGEN, 34), "Fundacion Humedales y Pastizales", font=f_marca, fill=BLANCO)
    draw.text((MARGEN, 94),
              "Alturas del Rio Salado   |   Tostado -> Santo Tome",
              font=f_sub, fill=AZUL2)
    draw.text((W - MARGEN, 42), fecha_str, font=f_fecha, fill=BLANCO, anchor="rm")

    # ── Encabezado de columnas ───────────────────────────────────────
    yc = H_HEADER
    draw.text((X_EST,   yc + H_COLHD // 2), "Estacion (Norte -> Sur)", font=f_colh, fill=AZUL, anchor="lm")
    draw.text((X_ALT,   yc + H_COLHD // 2), "Altura",    font=f_colh, fill=AZUL, anchor="mm")
    draw.text((X_VAR,   yc + H_COLHD // 2), "Variacion", font=f_colh, fill=AZUL, anchor="mm")
    draw.text((X_BADGE, yc + H_COLHD // 2), "Estado",    font=f_colh, fill=AZUL, anchor="mm")
    draw.line([MARGEN, yc + H_COLHD, W - MARGEN, yc + H_COLHD], fill=AZUL, width=2)

    # ── Filas ────────────────────────────────────────────────────────
    y0 = H_HEADER + H_COLHD
    for i, d in enumerate(datos_validos):
        top = y0 + i * ROW_H
        cy  = top + ROW_H // 2
        if i % 2 == 0:
            draw.rectangle([0, top, W, top + ROW_H], fill=GRISFILA)

        nombre_corto = d.get("etiqueta") or d["estacion"].split(" (")[0]
        curso = d.get("curso", "")
        # Aclarar entre parentesis solo los tributarios (no el propio Salado).
        if curso and "salado" not in curso.lower():
            draw.text((X_EST, cy - 11), nombre_corto, font=f_est, fill=OSCURO, anchor="lm")
            draw.text((X_EST, cy + 15), curso, font=f_foot, fill=(90, 105, 120), anchor="lm")
        else:
            draw.text((X_EST, cy), nombre_corto, font=f_est, fill=OSCURO, anchor="lm")
        draw.text((X_ALT, cy), f"{d['altura_m']:.2f} m", font=f_alt, fill=AZUL, anchor="mm")

        v = d.get("variacion_m")
        if v is None:
            draw.text((X_VAR, cy), "s/d", font=f_var, fill=OSCURO, anchor="mm")
        elif v == 0:
            draw.text((X_VAR, cy), "sin cambios", font=f_var, fill=OSCURO, anchor="mm")
        else:
            signo = "+" if v > 0 else "-"
            draw.text((X_VAR, cy), f"{signo}{abs(v):.2f} m", font=f_var,
                      fill=VERDE if v > 0 else ROJO, anchor="mm")

        es_alerta = d.get("estado") == "ALERTA"
        bw, bh = 150, 40
        bbox = [X_BADGE - bw // 2, cy - bh // 2, X_BADGE + bw // 2, cy + bh // 2]
        draw.rounded_rectangle(bbox, radius=10, fill=ROJO if es_alerta else VERDE)
        draw.text((X_BADGE, cy), "ALERTA" if es_alerta else "NORMAL",
                  font=f_badge, fill=BLANCO, anchor="mm")

    # ── Pie ──────────────────────────────────────────────────────────
    fy = H - H_FOOTER
    draw.line([MARGEN, fy, W - MARGEN, fy], fill=LINEA, width=1)
    draw.text((MARGEN, fy + H_FOOTER // 2),
              "Fuente: Sec. de Recursos Hidricos - Santa Fe", font=f_foot, fill=OSCURO, anchor="lm")
    gen = datetime.now(ARGENTINA_TZ).strftime("Generado %d/%m/%Y %H:%M")
    draw.text((W - MARGEN, fy + H_FOOTER // 2), gen, font=f_foot, fill=OSCURO, anchor="rm")

    img_path = BASE_DIR / "informe_rios.png"
    img.save(img_path)
    return img_path


def publicar_facebook(config, texto, img_path):
    page_token = config.get("facebook_page_token", "")
    if not page_token:
        print("ERROR Facebook: token vacío en config", file=sys.stderr)
        return
    try:
        with open(img_path, "rb") as img:
            resp = requests.post(
                "https://graph.facebook.com/v25.0/1147087285146142/photos",
                data={"message": texto, "access_token": page_token},
                files={"source": img},
                timeout=30,
            )
        print(f"Facebook: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Facebook error body: {resp.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR Facebook: {e}", file=sys.stderr)


def enviar_whatsapp(config, texto):
    phone  = config.get("callmebot_phone", "")
    apikey = config.get("callmebot_apikey", "")
    if not phone or not apikey:
        return
    try:
        resp = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": texto, "apikey": apikey},
            timeout=15,
        )
        print(f"WhatsApp enviado: {resp.status_code}")
    except Exception as e:
        print(f"ERROR WhatsApp: {e}", file=sys.stderr)


def notificacion_macos(titulo, mensaje):
    if sys.platform == "darwin":
        script = f'display notification "{mensaje}" with title "{titulo}" sound name "Default"'
        subprocess.run(["osascript", "-e", script], check=False)


def fetch_precipitaciones():
    resultados = {}
    for nombre, (lat, lon) in LOCALIDADES_CLIMA.items():
        try:
            url = (
                f"https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                f"&daily=precipitation_sum"
                f"&timezone=America%2FArgentina%2FBuenos_Aires"
                f"&forecast_days=7"
            )
            r = requests.get(url, timeout=10)
            data = r.json()
            dias   = data["daily"]["time"]
            lluvia = data["daily"]["precipitation_sum"]
            total  = round(sum(x for x in lluvia if x), 1)
            # Dia de mayor lluvia (excluir hoy)
            pares = list(zip(dias[1:], lluvia[1:]))
            dia_pico, mm_pico = max(pares, key=lambda x: x[1])
            resultados[nombre] = {"total": total, "dia_pico": dia_pico, "mm_pico": round(mm_pico, 1)}
        except Exception as e:
            print(f"CLIMA {nombre}: error {e}", file=sys.stderr)
    return resultados


def comentario_precipitaciones(precip, hay_alerta):
    if not precip:
        return ""

    DIAS_ES = {"Monday":"lun","Tuesday":"mar","Wednesday":"mie","Thursday":"jue","Friday":"vie","Saturday":"sab","Sunday":"dom"}

    lineas = []
    for nombre, d in precip.items():
        lineas.append(f"{nombre}: {d['total']} mm")
    resumen = " | ".join(lineas)

    # Dia del evento principal
    totales = [d["total"] for d in precip.values()]
    total_max = max(totales)
    picos = [d for d in precip.values() if d["mm_pico"] > 5]
    dia_evento = ""
    if picos:
        fecha_pico = datetime.strptime(picos[0]["dia_pico"], "%Y-%m-%d")
        dia_semana = DIAS_ES.get(fecha_pico.strftime("%A"), "")
        dia_evento = f" — evento principal: {dia_semana} {fecha_pico.strftime('%d/%m')}"

    if total_max < 10:
        interpretacion = "Sin lluvias relevantes previstas. El sistema podria seguir drenando."
    elif total_max < 30:
        interpretacion = "Lluvias leves previstas, sin impacto significativo esperado en los rios."
    elif total_max < 60:
        if hay_alerta:
            interpretacion = "Lluvias moderadas previstas. Con el sistema en alerta, podrian frenar el drenaje actual."
        else:
            interpretacion = "Lluvias moderadas previstas. A monitorear si los rios responden."
    else:
        if hay_alerta:
            interpretacion = "Lluvias importantes previstas. Podrian empeorar la situacion en las estaciones en alerta."
        else:
            interpretacion = "Lluvias importantes previstas. El sistema podria volver a cargarse."

    return f"Lluvia prevista 7 dias{dia_evento}:\n{resumen}\n{interpretacion}"


def cargar_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"Falta {CONFIG_FILE}")
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    config["gmail_usuario"] = config["gmail_usuario"].strip()
    config["gmail_password"] = config["gmail_password"].strip()
    return config


# Marcador para publicar como maximo una vez por dia (evita posts duplicados).
POST_MARKER = BASE_DIR / "ultimo_post_facebook.json"


def ya_publico_hoy():
    """True si ya se publico un boletin hoy (fecha local Argentina)."""
    if not POST_MARKER.exists():
        return False
    try:
        d = json.loads(POST_MARKER.read_text())
    except Exception:
        return False
    return d.get("fecha") == datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d")


def marcar_publicado_hoy():
    hoy = datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d")
    try:
        POST_MARKER.write_text(json.dumps({"fecha": hoy}))
    except Exception as e:
        print(f"No se pudo guardar el marcador de publicacion: {e}", file=sys.stderr)


def main():
    print(f"[{datetime.now(ARGENTINA_TZ).strftime('%Y-%m-%d %H:%M:%S')}] Monitor Rios - Ganadera Fortines")

    try:
        config = cargar_config()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    claves = [e["clave"] for e in ESTACIONES]
    try:
        por_clave = fetch_datos(claves)
    except (ReqConnectionError, ReqTimeout):
        print("Servidor Santa Fe no disponible tras 3 intentos — abortando", file=sys.stderr)
        notificacion_macos("Monitor Rios - Error", "Servidor Santa Fe no disponible tras 3 intentos")
        sys.exit(1)
    except Exception as e:
        print(f"ERROR al consultar API: {e}", file=sys.stderr)
        notificacion_macos("Monitor Rios - Error", str(e))
        sys.exit(1)

    resultados = []
    hay_dato_nuevo    = False
    hay_alerta        = False
    hay_variacion_brusca = False
    resumen_brusca    = ""

    for estacion in ESTACIONES:
        nombre = estacion["nombre"]
        props  = por_clave.get(estacion["clave"])
        print(f"\n--- {nombre} ---")

        if not props or props.get("altura") is None:
            print(f"  Sin dato disponible")
            resultados.append({"estacion": nombre, "error": "sin dato"})
            continue

        altura    = float(props["altura"])
        anterior  = float(props["anterior"]) if props.get("anterior") is not None else None
        variacion = round(altura - anterior, 2) if anterior is not None else None
        alerta_api = props.get("alerta_hidrologica", "")
        estado    = "ALERTA" if "alerta" in alerta_api.lower() else "NORMAL"

        # Usar la fecha que devuelve la API, no el reloj del servidor
        api_fecha_raw = props.get("fecha", "").rstrip("Z")
        try:
            fecha_dato = datetime.strptime(api_fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
        except ValueError:
            fecha_dato = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y")

        datos = {
            "estacion":   nombre,
            "curso":      estacion.get("curso", ""),
            "etiqueta":   estacion.get("etiqueta", ""),
            "fecha":      fecha_dato,
            "altura_m":   altura,
            "variacion_m": variacion,
            "estado":     estado,
        }

        print(f"  {datos['fecha']} | {altura:.2f} m | {estado}")

        ultimo = cargar_ultimo(estacion["archivo_ultimo"])
        nuevo  = (ultimo is None or ultimo.get("fecha") != datos["fecha"] or ultimo.get("altura_m") != altura)
        print(f"  Dato nuevo: {nuevo}")

        if nuevo:
            hay_dato_nuevo = True
            guardar_ultimo(estacion["archivo_ultimo"], datos)
            guardar_historico(estacion["archivo_historico"], datos)

        if estado == "ALERTA":
            hay_alerta = True

        if ultimo and anterior is not None:
            diff = altura - ultimo["altura_m"]
            if abs(diff) >= 0.50:
                datos["variacion_brusca"] = diff
                hay_variacion_brusca = True
                d = "SUBIO" if diff > 0 else "BAJO"
                resumen_brusca += f"{nombre.split(' (')[0]} {d} {abs(diff):.2f}m "

        resultados.append(datos)

    datos_validos = [r for r in resultados if "error" not in r]

    forzar = os.environ.get("FORCE_PUBLISH", "").lower() == "true"
    publico_hoy = ya_publico_hoy()

    # Publicar solo si se fuerza, o si hay dato nuevo y todavia NO se publico hoy.
    # Evita los posts duplicados cuando el script corre varias veces en el mismo dia.
    if datos_validos and (forzar or (hay_dato_nuevo and not publico_hoy)):
        cuerpo = ""
        for d in datos_validos:
            cuerpo += construir_bloque(d) + "\n"

        precip = fetch_precipitaciones()
        historicos = {e["clave"]: leer_historico(e["archivo_historico"]) for e in ESTACIONES}
        comentario = generar_comentario(resultados, precip, historicos)
        # Sumar el tramo nuevo (Paso de las Piedras -> Santo Tome) como hechos para la IA.
        tramo = resumen_aguas_abajo(resultados)
        if tramo:
            comentario = comentario + " " + tramo
        # NIM reescribe el comentario en prosa mas natural, conservando todos los
        # datos. Si NIM no esta disponible o falla, devuelve el mismo texto de la
        # plantilla (fallback), asi la publicacion nunca se rompe.
        comentario = nim_client.reescribir_boletin(comentario)
        cuerpo += comentario + "\n"

        bloque_clima = comentario_precipitaciones(precip, hay_alerta)
        if bloque_clima:
            cuerpo += "\n" + bloque_clima + "\n"

        fecha_fmt = datos_validos[0]["fecha"] if datos_validos else datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y")
        if hay_variacion_brusca:
            asunto = f"GF | Rios {fecha_fmt} | VARIACION BRUSCA {resumen_brusca.strip()}"
        elif hay_alerta:
            asunto = f"GF | Rios {fecha_fmt} | ALERTA"
        else:
            asunto = f"GF | Rios {fecha_fmt} | Normal"

        # Generar la imagen UNA sola vez: se usa igual en el mail y en Facebook.
        img_path = generar_imagen_rios(datos_validos, fecha_fmt)

        try:
            enviar_email(config, asunto, cuerpo, img_path)
            print(f"\nMail enviado: {asunto}")
            notificacion_macos("Informe Rios enviado", asunto)
        except Exception as e:
            print(f"\nERROR mail ({type(e).__name__}): {e}", file=sys.stderr)
            notificacion_macos("Rios - Error mail", str(e))

        # WhatsApp: tres mensajes para no superar el limite de caracteres
        msg_datos = "-Informe altura de los Rios-\nFundacion Humedales y Pastizales.\n\n" + "".join(construir_bloque(d) + "\n" for d in datos_validos) + f"\n{FACEBOOK_PAGE_URL}"
        enviar_whatsapp(config, msg_datos)
        enviar_whatsapp(config, comentario)
        if bloque_clima:
            enviar_whatsapp(config, bloque_clima)

        # Facebook: la MISMA imagen que fue al mail + texto completo
        mensaje = "-Informe altura de los Rios-\nFundacion Humedales y Pastizales.\n\n" + cuerpo + f"\n{FACEBOOK_PAGE_URL}"
        publicar_facebook(config, mensaje, img_path)

        marcar_publicado_hoy()   # registrar que ya se publico hoy (evita duplicados)
    elif datos_validos and hay_dato_nuevo and publico_hoy:
        print("\nYa se publico hoy; no se vuelve a publicar (usa FORCE_PUBLISH=true para forzar).")
    else:
        print("\nSin datos nuevos, no se envia.")

    print("\nListo.")


if __name__ == "__main__":
    main()
