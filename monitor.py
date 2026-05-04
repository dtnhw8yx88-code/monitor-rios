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
import textwrap
from datetime import datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import requests

ARGENTINA_TZ = timezone(timedelta(hours=-3))

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

DESTINATARIOS = [
    "gabrielluna@ganaderafortines.com",
    "jorgeluna@ganaderafortines.com",
    "alejandroluna@ganaderafortines.com",
    "gonzaloluna@ganaderafortines.com",
]

# Orden del correo — "clave" es substring del nombre en la API
ESTACIONES = [
    {"nombre": "El Bonete (Golondrina, Vera)",        "clave": "bonete",   "archivo_ultimo": BASE_DIR / "ultimo_bonete.json",   "archivo_historico": BASE_DIR / "historico_bonete.csv"},
    {"nombre": "Tostado (Rio Salado, R.N. 95)",       "clave": "tostado",  "archivo_ultimo": BASE_DIR / "ultimo_tostado.json",  "archivo_historico": BASE_DIR / "historico_tostado.csv"},
    {"nombre": "Calchaqui (Rio Calchaqui, R.P. 38)",  "clave": "calchaqui","archivo_ultimo": BASE_DIR / "ultimo_calchaqui.json","archivo_historico": BASE_DIR / "historico_calchaqui.csv"},
    {"nombre": "Paso de las Piedras (Rio Salado, La Penca)","clave": "piedras",  "archivo_ultimo": BASE_DIR / "ultimo_piedras.json",  "archivo_historico": BASE_DIR / "historico_piedras.csv"},
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


def generar_comentario(resultados):
    def get_est(clave):
        for r in resultados:
            if "error" not in r and clave.lower() in r["estacion"].lower():
                return r
        return None

    def clasif(v):
        if v is None or abs(v) < 0.005:
            return "estable", None
        return ("leve" if abs(v) <= 0.03 else "marcada"), (v > 0)

    T = get_est("tostado")
    B = get_est("bonete")
    C = get_est("calchaqui")
    P = get_est("piedras")

    hay_alerta = any(r.get("estado") == "ALERTA" for r in resultados if "error" not in r)

    def info(est):
        if not est:
            return "estable", None, 0.0, "NORMAL"
        v = est.get("variacion_m")
        d, s = clasif(v)
        return d, s, float(est.get("altura_m", 0)), est.get("estado", "NORMAL")

    dt, st, at, et = info(T)
    db, sb, ab, eb = info(B)
    dc, sc, ac, ec = info(C)
    dp, sp, ap, ep = info(P)

    n_sube = sum(1 for s in [st, sb, sc] if s is True)
    n_alerta_sube = sum(1 for s, e in [(sb, eb), (sc, ec)] if s is True and e == "ALERTA")

    oraciones = []

    # Alertas activas
    alertas = [nombre for nombre, est in [("Tostado", et), ("Calchaqui", ec), ("El Bonete", eb), ("Paso de las Piedras", ep)] if est == "ALERTA"]
    if alertas:
        oraciones.append(f"{' y '.join(alertas)} {'esta' if len(alertas) == 1 else 'estan'} en alerta")

    # Tostado: entrada principal del sistema
    if dt != "estable":
        if st:
            if et == "ALERTA":
                oraciones.append(f"Tostado sube ({at:.2f} m) — el Salado sigue empujando agua desde el oeste")
            else:
                oraciones.append(f"Tostado sube a {at:.2f} m")
        else:
            if et == "ALERTA":
                oraciones.append(f"Tostado cede un poco ({at:.2f} m) pero sigue en zona de alerta")
            else:
                oraciones.append(f"Tostado baja a {at:.2f} m")
    else:
        if et == "ALERTA":
            oraciones.append(f"Tostado no cambia pero sigue alto ({at:.2f} m, en alerta)")

    # Calchaqui y Bonete: aportes internos
    internos_subiendo = []
    internos_bajando  = []
    if sc is True:
        if ec == "ALERTA":
            internos_subiendo.append(f"Calchaqui sube y esta en alerta ({ac:.2f} m)")
        else:
            internos_subiendo.append(f"Calchaqui sube ({ac:.2f} m)")
    elif sc is False:
        internos_bajando.append(f"Calchaqui baja ({ac:.2f} m)")

    if sb is True:
        if eb == "ALERTA":
            internos_subiendo.append(f"El Bonete sube y esta en alerta ({ab:.2f} m)")
        else:
            internos_subiendo.append(f"El Bonete tambien sube ({ab:.2f} m)")
    elif sb is False:
        internos_bajando.append(f"El Bonete baja ({ab:.2f} m)")

    for frase in internos_subiendo + internos_bajando:
        oraciones.append(frase)

    # Paso de las Piedras: resultado final
    if P:
        if sp is True:
            oraciones.append(f"Paso de las Piedras sube a {ap:.2f} m — el agua llega al punto de cierre")
            if n_alerta_sube >= 1:
                oraciones.append("La situacion puede seguir empeorando si se mantienen los aportes del norte")
        elif sp is False:
            oraciones.append(f"Paso de las Piedras baja a {ap:.2f} m — el sistema esta drenando")
            if n_sube >= 1 and n_alerta_sube >= 1:
                oraciones.append("Ojo: con Calchaqui o Tostado todavia en alerta, ese descenso puede no durar")
            elif n_sube >= 1:
                oraciones.append("Hay aportes aguas arriba que podrian frenar esa baja")
        else:
            if n_sube >= 1:
                oraciones.append(f"Paso de las Piedras se mantiene en {ap:.2f} m, el agua de arriba todavia no llego")
            else:
                oraciones.append(f"Paso de las Piedras estable en {ap:.2f} m")

    cierre = "Situacion para seguir de cerca." if hay_alerta else "Sin novedades por ahora."
    return ". ".join(oraciones) + ". " + cierre


def enviar_email(config, asunto, cuerpo_texto):
    remitente = config["gmail_usuario"]
    password  = config["gmail_password"]

    msg = MIMEMultipart("alternative")
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


def generar_imagen_rios(datos_validos, fecha_str, comentario):
    template = BASE_DIR / "template_rios.png"
    img  = Image.open(template).convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    AZUL   = (26,  58,  92)
    VERDE  = (30, 130,  76)
    ROJO   = (176,  0,  32)
    BLANCO = (255, 255, 255)
    OSCURO = (40,  40,  40)

    f_fecha  = _font(int(W * 0.026), bold=True)
    f_altura = _font(int(W * 0.038), bold=True)
    f_var    = _font(int(W * 0.026), bold=True)
    f_estado = _font(int(W * 0.024), bold=True)
    f_tend   = _font(int(W * 0.021))

    # Fecha
    draw.text((int(W * 0.535), int(H * 0.382)),
              fecha_str, font=f_fecha, fill=AZUL, anchor="mm")

    # Filas de estaciones
    ROW_CY     = [0.500, 0.600, 0.695, 0.800]
    COL_ALTURA = 0.420
    COL_VAR    = 0.645
    COL_ESTADO = 0.855

    for i, d in enumerate(datos_validos[:4]):
        cy = int(H * ROW_CY[i])
        es_alerta = d.get("estado") == "ALERTA"

        draw.text((int(W * COL_ALTURA), cy),
                  f"{d['altura_m']:.2f}", font=f_altura, fill=AZUL, anchor="mm")

        v = d.get("variacion_m")
        if v is not None:
            flecha  = "+" if v > 0 else ("-" if v < 0 else "")
            color_v = VERDE if v > 0 else (ROJO if v < 0 else OSCURO)
            draw.text((int(W * COL_VAR), cy),
                      f"{flecha}{abs(v):.2f} m", font=f_var, fill=color_v, anchor="mm")

        bw = int(W * 0.130); bh = int(H * 0.038)
        bx = int(W * COL_ESTADO)
        bbox = [bx - bw//2, cy - bh//2, bx + bw//2, cy + bh//2]
        draw.rounded_rectangle(bbox, radius=int(bh * 0.35),
                                fill=ROJO if es_alerta else VERDE)
        draw.text((bx, cy), "ALERTA" if es_alerta else "NORMAL",
                  font=f_estado, fill=BLANCO, anchor="mm")

    # Tendencia general — solo texto, sin números
    n_sube = sum(1 for d in datos_validos if (d.get("variacion_m") or 0) > 0)
    n_baja = sum(1 for d in datos_validos if (d.get("variacion_m") or 0) < 0)
    tend_corta = "En ascenso" if n_sube > n_baja else ("En descenso" if n_baja > n_sube else "Estable")
    draw.text((int(W * 0.375), int(H * 0.875)),
              tend_corta, font=f_tend, fill=AZUL, anchor="mm")

    oraciones  = comentario.split(". ")
    tend_larga = ". ".join(oraciones[:2]) + "."
    lines  = textwrap.wrap(tend_larga, width=38)
    line_h = int(H * 0.026)
    ty     = int(H * 0.858)
    for line in lines[:3]:
        draw.text((int(W * 0.470), ty), line, font=f_tend, fill=OSCURO, anchor="lm")
        ty += line_h

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

    if (hay_dato_nuevo or forzar) and datos_validos:
        cuerpo = ""
        for d in datos_validos:
            cuerpo += construir_bloque(d) + "\n"

        comentario = generar_comentario(resultados)
        cuerpo += comentario + "\n"

        precip = fetch_precipitaciones()
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

        try:
            enviar_email(config, asunto, cuerpo)
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

        # Facebook: imagen generada + texto completo
        mensaje = "-Informe altura de los Rios-\nFundacion Humedales y Pastizales.\n\n" + cuerpo + f"\n{FACEBOOK_PAGE_URL}"
        img_path = generar_imagen_rios(datos_validos, fecha_fmt, comentario)
        publicar_facebook(config, mensaje, img_path)
    else:
        print("\nSin datos nuevos, no se envia mail.")

    print("\nListo.")


if __name__ == "__main__":
    main()
