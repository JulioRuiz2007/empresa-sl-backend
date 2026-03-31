"""
Empresa SL — Backend de Gestión de Citas para Salón de Belleza
Diseñado para integrarse con Retell AI como sistema de reservas.

Endpoints principales:
  - GET  /disponibilidad       → consultar huecos libres
  - POST /citas                → crear nueva cita
  - PUT  /citas/{cita_id}      → modificar cita existente
  - DELETE /citas/{cita_id}    → cancelar cita
  - GET  /citas/buscar         → buscar citas por teléfono o nombre
  - GET  /servicios            → listar servicios y precios
  - GET  /estilistas           → listar estilistas y horarios
"""

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks, Request
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo
import sqlite3
import json
import os
import logging

TZ = ZoneInfo("Europe/Madrid")


def ahora_madrid() -> datetime:
    return datetime.now(TZ)


def hoy_madrid() -> date:
    return ahora_madrid().date()

from google_calendar import calendar_service

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN DEL SALÓN — Modifica estos datos según tu negocio
# ═══════════════════════════════════════════════════════════════

SALON_CONFIG = {
    "nombre": "Empresa SL",
    "direccion": "Calle Colón 48, 46004 Valencia",
    "telefono": "+34 961 234 567",
    "horario": {
        "lunes":    {"abre": "09:00", "cierra": "20:00"},
        "martes":   {"abre": "09:00", "cierra": "20:00"},
        "miércoles": {"abre": "09:00", "cierra": "20:00"},
        "jueves":   {"abre": "09:00", "cierra": "20:00"},
        "viernes":  {"abre": "09:00", "cierra": "20:00"},
        "sábado":   {"abre": "09:00", "cierra": "20:00"},
        "domingo":  None,  # Cerrado
    },
    "buffer_minutos": 10,  # Descanso obligatorio entre citas
    "antelacion_minima_horas": 2,  # Mínimo 2h antes para reservar
}

# Servicios: id, nombre, duración en minutos, precio en euros
SERVICIOS = [
    {"id": "corte",       "nombre": "Corte de cabello",           "duracion_min": 45,  "precio": 25.0,  "descripcion": "Corte personalizado para mujer u hombre, incluye lavado"},
    {"id": "coloracion",  "nombre": "Coloración / Mechas",        "duracion_min": 90,  "precio": 65.0,  "descripcion": "Coloración completa, mechas o reflejos. Precio desde, varía según longitud"},
    {"id": "brushing",    "nombre": "Brushing / Secado con forma", "duracion_min": 30,  "precio": 18.0,  "descripcion": "Secado profesional con forma, liso o ondas"},
    {"id": "unas",        "nombre": "Manicura y Pedicura",        "duracion_min": 60,  "precio": 30.0,  "descripcion": "Manicura clásica o semipermanente, pedicura disponible"},
    {"id": "facial",      "nombre": "Tratamiento Facial",         "duracion_min": 50,  "precio": 40.0,  "descripcion": "Limpieza facial profunda con productos profesionales"},
    {"id": "depilacion",  "nombre": "Depilación",                 "duracion_min": 30,  "precio": 20.0,  "descripcion": "Depilación con cera. Precio desde, varía según zona"},
]

# Estilistas: id, nombre, especialidades, días que trabaja (0=lunes, 5=sábado)
ESTILISTAS = [
    {
        "id": "maria",
        "nombre": "María García",
        "especialidades": ["coloracion", "corte", "brushing"],
        "dias_trabaja": [0, 1, 2, 3, 4, 5],  # Lunes a Sábado
    },
    {
        "id": "lucia",
        "nombre": "Lucía Fernández",
        "especialidades": ["corte", "brushing", "depilacion"],
        "dias_trabaja": [0, 1, 2, 3, 4],  # Lunes a Viernes (no sábados)
    },
    {
        "id": "carmen",
        "nombre": "Carmen Ruiz",
        "especialidades": ["unas", "facial", "depilacion", "brushing"],
        "dias_trabaja": [0, 2, 3, 4, 5],  # No trabaja los martes
    },
]

DIAS_SEMANA_ES = {
    0: "lunes", 1: "martes", 2: "miércoles",
    3: "jueves", 4: "viernes", 5: "sábado", 6: "domingo"
}

DIAS_NOMBRE_A_NUM = {
    # Español
    "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2,
    "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6,
    # Inglés (el LLM a veces los envía en inglés)
    "monday": 0, "tuesday": 1, "wednesday": 2,
    "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
}

MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12
}


def parsear_fecha(texto: str) -> date:
    """
    Convierte una fecha en cualquier formato al tipo date.
    Acepta: YYYY-MM-DD, nombres de días en español, 'mañana', 'pasado mañana',
    'hoy', '5 de abril', '30 de marzo', etc.
    """
    import re as _re
    texto = texto.strip().lower()
    texto = _re.sub(r"^(el|la)\s+", "", texto).strip()
    texto = _re.sub(r"\b(que viene|próximo|proximo|este|esta|next|this|coming)\b", "", texto).strip()
    hoy = hoy_madrid()

    # Formato estándar YYYY-MM-DD
    try:
        resultado = date.fromisoformat(texto)
        # Si la fecha es en el pasado (el LLM la calculó con su año de entrenamiento),
        # corregirla al próximo día de esa semana
        if resultado < hoy:
            dias_hasta = (resultado.weekday() - hoy.weekday()) % 7
            if dias_hasta == 0:
                dias_hasta = 7
            return hoy + timedelta(days=dias_hasta)
        return resultado
    except ValueError:
        pass

    # Palabras clave
    if texto in ("hoy",):
        return hoy
    if texto in ("mañana", "manana"):
        return hoy + timedelta(days=1)
    if texto in ("pasado mañana", "pasado manana"):
        return hoy + timedelta(days=2)

    # Nombre de día de la semana ("lunes", "el martes", etc.)
    for nombre, num in DIAS_NOMBRE_A_NUM.items():
        if nombre in texto:
            dias_hasta = (num - hoy.weekday()) % 7
            if dias_hasta == 0:
                dias_hasta = 7  # Si es hoy el mismo día, va a la semana siguiente
            return hoy + timedelta(days=dias_hasta)

    # "5 de abril", "30 de marzo de 2026", etc.
    import re
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)(?:\s+de\s+(\d{4}))?", texto)
    if m:
        dia = int(m.group(1))
        mes_str = m.group(2)
        anio = int(m.group(3)) if m.group(3) else hoy.year
        mes = MESES_ES.get(mes_str)
        if mes:
            try:
                return date(anio, mes, dia)
            except ValueError:
                pass

    raise ValueError(f"No se pudo interpretar la fecha: '{texto}'")


# ═══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ═══════════════════════════════════════════════════════════════

DATABASE_URL = os.getenv("DATABASE_URL")  # PostgreSQL en Railway; None = SQLite local
DB_PATH = os.path.join(os.path.dirname(__file__), "citas.db")

_USE_PG = bool(DATABASE_URL)

if _USE_PG:
    import psycopg2
    import psycopg2.extras


def get_db():
    if _USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
        conn.autocommit = False
        return conn
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _exec(conn, sql, params=()):
    """Ejecuta una query normalizando placeholders: ? (SQLite) → %s (PostgreSQL)."""
    if _USE_PG:
        sql = sql.replace("?", "%s")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
        return conn
    conn.execute(sql, params)
    return conn


def _query(conn, sql, params=()):
    """Ejecuta una SELECT y devuelve lista de dicts (igual para SQLite y PostgreSQL)."""
    if _USE_PG:
        sql = sql.replace("?", "%s")
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _insert(conn, sql, params=()):
    """INSERT devolviendo el id del nuevo registro."""
    if _USE_PG:
        sql = sql.replace("?", "%s")
        if "RETURNING id" not in sql:
            sql += " RETURNING id"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return row[0]
    cursor = conn.execute(sql, params)
    return cursor.lastrowid


def init_db():
    conn = get_db()
    if _USE_PG:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS citas (
                    id SERIAL PRIMARY KEY,
                    cliente_nombre TEXT NOT NULL,
                    cliente_telefono TEXT NOT NULL,
                    cliente_nuevo INTEGER DEFAULT 1,
                    servicio_id TEXT NOT NULL,
                    estilista_id TEXT NOT NULL,
                    fecha DATE NOT NULL,
                    hora_inicio TIME NOT NULL,
                    hora_fin TIME NOT NULL,
                    duracion_min INTEGER NOT NULL,
                    precio_estimado REAL NOT NULL,
                    notas TEXT DEFAULT '',
                    estado TEXT DEFAULT 'confirmada',
                    google_event_id TEXT DEFAULT '',
                    creada_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    modificada_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_citas_fecha_estilista
                ON citas(fecha, estilista_id, estado)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_citas_telefono
                ON citas(cliente_telefono, estado)
            """)
        conn.commit()
    else:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS citas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cliente_nombre TEXT NOT NULL,
                cliente_telefono TEXT NOT NULL,
                cliente_nuevo INTEGER DEFAULT 1,
                servicio_id TEXT NOT NULL,
                estilista_id TEXT NOT NULL,
                fecha DATE NOT NULL,
                hora_inicio TIME NOT NULL,
                hora_fin TIME NOT NULL,
                duracion_min INTEGER NOT NULL,
                precio_estimado REAL NOT NULL,
                notas TEXT DEFAULT '',
                estado TEXT DEFAULT 'confirmada',
                google_event_id TEXT DEFAULT '',
                creada_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                modificada_en TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_citas_fecha_estilista
            ON citas(fecha, estilista_id, estado)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_citas_telefono
            ON citas(cliente_telefono, estado)
        """)
        conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════
# MODELOS PYDANTIC
# ═══════════════════════════════════════════════════════════════

class CrearCitaRequest(BaseModel):
    cliente_nombre: str = Field(..., min_length=2, description="Nombre completo del cliente")
    cliente_telefono: str = Field(default="", description="Teléfono del cliente")
    cliente_nuevo: bool = Field(default=True, description="¿Es su primera visita?")
    servicio_id: str = Field(..., description="ID del servicio (corte, coloracion, brushing, unas, facial, depilacion)")
    estilista_id: str = Field(..., description="ID del estilista (maria, lucia, carmen) o 'cualquiera'")
    fecha: str = Field(..., description="Fecha en formato YYYY-MM-DD")
    hora: str = Field(..., description="Hora de inicio en formato HH:MM")
    notas: str = Field(default="", description="Notas adicionales")


class ModificarCitaRequest(BaseModel):
    nueva_fecha: Optional[str] = Field(None, description="Nueva fecha YYYY-MM-DD")
    nueva_hora: Optional[str] = Field(None, description="Nueva hora HH:MM")
    nuevo_estilista_id: Optional[str] = Field(None, description="Nuevo estilista")
    nuevo_servicio_id: Optional[str] = Field(None, description="Nuevo servicio")
    notas: Optional[str] = Field(None, description="Nuevas notas")


class CrearComboRequest(BaseModel):
    cliente_nombre: str = Field(..., min_length=2, description="Nombre completo del cliente")
    cliente_telefono: str = Field(default="", description="Teléfono del cliente")
    cliente_nuevo: bool = Field(default=True, description="¿Es su primera visita?")
    servicios: List[str] = Field(..., min_items=1, description="Lista de IDs de servicios en orden (ej: ['corte', 'unas'])")
    estilista_id: str = Field(default="cualquiera", description="ID del estilista o 'cualquiera'")
    fecha: str = Field(..., description="Fecha en formato YYYY-MM-DD")
    hora: str = Field(..., description="Hora de inicio del primer servicio HH:MM")
    notas: str = Field(default="")


