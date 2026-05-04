#!/usr/bin/env python3
"""
Monitor de focos de calor - Fundación Humedales y Pastizales
Fuente: NASA FIRMS / VIIRS NOAA-20
Departamentos: Vera, 9 de Julio, Gral. Obligado, San Justo, San Cristóbal, San Javier
"""

import csv
import io
import json
import sys
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from pyproj import Transformer
from pathlib import Path
from datetime import datetime, timezone, timedelta

ARGENTINA_TZ = timezone(timedelta(hours=-3))
BASE_DIR      = Path(__file__).parent
CONFIG_FILE   = BASE_DIR / "config.json"
ESTADO_FILE   = BASE_DIR / "estado_incendios.json"

FACEBOOK_PAGE_ID  = "1147087285146142"
FACEBOOK_PAGE_URL = "facebook.com/profile.php?id=1147087285146142"
DEPARTAMENTOS     = "Vera, 9 de Julio, Gral. Obligado, San Justo, San Cristóbal y San Javier"

# Bounding box de los 6 departamentos: W,S,E,N
BBOX = "-63.0,-31.5,-58.5,-27.5"

CIUDADES = {
    "Reconquista":   (-29.15, -59.65),
    "Vera":          (-29.47, -60.21),
    "Tostado":       (-29.23, -61.77),
    "San Justo":     (-30.79, -60.59),
    "San Cristobal": (-30.31, -61.24),
    "San Javier":    (-30.58, -59.93),
    "Calchaqui":     (-29.89, -60.29),
    "Malabrigo":     (-29.35, -59.97),
}


def fetch_focos(firms_key):
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv"
        f"/{firms_key}/VIIRS_SNPP_NRT/{BBOX}/1"
    )
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()
    focos = []
    reader = csv.DictReader(io.StringIO(resp.text))
    for row in reader:
        try:
            focos.append({
                "lat": float(row["latitude"]),
                "lon": float(row["longitude"]),
                "frp": float(row.get("frp") or 0),
                "confidence": row.get("confidence", ""),
                "acq_date": row.get("acq_date", ""),
                "acq_time": row.get("acq_time", ""),
            })
        except (ValueError, KeyError):
            continue
    return focos


def clasificar_focos(focos):
    if not focos:
        return 0, "ninguno"
    max_frp = max(f["frp"] for f in focos)
    if max_frp > 50:
        return max_frp, "alto"
    elif max_frp > 10:
        return max_frp, "moderado"
    else:
        return max_frp, "bajo"


def localizar_focos(focos):
    if not focos:
        return "la zona monitoreada"
    avg_lat = sum(f["lat"] for f in focos) / len(focos)
    avg_lon = sum(f["lon"] for f in focos) / len(focos)
    closest = min(CIUDADES, key=lambda n: (avg_lat - CIUDADES[n][0])**2 + (avg_lon - CIUDADES[n][1])**2)
    return f"zona de {closest}"


def generar_mapa(focos, fecha_str):
    import contextily as ctx

    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    W, S, E, N = -63.0, -31.5, -58.5, -27.5
    xmin, ymin = transformer.transform(W, S)
    xmax, ymax = transformer.transform(E, N)

    fig, ax = plt.subplots(figsize=(10, 12))
    ax.set_xlim(xmin, xmax)
    ax.set_ylim(ymin, ymax)
    ctx.add_basemap(ax, source=ctx.providers.OpenStreetMap.Mapnik, zoom=8, reset_extent=False)

    # Ciudades de referencia
    for nombre, (lat, lon) in CIUDADES.items():
        cx, cy = transformer.transform(lon, lat)
        ax.scatter([cx], [cy], c="#333333", s=20, zorder=4, alpha=0.8)
        ax.annotate(nombre, xy=(cx, cy), xytext=(5, 5), textcoords="offset points",
                    fontsize=8, color="#222222", fontweight="bold", zorder=6)

    if focos:
        xs, ys, frps_list = [], [], []
        for f in focos:
            x, y = transformer.transform(f["lon"], f["lat"])
            xs.append(x); ys.append(y); frps_list.append(f["frp"])

        colores = ["#ff2200" if f > 50 else "#ff8c00" if f > 10 else "#ffdd00" for f in frps_list]
        sizes   = [max(150, f * 3.0) for f in frps_list]
        ax.scatter(xs, ys, c=colores, s=sizes, marker="*", zorder=5, alpha=0.95,
                   edgecolors="white", linewidths=0.4)

        titulo = (
            f"Focos de calor activos\n"
            f"Depts. Vera · 9 de Julio · Gral. Obligado · San Justo · San Cristóbal · San Javier\n"
            f"{fecha_str}"
        )
        legend_elements = [
            mpatches.Patch(color="#ffdd00", label="Bajo < 10 MW  (posible quema controlada)"),
            mpatches.Patch(color="#ff8c00", label="Moderado  10–50 MW"),
            mpatches.Patch(color="#ff2200", label="Alto > 50 MW"),
        ]
        ax.legend(handles=legend_elements, loc="lower left", fontsize=9,
                  facecolor="white", edgecolor="#cccccc", labelcolor="#111111",
                  title="Intensidad (FRP)", title_fontsize=9)
    else:
        titulo = (
            f"Sin focos de calor detectados\n"
            f"Depts. Vera · 9 de Julio · Gral. Obligado · San Justo · San Cristóbal · San Javier\n"
            f"{fecha_str}"
        )
        ax.text(0.5, 0.5, "✓  Sin focos activos", transform=ax.transAxes,
                ha="center", va="center", fontsize=18, color="#2d7a2d", fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.6", facecolor="white", alpha=0.88))

    ax.set_title(titulo, color="#111111", fontsize=11, fontweight="bold", pad=12,
                 linespacing=1.6,
                 bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.85))
    ax.text(0.99, 0.005, "Fuente: NASA FIRMS / VIIRS NOAA-20",
            transform=ax.transAxes, ha="right", va="bottom", fontsize=7, color="#555555")
    ax.axis("off")
    plt.tight_layout()

    img_path = BASE_DIR / "mapa_incendios.png"
    plt.savefig(img_path, dpi=150, bbox_inches="tight")
    plt.close()
    return img_path


