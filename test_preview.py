#!/usr/bin/env python3
"""Preview del mensaje completo sin enviar nada."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent))

from monitor import (fetch_datos, cargar_ultimo, generar_comentario, construir_bloque,
                     fetch_precipitaciones, comentario_precipitaciones,
                     ESTACIONES, FACEBOOK_PAGE_URL)
from datetime import datetime, timezone, timedelta

ARGENTINA_TZ = timezone(timedelta(hours=-3))

claves = [e["clave"] for e in ESTACIONES]
por_clave = fetch_datos(claves)

resultados = []
for estacion in ESTACIONES:
    nombre = estacion["nombre"]
    props  = por_clave.get(estacion["clave"])
    if not props or props.get("altura") is None:
        resultados.append({"estacion": nombre, "error": "sin dato"})
        continue

    altura    = float(props["altura"])
    anterior  = float(props["anterior"]) if props.get("anterior") is not None else None
    variacion = round(altura - anterior, 2) if anterior is not None else None
    alerta_api = props.get("alerta_hidrologica", "")
    estado    = "ALERTA" if "alerta" in alerta_api.lower() else "NORMAL"

    api_fecha_raw = props.get("fecha", "").rstrip("Z")
    try:
        fecha_dato = datetime.strptime(api_fecha_raw, "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        fecha_dato = datetime.now(ARGENTINA_TZ).strftime("%d/%m/%Y")

    datos = {
        "estacion":    nombre,
        "fecha":       fecha_dato,
        "altura_m":    altura,
        "variacion_m": variacion,
        "estado":      estado,
    }

    ultimo = cargar_ultimo(estacion["archivo_ultimo"])
    if ultimo:
        diff = altura - ultimo["altura_m"]
        if abs(diff) >= 0.50:
            datos["variacion_brusca"] = diff

    resultados.append(datos)

datos_validos = [r for r in resultados if "error" not in r]
hay_alerta    = any(r.get("estado") == "ALERTA" for r in datos_validos)

cuerpo = ""
for d in datos_validos:
    cuerpo += construir_bloque(d) + "\n"

comentario   = generar_comentario(resultados)
precip       = fetch_precipitaciones()
bloque_clima = comentario_precipitaciones(precip, hay_alerta)

cuerpo += comentario + "\n"
if bloque_clima:
    cuerpo += "\n" + bloque_clima + "\n"

mensaje = (
    "-Informe altura de los Rios-\n"
    "Fundacion Humedales y Pastizales.\n\n"
    + cuerpo
    + f"\n{FACEBOOK_PAGE_URL}"
)

print("=" * 60)
print(mensaje)
print("=" * 60)
print("\n--- WhatsApp msg 2 (analisis) ---")
print(comentario)
print("\n--- WhatsApp msg 3 (clima) ---")
print(bloque_clima)
