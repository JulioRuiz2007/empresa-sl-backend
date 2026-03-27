# Guía completa: Desplegar el backend + Configurar funciones en Retell

## PARTE 1: Subir el backend a internet (Railway)

Railway es una plataforma que toma tu código y lo pone en un servidor con una URL pública.
Es gratis para empezar (te dan $5 de crédito, suficiente para semanas de pruebas).

### Paso 1: Crear cuenta en Railway
1. Ve a https://railway.app
2. Haz clic en "Start a New Project"
3. Inicia sesión con tu cuenta de GitHub (si no tienes GitHub, créate una en github.com)

### Paso 2: Subir los archivos a GitHub
1. Ve a https://github.com/new
2. Nombre del repositorio: "empresa-sl-backend"
3. Déjalo como Public (o Private, da igual)
4. Haz clic en "Create repository"
5. En la página que aparece, haz clic en "uploading an existing file"
6. Arrastra estos 4 archivos:
   - main.py
   - google_calendar.py
   - requirements.txt
   - Procfile (lo crearás abajo)
7. Haz clic en "Commit changes"

### Paso 3: Crear el archivo Procfile
Antes de subir, crea un archivo de texto llamado exactamente "Procfile" (sin extensión) con este contenido:

    web: uvicorn main:app --host 0.0.0.0 --port $PORT

Este archivo le dice a Railway cómo arrancar tu servidor.

### Paso 4: Conectar Railway con GitHub
1. En Railway, haz clic en "New Project" → "Deploy from GitHub repo"
2. Selecciona el repositorio "empresa-sl-backend"
3. Railway detectará automáticamente que es un proyecto Python y empezará a instalarlo
4. Espera 2-3 minutos hasta que diga "Deployed successfully"

### Paso 5: Obtener tu URL
1. En Railway, haz clic en tu proyecto → "Settings" → "Networking"
2. Haz clic en "Generate Domain"
3. Te dará algo como: https://empresa-sl-backend-production.up.railway.app
4. ¡ESA ES TU URL BASE! Guárdala.

### Paso 6: Verificar que funciona
Abre en tu navegador:
    https://TU-URL.up.railway.app/servicios

Si ves una lista de servicios en JSON, ¡funciona!
También prueba:
    https://TU-URL.up.railway.app/docs

Esto abre la documentación interactiva de la API donde puedes probar todos los endpoints.

---

## PARTE 2: Configurar las funciones en Retell AI

Una vez tengas tu URL (ejemplo: https://empresa-sl-backend-production.up.railway.app),
crea las siguientes funciones en Retell. Para cada una:
1. En el panel izquierdo, arrastra un nodo "Function" al flujo
2. Rellena los campos exactamente como se indica abajo

### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 1: consultar_disponibilidad
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: consultar_disponibilidad

Description: Consulta los huecos disponibles para un servicio en una fecha específica. Llama a esta función cuando el cliente diga qué servicio quiere y para qué día. Devuelve los huecos libres de cada estilista.

API Endpoint: GET → https://TU-URL.up.railway.app/disponibilidad

Timeout: 10000

Headers: (vacío, no necesita)

Query Parameters: (vacío — los parámetros van en el JSON Schema)

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "fecha": {
      "type": "string",
      "description": "Fecha en formato YYYY-MM-DD, por ejemplo 2026-04-01"
    },
    "servicio_id": {
      "type": "string",
      "enum": ["corte", "coloracion", "brushing", "unas", "facial", "depilacion"],
      "description": "ID del servicio que quiere el cliente"
    },
    "estilista_id": {
      "type": "string",
      "enum": ["maria", "lucia", "carmen", "cualquiera"],
      "description": "ID del estilista preferido, o 'cualquiera' si no tiene preferencia"
    }
  },
  "required": ["fecha", "servicio_id"]
}


### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 2: crear_cita
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: crear_cita

Description: Crea una nueva cita. Llama a esta función SOLO cuando tengas TODOS los datos confirmados por el cliente: servicio, estilista, fecha, hora, nombre completo y teléfono.

API Endpoint: POST → https://TU-URL.up.railway.app/citas

Timeout: 10000

Headers:
  Key: Content-Type
  Value: application/json

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "cliente_nombre": {
      "type": "string",
      "description": "Nombre completo del cliente"
    },
    "cliente_telefono": {
      "type": "string",
      "description": "Número de teléfono del cliente"
    },
    "cliente_nuevo": {
      "type": "boolean",
      "description": "true si es su primera visita, false si ya ha venido antes"
    },
    "servicio_id": {
      "type": "string",
      "enum": ["corte", "coloracion", "brushing", "unas", "facial", "depilacion"],
      "description": "ID del servicio"
    },
    "estilista_id": {
      "type": "string",
      "enum": ["maria", "lucia", "carmen", "cualquiera"],
      "description": "ID del estilista"
    },
    "fecha": {
      "type": "string",
      "description": "Fecha en formato YYYY-MM-DD"
    },
    "hora": {
      "type": "string",
      "description": "Hora de inicio en formato HH:MM, por ejemplo 10:00"
    },
    "notas": {
      "type": "string",
      "description": "Cualquier nota adicional sobre la cita"
    }
  },
  "required": ["cliente_nombre", "cliente_telefono", "servicio_id", "estilista_id", "fecha", "hora"]
}


### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 3: buscar_citas
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: buscar_citas

