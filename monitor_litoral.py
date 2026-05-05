#!/usr/bin/env python3
"""
Monitor de alturas - Litoral / Principales estaciones del Paraná
Fuente: INA SIyAH (Instituto Nacional del Agua)
Estaciones: Iguazú, Corrientes, Paraná, Santa Fe, Victoria, Gualeguaychú, Paranacito
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import requests

ARGENTINA_TZ = timezone(timedelta(hours=-3))
BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / "config.json"

FACEBOOK_PAGE_ID  = "1147087285146142"
FACEBOOK_PAGE_URL = "facebook.com/profile.php?id=1147087285146142"

INA_BASE = "https://alerta.ina.gob.ar/a5"

# series_id de INA = estacion_id para cada estación PNA
ESTACIONES_LITORAL = [
    {"series_id":  9, "display": "Iguazú",       "nivel_alerta": 25.0},
    {"series_id": 19, "display": "Corrientes",    "nivel_alerta":  6.5},
    {"series_id": 29, "display": "Paraná",        "nivel_alerta":  4.7},
    {"series_id": 30, "display": "Santa Fe",      "nivel_alerta":  5.3},
    {"series_id": 32, "display": "Victoria",      "nivel_alerta":  4.6},
    {"series_id": 99, "display": "Gualeguaychú",  "nivel_alerta":  3.5},
    {"series_id": 43, "display": "Paranacito",    "nivel_alerta":  2.3},
]


def fetch_observaciones(series_id):
    ahora      = datetime.now(ARGENTINA_TZ)
    timestart  = (ahora - timedelta(days=2)).strftime("%Y-%m-%dT00:00:00")
    timeend    = ahora.strftime("%Y-%m-%dT23:59:59")
    url = (
        f"{INA_BASE}/obs/puntual/observaciones/"
        f"?series_id={series_id}&timestart={timestart}&timeend={timeend}&output=json"
    )
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else data.get("rows", [])


def procesar_estacion(est):
    obs = fetch_observaciones(est["series_id"])
    if not obs:
        return None

    ultimo    = obs[-1]
    penultimo = obs[-2] if len(obs) >= 2 else None

    try:
        altura = float(ultimo["valor"])
    except (ValueError, TypeError, KeyError):
        return None

    variacion = None
    if penultimo:
        try:
            variacion = round(altura - float(penultimo["valor"]), 2)
        except (ValueError, TypeError, KeyError):
            pass

    es_alerta = altura >= est["nivel_alerta"]

    return {
        "nombre":      est["display"],
        "altura_m":    altura,
        "variacion_m": variacion,
        "estado":      "ALERTA" if es_alerta else "NORMAL",
    }


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


def generar_imagen_litoral(datos, fecha_str):
    template = BASE_DIR / "template_litoral.png"
    img  = Image.open(template).convert("RGB")
    draw = ImageDraw.Draw(img)
    W, H = img.size

    AZUL   = (26,  58,  92)
    VERDE  = (30, 130,  76)
    ROJO   = (176,  0,  32)
    BLANCO = (255, 255, 255)
    OSCURO = (40,  40,  40)

    f_fecha  = _font(int(W * 0.026), bold=True)
    f_altura = _font(int(W * 0.034), bold=True)
    f_var    = _font(int(W * 0.023), bold=True)
    f_estado = _font(int(W * 0.021), bold=True)
    f_tend   = _font(int(W * 0.021))

    draw.text((int(W * 0.320), int(H * 0.345)),
              fecha_str, font=f_fecha, fill=AZUL, anchor="mm")

    ROW_CY     = [0.450, 0.510, 0.570, 0.630, 0.690, 0.750, 0.810]
    COL_ALTURA = 0.490
    COL_VAR    = 0.685
    COL_ESTADO = 0.880

    for i, d in enumerate(datos[:7]):
        cy = int(H * ROW_CY[i])
        es_alerta = d.get("estado") == "ALERTA"

        draw.text((int(W * COL_ALTURA), cy),
                  f"{d['altura_m']:.2f}", font=f_altura, fill=AZUL, anchor="mm")

        v = d.get("variacion_m")
        if v is not None:
            if v == 0:
                draw.text((int(W * COL_VAR), cy), "Sin cambios", font=f_var, fill=OSCURO, anchor="mm")
            else:
                flecha  = "+" if v > 0 else "-"
                color_v = VERDE if v > 0 else ROJO
                draw.text((int(W * COL_VAR), cy),
                          f"{flecha}{abs(v):.2f} m", font=f_var, fill=color_v, anchor="mm")

        v_val = d.get("variacion_m") or 0
        tendencia_badge = "Sube" if v_val > 0 else ("Baja" if v_val < 0 else "Sin cambios")
        estado_txt = "ALERTA" if es_alerta else "NORMAL"

        bw = int(W * 0.110); bh = int(H * 0.042)
        bx = int(W * COL_ESTADO)
        bbox = [bx - bw//2, cy - bh//2, bx + bw//2, cy + bh//2]
        draw.rounded_rectangle(bbox, radius=int(bh * 0.25),
                                fill=ROJO if es_alerta else VERDE)
        draw.text((bx, cy - int(bh * 0.18)), estado_txt,
                  font=f_estado, fill=BLANCO, anchor="mm")
        draw.text((bx, cy + int(bh * 0.22)), tendencia_badge,
                  font=_font(int(W * 0.016)), fill=BLANCO, anchor="mm")

    n_sube = sum(1 for d in datos if (d.get("variacion_m") or 0) > 0)
    n_baja = sum(1 for d in datos if (d.get("variacion_m") or 0) < 0)
    tend_corta = "En ascenso" if n_sube > n_baja else ("En descenso" if n_baja > n_sube else "Estable")
    draw.text((int(W * 0.320), int(H * 0.885)),
              tend_corta, font=f_tend, fill=AZUL, anchor="mm")

    img_path = BASE_DIR / "informe_litoral.png"
    img.save(img_path)
    return img_path


def publicar_facebook(config, texto, img_path):
    page_token = config.get("facebook_page_token", "")
    if not page_token:
        print("Sin token de Facebook — no se publica")
        return
    try:
        with open(img_path, "rb") as img:
            resp = requests.post(
                f"https://graph.facebook.com/v25.0/{FACEBOOK_PAGE_ID}/photos",
                data={"message": texto, "access_token": page_token},
                files={"source": img},
                timeout=30,
            )
        print(f"Facebook: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Facebook error body: {resp.text[:300]}", file=sys.stderr)
    except Exception as e:
        print(f"ERROR Facebook: {e}", file=sys.stderr)


def main():
    ahora     = datetime.now(ARGENTINA_TZ)
    fecha_str = ahora.strftime("%d/%m/%Y")

    print(f"[{ahora.strftime('%Y-%m-%d %H:%M:%S')}] Monitor Litoral")

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    datos = []
    for est in ESTACIONES_LITORAL:
        try:
            d = procesar_estacion(est)
            if d:
                datos.append(d)
                v_str = f"{d['variacion_m']:+.2f}" if d["variacion_m"] is not None else "s/d"
                print(f"  {est['display']:15s}: {d['altura_m']:.2f} m | {v_str} | {d['estado']}")
            else:
                print(f"  {est['display']:15s}: sin dato")
        except Exception as e:
            print(f"  {est['display']:15s}: ERROR {e}", file=sys.stderr)

    if not datos:
        print("Sin datos disponibles — no se publica.")
        sys.exit(0)

    img_path = generar_imagen_litoral(datos, fecha_str)

    hay_alerta = any(d["estado"] == "ALERTA" for d in datos)
    lineas = []
    for d in datos:
        v = d.get("variacion_m")
        tend = "↑" if (v or 0) > 0 else ("↓" if (v or 0) < 0 else "→")
        lineas.append(f"{d['nombre']}: {d['altura_m']:.2f} m {tend}")

    encabezado = "⚠️ ALERTA en el Paraná\n" if hay_alerta else "Alturas del Paraná y afluentes\n"
    texto = (
        encabezado
        + "Principales estaciones del litoral — " + fecha_str + "\n\n"
        + "\n".join(lineas)
        + f"\n\nFuente: INA - Instituto Nacional del Agua\n{FACEBOOK_PAGE_URL}"
    )

    print(f"\nTexto:\n{texto}\n")
    publicar_facebook(config, texto, img_path)
    print("Listo.")


if __name__ == "__main__":
    main()
