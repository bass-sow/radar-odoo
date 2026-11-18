"""Segunda opinion con un LLM: lee cada oferta y la compara con el CV real.

El filtro por palabras clave (match.py) es rapido pero tosco: no distingue un
"Python deseable" de un "Python es el puesto". Aqui un modelo lee la oferta
entera y devuelve tres cosas:

    encaje   0-100, cuanto encaja con el perfil real
    motivo   una frase: por que si o por que no
    parrafo  2-3 frases para la carta, que conectan ESA oferta con experiencia
             que de verdad tiene (nada inventado)

Usa GitHub Models: gratis desde GitHub Actions con el GITHUB_TOKEN del propio
workflow (permiso `models: read`), sin claves ni tarjeta. La API es compatible
con la de chat completions de OpenAI. Si falla o no hay token, el radar sigue
igual que antes, solo con la puntuacion por palabras clave.

    python -m radar.ia      # prueba con dos ofertas de ejemplo
"""
from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

URL = "https://models.github.ai/inference/chat/completions"
MODELO = os.getenv("IA_MODEL", "openai/gpt-4.1-mini")
TIMEOUT = 60
PAUSA = 4.5        # el plan gratuito permite ~15 peticiones por minuto
DESCARTE = 25      # por debajo de esto la IA considera que no encaja

PERFIL = """\
Bassirou Sow. Terrassa (Barcelona). Desarrollador Python especializado en Odoo, 4 años de experiencia.
- Odoo v15-v19 (On-Premise y Odoo Online/SaaS): módulos a medida de MRP/fabricación, inventario, ventas y contabilidad; OCA; trazabilidad FEFO, códigos de barras, GS1 DataMatrix; automatizaciones.
- Backend: Python, FastAPI, Flask, Django; APIs REST para fintech (FastAPI + MariaDB); microservicio FastAPI que integra Odoo con transportistas y genera etiquetas PDF.
- Datos: PostgreSQL con optimización (dashboards de más de 300 s a pocos segundos con archivado y tuning), MariaDB, SQLite; ETL.
- Integraciones y automatización: n8n, Selenium/web scraping (2FA por SMS, perfiles persistentes), exportador LittleHotelier -> Odoo.
- DevOps: Docker y Docker Compose, Linux, Git/GitHub/GitLab, despliegues por SSH.
- IA: usa a diario agentes de código (Claude Code); proyecto propio radar-odoo en Python que corre en GitHub Actions y usa un LLM para evaluar ofertas.
- Idiomas: castellano y francés nativos, catalán intermedio, inglés B1 (no fluido).
- No tiene experiencia con: Java, .NET, PHP, React/Angular, Go, Rust, AWS/Azure/GCP gestionados, Spark/Databricks, Kubernetes en producción.
- Busca: remoto desde España, o Barcelona en cualquier modalidad. Salario negociable desde 27.000 EUR.
"""

INSTRUCCIONES = """\
Eres un reclutador técnico honesto. Te paso el perfil real de un candidato y una oferta.
Devuelve SOLO un JSON con estas claves:
  "encaje": entero 0-100. 80+ si el núcleo del puesto es lo que él ya hace; 50-79 si encaja con matices;
            menos de 40 si el puesto exige de forma imprescindible algo que no tiene (otro lenguaje
            principal, inglés fluido o nativo, muchos más años, una nube concreta como requisito, o
            ubicación incompatible).
  "motivo": una frase en español, máximo 25 palabras, con lo que más pesa a favor o en contra.
  "parrafo": 2-3 frases en español, en primera persona, para una carta a ESTA empresa: conecta lo que
             pide la oferta con experiencia concreta del perfil. No inventes nada que no esté en el perfil.
             Si el encaje es menor de 40, deja "parrafo" vacío.
"""


def _token() -> str | None:
    return os.getenv("GITHUB_TOKEN") or os.getenv("GH_MODELS_TOKEN")


def _oferta(job: dict) -> str:
    return (f"Puesto: {job.get('title', '')}\nEmpresa: {job.get('company', '')}\n"
            f"Ubicación: {job.get('location', '')}\nSalario: {job.get('salary') or 'no indicado'}\n"
            f"Fuente: {job.get('source', '')}\n\nTexto:\n{(job.get('text') or '')[:3500]}")