# ═══════════════════════════════════════════════════════════════
# LÓGICA DE NEGOCIO
# ═══════════════════════════════════════════════════════════════

SERVICIO_ALIAS = {
    # corte
    "corte": "corte", "corte de pelo": "corte", "corte de cabello": "corte",
    "pelo": "corte", "haircut": "corte", "cut": "corte",
    "un corte": "corte", "cortarme el pelo": "corte", "cortarme": "corte",
    "quiero cortarme": "corte", "necesito un corte": "corte",
    # coloracion
    "coloracion": "coloracion", "coloración": "coloracion", "color": "coloracion",
    "mechas": "coloracion", "tinte": "coloracion", "tinte de pelo": "coloracion",
    "highlights": "coloracion", "balayage": "coloracion",
    "tenirme": "coloracion", "tintura": "coloracion", "quiero tenirme": "coloracion",
    "quiero tenerme": "coloracion", "reflejos": "coloracion",
    # brushing
    "brushing": "brushing", "secado": "brushing", "blow dry": "brushing",
    "secado con forma": "brushing", "peinado": "brushing",
    # unas
    "unas": "unas", "unas": "unas", "manicura": "unas", "pedicura": "unas",
    "nails": "unas", "manicure": "unas", "arreglarme las unas": "unas",
    "pintarme las unas": "unas",
    # facial
    "facial": "facial", "limpieza facial": "facial", "tratamiento facial": "facial",
    "limpieza": "facial", "hidratacion facial": "facial", "hidratacion": "facial",
    "tratamiento de piel": "facial", "peeling": "facial",
    # depilacion
    "depilacion": "depilacion", "depilación": "depilacion", "waxing": "depilacion",
    "cera": "depilacion", "depilacion de cejas": "depilacion", "cejas": "depilacion",
    "depilarme": "depilacion",
}


def obtener_servicio(servicio_id: str) -> dict:
    import unicodedata
    def norm(s):
        return unicodedata.normalize("NFD", s.lower().strip()).encode("ascii", "ignore").decode()

    sid = norm(servicio_id)
    # Exact ID match
    for s in SERVICIOS:
        if s["id"] == sid:
            return s
    # Alias match (exact and partial)
    alias_id = SERVICIO_ALIAS.get(sid)
    if alias_id:
        for s in SERVICIOS:
            if s["id"] == alias_id:
                return s
    # Try each alias key as substring of the input
    for alias_key, alias_val in SERVICIO_ALIAS.items():
        if alias_key in sid or sid in alias_key:
            for s in SERVICIOS:
                if s["id"] == alias_val:
                    return s
    # Partial match against ID or name
    for s in SERVICIOS:
        if sid in norm(s["id"]) or sid in norm(s["nombre"]) or norm(s["nombre"]) in sid:
            return s
    return None


def obtener_estilista(estilista_id: str) -> dict:
    for e in ESTILISTAS:
        if e["id"] == estilista_id:
            return e
    return None


def dia_nombre(fecha: date) -> str:
    return DIAS_SEMANA_ES.get(fecha.weekday(), "desconocido")


def dia_en_plural(fecha: date) -> str:
    """Devuelve el día para usarlo en frases como 'los lunes', 'los sábados'.
    Lunes/martes/miércoles/jueves/viernes ya terminan en 's', no se añade otra."""
    dia = dia_nombre(fecha)
    return dia if dia.endswith("s") else dia + "s"


def salon_abierto(fecha: date) -> dict:
    """Devuelve el horario del salón para esa fecha, o None si está cerrado."""
    dia = dia_nombre(fecha)
    return SALON_CONFIG["horario"].get(dia)


def estilista_trabaja(estilista: dict, fecha: date) -> bool:
    return fecha.weekday() in estilista["dias_trabaja"]


import re as _re_hora

MINUTOS_ES = {
    5: "cinco", 10: "diez", 15: "cuarto", 20: "veinte", 25: "veinticinco",
    30: "media", 35: "veinticinco", 40: "veinte", 45: "cuarto", 50: "diez", 55: "cinco",
}


def normalizar_hora(hora_raw: str) -> str:
    """Normaliza expresiones de hora a 'HH:MM'.
    Acepta: '10', '10:00', '9:30', '2 de la tarde', '14:00', 'pm', etc.
    Lanza ValueError si no puede parsear.
    """
    hora_s = hora_raw.strip().lower()
    es_tarde = bool(_re_hora.search(r"(tarde|pm)", hora_s))
    hora_s = _re_hora.sub(r"\s*(de\s+la\s+tarde|de\s+la\s+mañana|de\s+la\s+manana|pm|am)\s*", "", hora_s).strip()
    m = _re_hora.match(r"^(\d{1,2})(?:[:\s](\d{2}))?", hora_s)
    if m:
        h = int(m.group(1))
        mins = m.group(2) or "00"
        if es_tarde and h < 12:
            h += 12
        return f"{h:02d}:{mins}"
    if ":" not in hora_s:
        hora_s = hora_s.zfill(2) + ":00"
    elif len(hora_s.split(":")[0]) == 1:
        hora_s = "0" + hora_s
    # Validar formato final
    datetime.strptime(hora_s, "%H:%M")
    return hora_s


def hora_a_texto(hhmm: str) -> str:
    """Convierte "14:45" → "las 3 menos cuarto de la tarde" para que Sofía suene natural.
    Para minutos ≤30 usa "y X", para minutos >30 usa "menos X" de la hora siguiente.
    """
    h, m = map(int, hhmm.split(":"))

    def _franja(hora_24):
        if hora_24 < 12:
            return "de la mañana"
        elif hora_24 < 14:
            return "del mediodía"
        elif hora_24 < 21:
            return "de la tarde"
        else:
            return "de la noche"

    def _h12(hora_24):
        h12 = hora_24 if hora_24 <= 12 else hora_24 - 12
        return 12 if h12 == 0 else h12

    def _base(h12, prefijo):
        return f"la 1 {prefijo}" if h12 == 1 else f"las {h12} {prefijo}"

    if m == 0:
        return f"{_base(_h12(h), '').rstrip()} {_franja(h)}"

    if m <= 30:
        texto_min = MINUTOS_ES.get(m, str(m))
        prefijo = f"y {texto_min}"
        return f"{_base(_h12(h), prefijo)} {_franja(h)}"

    # m > 30: usar "menos X" con la hora siguiente
    h_sig = h + 1
    texto_min = MINUTOS_ES.get(60 - m, str(60 - m))
    prefijo = f"menos {texto_min}"
    return f"{_base(_h12(h_sig), prefijo)} {_franja(h_sig)}"


def estilista_hace_servicio(estilista: dict, servicio_id: str) -> bool:
    return servicio_id in estilista["especialidades"]


def obtener_citas_estilista(conn, estilista_id: str, fecha: date) -> list:
    """Obtiene todas las citas activas de un estilista en una fecha."""
    return _query(
        conn,
        "SELECT * FROM citas WHERE estilista_id = ? AND fecha = ? AND estado = 'confirmada' ORDER BY hora_inicio",
        (estilista_id, fecha.isoformat())
    )


def obtener_ids_confirmadas_dia(conn, fecha: date) -> set:
    """Obtiene todos los IDs de citas confirmadas para un día (todos los estilistas).
    Se usa para filtrar eventos huérfanos de Google Calendar."""
    rows = _query(conn, "SELECT id FROM citas WHERE fecha = ? AND estado = 'confirmada'", (fecha.isoformat(),))
    return {r["id"] for r in rows}


def _parse_time(val) -> time:
    """Convierte un valor a time: acepta str 'HH:MM' y objetos time (PostgreSQL)."""
    if isinstance(val, time):
        return val
    return datetime.strptime(str(val)[:5], "%H:%M").time()


def hay_conflicto(citas_existentes: list, hora_inicio: time, hora_fin: time, buffer: int) -> bool:
    """Comprueba si un nuevo hueco colisiona con citas existentes (incluyendo buffer)."""
    for cita in citas_existentes:
        cita_inicio = _parse_time(cita["hora_inicio"])
        cita_fin = _parse_time(cita["hora_fin"])

        # Añadir buffer después de la cita existente
        cita_fin_con_buffer = (datetime.combine(date.today(), cita_fin) + timedelta(minutes=buffer)).time()
        # Añadir buffer antes de la cita existente (la nueva cita necesita terminar + buffer antes)
        cita_inicio_con_buffer = (datetime.combine(date.today(), cita_inicio) - timedelta(minutes=buffer)).time()

        # Hay conflicto si los rangos se solapan
        if hora_inicio < cita_fin_con_buffer and hora_fin > cita_inicio_con_buffer:
            return True
    return False


def calcular_hora_fin(hora_inicio_str: str, duracion_min: int) -> str:
    inicio = datetime.strptime(hora_inicio_str, "%H:%M")
    fin = inicio + timedelta(minutes=duracion_min)
    return fin.strftime("%H:%M")


def gcal_bloques_estilista(estilista_id: str, fecha: date, citas_confirmadas_ids: set = None) -> list:
    """
    Lee Google Calendar y devuelve los bloques ocupados para un estilista en una fecha.
    Un evento bloquea a un estilista si:
      - tiene su ID en extendedProperties (eventos creados por nuestro sistema), O
      - su nombre aparece en el título del evento, O
      - el evento no tiene estilista identificable (bloqueo de salón en general).
    Eventos de OTRO estilista concreto se ignoran.

    Si citas_confirmadas_ids se proporciona, los eventos con empresa_sl_cita_id que NO están
    en ese set se ignoran (son eventos huérfanos de deploys anteriores).
    """
    if not calendar_service.enabled:
        return []

    est = obtener_estilista(estilista_id)
    if not est:
        return []

    eventos = calendar_service.obtener_eventos_dia(fecha.isoformat())
    otros_nombres = [e["nombre"].lower() for e in ESTILISTAS if e["id"] != estilista_id]
    bloques = []

    for ev in eventos:
        if "dateTime" not in ev.get("start", {}):
            continue  # Evento de día completo, ignorar

        try:
            dt_start = datetime.fromisoformat(ev["start"]["dateTime"])
            dt_end = datetime.fromisoformat(ev["end"]["dateTime"])
        except (ValueError, KeyError):
            continue

        ext = ev.get("extendedProperties", {}).get("private", {})
        ev_est_id = ext.get("estilista_id", "")
        ev_cita_id = ext.get("empresa_sl_cita_id", "")
        titulo = ev.get("summary", "").lower()

        # Si el evento es explícitamente de otro estilista, ignorarlo
        if ev_est_id and ev_est_id != estilista_id:
            continue

        # Si el evento fue creado por nuestro sistema (tiene cita_id) pero esa cita
        # ya no existe como confirmada en la BD, es un evento huérfano → ignorarlo
        if ev_cita_id and citas_confirmadas_ids is not None:
            try:
                if int(ev_cita_id) not in citas_confirmadas_ids:
                    logger.info(f"⏭️ Ignorando evento huérfano de Calendar: cita_id={ev_cita_id}, título={ev.get('summary', '')}")
                    continue
            except (ValueError, TypeError):
                pass

        # Si el título menciona a otro estilista (pero no al nuestro), ignorarlo
        if not ev_est_id:
            otro_en_titulo = any(n in titulo for n in otros_nombres)
            nuestro_en_titulo = est["nombre"].lower() in titulo
            if otro_en_titulo and not nuestro_en_titulo:
                continue

        bloques.append({
            "hora_inicio": dt_start.strftime("%H:%M"),
            "hora_fin": dt_end.strftime("%H:%M"),
        })

    return bloques


