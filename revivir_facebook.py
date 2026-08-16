#!/usr/bin/env python3
"""
Revivir la publicacion en Facebook: obtiene un Page Access Token PERMANENTE
y lo guarda en config.json (clave facebook_page_token).

Necesita 3 datos de developers.facebook.com, pasados por variable de entorno:
    FB_APP_ID       -> App ID            (Tu App > Configuracion > Basica)
    FB_APP_SECRET   -> App Secret        (misma pantalla, boton "Mostrar")
    FB_USER_TOKEN   -> User Access Token  (Graph API Explorer, con permisos
                       pages_show_list, pages_read_engagement, pages_manage_posts)

Uso:
    export FB_APP_ID="..."
    export FB_APP_SECRET="..."
    export FB_USER_TOKEN="..."
    python3 revivir_facebook.py

NO publica nada: solo genera/guarda el token y hace una verificacion de lectura.
"""

import json
import os
import sys
from pathlib import Path

import requests

GRAPH = "https://graph.facebook.com/v25.0"
PAGE_ID = "1147087285146142"          # Humedales y Pastizales
CONFIG_FILE = Path(__file__).parent / "config.json"


def _need(var):
    val = os.environ.get(var, "").strip()
    if not val:
        print(f"[X] Falta la variable {var}. Ver instrucciones al inicio del archivo.",
              file=sys.stderr)
        sys.exit(1)
    return val


def main():
    app_id     = _need("FB_APP_ID")
    app_secret = _need("FB_APP_SECRET")
    user_token = _need("FB_USER_TOKEN")

    # 1) Cambiar el token de usuario corto por uno de larga duracion (~60 dias).
    print("[1] Extendiendo el token de usuario (larga duracion)...")
    r = requests.get(f"{GRAPH}/oauth/access_token", params={
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": user_token,
    }, timeout=30)
    data = r.json()
    if "access_token" not in data:
        print("[X] No se pudo extender el token:",
              json.dumps(data, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    long_user_token = data["access_token"]
    print("    OK.")

    # 2) Pedir las paginas del usuario. El token de pagina derivado de un token
    #    de usuario de larga duracion NO expira (permanente).
    print("[2] Buscando el token de la pagina...")
    r = requests.get(f"{GRAPH}/me/accounts", params={
        "access_token": long_user_token,
        "limit": 200,
    }, timeout=30)
    data = r.json()
    paginas = data.get("data", [])
    if not paginas:
        print("[X] No aparecen paginas. Revisa que seas admin y que diste los permisos.",
              file=sys.stderr)
        print("    Respuesta:", json.dumps(data, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)

    page = next((p for p in paginas if p.get("id") == PAGE_ID), None)
    if not page:
        print(f"[X] No encontre la pagina {PAGE_ID} entre tus paginas:", file=sys.stderr)
        for p in paginas:
            print(f"    - {p.get('name')} (id {p.get('id')})", file=sys.stderr)
        sys.exit(1)

    page_token = page["access_token"]
    print(f"    OK. Pagina: {page.get('name')}")

    # 3) Verificar (solo lectura) que el token de pagina funciona. No publica nada.
    print("[3] Verificando el token de la pagina (lectura)...")
    r = requests.get(f"{GRAPH}/{PAGE_ID}", params={
        "fields": "name,fan_count",
        "access_token": page_token,
    }, timeout=30)
    chk = r.json()
    if "error" in chk:
        print("[X] El token de pagina no valida:",
              json.dumps(chk, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
    print(f"    OK. La pagina responde: {chk.get('name')}")

    # 4) Guardar en config.json conservando el resto de las claves.
    print("[4] Guardando en config.json...")
    config = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    config["facebook_page_token"] = page_token
    CONFIG_FILE.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    print("    OK. facebook_page_token guardado en config.json.")

    print("\n[OK] Facebook revivido. El token es permanente: no expira salvo que")
    print("     cambies la contrasena de Facebook o quites la app de tu cuenta.")


if __name__ == "__main__":
    main()
