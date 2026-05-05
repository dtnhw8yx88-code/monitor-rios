#!/usr/bin/env python3
"""
Monitor de alturas - Litoral / Principales estaciones del Paraná
Fuente: Prefectura Naval Argentina
Estaciones: IGUAZÚ, CORRIENTES, PARANÁ, SANTA FE, VICTORIA, GUALEGUAYCHÚ, PARANACITO
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import requests
from bs4 import BeautifulSoup

ARGENTINA_TZ = timezone(timedelta(hours=-3))
BASE_DIR     = Path(__file__).parent
CONFIG_FILE  = BASE_DIR / "config.json"

FACEBOOK_PAGE_ID  = "1147087285146142"
FACEBOOK_PAGE_URL = "facebook.com/profile.php?id=1147087285146142"

PNA_URL = "https://contenidosweb.prefecturanaval.gob.ar/alturas/"

ESTACIONES_LITORAL = [
    {"nombre": "IGUAZU",       "display": "Iguazú"},
    {"nombre": "CORRIENTES",   "display": "Corrientes"},
    {"nombre": "PARANA",       "display": "Paraná"},
    {"nombre": "SANTA FE",     "display": "Santa Fe"},
    {"nombre": "VICTORIA",     "display": "Victoria"},
    {"nombre": "GUALEGUAYCHU", "display": "Gualeguaychú"},
    {"nombre": "PARANACITO",   "display": "Paranacito"},
]


def fetch_datos_pna():
    resp = requests.get(PNA_URL, timeout=20, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MonitorLitoral/1.0)"
    })
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    por_nombre = {}
    for row in soup.find_all("tr"):
        cols = row.find_all(["th", "td"])
        if len(cols) < 10:
            continue
        vals = [c.get_text(strip=True) for c in cols]
        puerto = vals[0].strip()
        if puerto in por_nombre:
            continue
        por_nombre[puerto] = {
            "altura":    vals[2],
            "variacion": vals[3],
            "fecha":     vals[5],
            "estado":    vals[6],
            "alerta":    vals[10] if len(vals) > 10 else "-",
        }
    return por_nombre


def procesar_estacion(datos_pna, estacion):
    raw = datos_pna.get(estacion["nombre"])
    if not raw:
        return None

    try:
        altura = float(raw["altura"])
    except (ValueError, TypeError):
        return None

    try:
        variacion = float(raw["variacion"])
    except (ValueError, TypeError):
        variacion = None

    try:
        alerta_nivel = float(raw["alerta"]) if raw["alerta"] not in ("-", "", "S/E") else None
    except (ValueError, TypeError):
        alerta_nivel = None

    es_alerta = alerta_nivel is not None and altura >= alerta_nivel

    return {
        "nombre":      estacion["display"],
        "altura_m":    altura,
        "variacion_m": variacion,
        "estado":      "ALERTA" if es_alerta else "NORMAL",
        "fecha":       raw["fecha"],
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

    # Fila 1 (Iguazú): 42-48% → centro 45%; resto paso 6%
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

    try:
        datos_pna = fetch_datos_pna()
        print(f"Estaciones disponibles en PNA: {len(datos_pna)}")
    except Exception as e:
        print(f"ERROR al consultar Prefectura Naval: {e}", file=sys.stderr)
        sys.exit(1)

    datos = []
    for est in ESTACIONES_LITORAL:
        d = procesar_estacion(datos_pna, est)
        if d:
            datos.append(d)
            v = d["variacion_m"]
            v_str = f"{v:+.2f}" if v is not None else "s/d"
            print(f"  {est['nombre']}: {d['altura_m']:.2f} m | {v_str} | {d['estado']}")
        else:
            print(f"  {est['nombre']}: sin dato")

    if not datos:
        print("Sin datos disponibles — no se publica.")
        sys.exit(0)

    img_path = generar_imagen_litoral(datos, fecha_str)

    hay_alerta = any(d["estado"] == "ALERTA" for d in datos)
    lineas = []
    for d in datos:
        v = d.get("variacion_m")
        if v is not None:
            tend = "↑" if v > 0 else ("↓" if v < 0 else "→")
            lineas.append(f"{d['nombre']}: {d['altura_m']:.2f} m {tend}")
        else:
            lineas.append(f"{d['nombre']}: {d['altura_m']:.2f} m")

    encabezado = "⚠️ ALERTA en el Paraná\n" if hay_alerta else "Alturas del Paraná y afluentes\n"
    texto = (
        encabezado
        + "Principales estaciones del litoral — " + fecha_str + "\n\n"
        + "\n".join(lineas)
        + f"\n\nFuente: Prefectura Naval Argentina\n{FACEBOOK_PAGE_URL}"
    )

    print(f"\nTexto:\n{texto}\n")
    publicar_facebook(config, texto, img_path)
    print("Listo.")


if __name__ == "__main__":
    main()