def _json(texto: str) -> dict:
    """Saca el objeto JSON aunque venga envuelto en ```json ... ``` o con texto alrededor."""
    texto = texto.strip()
    ini, fin = texto.find("{"), texto.rfind("}")
    if ini == -1 or fin <= ini:
        raise ValueError(f"sin JSON en la respuesta: {texto[:200]!r}")
    return json.loads(texto[ini:fin + 1])


def _preguntar(job: dict, token: str) -> dict | None:
    cuerpo = {
        "model": MODELO,
        "temperature": 0.2,
        "max_tokens": 400,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": INSTRUCCIONES + "\nPERFIL:\n" + PERFIL},
            {"role": "user", "content": _oferta(job)},
        ],
    }
    cab = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
           "Accept": "application/json"}
    for intento in range(2):
        r = requests.post(URL, headers=cab, json=cuerpo, timeout=TIMEOUT)
        if r.status_code == 429 and intento == 0:
            espera = int(r.headers.get("retry-after", "20"))
            log.info("IA: limite de peticiones, espero %ds", espera)
            time.sleep(min(espera, 60))
            continue
        r.raise_for_status()
        try:
            texto = r.json()["choices"][0]["message"]["content"] or ""
        except ValueError:
            raise RuntimeError(f"respuesta no JSON ({r.status_code}, "
                               f"{r.headers.get('content-type')}): {r.text[:200]!r}")
        datos = _json(texto)
        return {
            "encaje": max(0, min(100, int(datos.get("encaje", 0)))),
            "motivo": str(datos.get("motivo", "")).strip()[:300],
            "parrafo": str(datos.get("parrafo", "")).strip()[:900],
        }
    return None


def evaluar(jobs: list[dict]) -> list[dict]:
    """Anota cada oferta con la opinion de la IA y devuelve las que no descarta.

    Si no hay token o la API falla, devuelve las ofertas tal cual: la IA suma,
    nunca bloquea el radar.
    """
    token = _token()
    if not token or not jobs:
        if not token:
            log.info("IA: sin GITHUB_TOKEN, sigo solo con palabras clave")
        return jobs

    fallos = 0
    for n, job in enumerate(jobs):
        if n:
            time.sleep(PAUSA)
        try:
            res = _preguntar(job, token)
        except Exception as exc:  # red, cuota, JSON roto...
            fallos += 1
            log.warning("IA: %s -> %s", job.get("title", "")[:40], exc)
            if fallos >= 3:
                log.warning("IA: 3 fallos seguidos, dejo el resto sin evaluar")
                break
            continue
        if res:
            fallos = 0
            job["ia_score"], job["ia_motivo"], job["ia_parrafo"] = (
                res["encaje"], res["motivo"], res["parrafo"])
            log.info("IA %3d | %-40s | %s", res["encaje"], job.get("title", "")[:40], res["motivo"])

    quedan = [j for j in jobs if j.get("ia_score", 100) >= DESCARTE]
    # Primero las que la IA ve mejor; las no evaluadas, con su nota de palabras.
    quedan.sort(key=lambda j: j.get("ia_score", j.get("score", 0)), reverse=True)
    return quedan


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    ejemplos = [
        {"title": "Desarrollador/a Odoo", "company": "Partner Odoo", "location": "Barcelona (Híbrido)",
         "source": "prueba", "salary": "30.000€ - 36.000€",
         "text": "Buscamos desarrollador Odoo v17 con Python, PostgreSQL y experiencia en módulos de "
                 "inventario y fabricación. Valoramos OCA y Docker."},
        {"title": "Senior Java Engineer", "company": "Fintech", "location": "Remote",
         "source": "prueba", "salary": "",
         "text": "8+ years of Java and Spring Boot, Kafka, Kubernetes on AWS. Native-level English required."},
    ]
    for j in evaluar(ejemplos) or []:
        print(j.get("ia_score"), "|", j["title"], "|", j.get("ia_motivo"), "|", j.get("ia_parrafo"))
    print("Descartadas:", [j["title"] for j in ejemplos if j.get("ia_score", 100) < DESCARTE])
