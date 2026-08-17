#!/usr/bin/env python3
"""
Cliente para NVIDIA NIM (build.nvidia.com) — endpoint OpenAI-compatible.

Objetivo de diseño: NUNCA romper la publicación.
Ante cualquier problema (sin API key, timeout, error HTTP, 429 agotado,
respuesta inesperada) las funciones devuelven el texto original (fallback)
o None, pero jamás lanzan una excepción hacia afuera.

Uso típico:
    import nim_client
    texto = nim_client.reescribir_boletin(texto_de_plantilla)
    # -> texto reescrito por NIM, o el mismo texto si NIM no está disponible.

Configuración por entorno:
    NVIDIA_NIM_API_KEY   API key (formato nvapi-...). OBLIGATORIA para usar NIM.
    NVIDIA_NIM_MODEL     (opcional) nombre del modelo; default DEFAULT_MODEL.
"""

import os
import sys
import time

import requests

# ── Configuración ────────────────────────────────────────────────────────────
# Endpoint OpenAI-compatible de NVIDIA NIM.
NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_CHAT_ENDPOINT = f"{NIM_BASE_URL}/chat/completions"

# Modelo por defecto. Se puede sobreescribir con la variable NVIDIA_NIM_MODEL.
# Verificá el id exacto disponible en tu cuenta en https://build.nvidia.com
# Usamos el 8b: responde rápido y estable en el tier gratis (el 70b suele
# tardar demasiado y dar timeout). Probado y funcionando el 2026-07-01.
DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"

# Parámetros de red y reintentos.
# El modelo puede tardar bastante en el tier gratis (arranque en frío), así que
# damos un margen amplio. Configurable con la variable NVIDIA_NIM_TIMEOUT.
REQUEST_TIMEOUT = int(os.environ.get("NVIDIA_NIM_TIMEOUT", "60"))  # segundos por request
MAX_REINTENTOS_429 = 3      # reintentos ante rate limit (429)
BACKOFF_BASE = 5            # segundos; la espera crece: 5, 10, 15...

SYSTEM_PROMPT_REESCRITURA = (
    "Sos un redactor de una fundacion ambiental que publica boletines sobre el "
    "nivel de los rios en el norte de Santa Fe, Argentina, dirigidos a vecinos y "
    "productores rurales. Te paso un boletin ya redactado por un sistema "
    "automatico. Tu tarea es reescribirlo para que suene mas natural, claro y "
    "humano, en espanol rioplatense, con tono sereno e informativo.\n"
    "REGLAS ESTRICTAS:\n"
    "- No inventes ni cambies NINGUN dato: alturas en metros, nombres de "
    "estaciones y rios, fechas, estados de alerta y URLs deben quedar identicos.\n"
    "- No agregues informacion que no este en el texto original.\n"
    "- Manten un largo parecido; nunca mas largo que el original.\n"
    "- No uses markdown, ni titulos, ni vinetas. Texto corrido.\n"
    "- NO es una carta: no agregues saludos de apertura (como 'Queridos vecinos'), "
    "ni despedidas, firmas o cierres (como 'Atentamente', 'Saludos', 'Fundacion "
    "Ambiental' o un nombre).\n"
    "- NUNCA uses marcadores de posicion como '[Tu nombre]' ni firmes el texto.\n"
    "- Escribi como un parte informativo en tercera persona, no como una carta personal.\n"
    "- Devolve SOLO el boletin reescrito, sin comentarios ni aclaraciones tuyas."
)


# ── Helpers de configuración ────────────────────────────────────────────────
def _get_api_key():
    return os.environ.get("NVIDIA_NIM_API_KEY", "").strip()


def _get_model():
    return os.environ.get("NVIDIA_NIM_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def nim_disponible():
    """True si hay API key configurada (condición mínima para intentar NIM)."""
    return bool(_get_api_key())


def _log(msg):
    print(f"[nim] {msg}", file=sys.stderr)


# ── Llamada de bajo nivel ────────────────────────────────────────────────────
def completar(mensajes, *, temperature=0.4, max_tokens=600, model=None):
    """
    Llama al chat de NIM con la lista `mensajes` (formato OpenAI).
    Devuelve el string de respuesta, o None ante cualquier problema.
    Nunca lanza excepciones.
    """
    api_key = _get_api_key()
    if not api_key:
        _log("NVIDIA_NIM_API_KEY no definida — se omite NIM.")
        return None

    model = model or _get_model()
    payload = {
        "model": model,
        "messages": mensajes,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    for intento in range(MAX_REINTENTOS_429 + 1):
        try:
            resp = requests.post(
                NIM_CHAT_ENDPOINT,
                headers=headers,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            _log(f"Error de red ({type(e).__name__}): {e}")
            return None

        # Rate limit: esperar y reintentar con backoff creciente.
        if resp.status_code == 429:
            if intento < MAX_REINTENTOS_429:
                espera = BACKOFF_BASE * (intento + 1)
                _log(f"429 rate limit — reintento en {espera}s "
                     f"({intento + 1}/{MAX_REINTENTOS_429}).")
                time.sleep(espera)
                continue
            _log("429 rate limit — agotados los reintentos.")
            return None

        if resp.status_code != 200:
            _log(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return None

        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
        except (ValueError, KeyError, IndexError) as e:
            _log(f"Respuesta inesperada ({type(e).__name__}): {e}")
            return None

    return None


# ── API de alto nivel ────────────────────────────────────────────────────────
def reescribir_boletin(texto_base, *, instrucciones_extra="", temperature=0.4):
    """
    Reescribe `texto_base` en prosa mas natural usando NIM, preservando los
    datos. Si NIM no esta disponible o falla, devuelve `texto_base` intacto.

    Este es el punto de integracion recomendado: siempre es seguro llamarlo,
    porque en el peor caso devuelve exactamente el texto que le pasaste.
    """
    if not texto_base or not texto_base.strip():
        return texto_base
    if not nim_disponible():
        return texto_base

    system = SYSTEM_PROMPT_REESCRITURA
    if instrucciones_extra:
        system = system + "\n" + instrucciones_extra

    mensajes = [
        {"role": "system", "content": system},
        {"role": "user", "content": texto_base},
    ]
    resultado = completar(mensajes, temperature=temperature)
    if not resultado:
        return texto_base
    return resultado
