#!/usr/bin/env python3
"""
Monitor de alturas hidrométricas - Ganadera Fortines S.A.
Fuente: Secretaría de Recursos Hídricos, Santa Fe
"""

import json
import csv
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"

DESTINATARIOS = [
    "gabrielluna@ganaderafortines.com",
    "jorgeluna@ganaderafortines.com",
    "alejandroluna@ganaderafortines.com",
    "gonzaloluna@ganaderafortines.com",
]

# Orden del correo
ESTACIONES = [
    {"nombre": "El Bonete",          "id": 31102, "archivo_ultimo": BASE_DIR / "ultimo_bonete.json",   "archivo_historico": BASE_DIR / "historico_bonete.csv"},
    {"nombre": "Tostado (R.N. 95)",  "id": 31094, "archivo_ultimo": BASE_DIR / "ultimo_tostado.json",  "archivo_historico": BASE_DIR / "historico_tostado.csv"},
    {"nombre": "Calchaquí (R.P. 38)","id": 31101, "archivo_ultimo": BASE_DIR / "ultimo_calchaqui.json","archivo_historico": BASE_DIR / "historico_calchaqui.csv"},
    {"nombre": "Paso de las Piedras","id": 31095, "archivo_ultimo": BASE_DIR / "ultimo_piedras.json",  "archivo_historico": BASE_DIR / "historico_piedras.csv"},
]

API_PAGE  = "https://www.santafe.gob.ar/idesf/vis-pre/?user=rec_hidricos_alturas"
API_PROXY = "https://www.santafe.gob.ar/idesf/vis-pre/proxyPTRxml.php?url="
API_WFS   = "https://aswe.santafe.gov.ar/idesf/geoserver/RecursosHidricos/wfs/wfs"


def fetch_datos(ids):
    fecha = datetime.now().strftime("%Y-%m-%d")
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": API_PAGE,
    })
    session.get(API_PAGE, timeout=15)

    ids_str = ",".join(str(i) for i in ids)
    inner = (
        f"{API_WFS}?service=WFS&version=1.0.0&request=GetFeature"
        f"&typeName=diferencia_alturas&maxFeatures=100"
        f"&outputFormat=application/json"
        f"&CQL_FILTER=id_altura+IN+({ids_str})"
        f"&viewparams=fecha:{fecha}"
    )
    url = API_PROXY + requests.utils.quote(inner, safe="")
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    por_id = {}
    for f in data.get("features", []):
        p = f["properties"]
        por_id[p["id_altura"]] = p

    return por_id, fecha


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
    brusca     = ""
    if datos.get("variacion_brusca") is not None:
        d = datos["variacion_brusca"]
        brusca = f"\n  !! VARIACION BRUSCA: {'SUBIO' if d > 0 else 'BAJO'} {abs(d):.2f} m desde ayer !!"

    return (
        f"{datos['estacion']}{alerta_tag}\n"
        f"  Altura:    {altura}\n"
        f"  Variacion: {variacion}\n"
        f"  Estado:    {estado}{brusca}\n"
    )


def enviar_email(config, asunto, cuerpo_texto):
    remitente = config["gmail_usuario"]
    password  = config["gmail_password"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = asunto
    msg["From"]    = f"Ganadera Fortines <{remitente}>"
    msg["To"]      = ", ".join(DESTINATARIOS)

    cuerpo_completo = (
        "Ganadera Fortines S.A.\n"
        "Informe de los Rios\n"
        "----------------------------------------\n\n"
        + cuerpo_texto
        + "\n----------------------------------------\n"
        f"Fuente: Sec. Recursos Hidricos Santa Fe\n"
        f"Generado: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
    )

    msg.attach(MIMEText(cuerpo_completo, "plain", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as servidor:
        servidor.ehlo()
        servidor.starttls()
        servidor.login(remitente, password)
        servidor.sendmail(remitente, DESTINATARIOS, msg.as_string())


def notificacion_macos(titulo, mensaje):
    if sys.platform != "darwin":
        return
    script = f'display notification "{mensaje}" with title "{titulo}" sound name "Default"'
    subprocess.run(["osascript", "-e", script], check=False)


def cargar_config():
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"Falta {CONFIG_FILE}")
    with open(CONFIG_FILE) as f:
        config = json.load(f)
    config["gmail_usuario"] = config["gmail_usuario"].strip()
    config["gmail_password"] = config["gmail_password"].strip()
    return config


def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Monitor Rios - Ganadera Fortines")

    try:
        config = cargar_config()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    ids = [e["id"] for e in ESTACIONES]
    try:
        por_id, fecha_hoy = fetch_datos(ids)
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
        props  = por_id.get(estacion["id"])
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

        datos = {
            "estacion":   nombre,
            "fecha":      datetime.now().strftime("%d/%m/%Y"),
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

    if hay_dato_nuevo and datos_validos:
        cuerpo = ""
        for d in datos_validos:
            cuerpo += construir_bloque(d) + "\n"

        fecha_fmt = datetime.now().strftime("%d/%m/%Y")
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
            print(f"\nERROR al enviar mail: {e}", file=sys.stderr)
            notificacion_macos("Rios - Error mail", str(e))
    else:
        print("\nSin datos nuevos, no se envia mail.")

    print("\nListo.")


if __name__ == "__main__":
    main()
