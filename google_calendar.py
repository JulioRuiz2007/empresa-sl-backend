"""
Empresa SL — Integración con Google Calendar
=============================================

Este módulo gestiona la sincronización bidireccional con Google Calendar:
- Crear eventos cuando se reserva una cita
- Modificar eventos cuando se cambia una cita
- Cancelar eventos cuando se cancela una cita
- Consultar disponibilidad real desde el calendario

Usa una Service Account de Google para acceso server-to-server (sin login manual).

SETUP:
1. Ve a https://console.cloud.google.com
2. Crea un proyecto (o usa uno existente)
3. Habilita la API de Google Calendar
4. Crea una Service Account (IAM > Service Accounts > Create)
5. Descarga el archivo JSON de credenciales
6. Comparte tu Google Calendar con el email de la Service Account
   (ej: mi-servicio@mi-proyecto.iam.gserviceaccount.com) con permisos de edición
7. Renombra el archivo JSON a 'credentials.json' y ponlo en la carpeta del proyecto
8. Copia el ID del calendario (en Calendar Settings > Calendar ID) y ponlo en .env
"""

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from datetime import datetime, date, time, timedelta
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# CONFIGURACIÓN
# ═══════════════════════════════════════════════════════════════

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE = os.getenv("TIMEZONE", "Europe/Madrid")

SCOPES = ["https://www.googleapis.com/auth/calendar"]

# Colores de Google Calendar por tipo de servicio
# Ver: https://developers.google.com/calendar/api/v3/reference/colors
COLORES_SERVICIO = {
    "corte": "9",       # Blueberry (azul oscuro)
    "coloracion": "6",  # Tangerine (naranja)
    "brushing": "7",    # Peacock (turquesa)
    "unas": "3",        # Grape (púrpura)
    "facial": "2",      # Sage (verde)
    "depilacion": "5",  # Banana (amarillo)
}


class GoogleCalendarService:
    """Gestiona la conexión y operaciones con Google Calendar."""

    def __init__(self):
        self.service = None
        self.enabled = False
        self._init_service()

    def _init_service(self):
        """Inicializa la conexión con Google Calendar."""
        if not os.path.exists(CREDENTIALS_FILE):
            logger.warning(
                f"⚠️  Archivo de credenciales '{CREDENTIALS_FILE}' no encontrado. "
                f"Google Calendar deshabilitado. Las citas se guardarán solo en la BD local."
            )
            return

        try:
            credentials = service_account.Credentials.from_service_account_file(
                CREDENTIALS_FILE, scopes=SCOPES
            )
            self.service = build("calendar", "v3", credentials=credentials)
            self.enabled = True
            logger.info("✅ Google Calendar conectado correctamente.")
        except Exception as e:
            logger.error(f"❌ Error al conectar con Google Calendar: {e}")
            self.enabled = False

    def crear_evento(
        self,
        titulo: str,
        fecha: str,
        hora_inicio: str,
        hora_fin: str,
        descripcion: str = "",
        servicio_id: str = "",
        cliente_telefono: str = "",
        cita_id: int = None,
    ) -> Optional[str]:
        """
        Crea un evento en Google Calendar.
        Devuelve el event_id de Google o None si falla.
        """
        if not self.enabled:
            logger.info("Google Calendar no habilitado. Saltando creación de evento.")
            return None

        color_id = COLORES_SERVICIO.get(servicio_id, "1")

        event = {
            "summary": f"💇 {titulo}",
            "description": self._formatear_descripcion(descripcion, cliente_telefono, cita_id),
            "start": {
                "dateTime": f"{fecha}T{hora_inicio}:00",
                "timeZone": TIMEZONE,
            },
            "end": {
                "dateTime": f"{fecha}T{hora_fin}:00",
                "timeZone": TIMEZONE,
            },
            "colorId": color_id,
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},   # 1 hora antes
                    {"method": "popup", "minutes": 15},    # 15 min antes
                ],
            },
            # Metadatos internos para identificar la cita
            "extendedProperties": {
                "private": {
                    "empresa_sl_cita_id": str(cita_id) if cita_id else "",
                    "servicio_id": servicio_id,
                    "cliente_telefono": cliente_telefono,
                }
            },
        }

        try:
            result = self.service.events().insert(
                calendarId=CALENDAR_ID, body=event
            ).execute()
            event_id = result.get("id")
            logger.info(f"✅ Evento creado en Google Calendar: {event_id}")
            return event_id
        except HttpError as e:
            logger.error(f"❌ Error al crear evento en Google Calendar: {e}")
            return None

    def modificar_evento(
        self,
        google_event_id: str,
        titulo: str = None,
        fecha: str = None,
        hora_inicio: str = None,
        hora_fin: str = None,
        descripcion: str = None,
        servicio_id: str = None,
    ) -> bool:
        """Modifica un evento existente en Google Calendar."""
        if not self.enabled or not google_event_id:
            return False

        try:
            # Obtener evento actual
            event = self.service.events().get(
                calendarId=CALENDAR_ID, eventId=google_event_id
            ).execute()

            # Actualizar solo los campos proporcionados
            if titulo:
                event["summary"] = f"💇 {titulo}"
            if fecha and hora_inicio:
                event["start"] = {
                    "dateTime": f"{fecha}T{hora_inicio}:00",
                    "timeZone": TIMEZONE,
                }
            if fecha and hora_fin:
                event["end"] = {
                    "dateTime": f"{fecha}T{hora_fin}:00",
                    "timeZone": TIMEZONE,
                }
            if descripcion:
                event["description"] = descripcion
            if servicio_id:
                event["colorId"] = COLORES_SERVICIO.get(servicio_id, "1")

            self.service.events().update(
                calendarId=CALENDAR_ID, eventId=google_event_id, body=event
            ).execute()
            logger.info(f"✅ Evento actualizado en Google Calendar: {google_event_id}")
            return True
        except HttpError as e:
            logger.error(f"❌ Error al modificar evento: {e}")
            return False

    def cancelar_evento(self, google_event_id: str) -> bool:
        """Cancela (elimina) un evento de Google Calendar."""
        if not self.enabled or not google_event_id:
            return False

        try:
            self.service.events().delete(
                calendarId=CALENDAR_ID, eventId=google_event_id
            ).execute()
            logger.info(f"✅ Evento cancelado en Google Calendar: {google_event_id}")
            return True
        except HttpError as e:
            logger.error(f"❌ Error al cancelar evento: {e}")
            return False

    def obtener_eventos_dia(self, fecha: str) -> list:
        """Obtiene todos los eventos de un día específico."""
        if not self.enabled:
            return []

        try:
            time_min = f"{fecha}T00:00:00+01:00"
            time_max = f"{fecha}T23:59:59+01:00"

            result = self.service.events().list(
                calendarId=CALENDAR_ID,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
            ).execute()

            return result.get("items", [])
        except HttpError as e:
            logger.error(f"❌ Error al obtener eventos: {e}")
            return []

    def _formatear_descripcion(
        self, descripcion: str, telefono: str, cita_id: int
    ) -> str:
        """Formatea la descripción del evento con información útil."""
        partes = []
        if descripcion:
            partes.append(descripcion)
        if telefono:
            partes.append(f"📱 Teléfono cliente: {telefono}")
        if cita_id:
            partes.append(f"🔗 ID cita: {cita_id}")
        partes.append("—")
        partes.append("Reservado automáticamente por el asistente virtual de Empresa SL")
        return "\n".join(partes)


# Instancia global (singleton)
calendar_service = GoogleCalendarService()