def encontrar_huecos_libres(conn, estilista_id: str, fecha: date, duracion_min: int, _ids_confirmadas: set = None) -> list:
    """Encuentra todos los huecos disponibles para un estilista en una fecha.
    Combina citas de la BD con eventos de Google Calendar."""
    horario = salon_abierto(fecha)
    if not horario:
        return []

    estilista = obtener_estilista(estilista_id)
    if not estilista or not estilista_trabaja(estilista, fecha):
        return []

    buffer = SALON_CONFIG["buffer_minutos"]
    abre = datetime.strptime(horario["abre"], "%H:%M")
    cierra = datetime.strptime(horario["cierra"], "%H:%M")

    # Obtener IDs confirmadas del día para filtrar eventos huérfanos de Calendar
    if _ids_confirmadas is None:
        _ids_confirmadas = obtener_ids_confirmadas_dia(conn, fecha)

    # Combinar citas de BD + bloques de Google Calendar
    citas_bd = obtener_citas_estilista(conn, estilista_id, fecha)
    bloques_gcal = gcal_bloques_estilista(estilista_id, fecha, _ids_confirmadas)
    # Normalizar bloques de Calendar al mismo formato que citas de BD
    citas_gcal = [
        {"hora_inicio": b["hora_inicio"], "hora_fin": b["hora_fin"]}
        for b in bloques_gcal
    ]
    todas_las_citas = citas_bd + citas_gcal

    huecos = []
    slot = abre
    while slot + timedelta(minutes=duracion_min) <= cierra:
        hora_inicio = slot.time()
        hora_fin = (slot + timedelta(minutes=duracion_min)).time()

        if not hay_conflicto(todas_las_citas, hora_inicio, hora_fin, buffer):
            huecos.append(slot.strftime("%H:%M"))

        slot += timedelta(minutes=15)

    return huecos


def buscar_mejor_estilista(conn, servicio_id: str, fecha: date, hora_str: str, duracion_min: int) -> Optional[dict]:
    """Busca el estilista con mejor disponibilidad para un servicio/fecha/hora.
    Combina citas de la BD con eventos de Google Calendar."""
    buffer = SALON_CONFIG["buffer_minutos"]
    hora_inicio = datetime.strptime(hora_str, "%H:%M").time()
    hora_fin_str = calcular_hora_fin(hora_str, duracion_min)
    hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()
    ids_confirmadas = obtener_ids_confirmadas_dia(conn, fecha)

    for estilista in ESTILISTAS:
        if not estilista_hace_servicio(estilista, servicio_id):
            continue
        if not estilista_trabaja(estilista, fecha):
            continue

        citas_bd = obtener_citas_estilista(conn, estilista["id"], fecha)
        bloques_gcal = gcal_bloques_estilista(estilista["id"], fecha, ids_confirmadas)
        citas_gcal = [{"hora_inicio": b["hora_inicio"], "hora_fin": b["hora_fin"]} for b in bloques_gcal]
        todas = citas_bd + citas_gcal

        if not hay_conflicto(todas, hora_inicio, hora_fin, buffer):
            return estilista

    return None


# ═══════════════════════════════════════════════════════════════
# GOOGLE CALENDAR — TAREAS EN BACKGROUND (no bloquean la respuesta)
# ═══════════════════════════════════════════════════════════════

def _bg_gcal_crear(cita_id: int, titulo: str, fecha: str, hora_inicio: str,
                   hora_fin: str, descripcion: str, servicio_id: str, telefono: str,
                   estilista_id: str = ""):
    google_event_id = calendar_service.crear_evento(
        titulo=titulo, fecha=fecha, hora_inicio=hora_inicio, hora_fin=hora_fin,
        descripcion=descripcion, servicio_id=servicio_id,
        cliente_telefono=telefono, cita_id=cita_id, estilista_id=estilista_id,
    )
    if google_event_id:
        conn = get_db()
        _exec(conn, "UPDATE citas SET google_event_id = ? WHERE id = ?", (google_event_id, cita_id))
        conn.commit()
        conn.close()


def _bg_gcal_modificar(google_event_id: str, titulo: str, fecha: str,
                        hora_inicio: str, hora_fin: str, servicio_id: str):
    calendar_service.modificar_evento(
        google_event_id=google_event_id, titulo=titulo, fecha=fecha,
        hora_inicio=hora_inicio, hora_fin=hora_fin, servicio_id=servicio_id,
    )


def _bg_gcal_cancelar(google_event_id: str):
    calendar_service.cancelar_evento(google_event_id)


# ═══════════════════════════════════════════════════════════════
# API FASTAPI
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="Empresa SL — API de Citas",
    description="Backend de gestión de citas para salón de belleza. Integrable con Retell AI.",
    version="1.0.0",
)