def generar_texto(focos, modo, turno):
    ahora     = datetime.now(ARGENTINA_TZ)
    fecha_str = ahora.strftime("%d/%m/%Y  %H:%M hs")
    n         = len(focos)

    if modo == "sin_focos":
        cierre = "mediodía" if turno == "manana" else "cierre del día"
        return (
            f"Sin focos de calor detectados hoy al {cierre} en los departamentos {DEPARTAMENTOS}. "
            f"El sistema satelital NASA FIRMS no registra actividad en la zona. 🌿"
        )

    if modo == "apagado":
        zona = localizar_focos([])
        return (
            f"Los focos detectados anteriormente en la zona ya no se registran "
            f"en el sistema satelital — {fecha_str}. "
            f"La situación parece estar controlada. Seguimos monitoreando."
        )

    # modo == "alerta"
    max_frp, tipo = clasificar_focos(focos)
    zona = localizar_focos(focos)
    n_str = "1 foco" if n == 1 else f"{n} focos"

    if tipo == "bajo":
        return (
            f"Se {'detectó' if n == 1 else 'detectaron'} {n_str} de calor en {zona} — {fecha_str}. "
            f"Intensidad baja, posiblemente {'una quema controlada' if n == 1 else 'quemas controladas'} o de rastrojos. "
            f"Si tenés información, escribinos. 🔥"
        )
    elif tipo == "moderado":
        return (
            f"Se {'detectó' if n == 1 else 'detectaron'} {n_str} de calor en {zona} — {fecha_str}. "
            f"Intensidad moderada, incendio activo en la zona. "
            f"Si ves humo o tenés información, avisanos o comunicate con bomberos."
        )
    else:
        return (
            f"⚠️ Se {'detectó' if n == 1 else 'detectaron'} {n_str} de alta intensidad en {zona} — {fecha_str}. "
            f"El sistema registra un incendio significativo en la zona. "
            f"Comunicate urgente con el cuartel de bomberos más cercano o escribinos."
        )


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
            print(resp.text[:200], file=sys.stderr)
    except Exception as e:
        print(f"ERROR Facebook: {e}", file=sys.stderr)


def cargar_estado():
    if ESTADO_FILE.exists():
        with open(ESTADO_FILE) as f:
            return json.load(f)
    return {"habia_focos": False, "n_focos": 0, "fecha": ""}


def guardar_estado(hay_focos, n_focos):
    with open(ESTADO_FILE, "w") as f:
        json.dump({
            "habia_focos": hay_focos,
            "n_focos": n_focos,
            "fecha": datetime.now(ARGENTINA_TZ).strftime("%Y-%m-%d %H:%M"),
        }, f, indent=2)


def main():
    ahora     = datetime.now(ARGENTINA_TZ)
    hora      = ahora.hour
    turno     = "manana" if hora < 16 else "tarde"
    fecha_str = ahora.strftime("%d/%m/%Y  %H:%M hs")

    print(f"[{fecha_str}] Monitor Incendios — turno {turno}")

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    firms_key = config.get("firms_key", "")
    if not firms_key:
        print("ERROR: falta firms_key en config.json", file=sys.stderr)
        sys.exit(1)

    try:
        focos = fetch_focos(firms_key)
        print(f"Focos detectados: {len(focos)}")
    except Exception as e:
        print(f"ERROR FIRMS: {e}", file=sys.stderr)
        sys.exit(1)

    estado_prev = cargar_estado()
    hay_focos   = len(focos) > 0

    if turno == "manana":
        modo = "alerta" if hay_focos else "sin_focos"
    else:
        if hay_focos:
            modo = "alerta"
        elif estado_prev["habia_focos"]:
            modo = "apagado"
        else:
            modo = "sin_focos"

    # Imagen: mapa generado si hay focos, foto fija si no hay
    FOTO_SIN_FOCOS = BASE_DIR / "foto_sin_focos.png"
    if hay_focos:
        img_path = generar_mapa(focos, fecha_str)
    elif FOTO_SIN_FOCOS.exists():
        img_path = FOTO_SIN_FOCOS
    else:
        img_path = generar_mapa(focos, fecha_str)  # mapa vacío como fallback

    texto    = generar_texto(focos, modo, turno)
    texto_completo = texto + f"\n\n{FACEBOOK_PAGE_URL}"

    print(f"\nModo: {modo}")
    print(f"Texto:\n{texto_completo}\n")

    publicar_facebook(config, texto_completo, img_path)
    guardar_estado(hay_focos, len(focos))
    print("Listo.")


if __name__ == "__main__":
    main()
