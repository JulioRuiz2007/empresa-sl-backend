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

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, date, time, timedelta
import sqlite3
import json
import os
import logging

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


# ═══════════════════════════════════════════════════════════════
# BASE DE DATOS
# ═══════════════════════════════════════════════════════════════

DB_PATH = os.path.join(os.path.dirname(__file__), "citas.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = get_db()
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
    cliente_telefono: str = Field(..., min_length=9, description="Teléfono del cliente")
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


# ═══════════════════════════════════════════════════════════════
# LÓGICA DE NEGOCIO
# ═══════════════════════════════════════════════════════════════

def obtener_servicio(servicio_id: str) -> dict:
    for s in SERVICIOS:
        if s["id"] == servicio_id:
            return s
    return None


def obtener_estilista(estilista_id: str) -> dict:
    for e in ESTILISTAS:
        if e["id"] == estilista_id:
            return e
    return None


def dia_nombre(fecha: date) -> str:
    return DIAS_SEMANA_ES.get(fecha.weekday(), "desconocido")


def salon_abierto(fecha: date) -> dict:
    """Devuelve el horario del salón para esa fecha, o None si está cerrado."""
    dia = dia_nombre(fecha)
    return SALON_CONFIG["horario"].get(dia)


def estilista_trabaja(estilista: dict, fecha: date) -> bool:
    return fecha.weekday() in estilista["dias_trabaja"]


def estilista_hace_servicio(estilista: dict, servicio_id: str) -> bool:
    return servicio_id in estilista["especialidades"]


def obtener_citas_estilista(conn, estilista_id: str, fecha: date) -> list:
    """Obtiene todas las citas activas de un estilista en una fecha."""
    rows = conn.execute(
        "SELECT * FROM citas WHERE estilista_id = ? AND fecha = ? AND estado = 'confirmada' ORDER BY hora_inicio",
        (estilista_id, fecha.isoformat())
    ).fetchall()
    return [dict(r) for r in rows]


def hay_conflicto(citas_existentes: list, hora_inicio: time, hora_fin: time, buffer: int) -> bool:
    """Comprueba si un nuevo hueco colisiona con citas existentes (incluyendo buffer)."""
    for cita in citas_existentes:
        cita_inicio = datetime.strptime(cita["hora_inicio"], "%H:%M").time()
        cita_fin = datetime.strptime(cita["hora_fin"], "%H:%M").time()

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


def encontrar_huecos_libres(conn, estilista_id: str, fecha: date, duracion_min: int) -> list:
    """Encuentra todos los huecos disponibles para un estilista en una fecha."""
    horario = salon_abierto(fecha)
    if not horario:
        return []

    estilista = obtener_estilista(estilista_id)
    if not estilista or not estilista_trabaja(estilista, fecha):
        return []

    buffer = SALON_CONFIG["buffer_minutos"]
    abre = datetime.strptime(horario["abre"], "%H:%M")
    cierra = datetime.strptime(horario["cierra"], "%H:%M")

    citas = obtener_citas_estilista(conn, estilista_id, fecha)
    huecos = []

    # Generar slots cada 15 minutos
    slot = abre
    while slot + timedelta(minutes=duracion_min) <= cierra:
        hora_inicio = slot.time()
        hora_fin = (slot + timedelta(minutes=duracion_min)).time()

        if not hay_conflicto(citas, hora_inicio, hora_fin, buffer):
            huecos.append(slot.strftime("%H:%M"))

        slot += timedelta(minutes=15)

    return huecos


def buscar_mejor_estilista(conn, servicio_id: str, fecha: date, hora_str: str, duracion_min: int) -> Optional[dict]:
    """Busca el estilista con mejor disponibilidad para un servicio/fecha/hora."""
    buffer = SALON_CONFIG["buffer_minutos"]
    hora_inicio = datetime.strptime(hora_str, "%H:%M").time()
    hora_fin_str = calcular_hora_fin(hora_str, duracion_min)
    hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()

    for estilista in ESTILISTAS:
        if not estilista_hace_servicio(estilista, servicio_id):
            continue
        if not estilista_trabaja(estilista, fecha):
            continue

        citas = obtener_citas_estilista(conn, estilista["id"], fecha)
        if not hay_conflicto(citas, hora_inicio, hora_fin, buffer):
            return estilista

    return None


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

@app.get("/disponibilidad", summary="Consultar huecos libres")
def consultar_disponibilidad(
    fecha: str = Query(..., description="Fecha YYYY-MM-DD"),
    servicio_id: str = Query(..., description="ID del servicio"),
    estilista_id: str = Query(default="cualquiera", description="ID del estilista o 'cualquiera'"),
):
    """
    Devuelve los huecos disponibles para un servicio en una fecha.
    Si estilista_id es 'cualquiera', devuelve disponibilidad de todos los que hacen ese servicio.
    """
    try:
        fecha_dt = date.fromisoformat(fecha)
    except ValueError:
        raise HTTPException(400, "Formato de fecha inválido. Usa YYYY-MM-DD.")

    servicio = obtener_servicio(servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{servicio_id}' no encontrado. Servicios válidos: {[s['id'] for s in SERVICIOS]}")

    horario = salon_abierto(fecha_dt)
    if not horario:
        dia = dia_nombre(fecha_dt)
        return {
            "disponible": False,
            "mensaje": f"El salón está cerrado los {dia}s.",
            "sugerencia": "Prueba otro día de lunes a sábado.",
            "huecos": {},
        }

    # Comprobar antelación mínima
    ahora = datetime.now()
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
            return {
                "disponible": False,
                "mensaje": f"{est['nombre']} no realiza el servicio '{servicio['nombre']}'. Estilistas disponibles para este servicio: {[e['nombre'] for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]}",
                "huecos": {},
            }
        estilistas_validos = [est]

    for est in estilistas_validos:
        huecos = encontrar_huecos_libres(conn, est["id"], fecha_dt, servicio["duracion_min"])

        # Filtrar huecos pasados si es hoy
        if fecha_dt == ahora.date():
            hora_minima = fecha_hora_minima.strftime("%H:%M")
            huecos = [h for h in huecos if h >= hora_minima]

        if huecos:
            resultado[est["nombre"]] = {
                "estilista_id": est["id"],
                "huecos_disponibles": huecos,
                "total_huecos": len(huecos),
            }

    conn.close()

    if not resultado:
        return {
            "disponible": False,
            "mensaje": f"No hay disponibilidad para '{servicio['nombre']}' el {fecha}.",
            "sugerencia": "Prueba otro día o consulta disponibilidad para los próximos días.",
            "huecos": {},
        }

    return {
        "disponible": True,
        "fecha": fecha,
        "servicio": servicio["nombre"],
        "duracion_min": servicio["duracion_min"],
        "precio_desde": servicio["precio"],
        "buffer_entre_citas_min": SALON_CONFIG["buffer_minutos"],
        "huecos": resultado,
    }


# --- CREAR CITA ---

@app.post("/citas", summary="Reservar una nueva cita")
def crear_cita(req: CrearCitaRequest):
    """Crea una nueva cita validando disponibilidad, buffer, horario y conflictos."""

    servicio = obtener_servicio(req.servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{req.servicio_id}' no encontrado.")

    try:
        fecha_dt = date.fromisoformat(req.fecha)
    except ValueError:
        raise HTTPException(400, "Formato de fecha inválido.")

    # Validar que el salón está abierto
    horario = salon_abierto(fecha_dt)
    if not horario:
        raise HTTPException(400, f"El salón está cerrado los {dia_nombre(fecha_dt)}s.")

    # Validar hora dentro de horario
    try:
        hora_inicio = datetime.strptime(req.hora, "%H:%M").time()
    except ValueError:
        raise HTTPException(400, "Formato de hora inválido. Usa HH:MM.")

    hora_fin_str = calcular_hora_fin(req.hora, servicio["duracion_min"])
    hora_fin = datetime.strptime(hora_fin_str, "%H:%M").time()

    hora_abre = datetime.strptime(horario["abre"], "%H:%M").time()
    hora_cierra = datetime.strptime(horario["cierra"], "%H:%M").time()

    if hora_inicio < hora_abre or hora_fin > hora_cierra:
        raise HTTPException(400, f"El horario del salón es de {horario['abre']} a {horario['cierra']}. El servicio terminaría a las {hora_fin_str}, que está fuera de horario.")

    # Validar antelación mínima
    ahora = datetime.now()
    fecha_hora_cita = datetime.combine(fecha_dt, hora_inicio)
    minimo = ahora + timedelta(hours=SALON_CONFIG["antelacion_minima_horas"])
    if fecha_hora_cita < minimo:
        raise HTTPException(400, f"Las citas deben reservarse con al menos {SALON_CONFIG['antelacion_minima_horas']} horas de antelación.")

    conn = get_db()

    # Resolver estilista
    if req.estilista_id == "cualquiera":
        estilista = buscar_mejor_estilista(conn, req.servicio_id, fecha_dt, req.hora, servicio["duracion_min"])
        if not estilista:
            conn.close()
            raise HTTPException(409, "No hay estilistas disponibles para ese servicio, fecha y hora.")
    else:
        estilista = obtener_estilista(req.estilista_id)
        if not estilista:
            conn.close()
            raise HTTPException(404, f"Estilista '{req.estilista_id}' no encontrado.")

    # Validar que el estilista trabaja ese día
    if not estilista_trabaja(estilista, fecha_dt):
        dias = [DIAS_SEMANA_ES[d] for d in estilista["dias_trabaja"]]
        conn.close()
        raise HTTPException(400, f"{estilista['nombre']} no trabaja los {dia_nombre(fecha_dt)}s. Trabaja: {', '.join(dias)}.")

    # Validar que el estilista hace ese servicio
    if not estilista_hace_servicio(estilista, req.servicio_id):
        servicios_est = [obtener_servicio(s)["nombre"] for s in estilista["especialidades"]]
        conn.close()
        raise HTTPException(400, f"{estilista['nombre']} no realiza '{servicio['nombre']}'. Sus servicios: {', '.join(servicios_est)}.")

    # Comprobar conflictos con buffer
    citas = obtener_citas_estilista(conn, estilista["id"], fecha_dt)
    buffer = SALON_CONFIG["buffer_minutos"]

    if hay_conflicto(citas, hora_inicio, hora_fin, buffer):
        # Buscar alternativas cercanas
        huecos = encontrar_huecos_libres(conn, estilista["id"], fecha_dt, servicio["duracion_min"])
        conn.close()
        sugerencias = huecos[:5] if huecos else []
        raise HTTPException(409, {
            "error": f"Ese horario no está disponible con {estilista['nombre']} (recuerda que necesitamos {buffer} min de descanso entre citas).",
            "alternativas_mismo_dia": sugerencias,
            "mensaje": f"Huecos disponibles: {', '.join(sugerencias)}" if sugerencias else "No hay más huecos ese día con este estilista.",
        })

    # Todo OK — crear la cita
    cursor = conn.execute(
        """INSERT INTO citas
           (cliente_nombre, cliente_telefono, cliente_nuevo, servicio_id, estilista_id,
            fecha, hora_inicio, hora_fin, duracion_min, precio_estimado, notas, estado)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmada')""",
        (
            req.cliente_nombre,
            req.cliente_telefono,
            1 if req.cliente_nuevo else 0,
            req.servicio_id,
            estilista["id"],
            fecha_dt.isoformat(),
            req.hora,
            hora_fin_str,
            servicio["duracion_min"],
            servicio["precio"],
            req.notas,
        )
    )
    conn.commit()
    cita_id = cursor.lastrowid

    # ── Google Calendar: crear evento ──
    google_event_id = calendar_service.crear_evento(
        titulo=f"{servicio['nombre']} — {req.cliente_nombre} (con {estilista['nombre']})",
        fecha=req.fecha,
        hora_inicio=req.hora,
        hora_fin=hora_fin_str,
        descripcion=req.notas,
        servicio_id=req.servicio_id,
        cliente_telefono=req.cliente_telefono,
        cita_id=cita_id,
    )
    if google_event_id:
        conn.execute(
            "UPDATE citas SET google_event_id = ? WHERE id = ?",
            (google_event_id, cita_id)
        )
        conn.commit()
        logger.info(f"Cita {cita_id} sincronizada con Google Calendar: {google_event_id}")

    conn.close()

    return {
        "exito": True,
        "cita_id": cita_id,
        "google_calendar_sincronizado": bool(google_event_id),
        "resumen": {
            "cliente": req.cliente_nombre,
            "telefono": req.cliente_telefono,
            "servicio": servicio["nombre"],
            "estilista": estilista["nombre"],
            "fecha": req.fecha,
            "hora_inicio": req.hora,
            "hora_fin": hora_fin_str,
            "duracion_min": servicio["duracion_min"],
            "precio_desde": servicio["precio"],
            "notas": req.notas,
        },
        "mensaje": f"Cita confirmada para {req.cliente_nombre}: {servicio['nombre']} con {estilista['nombre']} el {req.fecha} a las {req.hora}.",
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
    rows = conn.execute(query, params).fetchall()
    conn.close()

    citas = []
    for r in rows:
        servicio = obtener_servicio(r["servicio_id"])
        estilista = obtener_estilista(r["estilista_id"])
        citas.append({
            "cita_id": r["id"],
            "cliente": r["cliente_nombre"],
            "telefono": r["cliente_telefono"],
            "servicio": servicio["nombre"] if servicio else r["servicio_id"],
            "estilista": estilista["nombre"] if estilista else r["estilista_id"],
            "fecha": r["fecha"],
            "hora_inicio": r["hora_inicio"],
            "hora_fin": r["hora_fin"],
            "duracion_min": r["duracion_min"],
            "precio_estimado": r["precio_estimado"],
            "estado": r["estado"],
            "notas": r["notas"],
        })

    return {"total": len(citas), "citas": citas}


# --- MODIFICAR CITA ---

@app.put("/citas/{cita_id}", summary="Modificar una cita existente")
def modificar_cita(cita_id: int, req: ModificarCitaRequest):
    conn = get_db()
    cita = conn.execute(
        "SELECT * FROM citas WHERE id = ? AND estado = 'confirmada'", (cita_id,)
    ).fetchone()

    if not cita:
        conn.close()
        raise HTTPException(404, f"No se encontró cita activa con ID {cita_id}.")

    # Determinar nuevos valores
    nuevo_servicio_id = req.nuevo_servicio_id or cita["servicio_id"]
    nuevo_estilista_id = req.nuevo_estilista_id or cita["estilista_id"]
    nueva_fecha_str = req.nueva_fecha or cita["fecha"]
    nueva_hora = req.nueva_hora or cita["hora_inicio"]
    nuevas_notas = req.notas if req.notas is not None else cita["notas"]

    servicio = obtener_servicio(nuevo_servicio_id)
    if not servicio:
        conn.close()
        raise HTTPException(404, f"Servicio '{nuevo_servicio_id}' no encontrado.")

    try:
        nueva_fecha_dt = date.fromisoformat(nueva_fecha_str)
    except ValueError:
        conn.close()
        raise HTTPException(400, "Formato de fecha inválido.")

    estilista = obtener_estilista(nuevo_estilista_id)
    if not estilista:
        conn.close()
        raise HTTPException(404, f"Estilista '{nuevo_estilista_id}' no encontrado.")

    # Validaciones
    horario = salon_abierto(nueva_fecha_dt)
    if not horario:
        conn.close()
        raise HTTPException(400, f"El salón está cerrado los {dia_nombre(nueva_fecha_dt)}s.")

    if not estilista_trabaja(estilista, nueva_fecha_dt):
        conn.close()
        raise HTTPException(400, f"{estilista['nombre']} no trabaja los {dia_nombre(nueva_fecha_dt)}s.")

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
    conn.execute(
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

    # ── Google Calendar: actualizar evento ──
    google_event_id = cita["google_event_id"] if "google_event_id" in cita.keys() else ""
    calendar_synced = False
    if google_event_id:
        calendar_synced = calendar_service.modificar_evento(
            google_event_id=google_event_id,
            titulo=f"{servicio['nombre']} — {cita['cliente_nombre']} (con {estilista['nombre']})",
            fecha=nueva_fecha_str,
            hora_inicio=nueva_hora,
            hora_fin=hora_fin_str,
            servicio_id=nuevo_servicio_id,
        )

    conn.close()

    return {
        "exito": True,
        "cita_id": cita_id,
        "google_calendar_sincronizado": calendar_synced,
        "mensaje": f"Cita modificada: {servicio['nombre']} con {estilista['nombre']} el {nueva_fecha_str} a las {nueva_hora}.",
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
def cancelar_cita(cita_id: int):
    conn = get_db()
    cita = conn.execute(
        "SELECT * FROM citas WHERE id = ? AND estado = 'confirmada'", (cita_id,)
    ).fetchone()

    if not cita:
        conn.close()
        raise HTTPException(404, f"No se encontró cita activa con ID {cita_id}.")

    servicio = obtener_servicio(cita["servicio_id"])
    estilista = obtener_estilista(cita["estilista_id"])

    conn.execute(
        "UPDATE citas SET estado = 'cancelada', modificada_en = CURRENT_TIMESTAMP WHERE id = ?",
        (cita_id,)
    )
    conn.commit()

    # ── Google Calendar: eliminar evento ──
    google_event_id = cita["google_event_id"] if "google_event_id" in cita.keys() else ""
    calendar_synced = False
    if google_event_id:
        calendar_synced = calendar_service.cancelar_evento(google_event_id)

    conn.close()

    return {
        "exito": True,
        "cita_id": cita_id,
        "google_calendar_sincronizado": calendar_synced,
        "mensaje": f"Cita cancelada: {servicio['nombre']} con {estilista['nombre']} el {cita['fecha']} a las {cita['hora_inicio']}.",
    }


# --- PRÓXIMOS DÍAS DISPONIBLES (útil para Retell) ---

@app.get("/disponibilidad/proximos-dias", summary="Buscar disponibilidad en los próximos N días")
def proximos_dias_disponibles(
    servicio_id: str = Query(...),
    estilista_id: str = Query(default="cualquiera"),
    dias: int = Query(default=7, ge=1, le=30),
):
    """Devuelve un resumen de disponibilidad para los próximos N días. Útil para cuando el cliente no tiene fecha fija."""
    servicio = obtener_servicio(servicio_id)
    if not servicio:
        raise HTTPException(404, f"Servicio '{servicio_id}' no encontrado.")

    conn = get_db()
    resultado = []
    hoy = date.today()

    for i in range(dias):
        fecha = hoy + timedelta(days=i)
        if not salon_abierto(fecha):
            continue

        if estilista_id == "cualquiera":
            estilistas_validos = [e for e in ESTILISTAS if estilista_hace_servicio(e, servicio_id)]
        else:
            est = obtener_estilista(estilista_id)
            estilistas_validos = [est] if est and estilista_hace_servicio(est, servicio_id) else []

        huecos_dia = {}
        for est in estilistas_validos:
            huecos = encontrar_huecos_libres(conn, est["id"], fecha, servicio["duracion_min"])
            if huecos:
                huecos_dia[est["nombre"]] = {
                    "estilista_id": est["id"],
                    "total_huecos": len(huecos),
                    "primer_hueco": huecos[0],
                    "ultimo_hueco": huecos[-1],
                }

        if huecos_dia:
            resultado.append({
                "fecha": fecha.isoformat(),
                "dia": dia_nombre(fecha),
                "estilistas_disponibles": huecos_dia,
            })

    conn.close()
    return {
        "servicio": servicio["nombre"],
        "dias_consultados": dias,
        "dias_con_disponibilidad": resultado,
    }


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
def modificar_cita_post(req: ModificarConIdRequest):
    """Ruta alternativa POST para Retell AI que no soporta PUT."""
    mod_req = ModificarCitaRequest(
        nueva_fecha=req.nueva_fecha,
        nueva_hora=req.nueva_hora,
        nuevo_estilista_id=req.nuevo_estilista_id,
        nuevo_servicio_id=req.nuevo_servicio_id,
        notas=req.notas,
    )
    return modificar_cita(req.cita_id, mod_req)


@app.post("/citas/cancelar", summary="Cancelar cita (vía POST para Retell)")
def cancelar_cita_post(req: CancelarConIdRequest):
    """Ruta alternativa POST para Retell AI que no soporta DELETE."""
    return cancelar_cita(req.cita_id)


# ═══════════════════════════════════════════════════════════════
# EJECUTAR
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