@app.on_event("startup")
def startup():
    init_db()


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Devuelve siempre mensaje_voz para que Retell pueda hablar aunque haya un error."""
    msg = str(exc.detail)
    if exc.status_code == 404:
        voz = f"Lo siento, no encontré lo que buscabas. {msg}"
    elif exc.status_code == 409:
        voz = f"{msg}"
    elif exc.status_code == 400:
        voz = msg
    else:
        voz = "Ha ocurrido un error inesperado. Por favor, inténtalo de nuevo."
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": True, "detalle": msg, "mensaje_voz": voz},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    campos = [e["loc"][-1] for e in exc.errors() if e.get("loc")]
    voz = f"Faltan algunos datos para continuar: {', '.join(str(c) for c in campos)}. ¿Puedes repetirlos?"
    return JSONResponse(
        status_code=422,
        content={"error": True, "detalle": str(exc.errors()), "mensaje_voz": voz},
    )


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"❌ Excepción no controlada en {request.method} {request.url.path}: {type(exc).__name__}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "error": True,
            "detalle": f"{type(exc).__name__}: {str(exc)}",
            "mensaje_voz": "Lo siento, ha ocurrido un error interno. Por favor, inténtalo de nuevo.",
        },
    )


# --- STATUS ---

@app.get("/status")
def status():
    return {
        "backend": "ok",
        "google_calendar": "conectado" if calendar_service.enabled else "no configurado (las citas se guardan solo en BD local)",
        "calendar_id": os.getenv("GOOGLE_CALENDAR_ID", "no configurado"),
        "base_datos": "postgresql (persistente)" if _USE_PG else "sqlite (efímera — se borra en cada deploy)",
    }


# --- INFO ENDPOINTS ---

@app.get("/servicios", summary="Listar todos los servicios disponibles")
def listar_servicios():
    return {
        "servicios": SERVICIOS,
        "buffer_entre_citas_min": SALON_CONFIG["buffer_minutos"],
    }


@app.get("/estilistas", summary="Listar estilistas y sus especialidades")
def listar_estilistas():
    resultado = []
    for e in ESTILISTAS:
        dias_nombres = [DIAS_SEMANA_ES[d] for d in e["dias_trabaja"]]
        servicios_nombres = [obtener_servicio(s)["nombre"] for s in e["especialidades"]]
        resultado.append({
            "id": e["id"],
            "nombre": e["nombre"],
            "especialidades": servicios_nombres,
            "dias_trabaja": dias_nombres,
        })
    return {"estilistas": resultado}


@app.get("/info", summary="Información general del salón")
def info_salon():
    return SALON_CONFIG


# --- DISPONIBILIDAD ---

class DisponibilidadRequest(BaseModel):
    fecha: str = Field(..., description="Fecha (YYYY-MM-DD o nombre de día en español/inglés)")
    servicio_id: str = Field(..., description="ID del servicio")
    estilista_id: str = Field(default="cualquiera", description="ID del estilista o 'cualquiera'")
    horario_preferido: str = Field(default="cualquiera", description="'manana', 'tarde' o 'cualquiera'")
    hora_preferida: str = Field(default="", description="Hora exacta solicitada por el cliente, ej: '10:00'. Si se indica, el mensaje_voz aclarará si esa hora no está disponible.")


@app.post("/disponibilidad", summary="Consultar huecos libres (POST)")
def consultar_disponibilidad_post(req: DisponibilidadRequest):
    return _consultar_disponibilidad(req.fecha, req.servicio_id, req.estilista_id, req.horario_preferido, req.hora_preferida)


@app.get("/disponibilidad", summary="Consultar huecos libres (GET legacy)")
def consultar_disponibilidad(
    fecha: str = Query(..., description="Fecha YYYY-MM-DD"),
    servicio_id: str = Query(..., description="ID del servicio"),
    estilista_id: str = Query(default="cualquiera", description="ID del estilista o 'cualquiera'"),
    horario_preferido: str = Query(default="cualquiera", description="'manana', 'tarde' o 'cualquiera'"),
    hora_preferida: str = Query(default="", description="Hora exacta solicitada, ej: '10:00'"),
):
    return _consultar_disponibilidad(fecha, servicio_id, estilista_id, horario_preferido, hora_preferida)


def _consultar_disponibilidad(fecha: str, servicio_id: str, estilista_id: str = "cualquiera", horario_preferido: str = "cualquiera", hora_preferida: str = ""):
    """
    Devuelve los huecos disponibles para un servicio en una fecha.
    Si estilista_id es 'cualquiera', devuelve disponibilidad de todos los que hacen ese servicio.
    """
    try:
        fecha_dt = parsear_fecha(fecha)
    except ValueError:
        raise HTTPException(400, f"No entendí la fecha '{fecha}'. Usa YYYY-MM-DD o un nombre de día como 'lunes'.")

    servicio = obtener_servicio(servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{servicio_id}' no encontrado. Servicios válidos: {[s['id'] for s in SERVICIOS]}")
    servicio_id = servicio["id"]  # normalizar al ID canónico

    horario = salon_abierto(fecha_dt)
    if not horario:
        return {
            "disponible": False,
            "mensaje": f"El salón está cerrado los {dia_en_plural(fecha_dt)}.",
            "sugerencia": "Prueba otro día de lunes a sábado.",
            "huecos": {},
            "mensaje_voz": f"Lo siento, los {dia_en_plural(fecha_dt)} el salón está cerrado. ¿Probamos otro día?",
        }

    # Comprobar antelación mínima
    ahora = ahora_madrid()
    fecha_hora_minima = ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])
    if fecha_dt < fecha_hora_minima.date():
        return {
            "disponible": False,
            "mensaje": f"Las citas deben reservarse con al menos {SALON_CONFIG['antelacion_minima_horas']} horas de antelación.",
            "huecos": {},
        }

    conn = get_db()
    resultado = {}

    if estilista_id == "cualquiera":
        estilistas_validos = [e for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
    else:
        est = obtener_estilista(estilista_id)
        if not est:
            conn.close()
            raise HTTPException(404, f"Estilista '{estilista_id}' no encontrado.")
        if not estilista_hace_servicio(est, servicio_id):
            conn.close()
            otros = [e["nombre"] for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
            otros_str = " o ".join(otros) if otros else "ninguno disponible"
            return {
                "disponible": False,
                "mensaje": f"{est['nombre']} no realiza el servicio '{servicio['nombre']}'.",
                "estilistas_que_lo_hacen": otros,
                "huecos": {},
                "mensaje_voz": (
                    f"Lo siento, {est['nombre']} no hace {servicio['nombre']}. "
                    f"Para este servicio puedes ir con {otros_str}. ¿Cuál te viene mejor?"
                ),
            }
        estilistas_validos = [est]

    # IDs confirmadas del día para filtrar eventos huérfanos de Calendar
    ids_confirmadas = obtener_ids_confirmadas_dia(conn, fecha_dt)

    # Normalizar hora_preferida ANTES del bucle para poder comparar contra todos los huecos
    hora_pref_pre = ""
    if hora_preferida:
        try:
            hora_pref_pre = normalizar_hora(hora_preferida)
        except Exception:
            hora_pref_pre = ""

    for est in estilistas_validos:
        huecos = encontrar_huecos_libres(conn, est["id"], fecha_dt, servicio["duracion_min"], ids_confirmadas)

        # Filtrar huecos pasados si es hoy
        if fecha_dt == ahora.date():
            hora_minima = fecha_hora_minima.strftime("%H:%M")
            huecos = [h for h in huecos if h >= hora_minima]

        if huecos:
            # Comprobar si la hora_preferida exacta está disponible para ESTE estilista
            # antes de reducir a la muestra de 3
            tiene_hora_exacta = hora_pref_pre and hora_pref_pre in huecos

            manana = [h for h in huecos if h < "13:00"]
            tarde = [h for h in huecos if h >= "13:00"]

            # Filtrar según preferencia horaria del cliente
            hp = horario_preferido.lower().replace("ñ", "n")
            if hp in ("manana", "mañana", "morning"):
                pool = manana if manana else huecos
            elif hp in ("tarde", "afternoon", "evening"):
                pool = tarde if tarde else huecos
            else:
                pool = huecos

            if hora_pref_pre and not tiene_hora_exacta:
                # Hora específica pedida pero no disponible para este estilista
                # → mostrar las 2 opciones más cercanas a la hora pedida
                def _mins(hhmm):
                    h, m = map(int, hhmm.split(":"))
                    return h * 60 + m
                pref_mins = _mins(hora_pref_pre)
                pool_ordenado = sorted(pool, key=lambda h: abs(_mins(h) - pref_mins))
                huecos_muestra = sorted(pool_ordenado[:2])
            else:
                # Sin hora preferida o con hora exacta disponible → 3 representativas
                huecos_muestra = []
                if pool:
                    huecos_muestra.append(pool[0])
                if len(pool) > 2:
                    huecos_muestra.append(pool[len(pool) // 2])
                if len(pool) > 1:
                    huecos_muestra.append(pool[-1])
                huecos_muestra = sorted(set(huecos_muestra))

            # Si la hora exacta está disponible pero no cayó en la muestra, añadirla
            if tiene_hora_exacta and hora_pref_pre not in huecos_muestra:
                huecos_muestra = sorted(set(huecos_muestra + [hora_pref_pre]))

            # Seleccionar hasta 4 huecos representativos por franja para el LLM
            manana_muestra = manana[:4] if len(manana) > 4 else manana
            tarde_muestra = tarde[:4] if len(tarde) > 4 else tarde

            resultado[est["nombre"]] = {
                "estilista_id": est["id"],
                "huecos_disponibles": huecos_muestra,
                "huecos_legibles": [hora_a_texto(h) for h in huecos_muestra],
                "hay_mas_opciones": len(pool) > len(huecos_muestra),
                "tiene_hora_exacta": bool(tiene_hora_exacta),
                "huecos_manana": manana_muestra,
                "huecos_manana_legibles": [hora_a_texto(h) for h in manana_muestra],
                "huecos_tarde": tarde_muestra,
                "huecos_tarde_legibles": [hora_a_texto(h) for h in tarde_muestra],
                "total_manana": len(manana),
                "total_tarde": len(tarde),
            }

    conn.close()

    if not resultado:
        # Normalizar hora_preferida para el mensaje de no disponibilidad
        hora_pref_norm_nd = ""
        if hora_preferida:
            try:
                hora_pref_norm_nd = normalizar_hora(hora_preferida)
            except Exception:
                hora_pref_norm_nd = ""

        dias_es_nd = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
        meses_es_nd = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
        fecha_legible_nd = f"{dias_es_nd[fecha_dt.weekday()]} {fecha_dt.day} de {meses_es_nd[fecha_dt.month-1]}"

        if hora_pref_norm_nd:
            hora_legible_nd = hora_a_texto(hora_pref_norm_nd)
            msg_nd = (
                f"Lo siento, {hora_legible_nd} el {fecha_legible_nd} no tenemos disponibilidad para {servicio['nombre']}. "
                f"¿Quieres que busque ese mismo servicio otro día de la semana, o te doy las primeras opciones disponibles?"
            )
        else:
            msg_nd = f"Lo siento, no tenemos disponibilidad para {servicio['nombre']} el {fecha_legible_nd}. ¿Probamos otro día?"

        return {
            "disponible": False,
            "mensaje": f"No hay disponibilidad para '{servicio['nombre']}' el {fecha}.",
            "sugerencia": "Prueba otro día o consulta /disponibilidad/proximos-dias.",
            "huecos": {},
            "mensaje_voz": msg_nd,
        }

    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible = f"{dias_es[fecha_dt.weekday()]} {fecha_dt.day} de {meses_es[fecha_dt.month-1]}"

    # hora_pref_norm ya se calculó antes del bucle como hora_pref_pre
    hora_pref_norm = hora_pref_pre

    # ¿La hora pedida exacta está disponible para algún estilista?
    # Usamos tiene_hora_exacta calculado contra TODOS los huecos (no solo la muestra)
    hora_exacta_disponible = any(datos.get("tiene_hora_exacta") for datos in resultado.values())

    # ── Generar mensaje_voz natural para Retell ──
    nombres_estilistas = list(resultado.keys())
    num_est = len(nombres_estilistas)

    # Recopilar info resumida
    todos_huecos_legibles = []  # lista plana de horas legibles (sin repetir)
    partes_voz = []
    for nombre_est, datos in resultado.items():
        legibles = datos["huecos_legibles"]
        todos_huecos_legibles.extend(legibles)
        if len(legibles) == 1:
            partes_voz.append(f"con {nombre_est} a {legibles[0]}")
        elif len(legibles) == 2:
            partes_voz.append(f"con {nombre_est} a {legibles[0]} o {legibles[1]}")
        else:
            partes_voz.append(f"con {nombre_est} a {', '.join(legibles[:-1])} o {legibles[-1]}")

    # Detectar si hay huecos de mañana y tarde para preguntar preferencia
    hay_manana = any(
        any(h < "13:00" for h in datos["huecos_disponibles"])
        for datos in resultado.values()
    )
    hay_tarde = any(
        any(h >= "13:00" for h in datos["huecos_disponibles"])
        for datos in resultado.values()
    )

    if hora_pref_norm and hora_exacta_disponible:
        # ── Hora pedida exacta SÍ disponible → confirmar directamente ──
        hora_pref_legible = hora_a_texto(hora_pref_norm)
        estilistas_con_hora = [
            nombre for nombre, datos in resultado.items()
            if hora_pref_norm in datos["huecos_disponibles"]
        ]
        if len(estilistas_con_hora) == 1:
            msg_voz = (
                f"Perfecto, tengo hueco a {hora_pref_legible} el {fecha_legible} "
                f"con {estilistas_con_hora[0]}. ¿Te lo reservo?"
            )
        else:
            estilistas_str = " o con ".join(estilistas_con_hora)
            msg_voz = (
                f"Tengo {hora_pref_legible} el {fecha_legible} disponible "
                f"con {estilistas_str}. ¿Con quién lo prefieres?"
            )

    elif hora_pref_norm and not hora_exacta_disponible:
        # ── Hora pedida pero NO disponible → sugerir cercanas ──
        hora_pref_legible = hora_a_texto(hora_pref_norm)
        if num_est == 1:
            datos_unico = list(resultado.values())[0]
            legibles = datos_unico["huecos_legibles"]
            opciones = " o a ".join(legibles)
            msg_voz = (
                f"Uy, a {hora_pref_legible} no queda hueco. "
                f"Lo más cercano con {nombres_estilistas[0]} sería a {opciones}. "
                f"¿Te viene bien?"
            )
        else:
            # Coger las 2 opciones más cercanas de cualquier estilista
            msg_voz = (
                f"A {hora_pref_legible} no tengo hueco para {servicio['nombre']}. "
                f"Ese día tengo: {'; '.join(partes_voz[:2])}. "
                f"¿Alguna de estas te viene bien?"
            )

    elif num_est == 1:
        # ── Un solo estilista con disponibilidad ──
        datos_unico = list(resultado.values())[0]
        legibles = datos_unico["huecos_legibles"]
        hay_mas = datos_unico["hay_mas_opciones"]
        m_leg = datos_unico["huecos_manana_legibles"]
        t_leg = datos_unico["huecos_tarde_legibles"]
        if len(legibles) == 1:
            msg_voz = (
                f"Para {servicio['nombre']} el {fecha_legible}, "
                f"tengo un hueco a {legibles[0]} con {nombres_estilistas[0]}. ¿Te va bien?"
            )
        elif hay_mas and hay_manana and hay_tarde:
            # Desglosar mañana/tarde para que el LLM pueda responder sin segunda llamada
            m_str = ", ".join(m_leg[:-1]) + " o " + m_leg[-1] if len(m_leg) > 1 else m_leg[0] if m_leg else ""
            t_str = ", ".join(t_leg[:-1]) + " o " + t_leg[-1] if len(t_leg) > 1 else t_leg[0] if t_leg else ""
            msg_voz = (
                f"Para {servicio['nombre']} el {fecha_legible} con {nombres_estilistas[0]} "
                f"tengo huecos por la mañana a {m_str}, y por la tarde a {t_str}. "
                f"¿Qué te viene mejor?"
            )
        else:
            opciones = ", ".join(legibles[:-1]) + " o " + legibles[-1]
            msg_voz = (
                f"Para {servicio['nombre']} el {fecha_legible} con {nombres_estilistas[0]} "
                f"tengo a {opciones}. ¿Cuál prefieres?"
            )

    else:
        # ── Múltiples estilistas con disponibilidad ──
        alguno_tiene_mas = any(d.get("hay_mas_opciones") for d in resultado.values())
        nombres_str = ", ".join(nombres_estilistas[:-1]) + " y " + nombres_estilistas[-1]

        if alguno_tiene_mas and hay_manana and hay_tarde:
            # Muchas opciones → preguntar preferencia en vez de listar todo
            nombres_str_detalle = ", ".join(nombres_estilistas[:-1]) + " y " + nombres_estilistas[-1] if len(nombres_estilistas) > 1 else nombres_estilistas[0]
            msg_voz = (
                f"El {fecha_legible} tenemos bastante disponibilidad para {servicio['nombre']} "
                f"tanto por la mañana como por la tarde, con {nombres_str_detalle}. "
                f"¿Prefieres por la mañana o por la tarde?"
            )
        elif alguno_tiene_mas and hay_manana and not hay_tarde:
            # Solo mañana pero muchas opciones → resumir
            nombres_str_detalle = ", ".join(nombres_estilistas[:-1]) + " y " + nombres_estilistas[-1] if len(nombres_estilistas) > 1 else nombres_estilistas[0]
            # Dar solo primer y último hueco como rango
            todas_manana = sorted(set(h for datos in resultado.values() for h in datos["huecos_manana"][:2]))
            if len(todas_manana) >= 2:
                rango = f"desde {hora_a_texto(todas_manana[0])} hasta {hora_a_texto(todas_manana[-1])}"
            else:
                rango = f"a {hora_a_texto(todas_manana[0])}" if todas_manana else "por la mañana"
            msg_voz = (
                f"El {fecha_legible} para {servicio['nombre']} tenemos hueco por la mañana "
                f"{rango}, con {nombres_str_detalle}. "
                f"¿Tienes preferencia de hora o de estilista?"
            )
        elif alguno_tiene_mas and not hay_manana and hay_tarde:
            # Solo tarde pero muchas opciones → resumir
            nombres_str_detalle = ", ".join(nombres_estilistas[:-1]) + " y " + nombres_estilistas[-1] if len(nombres_estilistas) > 1 else nombres_estilistas[0]
            todas_tarde = sorted(set(h for datos in resultado.values() for h in datos["huecos_tarde"][:2]))
            if len(todas_tarde) >= 2:
                rango = f"desde {hora_a_texto(todas_tarde[0])} hasta {hora_a_texto(todas_tarde[-1])}"
            else:
                rango = f"a {hora_a_texto(todas_tarde[0])}" if todas_tarde else "por la tarde"
            msg_voz = (
                f"El {fecha_legible} para {servicio['nombre']} tenemos hueco por la tarde "
                f"{rango}, con {nombres_str_detalle}. "
                f"¿Tienes preferencia de hora o de estilista?"
            )
        else:
            # Pocas opciones → listar directamente pero máximo 2 estilistas
            msg_voz = (
                f"Para {servicio['nombre']} el {fecha_legible} tengo: "
                f"{'. '.join(partes_voz[:2])}. ¿Cuál te viene mejor?"
            )

    return {
        "disponible": True,
        "fecha": fecha_dt.isoformat(),
        "fecha_legible": fecha_legible,
        "servicio": servicio["nombre"],
        "duracion_min": servicio["duracion_min"],
        "precio_desde": servicio["precio"],
        "buffer_entre_citas_min": SALON_CONFIG["buffer_minutos"],
        "huecos": resultado,
        "mensaje_voz": msg_voz,
    }


# --- CREAR CITA ---

@app.post("/citas", summary="Reservar una nueva cita")
def crear_cita(req: CrearCitaRequest, background_tasks: BackgroundTasks):
    """Crea una nueva cita validando disponibilidad, buffer, horario y conflictos."""

    # Helper para devolver errores en formato que Retell puede leer
    def _error_cita(msg_voz: str):
        return {"exito": False, "mensaje_voz": msg_voz}

    # Validar que el nombre parece un nombre real (evitar que Retell pase "Sí", "No", "Ok"...)
    _NOMBRES_INVALIDOS = {"sí", "si", "no", "ok", "vale", "bueno", "claro", "hola", "adiós", "gracias", "perfecto", "bien"}
    nombre_limpio = req.cliente_nombre.strip()
    if nombre_limpio.lower() in _NOMBRES_INVALIDOS or len(nombre_limpio) < 3:
        return _error_cita("No he pillado bien el nombre. ¿Me lo puedes repetir, por favor?")

    # Validar teléfono: debe tener al menos 9 dígitos
    telefono_digitos = "".join(c for c in req.cliente_telefono if c.isdigit())
    if len(telefono_digitos) < 9:
        return _error_cita("Ese número de teléfono no parece completo. Necesito los 9 dígitos. ¿Me lo puedes repetir?")

    servicio = obtener_servicio(req.servicio_id)
    if not servicio:
        return _error_cita(f"No he encontrado el servicio '{req.servicio_id}'. ¿Puedes repetirme qué servicio necesitas?")
    servicio_id_canon = servicio["id"]  # normalizar al ID canónico

    try:
        fecha_dt = parsear_fecha(req.fecha)
    except ValueError:
        return _error_cita(f"No he entendido bien la fecha. ¿Me la puedes repetir?")

    # Validar que el salón está abierto
    horario = salon_abierto(fecha_dt)
    if not horario:
        return _error_cita(f"Lo siento, los {dia_en_plural(fecha_dt)} el salón está cerrado. ¿Probamos otro día?")

    # Normalizar hora: "9" → "09:00", "9:00" → "09:00", "2 de la tarde" → "14:00"
    try:
        hora_norm = normalizar_hora(req.hora)
        hora_inicio = datetime.strptime(hora_norm, "%H:%M").time()
    except (ValueError, AttributeError):
        return _error_cita("No he entendido bien la hora. ¿Me la puedes repetir?")

    hora_fin_str = calcular_hora_fin(hora_norm, servicio["duracion_min"])
    hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()

    hora_abre = datetime.strptime(horario["abre"], "%H:%M").time()
    hora_cierra = datetime.strptime(horario["cierra"], "%H:%M").time()

    if hora_inicio < hora_abre or hora_fin > hora_cierra:
        return _error_cita(f"Esa hora queda fuera de nuestro horario. Abrimos de {hora_a_texto(horario['abre'])} a {hora_a_texto(horario['cierra'])}. ¿Te busco otra hora?")

    # Validar antelación mínima
    ahora = ahora_madrid()
    fecha_hora_cita = datetime.combine(fecha_dt, hora_inicio, tzinfo=TZ)
    minimo = ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])
    if fecha_hora_cita < minimo:
        return _error_cita(f"Las citas necesitan al menos {SALON_CONFIG['antelacion_minima_horas']} horas de antelación. ¿Probamos con otra hora o día?")

    conn = get_db()

    # Resolver estilista — acepta ID ("maria") o nombre completo ("María García")
    def _resolver_estilista_id(valor: str):
        """Devuelve el objeto estilista buscando por ID o por nombre (fuzzy)."""
        est = obtener_estilista(valor)
        if est:
            return est
        # Buscar por nombre (case-insensitive, sin acentos)
        import unicodedata
        def normalizar(s):
            return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()
        valor_norm = normalizar(valor)
        for e in ESTILISTAS:
            if normalizar(e["nombre"]) == valor_norm or normalizar(e["id"]) == valor_norm:
                return e
        return None

    if req.estilista_id in ("cualquiera", "any", ""):
        estilista = buscar_mejor_estilista(conn, servicio_id_canon, fecha_dt, hora_norm, servicio["duracion_min"])
        if not estilista:
            conn.close()
            return _error_cita("Lo siento, no hay ningún estilista disponible para ese servicio en esa fecha y hora. ¿Probamos con otro horario?")
    else:
        estilista = _resolver_estilista_id(req.estilista_id)
        if not estilista:
            conn.close()
            return _error_cita(f"No encuentro al estilista '{req.estilista_id}'. ¿Puedes repetirme el nombre?")

    # Validar que el estilista trabaja ese día
    if not estilista_trabaja(estilista, fecha_dt):
        dias = [DIAS_SEMANA_ES[d] for d in estilista["dias_trabaja"]]
        conn.close()
        return _error_cita(f"{estilista['nombre']} no trabaja los {dia_en_plural(fecha_dt)}. ¿Quieres que busque otro estilista o probamos otro día?")

    # Validar que el estilista hace ese servicio
    if not estilista_hace_servicio(estilista, servicio_id_canon):
        otros = [e["nombre"] for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id_canon)]
        otros_str = " o ".join(otros) if otros else "ninguno disponible"
        conn.close()
        return _error_cita(f"{estilista['nombre']} no hace {servicio['nombre']}. Para ese servicio puedes ir con {otros_str}. ¿Cuál prefieres?")

    # Comprobar conflictos con buffer (BD + Google Calendar)
    ids_confirmadas = obtener_ids_confirmadas_dia(conn, fecha_dt)
    citas_bd = obtener_citas_estilista(conn, estilista["id"], fecha_dt)
    bloques_gcal = gcal_bloques_estilista(estilista["id"], fecha_dt, ids_confirmadas)
    citas_gcal = [{"hora_inicio": b["hora_inicio"], "hora_fin": b["hora_fin"]} for b in bloques_gcal]
    citas = citas_bd + citas_gcal
    buffer = SALON_CONFIG["buffer_minutos"]

    if hay_conflicto(citas, hora_inicio, hora_fin, buffer):
        # Buscar alternativas cercanas
        huecos = encontrar_huecos_libres(conn, estilista["id"], fecha_dt, servicio["duracion_min"])
        conn.close()
        sugerencias = huecos[:4] if huecos else []
        if sugerencias:
            opciones = ", ".join(hora_a_texto(h) for h in sugerencias)
            msg_voz = f"Uy, ese hueco de {hora_a_texto(hora_norm)} acaba de quedarse sin disponibilidad. Con {estilista['nombre']} ese día tengo hueco a {opciones}. ¿Te viene alguna?"
        else:
            msg_voz = f"Lo siento, ese día ya no tenemos más huecos disponibles con {estilista['nombre']}. ¿Probamos otro día?"
        return {
            "exito": False,
            "conflicto": True,
            "alternativas_mismo_dia": sugerencias,
            "mensaje_voz": msg_voz,
        }

    # Todo OK — crear la cita
    cita_id = _insert(
        conn,
        """INSERT INTO citas
           (cliente_nombre, cliente_telefono, cliente_nuevo, servicio_id, estilista_id,
            fecha, hora_inicio, hora_fin, duracion_min, precio_estimado, notas, estado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmada')""",
        (
            req.cliente_nombre,
            req.cliente_telefono,
            1 if req.cliente_nuevo else 0,
            servicio_id_canon,
            estilista["id"],
            fecha_dt.isoformat(),
            hora_norm,
            hora_fin_str,
            servicio["duracion_min"],
            servicio["precio"],
            req.notas,
        )
    )
    conn.commit()

    conn.close()

    # ── Google Calendar: crear evento en background (no bloquea la respuesta) ──
    background_tasks.add_task(
        _bg_gcal_crear, cita_id,
        f"{servicio['nombre']} — {req.cliente_nombre} (con {estilista['nombre']})",
        fecha_dt.isoformat(), hora_norm, hora_fin_str, req.notas, req.servicio_id, req.cliente_telefono,
        estilista["id"],
    )

    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible = f"{dias_es[fecha_dt.weekday()]} {fecha_dt.day} de {meses_es[fecha_dt.month-1]}"
    nombre_corto = req.cliente_nombre.split()[0]
    return {
        "exito": True,
        "cita_id": cita_id,
        "resumen": {
            "cliente": req.cliente_nombre,
            "telefono": req.cliente_telefono,
            "servicio": servicio["nombre"],
            "estilista": estilista["nombre"],
            "fecha": fecha_dt.isoformat(),
            "fecha_legible": fecha_legible,
            "hora_inicio": hora_norm,
            "hora_fin": hora_fin_str,
            "duracion_min": servicio["duracion_min"],
            "precio_desde": servicio["precio"],
            "notas": req.notas,
        },
        "mensaje": f"Cita confirmada: {servicio['nombre']} con {estilista['nombre']} el {fecha_legible} a {hora_a_texto(hora_norm)}.",
        "mensaje_voz": f"¡Perfecto, {nombre_corto}! Ya te he reservado {servicio['nombre']} con {estilista['nombre']} para el {fecha_legible} a {hora_a_texto(hora_norm)}. Durará aproximadamente {servicio['duracion_min']} minutos y el precio es desde {servicio['precio']:.0f} euros. ¿Necesitas algo más?",
    }


