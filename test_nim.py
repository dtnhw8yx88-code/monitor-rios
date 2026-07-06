#!/usr/bin/env python3
"""
Prueba de humo para NVIDIA NIM.

Verifica que NVIDIA_NIM_API_KEY y el modelo funcionan ANTES de tocar el flujo
real de publicacion. No envia nada a Facebook/WhatsApp/mail.

Uso:
    export NVIDIA_NIM_API_KEY="nvapi-..."
    python3 test_nim.py

    # opcional, para probar otro modelo:
    NVIDIA_NIM_MODEL="meta/llama-3.1-8b-instruct" python3 test_nim.py
"""

import sys

import nim_client


def main():
    print("=== Prueba NVIDIA NIM ===")
    print(f"Endpoint : {nim_client.NIM_CHAT_ENDPOINT}")
    print(f"Modelo   : {nim_client._get_model()}")

    if not nim_client.nim_disponible():
        print("\n[X] NVIDIA_NIM_API_KEY no esta definida en el entorno.")
        print('    Definila con:  export NVIDIA_NIM_API_KEY="nvapi-..."')
        sys.exit(1)

    key = nim_client._get_api_key()
    print(f"API key  : {key[:8]}...{key[-4:]}  (presente)")

    # 1) Llamada minima de conectividad.
    print("\n[1] Llamada simple...")
    r = nim_client.completar(
        [{"role": "user", "content": "Responde solo con la palabra: OK"}],
        max_tokens=10,
    )
    if r is None:
        print("[X] La llamada fallo. Revisa la key/el modelo (ver logs [nim] arriba).")
        sys.exit(1)
    print(f"    Respuesta: {r!r}")

    # 2) Reescritura de un boletin de ejemplo (mismo camino que usara monitor.py).
    print("\n[2] Reescritura de un boletin de ejemplo...")
    ejemplo = (
        "El Rio Salado en Tostado sube a 5.20 m. El Calchaqui baja a 3.10 m. "
        "Paso de las Piedras baja a 2.40 m — el sistema esta drenando."
    )
    reescrito = nim_client.reescribir_boletin(ejemplo)
    print("    Original :", ejemplo)
    print("    Reescrito:", reescrito)
    if reescrito == ejemplo:
        print("    [!] Volvio el texto original (fallback) — NIM no reescribio.")
    else:
        print("    [OK] NIM reescribio el texto.")

    print("\n[OK] Prueba completada.")


if __name__ == "__main__":
    main()
