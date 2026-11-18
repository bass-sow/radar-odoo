"""Recordatorios por Telegram: citas, renovaciones y seguimientos.

Se editan en recordatorios.json ({"fecha": "AAAA-MM-DD", "texto": "..."}).
Solo los manda la ejecucion de la manana (la de las 7): el dia antes y el
mismo dia, para que no se repitan en las tres pasadas diarias.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import notify

log = logging.getLogger(__name__)
FICHERO = Path(__file__).resolve().parent.parent / "recordatorios.json"


def es_pasada_de_la_manana() -> bool:
    if os.getenv("FORZAR_RECORDATORIOS"):
        return True
    return datetime.now(timezone.utc).hour < 9   # la de las 7 corre a las 6 UTC


def enviar() -> int:
    if not FICHERO.exists() or not es_pasada_de_la_manana():
        return 0
    hoy = date.today()
    manana = hoy + timedelta(days=1)
    lineas = []
    for r in json.loads(FICHERO.read_text(encoding="utf-8")):
        f = date.fromisoformat(r["fecha"])
        if f == hoy:
            lineas.append(f"🔴 <b>Hoy</b>: {r['texto']}")
        elif f == manana:
            lineas.append(f"🟡 <b>Mañana</b>: {r['texto']}")
    if not lineas or not os.getenv("TELEGRAM_CHAT_ID"):
        return 0
    notify._tg("sendMessage", chat_id=os.getenv("TELEGRAM_CHAT_ID"),
               text="⏰ <b>Recordatorios</b>\n\n" + "\n\n".join(lineas), parse_mode="HTML")
    return len(lineas)