# --- BUSCAR CITAS ---

@app.get("/citas/buscar", summary="Buscar citas por teléfono o nombre")
def buscar_citas(
    telefono: str = Query(default=None, description="Teléfono del cliente"),
    nombre: str = Query(default=None, description="Nombre del cliente (búsqueda parcial)"),
    estado: str = Query(default="confirmada", description="Estado: confirmada, cancelada, todas"),
):
    if not telefono and not nombre:
        raise HTTPException(400, "Debes proporcionar al menos un teléfono o nombre.")

    conn = get_db()
    query = "SELECT * FROM citas WHERE 1=1"
    params = []

    if telefono:
        query += " AND cliente_telefono = ?"
        params.append(telefono)
    if nombre:
        query += " AND cliente_nombre LIKE ?"
        params.append(f"%{nombre}%")
    if estado != "todas":
        query += " AND estado = ?"
        params.append(estado)

    query += " ORDER BY fecha DESC, hora_inicio DESC"
    rows = _query(conn, query, params)
    conn.close()

    citas = []
    for r in rows:
        servicio = obtener_servicio(r["servicio_id"])
        estilista = obtener_estilista(r["estilista_id"])
        # Normalizar fecha y hora a strings (PG devuelve objetos date/time)
        fecha_str = r["fecha"].isoformat() if hasattr(r["fecha"], "isoformat") else str(r["fecha"])
        hora_ini_str = str(r["hora_inicio"])[:5]
        hora_fin_str = str(r["hora_fin"])[:5]
        citas.append({
            "cita_id": r["id"],
            "cliente": r["cliente_nombre"],
            "telefono": r["cliente_telefono"],
            "servicio": servicio["nombre"] if servicio else r["servicio_id"],
            "estilista": estilista["nombre"] if estilista else r["estilista_id"],
            "fecha": fecha_str,
            "hora_inicio": hora_ini_str,
            "hora_fin": hora_fin_str,
            "duracion_min": r["duracion_min"],
            "precio_estimado": r["precio_estimado"],
            "estado": r["estado"],
            "notas": r["notas"],
        })

    if not citas:
        busqueda = f"el teléfono {telefono}" if telefono else f"el nombre {nombre}"
        return {
            "total": 0,
            "citas": [],
            "mensaje_voz": f"No he encontrado ninguna cita con {busqueda}. ¿Quieres que te ayude a hacer una nueva reserva?",
        }

    # Construir mensaje_voz con las citas encontradas (máx 3 para no saturar)
    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    proximas = [c for c in citas if c["fecha"] >= hoy_madrid().isoformat()][:3]
    if proximas:
        if len(proximas) == 1:
            c = proximas[0]
            f = date.fromisoformat(c["fecha"])
            fecha_leg = f"{dias_es[f.weekday()]} {f.day} de {meses_es[f.month-1]}"
            msg = f"Tienes una cita de {c['servicio']} con {c['estilista']} el {fecha_leg} a {hora_a_texto(c['hora_inicio'])}."
        else:
            partes = []
            for c in proximas:
                f = date.fromisoformat(c["fecha"])
                fecha_leg = f"{dias_es[f.weekday()]} {f.day} de {meses_es[f.month-1]}"
                partes.append(f"{c['servicio']} con {c['estilista']} el {fecha_leg} a {hora_a_texto(c['hora_inicio'])}")
            msg = "Tienes estas citas próximas: " + "; ".join(partes) + "."
    else:
        msg = "No tienes citas próximas. ¿Quieres reservar una?"

    return {"total": len(citas), "citas": citas, "mensaje_voz": msg}