Description: Busca las citas activas de un cliente por su teléfono o nombre. Usa esta función cuando el cliente quiera cambiar o cancelar una cita existente.

API Endpoint: GET → https://TU-URL.up.railway.app/citas/buscar

Timeout: 10000

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "telefono": {
      "type": "string",
      "description": "Número de teléfono del cliente"
    },
    "nombre": {
      "type": "string",
      "description": "Nombre del cliente para buscar coincidencias parciales"
    }
  },
  "required": []
}


### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 4: modificar_cita
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: modificar_cita

Description: Modifica una cita existente. Necesitas el cita_id que se obtiene de buscar_citas. Solo llama a esta función después de que el cliente confirme los cambios.

API Endpoint: POST → https://TU-URL.up.railway.app/citas/modificar

Timeout: 10000

Headers:
  Key: Content-Type
  Value: application/json

NOTA IMPORTANTE: Retell solo permite GET y POST en las funciones.
Nuestro backend original usa PUT, así que necesitamos añadir una ruta
alternativa. Ve a la sección "CAMBIO NECESARIO EN MAIN.PY" más abajo.

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "cita_id": {
      "type": "integer",
      "description": "ID de la cita a modificar, obtenido de buscar_citas"
    },
    "nueva_fecha": {
      "type": "string",
      "description": "Nueva fecha en formato YYYY-MM-DD"
    },
    "nueva_hora": {
      "type": "string",
      "description": "Nueva hora en formato HH:MM"
    },
    "nuevo_estilista_id": {
      "type": "string",
      "description": "Nuevo estilista si quiere cambiar"
    },
    "nuevo_servicio_id": {
      "type": "string",
      "description": "Nuevo servicio si quiere cambiar"
    }
  },
  "required": ["cita_id"]
}


### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 5: cancelar_cita
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: cancelar_cita

Description: Cancela una cita existente. SOLO llama a esta función después de que el cliente confirme EXPLÍCITAMENTE que quiere cancelar. Necesitas el cita_id de buscar_citas.

API Endpoint: POST → https://TU-URL.up.railway.app/citas/cancelar

Timeout: 10000

Headers:
  Key: Content-Type
  Value: application/json

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "cita_id": {
      "type": "integer",
      "description": "ID de la cita a cancelar, obtenido de buscar_citas"
    }
  },
  "required": ["cita_id"]
}


### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### FUNCIÓN 6: consultar_proximos_dias
### ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Name: consultar_proximos_dias

Description: Busca disponibilidad en los próximos días cuando el cliente no tiene una fecha fija. Muestra qué días tienen huecos libres para el servicio solicitado.

API Endpoint: GET → https://TU-URL.up.railway.app/disponibilidad/proximos-dias

Timeout: 10000

Parameters (JSON):
{
  "type": "object",
  "properties": {
    "servicio_id": {
      "type": "string",
      "enum": ["corte", "coloracion", "brushing", "unas", "facial", "depilacion"],
      "description": "ID del servicio"
    },
    "estilista_id": {
      "type": "string",
      "enum": ["maria", "lucia", "carmen", "cualquiera"],
      "description": "ID del estilista preferido"
    },
    "dias": {
      "type": "integer",
      "description": "Cuántos días hacia adelante buscar, por defecto 7"
    }
  },
  "required": ["servicio_id"]
}


---

## PARTE 3: Cambios necesarios en main.py

Retell AI solo permite métodos GET y POST en sus Custom Functions.
Nuestro backend usa PUT para modificar y DELETE para cancelar.
Necesitamos añadir rutas POST alternativas.

Añade este código al final de main.py, ANTES de la línea "if __name__":

```python
# ═══════════════════════════════════════════════════════════════
# RUTAS ALTERNATIVAS PARA RETELL (solo acepta GET y POST)
# ═══════════════════════════════════════════════════════════════

from pydantic import BaseModel as PydanticBaseModel

class ModificarConIdRequest(PydanticBaseModel):
    cita_id: int
    nueva_fecha: Optional[str] = None
    nueva_hora: Optional[str] = None
    nuevo_estilista_id: Optional[str] = None
    nuevo_servicio_id: Optional[str] = None
    notas: Optional[str] = None

class CancelarConIdRequest(PydanticBaseModel):
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
```


---

## PARTE 4: Ver la transcripción de las llamadas

En Retell AI, después de cada llamada (real o simulada):
1. Ve a la sección "Call History" o "Calls" en el dashboard
2. Haz clic en cualquier llamada
3. Verás la transcripción completa: lo que dijo el cliente, lo que respondió el agente,
   y qué funciones se llamaron (con los datos que envió y recibió)

Esto te permite ver exactamente qué está pasando y depurar errores.


---

## RESUMEN: Orden de pasos

1. ✅ Prompt pegado en Retell (ya lo tienes)
2. ⬜ Crear archivo Procfile
3. ⬜ Añadir rutas POST alternativas a main.py
4. ⬜ Subir los archivos a GitHub
5. ⬜ Desplegar en Railway → obtener URL
6. ⬜ Verificar que funciona abriendo /docs en el navegador
7. ⬜ Crear las 6 funciones en Retell con la URL
8. ⬜ Cambiar idioma a Spanish, voz a Andrea/Claudia, modelo a Gemini 2.5 Flash
9. ⬜ Probar con Simulation
10. ⬜ Revisar la transcripción en Call History