# --- MODIFICAR CITA ---

@app.put("/citas/{cita_id}", summary="Modificar una cita existente")
def modificar_cita(cita_id: int, req: ModificarCitaRequest, background_tasks: BackgroundTasks):
    conn = get_db()
    rows = _query(conn, "SELECT * FROM citas WHERE id = ? AND estado = 'confirmada'", (cita_id,))
    cita = rows[0] if rows else None

    if not cita:
        conn.close()
        raise HTTPException(404, f"No se encontró cita activa con ID {cita_id}.")

    # Determinar nuevos valores
    nuevo_servicio_id = req.nuevo_servicio_id or cita["servicio_id"]
    nuevo_estilista_id = req.nuevo_estilista_id or cita["estilista_id"]
    nueva_fecha_str = req.nueva_fecha or str(cita["fecha"])
    nueva_hora = req.nueva_hora or str(cita["hora_inicio"])[:5]
    nuevas_notas = req.notas if req.notas is not None else cita["notas"]

    servicio = obtener_servicio(nuevo_servicio_id)
    if not servicio:
        conn.close()
        raise HTTPException(404, f"Servicio '{nuevo_servicio_id}' no encontrado.")

    try:
        nueva_fecha_dt = parsear_fecha(nueva_fecha_str)
    except ValueError:
        conn.close()
        raise HTTPException(400, f"No entendí la fecha '{nueva_fecha_str}'. Prueba con 'lunes', 'el martes', '30 de marzo'...")

    estilista = obtener_estilista(nuevo_estilista_id)
    if not estilista:
        conn.close()
        raise HTTPException(404, f"Estilista '{nuevo_estilista_id}' no encontrado.")

    # Validaciones
    horario = salon_abierto(nueva_fecha_dt)
    if not horario:
        conn.close()
        raise HTTPException(400, f"El salón está cerrado los {dia_en_plural(nueva_fecha_dt)}.")

    if not estilista_trabaja(estilista, nueva_fecha_dt):
        conn.close()
        raise HTTPException(400, f"{estilista['nombre']} no trabaja los {dia_en_plural(nueva_fecha_dt)}.")

    if not estilista_hace_servicio(estilista, nuevo_servicio_id):
        conn.close()
        raise HTTPException(400, f"{estilista['nombre']} no realiza '{servicio['nombre']}'.")

    hora_inicio = datetime.strptime(nueva_hora, "%H:%M").time()
    hora_fin_str = calcular_hora_fin(nueva_hora, servicio["duracion_min"])
    hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()

    # Comprobar conflictos (excluyendo la cita actual)
    citas = obtener_citas_estilista(conn, estilista["id"], nueva_fecha_dt)
    citas = [c for c in citas if c["id"] != cita_id]
    buffer = SALON_CONFIG["buffer_minutos"]

    if hay_conflicto(citas, hora_inicio, hora_fin, buffer):
        conn.close()
        raise HTTPException(409, f"Ese horario no está disponible con {estilista['nombre']}.")

    # Actualizar
    _exec(
        conn,
        """UPDATE citas SET
            servicio_id = ?, estilista_id = ?, fecha = ?, hora_inicio = ?,
            hora_fin = ?, duracion_min = ?, precio_estimado = ?, notas = ?,
            modificada_en = CURRENT_TIMESTAMP
           WHERE id = ?""",
        (nuevo_servicio_id, estilista["id"], nueva_fecha_str, nueva_hora,
         hora_fin_str, servicio["duracion_min"], servicio["precio"], nuevas_notas,
         cita_id)
    )
    conn.commit()

    conn.close()

    # ── Google Calendar: actualizar evento en background ──
    google_event_id = cita.get("google_event_id", "")
    if google_event_id:
        background_tasks.add_task(
            _bg_gcal_modificar, google_event_id,
            f"{servicio['nombre']} — {cita['cliente_nombre']} (con {estilista['nombre']})",
            nueva_fecha_str, nueva_hora, hora_fin_str, nuevo_servicio_id,
        )

    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible_mod = f"{dias_es[nueva_fecha_dt.weekday()]} {nueva_fecha_dt.day} de {meses_es[nueva_fecha_dt.month-1]}"
    hora_legible_mod = hora_a_texto(nueva_hora)
    nombre_corto = cita["cliente_nombre"].split()[0]
    return {
        "exito": True,
        "cita_id": cita_id,
        "mensaje": f"Cita modificada: {servicio['nombre']} con {estilista['nombre']} el {fecha_legible_mod} a {hora_legible_mod}.",
        "mensaje_voz": f"Listo, {nombre_corto}. He cambiado tu cita: {servicio['nombre']} con {estilista['nombre']} el {fecha_legible_mod} a {hora_legible_mod}. ¿Necesitas algo más?",
        "resumen": {
            "servicio": servicio["nombre"],
            "estilista": estilista["nombre"],
            "fecha": nueva_fecha_str,
            "hora_inicio": nueva_hora,
            "hora_fin": hora_fin_str,
            "duracion_min": servicio["duracion_min"],
            "precio_desde": servicio["precio"],
        },
    }


# --- CANCELAR CITA ---

@app.delete("/citas/{cita_id}", summary="Cancelar una cita")
def cancelar_cita(cita_id: int, background_tasks: BackgroundTasks):
    conn = get_db()
    rows = _query(conn, "SELECT * FROM citas WHERE id = ? AND estado = 'confirmada'", (cita_id,))
    cita = rows[0] if rows else None

    if not cita:
        conn.close()
        return {
            "exito": False,
            "mensaje_voz": "No he encontrado ninguna cita activa con ese identificador. ¿Quieres que busque tu cita por tu número de teléfono?",
        }

    servicio = obtener_servicio(cita["servicio_id"])
    estilista = obtener_estilista(cita["estilista_id"])

    _exec(conn, "UPDATE citas SET estado = 'cancelada', modificada_en = CURRENT_TIMESTAMP WHERE id = ?", (cita_id,))
    conn.commit()

    conn.close()

    # ── Google Calendar: eliminar evento en background ──
    google_event_id = cita.get("google_event_id", "")
    if google_event_id:
        background_tasks.add_task(_bg_gcal_cancelar, google_event_id)

    fecha_can_str = cita["fecha"].isoformat() if hasattr(cita["fecha"], "isoformat") else str(cita["fecha"])
    hora_ini_can = str(cita["hora_inicio"])[:5]
    fecha_cancelada_dt = date.fromisoformat(fecha_can_str)
    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible_can = f"{dias_es[fecha_cancelada_dt.weekday()]} {fecha_cancelada_dt.day} de {meses_es[fecha_cancelada_dt.month-1]}"
    hora_legible_can = hora_a_texto(hora_ini_can)
    nombre_corto = cita["cliente_nombre"].split()[0]
    return {
        "exito": True,
        "cita_id": cita_id,
        "mensaje": f"Cita cancelada: {servicio['nombre']} con {estilista['nombre']} el {fecha_legible_can} a {hora_legible_can}.",
        "mensaje_voz": f"De acuerdo, {nombre_corto}. He cancelado tu cita de {servicio['nombre']} con {estilista['nombre']} del {fecha_legible_can}. Si quieres volver a reservar, aquí estaré.",
    }


# --- COMBO: MÚLTIPLES SERVICIOS EN UNA RESERVA ---

@app.post("/citas/combo", summary="Reservar varios servicios seguidos en una sola llamada")
def crear_combo(req: CrearComboRequest, background_tasks: BackgroundTasks):
    """
    Permite reservar varios servicios back-to-back (ej: corte + manicura).
    Los servicios se encadenan automáticamente añadiendo el buffer entre cada uno.
    Se puede asignar el mismo o distintos estilistas según disponibilidad.
    """
    def _error_combo(msg_voz: str):
        return {"exito": False, "mensaje_voz": msg_voz}

    # Validar teléfono
    telefono_digitos = "".join(c for c in req.cliente_telefono if c.isdigit())
    if len(telefono_digitos) < 9:
        return _error_combo("Ese número de teléfono no parece completo. Necesito los 9 dígitos. ¿Me lo puedes repetir?")

    # Validar todos los servicios
    servicios_objs = []
    for sid in req.servicios:
        s = obtener_servicio(sid)
        if not s:
            return _error_combo(f"No he encontrado el servicio '{sid}'. ¿Puedes repetirme qué servicio necesitas?")
        servicios_objs.append(s)

    try:
        fecha_dt = parsear_fecha(req.fecha)
    except ValueError:
        raise HTTPException(400, f"No entendí la fecha '{req.fecha}'. Usa YYYY-MM-DD o un nombre de día como 'lunes'.")

    horario = salon_abierto(fecha_dt)
    if not horario:
        raise HTTPException(400, f"El salón está cerrado los {dia_en_plural(fecha_dt)}.")

    ahora = ahora_madrid()
    minimo = ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])
    if fecha_dt < minimo.date():
        raise HTTPException(400, f"Las citas deben reservarse con al menos {SALON_CONFIG['antelacion_minima_horas']} horas de antelación.")

    conn = get_db()
    buffer = SALON_CONFIG["buffer_minutos"]
    hora_actual = req.hora
    citas_creadas = []

    try:
        for sid, servicio in zip(req.servicios, servicios_objs):
            hora_fin_str = calcular_hora_fin(hora_actual, servicio["duracion_min"])
            hora_fin_t = datetime.strptime(hora_fin_str, "%H:%M").time()
            hora_cierra = datetime.strptime(horario["cierra"], "%H:%M").time()

            if hora_fin_t > hora_cierra:
                raise HTTPException(
                    400,
                    f"El servicio '{servicio['nombre']}' terminaría a las {hora_fin_str}, "
                    f"fuera del horario de cierre ({horario['cierra']}). "
                    f"Elige una hora de inicio más temprana."
                )

            hora_inicio_t = datetime.strptime(hora_actual, "%H:%M").time()

            # Encontrar estilista disponible para este servicio y horario
            if req.estilista_id == "cualquiera":
                estilista = buscar_mejor_estilista(conn, sid, fecha_dt, hora_actual, servicio["duracion_min"])
                if not estilista:
                    ids_creadas = [c["cita_id"] for c in citas_creadas]
                    raise HTTPException(
                        409,
                        f"No hay estilista disponible para '{servicio['nombre']}' a las {hora_actual}. "
                        f"Prueba una hora diferente."
                    )
            else:
                estilista = obtener_estilista(req.estilista_id)
                if not estilista:
                    raise HTTPException(404, f"Estilista '{req.estilista_id}' no encontrado.")
                if not estilista_hace_servicio(estilista, sid):
                    raise HTTPException(400, f"{estilista['nombre']} no realiza '{servicio['nombre']}'.")
                if not estilista_trabaja(estilista, fecha_dt):
                    raise HTTPException(400, f"{estilista['nombre']} no trabaja los {dia_en_plural(fecha_dt)}.")
                citas_est = obtener_citas_estilista(conn, estilista["id"], fecha_dt)
                # Excluir las citas del combo ya creadas para evitar falsos conflictos
                ids_ya_creadas = [c["cita_id"] for c in citas_creadas]
                citas_est = [c for c in citas_est if c["id"] not in ids_ya_creadas]
                if hay_conflicto(citas_est, hora_inicio_t, hora_fin_t, buffer):
                    raise HTTPException(409, f"{estilista['nombre']} no está disponible para '{servicio['nombre']}' a las {hora_actual}.")

            cita_id = _insert(
                conn,
                """INSERT INTO citas
                   (cliente_nombre, cliente_telefono, cliente_nuevo, servicio_id, estilista_id,
                    fecha, hora_inicio, hora_fin, duracion_min, precio_estimado, notas, estado)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmada')""",
                (req.cliente_nombre, req.cliente_telefono, 1 if req.cliente_nuevo else 0,
                 sid, estilista["id"], fecha_dt.isoformat(), hora_actual, hora_fin_str,
                 servicio["duracion_min"], servicio["precio"], req.notas),
            )
            conn.commit()

            citas_creadas.append({
                "cita_id": cita_id,
                "servicio": servicio["nombre"],
                "estilista": estilista["nombre"],
                "hora_inicio": hora_actual,
                "hora_fin": hora_fin_str,
                "precio_desde": servicio["precio"],
            })

            background_tasks.add_task(
                _bg_gcal_crear, cita_id,
                f"{servicio['nombre']} — {req.cliente_nombre} (con {estilista['nombre']})",
                req.fecha, hora_actual, hora_fin_str, req.notas, sid, req.cliente_telefono,
                estilista["id"],
            )

            # El siguiente servicio empieza cuando termina éste + buffer
            siguiente_inicio = datetime.strptime(hora_fin_str, "%H:%M") + timedelta(minutes=buffer)
            hora_actual = siguiente_inicio.strftime("%H:%M")

    except HTTPException:
        # Si algo falla a mitad del combo, cancelar las citas ya creadas
        for c in citas_creadas:
            _exec(conn, "UPDATE citas SET estado = 'cancelada' WHERE id = ?", (c["cita_id"],))
        conn.commit()
        conn.close()
        raise

    conn.close()

    total_precio = sum(c["precio_desde"] for c in citas_creadas)
    hora_fin_total = citas_creadas[-1]["hora_fin"]
    nombres_servicios = " y ".join(c["servicio"] for c in citas_creadas)
    nombre_corto = req.cliente_nombre.split()[0]
    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]
    fecha_legible_combo = f"{dias_es[fecha_dt.weekday()]} {fecha_dt.day} de {meses_es[fecha_dt.month-1]}"

    return {
        "exito": True,
        "citas": citas_creadas,
        "resumen": {
            "cliente": req.cliente_nombre,
            "fecha": fecha_dt.isoformat(),
            "fecha_legible": fecha_legible_combo,
            "hora_inicio": req.hora,
            "hora_fin": hora_fin_total,
            "precio_total_desde": total_precio,
            "num_servicios": len(citas_creadas),
        },
        "mensaje": f"Combo confirmado: {nombres_servicios} el {fecha_legible_combo} de {hora_a_texto(req.hora)} a {hora_a_texto(hora_fin_total)}.",
        "mensaje_voz": (
            f"¡Perfecto, {nombre_corto}! He reservado {nombres_servicios} para el {fecha_legible_combo}. "
            f"Empezamos a {hora_a_texto(req.hora)} y terminamos sobre {hora_a_texto(hora_fin_total)}. "
            f"El precio total es desde {total_precio:.0f} euros. ¿Necesitas algo más?"
        ),
    }


# --- PRÓXIMOS DÍAS DISPONIBLES (útil para Retell) ---

@app.get("/disponibilidad/proximos-dias", summary="Buscar disponibilidad en los próximos N días")
def proximos_dias_disponibles(
    servicio_id: str = Query(...),
    estilista_id: str = Query(default="cualquiera"),
    dias: int = Query(default=7, ge=1, le=14),
    max_huecos_por_estilista: int = Query(default=4, ge=1, le=10, description="Máximo de huecos a devolver por estilista y día"),
):
    """
    Devuelve un resumen de disponibilidad para los próximos N días.
    Optimizado para Retell: devuelve solo los primeros huecos de cada día para ser conciso y rápido.
    """
    servicio = obtener_servicio(servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{servicio_id}' no encontrado.")
    servicio_id = servicio["id"]  # normalizar al ID canónico

    conn = get_db()
    resultado = []
    ahora = ahora_madrid()
    hoy = ahora.date()

    if estilista_id == "cualquiera":
        estilistas_base = [e for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
    else:
        est = obtener_estilista(estilista_id)
        estilistas_base = [est] if est and estilista_hace_servicio(est, servicio_id) else []

    _ids_cache_pd = {}
    for i in range(dias):
        fecha = hoy + timedelta(days=i)
        if not salon_abierto(fecha):
            continue

        estilistas_validos = [e for e in estilistas_base if estilista_trabaja(e, fecha)]
        if not estilistas_validos:
            continue

        if fecha not in _ids_cache_pd:
            _ids_cache_pd[fecha] = obtener_ids_confirmadas_dia(conn, fecha)

        huecos_dia = {}
        for est in estilistas_validos:
            huecos = encontrar_huecos_libres(conn, est["id"], fecha, servicio["duracion_min"], _ids_cache_pd[fecha])

            # Filtrar horas pasadas si es hoy
            if fecha == hoy:
                hora_minima = (ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])).strftime("%H:%M")
                huecos = [h for h in huecos if h >= hora_minima]

            if huecos:
                # Filtrar a intervalos de 30 min para que Sofía no ofrezca demasiadas opciones
                huecos_30 = [h for h in huecos if int(h.split(":")[1]) % 30 == 0]
                huecos_muestra = huecos_30[:max_huecos_por_estilista] if huecos_30 else huecos[:max_huecos_por_estilista]
                huecos_dia[est["nombre"]] = {
                    "estilista_id": est["id"],
                    "total_huecos": len(huecos),
                    "proximos_huecos": huecos_muestra,
                    "proximos_huecos_legibles": [hora_a_texto(h) for h in huecos_muestra],
                }

        if huecos_dia:
            resultado.append({
                "fecha": fecha.isoformat(),
                "fecha_legible": f"{dia_nombre(fecha)} {fecha.day} de {['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'][fecha.month-1]}",
                "dia": dia_nombre(fecha),
                "estilistas_disponibles": huecos_dia,
            })

    conn.close()

    if not resultado:
        msg_voz = f"Lo siento, no tenemos disponibilidad para {servicio['nombre']} en los próximos {dias} días. ¿Quieres que lo busque para más adelante?"
    else:
        # Resumir los primeros 2 días disponibles en voz natural
        partes = []
        for d in resultado[:2]:
            estilistas = list(d["estilistas_disponibles"].items())
            if estilistas:
                nombre_est, datos = estilistas[0]
                horas = datos["proximos_huecos_legibles"][:2]
                horas_str = " o ".join(horas) if horas else ""
                partes.append(f"el {d['fecha_legible']} {horas_str} con {nombre_est}")
        if partes:
            msg_voz = f"Para {servicio['nombre']} tengo disponibilidad {', y '.join(partes)}. ¿Cuál te viene mejor?"
        else:
            msg_voz = f"Tengo varios huecos disponibles para {servicio['nombre']} en los próximos días. ¿Qué día te va mejor?"

    return {
        "servicio": servicio["nombre"],
        "duracion_min": servicio["duracion_min"],
        "dias_consultados": dias,
        "dias_con_disponibilidad": resultado,
        "mensaje_voz": msg_voz,
    }


class ProximosDiasRequest(BaseModel):
    servicio_id: str = Field(..., description="ID del servicio")
    estilista_id: str = Field(default="cualquiera")
    dias: int = Field(default=7, ge=1, le=14)


@app.post("/disponibilidad/proximos-dias", summary="Buscar disponibilidad en los próximos N días (POST)")
def proximos_dias_disponibles_post(req: ProximosDiasRequest):
    return proximos_dias_disponibles(req.servicio_id, req.estilista_id, req.dias)


# ═══════════════════════════════════════════════════════════════
# RUTAS ALTERNATIVAS PARA RETELL AI (solo acepta GET y POST)
# ═══════════════════════════════════════════════════════════════

class ModificarConIdRequest(BaseModel):
    cita_id: int
    nueva_fecha: Optional[str] = None
    nueva_hora: Optional[str] = None
    nuevo_estilista_id: Optional[str] = None
    nuevo_servicio_id: Optional[str] = None
    notas: Optional[str] = None


class CancelarConIdRequest(BaseModel):
    cita_id: int


@app.post("/citas/modificar", summary="Modificar cita (vía POST para Retell)")
def modificar_cita_post(req: ModificarConIdRequest, background_tasks: BackgroundTasks):
    """Ruta alternativa POST para Retell AI que no soporta PUT."""
    mod_req = ModificarCitaRequest(
        nueva_fecha=req.nueva_fecha,
        nueva_hora=req.nueva_hora,
        nuevo_estilista_id=req.nuevo_estilista_id,
        nuevo_servicio_id=req.nuevo_servicio_id,
        notas=req.notas,
    )
    return modificar_cita(req.cita_id, mod_req, background_tasks)


@app.post("/citas/cancelar", summary="Cancelar cita (vía POST para Retell)")
def cancelar_cita_post(req: CancelarConIdRequest, background_tasks: BackgroundTasks):
    """Ruta alternativa POST para Retell AI que no soporta DELETE."""
    return cancelar_cita(req.cita_id, background_tasks)


class CancelarPorTelefonoRequest(BaseModel):
    telefono: str = Field(..., description="Teléfono del cliente para buscar su cita más reciente")
    cita_id: Optional[int] = Field(default=None, description="ID de la cita (opcional, si está disponible)")


@app.post("/citas/cancelar-por-telefono", summary="Cancelar la cita más reciente de un cliente por teléfono (para Retell)")
def cancelar_cita_por_telefono(req: CancelarPorTelefonoRequest, background_tasks: BackgroundTasks):
    """
    Busca la cita confirmada más próxima del cliente por teléfono y la cancela.
    Si se proporciona cita_id, cancela esa cita específica; si no, cancela la más próxima futura.
    """
    conn = get_db()

    # Si viene cita_id, intentar primero por ID
    if req.cita_id:
        rows = _query(conn, "SELECT * FROM citas WHERE id = ? AND estado = 'confirmada'", (req.cita_id,))
        if rows:
            cita = rows[0]
            conn.close()
            return cancelar_cita(cita["id"], background_tasks)

    # Buscar por teléfono: cita confirmada más próxima (hoy o futura)
    hoy = hoy_madrid().isoformat()
    rows = _query(
        conn,
        "SELECT * FROM citas WHERE cliente_telefono = ? AND estado = 'confirmada' AND fecha >= ? ORDER BY fecha ASC, hora_inicio ASC LIMIT 1",
        (req.telefono, hoy),
    )
    cita = rows[0] if rows else None

    if not cita:
        # Intentar sin normalizar el teléfono (puede tener espacios o formato distinto)
        tel_digits = "".join(c for c in req.telefono if c.isdigit())
        rows2 = _query(
            conn,
            "SELECT * FROM citas WHERE estado = 'confirmada' AND fecha >= ? ORDER BY fecha ASC, hora_inicio ASC",
            (hoy,),
        )
        for r in rows2:
            r_digits = "".join(c for c in str(r["cliente_telefono"]) if c.isdigit())
            if tel_digits and r_digits.endswith(tel_digits[-9:]):
                cita = r
                break

    conn.close()

    if not cita:
        return {
            "exito": False,
            "mensaje_voz": "No he encontrado ninguna cita activa con ese número de teléfono. ¿Puedes confirmarme el número?",
        }

    return cancelar_cita(cita["id"], background_tasks)


# --- SIGUIENTE HUECO DISPONIBLE ---

class SiguienteHuecoRequest(BaseModel):
    servicio_id: str = Field(..., description="Servicio deseado. Acepta lenguaje natural: 'corte de pelo', 'tinte', 'manicura', etc.")
    estilista_id: str = Field(default="cualquiera", description="ID del estilista o 'cualquiera'")
    dias_max: int = Field(default=14, ge=1, le=30)


@app.post("/disponibilidad/siguiente-hueco", summary="Primer hueco disponible (POST para Retell)")
def siguiente_hueco_post(req: SiguienteHuecoRequest):
    return _siguiente_hueco(req.servicio_id, req.estilista_id, req.dias_max)


@app.get("/disponibilidad/siguiente-hueco", summary="Primer hueco disponible para un servicio")
def siguiente_hueco_disponible(
    servicio_id: str = Query(..., description="ID del servicio"),
    estilista_id: str = Query(default="cualquiera", description="ID del estilista o 'cualquiera'"),
    dias_max: int = Query(default=14, ge=1, le=30, description="Días máximos a buscar hacia adelante"),
):
    return _siguiente_hueco(servicio_id, estilista_id, dias_max)


def _siguiente_hueco(servicio_id: str, estilista_id: str = "cualquiera", dias_max: int = 14):
    """
    Devuelve el primer hueco libre disponible para un servicio.
    Ideal para cuando el cliente pregunta: '¿cuándo antes me podéis atender?'
    """
    servicio = obtener_servicio(servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{servicio_id}' no encontrado.")
    servicio_id = servicio["id"]  # normalizar al ID canónico

    if estilista_id == "cualquiera":
        estilistas_base = [e for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
    else:
        est = obtener_estilista(estilista_id)
        if not est:
            raise HTTPException(404, f"Estilista '{estilista_id}' no encontrado.")
        if not estilista_hace_servicio(est, servicio_id):
            otros = [e["nombre"] for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
            otros_str = " o ".join(otros) if otros else "ninguno disponible"
            return {
                "disponible": False,
                "mensaje_voz": (
                    f"Lo siento, {est['nombre']} no hace {servicio['nombre']}. "
                    f"Para este servicio puedes ir con {otros_str}. ¿Con cuál prefieres?"
                ),
                "estilistas_que_lo_hacen": otros,
            }
        estilistas_base = [est]

    if not estilistas_base:
        otros = [e["nombre"] for e in ESTILISTAS]
        return {
            "disponible": False,
            "mensaje_voz": f"Lo siento, ningún estilista realiza {servicio['nombre']} actualmente.",
        }

    conn = get_db()
    ahora = ahora_madrid()
    hoy = ahora.date()
    dias_es = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]
    meses_es = ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"]

    _ids_cache = {}  # cache de ids confirmadas por fecha para no repetir queries
    for i in range(dias_max):
        fecha = hoy + timedelta(days=i)
        if not salon_abierto(fecha):
            continue

        if fecha not in _ids_cache:
            _ids_cache[fecha] = obtener_ids_confirmadas_dia(conn, fecha)

        for est in estilistas_base:
            if not estilista_trabaja(est, fecha):
                continue

            huecos = encontrar_huecos_libres(conn, est["id"], fecha, servicio["duracion_min"], _ids_cache[fecha])

            if fecha == hoy:
                hora_minima = (ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])).strftime("%H:%M")
                huecos = [h for h in huecos if h >= hora_minima]

            if huecos:
                conn.close()
                primer_hueco = huecos[0]
                fecha_legible = f"{dias_es[fecha.weekday()]} {fecha.day} de {meses_es[fecha.month-1]}"
                return {
                    "disponible": True,
                    "fecha": fecha.isoformat(),
                    "fecha_legible": fecha_legible,
                    "hora": primer_hueco,
                    "hora_legible": hora_a_texto(primer_hueco),
                    "estilista": est["nombre"],
                    "estilista_id": est["id"],
                    "servicio": servicio["nombre"],
                    "mensaje_voz": f"El primer hueco que tengo para {servicio['nombre']} es el {fecha_legible} a {hora_a_texto(primer_hueco)} con {est['nombre']}. ¿Te viene bien?",
                }

    conn.close()
    return {
        "disponible": False,
        "mensaje_voz": f"Ahora mismo no tenemos huecos para {servicio['nombre']} en los próximos {dias_max} días. ¿Quieres que te llame cuando haya disponibilidad?",
    }


# --- DIAGNÓSTICO CALENDAR (temporal) ---

@app.get("/debug/calendar-events", summary="[DEBUG] Ver eventos del calendario en una fecha")
def debug_calendar_events(fecha: str = Query(default="hoy")):
    """Endpoint temporal para diagnosticar qué eventos hay en Google Calendar."""
    try:
        fecha_dt = parsear_fecha(fecha)
    except ValueError:
        raise HTTPException(400, f"No entendí la fecha: {fecha}")

    if not calendar_service.enabled:
        return {"error": "Google Calendar no está habilitado"}

    eventos_raw = calendar_service.obtener_eventos_dia(fecha_dt.isoformat())

    eventos = []
    for ev in eventos_raw:
        ext = ev.get("extendedProperties", {}).get("private", {})
        eventos.append({
            "summary": ev.get("summary", "(sin título)"),
            "start": ev.get("start", {}),
            "end": ev.get("end", {}),
            "estilista_id_en_props": ext.get("estilista_id", ""),
            "servicio_id_en_props": ext.get("servicio_id", ""),
            "cita_id_en_props": ext.get("empresa_sl_cita_id", ""),
            "tiene_extended_props": bool(ext),
        })

    # Mostrar cómo afecta a cada estilista
    bloques_por_estilista = {}
    for est in ESTILISTAS:
        bloques = gcal_bloques_estilista(est["id"], fecha_dt)
        bloques_por_estilista[est["nombre"]] = bloques

    return {
        "fecha": fecha_dt.isoformat(),
        "total_eventos": len(eventos),
        "eventos": eventos,
        "bloques_por_estilista": bloques_por_estilista,
    }


@app.post("/debug/limpiar-huerfanos", summary="[DEBUG] Eliminar eventos huérfanos del Calendar")
def limpiar_eventos_huerfanos(fecha_inicio: str = Query(default="hoy"), dias: int = Query(default=30)):
    """Elimina eventos de Google Calendar cuyo empresa_sl_cita_id no existe como confirmada en la BD."""
    if not calendar_service.enabled:
        return {"error": "Google Calendar no habilitado"}

    try:
        inicio = parsear_fecha(fecha_inicio)
    except ValueError:
        raise HTTPException(400, f"No entendí la fecha: {fecha_inicio}")

    conn = get_db()
    eliminados = []

    for i in range(dias):
        fecha = inicio + timedelta(days=i)
        ids_confirmadas = obtener_ids_confirmadas_dia(conn, fecha)
        eventos = calendar_service.obtener_eventos_dia(fecha.isoformat())

        for ev in eventos:
            ext = ev.get("extendedProperties", {}).get("private", {})
            ev_cita_id = ext.get("empresa_sl_cita_id", "")
            if not ev_cita_id:
                continue  # No es un evento de nuestro sistema
            try:
                if int(ev_cita_id) not in ids_confirmadas:
                    # Evento huérfano → eliminar
                    event_id = ev.get("id")
                    if event_id:
                        calendar_service.cancelar_evento(event_id)
                        eliminados.append({
                            "fecha": fecha.isoformat(),
                            "summary": ev.get("summary", ""),
                            "cita_id": ev_cita_id,
                            "google_event_id": event_id,
                        })
            except (ValueError, TypeError):
                pass

    conn.close()
    return {
        "eliminados": len(eliminados),
        "detalle": eliminados,
    }


# ═══════════════════════════════════════════════════════════════
# EJECUTAR
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
