"""
Bot de Telegram — Sky Ingeniería Cashflow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usa Claude IA para interpretar mensajes y registrarlos en
Google Sheets (hoja: 003 Transacciones) con la estructura exacta
de SKY-FNN-DOC-001-Cash Flow_BETA.

INSTALACIÓN:
    pip install python-telegram-bot==20.7 anthropic gspread google-auth python-dotenv

ARCHIVO .env requerido:
    TELEGRAM_TOKEN=...
    ANTHROPIC_API_KEY=...
    GOOGLE_SHEET_ID=1YbxA1K_EnLMGC44o9159LiyLrii5Gi8F2a-H3mRf8us
    GOOGLE_CREDS_FILE=credentials.json
    ALLOWED_USER_ID=...   (tu user_id de Telegram — conseguilo con @userinfobot)
"""

import os, json, logging
from datetime import datetime, date
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import anthropic
import httpx
import gspread
from google.oauth2.service_account import Credentials

# Soporte local: cargar .env si existe (en Railway las variables vienen del entorno)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
GOOGLE_SHEET_ID   = os.getenv("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_FILE = os.getenv("GOOGLE_CREDS_FILE", "credentials.json")
ALLOWED_USER_ID   = int(os.getenv("ALLOWED_USER_ID", "0")) or None

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────
# CONOCIMIENTO DE LA PLANILLA
# Estructura real de SKY-FNN-DOC-001-Cash Flow_BETA
# ─────────────────────────────────────────────────────────

# 000 Plan de Cuentas — cuentas reales con código
CUENTAS_ORIGEN = {
    # Cajas (Activos)
    "1100": "1100-Mostrador AR$",
    "1200": "1200-Mostrador U$D",
    "1300": "1300-Mostrador EU",
    "1400": "1400-C.A. Galicia AR$",
    "1500": "1500-C.A. Galicia U$D",
    "1600": "1600-C.C. Galicia AR$",
    "1700": "1700-C.C. Galicia U$D",
    "1800": "1800-C.A. Wise EU",
    "1900": "1900-C.A. Prex UY U$D",
}

CUENTAS_DESTINO_INGRESO = {
    "2100": "2100-DE Vivienda Unifamiliar",
    "2200": "2200-DE Vivienda Multifamiliar",
    "2300": "2300-DE Obras Civiles",
    "2400": "2400-DE Reformas",
    "2500": "2500-Informe Técnico",
    "2600": "2600-Visita a Obra",
    "2900": "2900-Otros ingresos",
}

CUENTAS_DESTINO_EGRESO = {
    "3100": "3100-Salarios operativos",
    "4100": "4100-Salarios administrativos",
    "4200": "4200-Infraestructura de software",
    "4300": "4300-Infraestructura física",
    "4400": "4400-Marketing y publicidad",
    "4500": "4500-Formación",
    "4600": "4600-Contador",
    "4700": "4700-Impuestos",
    "4800": "4800-Comisiones bancarias",
    "4900": "4900-Devoluciones",
    "4950": "4950-Otros gastos",
    "5100": "5100-Dividendos pagados",
}

# 001 Clientes
CLIENTES = {
    "CLI001": "CLI001 - Edifizzi",
    "CLI002": "CLI002 - Analia Valle",
    "CLI003": "CLI003 - DBM",
    "CLI004": "CLI004 - SBMT Arquitectura",
    "CLI005": "CLI005 - BK Kreative Buildings",
    "CLI006": "CLI006 - MC Construcciones",
    "CLI007": "CLI007 - Estudio Rillo",
    "CLI008": "CLI008 - Pi Constructora",
    "CLI010": "CLI010 - Florencia Funes",
    "CLI013": "CLI013 - Rameh",
    "CLI019": "CLI019 - Leone Loray",
    "CLI021": "CLI021 - LGI",
    "CLI023": "CLI023 - Damke",
    "CLI025": "CLI025 - Arq. Indus",
    "CLI026": "CLI026 - Grupo SIEI",
    "CLI027": "CLI027 - Grupo Frali",
    "CLI029": "CLI029 - Arre",
    "CLI030": "CLI030 - Zerep",
    "CLI031": "CLI031 - Alejandro Fontana",
    "CLI032": "CLI032 - Crespo",
    "CLI033": "CLI033 - Nestor Lisi",
    "CLI034": "CLI034 - OMH Arquitectos",
    "CLI036": "CLI036 - IDEA",
    "CLI039": "CLI039 - Cubi",
    "CLI040": "CLI040 - Montaldo",
    "CLI041": "CLI041 - Volk",
    "CLI042": "CLI042 - Stark",
    "CLI044": "CLI044 - Ines Gonzalez",
    "CLI045": "CLI045 - Estudio Sauton",
    "CLI050": "CLI050 - Kubo Arch",
    "CLI067": "CLI067 - Mik Arquitectas",
    "CLI900": "CLI900 - Otros",
}

# 002 Proveedores
PROVEEDORES = {
    "PRV001": "PRV001 - Federico Alonso",
    "PRV002": "PRV002 - Gastón Argarañaz",
    "PRV003": "PRV003 - Daniel Tapia",
    "PRV004": "PRV004 - Andrea Palumbo",
    "PRV005": "PRV005 - Agencia de Marketing",
    "PRV006": "PRV006 - Contador",
    "PRV007": "PRV007 - ARCA / ARBA",
    "PRV008": "PRV008 - Banco Galicia",
    "PRV009": "PRV009 - Meta Ads",
    "PRV900": "PRV900 - Otros",
    "PRV901": "PRV901 - Ignacio Blois",
    "PRV902": "PRV902 - Ignacio Mignone",
    "PRV903": "PRV903 - Freelancer X",
}

# ─────────────────────────────────────────────────────────
# PROMPT PARA CLAUDE
# ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos el asistente financiero de Sky Ingeniería, empresa de ingeniería estructural argentina.
Interpretás mensajes y extraés datos para registrar en la planilla de cashflow (hoja: 003 Transacciones).

ESTRUCTURA EXACTA DE LA HOJA (19 columnas en orden):
1. Fecha — DD/MM/YYYY
2. Transacción — "Ingreso" o "Egreso"
3. Cuenta Origen — de dónde sale el dinero (código-nombre)
4. Cuenta Destino — hacia dónde va (código-nombre)
5. Moneda — "Pesos", "Dolares" o "Euros"
6. Importe — monto positivo para ingresos, NEGATIVO para egresos
7. Factura — "S/Factura" o "C/Factura"
8. Cliente — código CLI### - Nombre (solo en ingresos, vacío en egresos)
9. Proveedor — código PRV### - Nombre (solo en egresos, vacío en ingresos)
10. Expediente — código F25XXX si se menciona, sino vacío
11. Proyecto — descripción del proyecto (F25XXX - descripcion - Cliente)
12-19. Calculados automáticamente (Dolar, EUR/USD, Importe USD, ID Fecha, Year, Quarter, Month, YYYY-MM)

CUENTAS ORIGEN (dónde está la plata):
- 1100-Mostrador AR$ → efectivo en pesos
- 1200-Mostrador U$D → efectivo en dólares
- 1300-Mostrador EU → efectivo en euros
- 1400-C.A. Galicia AR$ → cuenta bancaria pesos
- 1500-C.A. Galicia U$D → cuenta bancaria dólares
- 1600-C.C. Galicia AR$ → cuenta corriente pesos
- 1900-C.A. Prex UY U$D → cuenta Prex dólares

CUENTAS DESTINO PARA INGRESOS:
- 2100-DE Vivienda Unifamiliar → diseño estructural casa individual
- 2200-DE Vivienda Multifamiliar → edificio, duplex, multifamiliar
- 2300-DE Obras Civiles → obras civiles, industrial
- 2400-DE Reformas → reforma, remodelación
- 2500-Informe Técnico → informe, certificado, pericia
- 2600-Visita a Obra → visita, inspección
- 2900-Otros ingresos → cualquier otro ingreso

CUENTAS DESTINO PARA EGRESOS:
- 3100-Salarios operativos → sueldos del equipo técnico
- 4100-Salarios administrativos → sueldos del área admin
- 4200-Infraestructura de software → software, licencias, apps
- 4300-Infraestructura física → alquiler, equipos, mobiliario
- 4400-Marketing y publicidad → publicidad, redes, agencia
- 4500-Formación → cursos, capacitación
- 4600-Contador → honorarios del contador
- 4700-Impuestos → ARCA, ARBA, monotributo, impuestos
- 4800-Comisiones bancarias → comisiones del banco
- 4900-Devoluciones → devolución a cliente
- 4950-Otros gastos → cualquier otro gasto
- 5100-Dividendos pagados → dividendos

CLIENTES — lista exacta de 001 Clientes (usá SIEMPRE estos nombres y códigos exactos):
CLI027=Grupo Frali, CLI019=Leone Loray, CLI001=Edifizzi, CLI004=SBMT Arquitectura,
CLI005=BK Kreative Buildings, CLI039=Cubi, CLI002=Analia Valle, CLI007=Estudio Rillo,
CLI045=Estudio Sauton, CLI050=Kubo Arch, CLI010=Florencia Funes, CLI036=IDEA,
CLI006=MC Construcciones, CLI013=Rameh, CLI034=OMH Arquitectos, CLI021=LGI,
CLI033=Nestor Lisi, CLI008=Pi Constructora, CLI031=Alejandro Fontana, CLI042=Stark,
CLI040=Montaldo, CLI023=Damke, CLI029=Arre, CLI030=Zerep, CLI026=Grupo SIEI,
CLI003=DBM, CLI025=Arq. Indus, CLI032=Crespo, CLI041=Volk, CLI044=Ines Gonzalez,
CLI067=Mik Arquitectas, CLI900=Otros

NORMALIZACIÓN DE CLIENTES:
- Si el usuario escribe "sbmt", "SBMT", "sbmt arq" → CLI004 - SBMT Arquitectura
- Si escribe "leone", "leone loray", "Leone" → CLI019 - Leone Loray
- Si escribe "BK", "bk kreative", "BK buildings" → CLI005 - BK Kreative Buildings
- Si escribe "edifizzi", "edifizi" → CLI001 - Edifizzi
- Si escribe "analia", "analia valle" → CLI002 - Analia Valle
- Si escribe "rillo", "estudio rillo" → CLI007 - Estudio Rillo
- Si escribe "frali", "grupo frali" → CLI027 - Grupo Frali
- Si escribe "pi", "pi constructora" → CLI008 - Pi Constructora
- Si escribe "rameh" → CLI013 - Rameh
- Si escribe "mc", "mc construcciones" → CLI006 - MC Construcciones
- Siempre corregí el nombre al formato exacto de la lista aunque el usuario lo escriba mal

PROVEEDORES FRECUENTES (usar código PRV###):
Federico Alonso=PRV001, Gastón Argarañaz=PRV002, Daniel Tapia=PRV003,
Andrea Palumbo=PRV004, Agencia Marketing=PRV005, Contador=PRV006,
ARCA/ARBA/impuestos=PRV007, Banco Galicia=PRV008, Meta Ads=PRV009,
Ignacio Blois=PRV901, Ignacio Mignone=PRV902, Freelancer=PRV903

REGLAS DE INFERENCIA:
- "cobré/me pagaron/ingreso/anticipo/saldo" → Ingreso
- "pagué/gasté/egreso/salario/sueldo" → Egreso
- Sin moneda + monto grande (>5000) → Pesos
- Sin moneda + monto chico (<5000) con "dólar/usd/dólares" → Dolares
- Efectivo sin aclarar → Mostrador AR$ (pesos) o Mostrador U$D (dólares)
- Transferencia → C.A. Galicia AR$ (pesos) o C.A. Galicia U$D (dólares)
- Vivienda sin aclarar → 2100-DE Vivienda Unifamiliar
- El importe en la planilla es NEGATIVO para egresos
- Si cliente no está en la lista → usar CLI900 - Otros
- Si proveedor no está en la lista → usar PRV900 - Otros

Respondé ÚNICAMENTE con JSON válido, sin texto ni markdown."""

USER_PROMPT = """Fecha de hoy: {today}

Mensaje del usuario: "{message}"

Extraé los datos y respondé con este JSON:
{{
  "tipo": "Ingreso" o "Egreso",
  "fecha": "MM/DD/YYYY",
  "cuenta_origen": "XXXX-Nombre completo",
  "cuenta_destino": "XXXX-Nombre completo",
  "moneda": "Pesos" o "Dolares" o "Euros",
  "importe": número (positivo si ingreso, NEGATIVO si egreso),
  "factura": "S/Factura" o "C/Factura",
  "cliente": "CLIXX - Nombre" o "" (solo si ingreso),
  "proveedor": "PRVXX - Nombre" o "" (solo si egreso),
  "expediente": "F25XXX" o "",
  "proyecto": "descripción del proyecto" o "",
  "confianza": 0-100,
  "dudas": "qué no quedó claro" o null
}}"""

# ─────────────────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────────────────

def get_worksheet(sheet_name="003 Transacciones"):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    # Soporte para Railway: leer credenciales desde variable de entorno
    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if creds_json:
        import tempfile
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    return sh.worksheet(sheet_name)

def excel_date(dt: date) -> int:
    """Número serial de Excel para la fecha."""
    return (dt - date(1899, 12, 30)).days

def build_row(parsed: dict, dolar_blue: float = 1390.0) -> list:
    """
    Construye la fila exacta para 003 Transacciones (19 columnas):
    Fecha | Transacción | Cuenta Origen | Cuenta Destino | Moneda | Importe |
    Factura | Cliente | Proveedor | Expediente | Proyecto |
    Dolar | EUR/USD | Importe USD | ID Fecha | Year | Quarter | Month | YYYY-MM
    """
    tx_date = datetime.strptime(parsed["fecha"], "%m/%d/%Y")
    moneda  = parsed.get("moneda", "Pesos")
    importe = float(parsed.get("importe", 0))

    # Conversión a USD
    if moneda == "Pesos":
        importe_usd = importe / dolar_blue
    elif moneda == "Dolares":
        importe_usd = importe
    else:
        importe_usd = importe  # Euros: sin conversión exacta

    year    = tx_date.year
    month   = tx_date.month
    quarter = (month - 1) // 3 + 1
    yyyy_mm = f"{year}-{month}"

    return [
        parsed["fecha"],                                          # 1. Fecha
        parsed["tipo"],                                           # 2. Transacción
        parsed.get("cuenta_origen", "1100-Mostrador AR$"),       # 3. Cuenta Origen
        parsed.get("cuenta_destino", ""),                        # 4. Cuenta Destino
        moneda,                                                   # 5. Moneda
        importe,                                                  # 6. Importe
        parsed.get("factura", "S/Factura"),                      # 7. Factura
        parsed.get("cliente", ""),                               # 8. Cliente
        parsed.get("proveedor", ""),                             # 9. Proveedor
        parsed.get("expediente", ""),                            # 10. Expediente
        parsed.get("proyecto", ""),                              # 11. Proyecto
        dolar_blue,                                              # 12. Dolar
        1.0,                                                     # 13. EUR/USD
        round(importe_usd, 4),                                   # 14. Importe USD
        excel_date(tx_date.date()),                              # 15. ID Fecha
        year,                                                    # 16. Year
        quarter,                                                 # 17. Quarter
        month,                                                   # 18. Month
        yyyy_mm,                                                 # 19. YYYY-MM
    ]

async def append_to_sheet(parsed: dict) -> int:
    """Inserta la fila y devuelve el número de fila. Usa cotización blue del día."""
    dolar = await get_dolar_blue()
    ws    = get_worksheet()
    row   = build_row(parsed, dolar_blue=dolar)
    ws.append_row(row, value_input_option="USER_ENTERED")
    return len(ws.col_values(1))

def get_month_summary() -> dict:
    """Calcula totales del mes actual en USD."""
    ws      = get_worksheet()
    data    = ws.get_all_values()
    if len(data) < 2:
        return {"ingresos": 0, "egresos": 0, "balance": 0, "tx_count": 0, "mes": ""}

    headers = data[0]
    now     = datetime.now()
    ym      = f"{now.year}-{now.month}"

    try:
        col_ym      = headers.index("YYYY-MM")
        col_imp_usd = headers.index("Importe USD")
        col_tipo    = headers.index("Transacción")
    except ValueError:
        col_ym, col_imp_usd, col_tipo = 18, 13, 1

    total_in = total_eg = count = 0
    for row in data[1:]:
        if len(row) <= col_ym or row[col_ym] != ym:
            continue
        try:
            val  = float(str(row[col_imp_usd]).replace(",", ".") or 0)
            tipo = row[col_tipo]
            if tipo == "Ingreso":
                total_in += val
            elif tipo == "Egreso":
                total_eg += abs(val)
            count += 1
        except (ValueError, IndexError):
            continue

    return {
        "ingresos": round(total_in, 2),
        "egresos":  round(total_eg, 2),
        "balance":  round(total_in - total_eg, 2),
        "tx_count": count,
        "mes":      f"{now.month}/{now.year}",
    }

# ─────────────────────────────────────────────────────────
# CLAUDE IA
# ─────────────────────────────────────────────────────────


async def get_dolar_blue() -> float:
    """Obtiene la cotización del dólar blue desde dolarapi.com."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://dolarapi.com/v1/dolares/blue")
            data = r.json()
            return float(data.get("venta", 1390))
    except Exception as e:
        log.warning(f"No se pudo obtener dólar blue: {e}. Usando valor por defecto.")
        return 1390.0


# ─────────────────────────────────────────────────────────
# SISTEMA DE APRENDIZAJE
# Guarda correcciones del usuario en una hoja "Bot_Memoria"
# y las usa como contexto en cada llamada a Claude
# ─────────────────────────────────────────────────────────

def get_or_create_memoria_sheet():
    """Obtiene o crea la hoja Bot_Memoria para guardar aprendizajes."""
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        creds_json = os.getenv("GOOGLE_CREDS_JSON")
        if creds_json:
            creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
        else:
            creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(GOOGLE_SHEET_ID)
        try:
            return sh.worksheet("Bot_Memoria")
        except:
            ws = sh.add_worksheet(title="Bot_Memoria", rows=500, cols=4)
            ws.append_row(["Fecha", "Tipo", "Original", "Corregido"])
            return ws
    except Exception as e:
        log.error(f"Error accediendo a Bot_Memoria: {e}")
        return None

def guardar_aprendizaje(tipo: str, original: str, corregido: str):
    """Guarda una corrección en Bot_Memoria."""
    try:
        ws = get_or_create_memoria_sheet()
        if ws:
            ws.append_row([datetime.now().strftime("%m/%d/%Y"), tipo, original, corregido])
    except Exception as e:
        log.error(f"Error guardando aprendizaje: {e}")

def get_memoria() -> str:
    """Lee la hoja Bot_Memoria y devuelve el contexto de aprendizajes."""
    try:
        ws   = get_or_create_memoria_sheet()
        if not ws:
            return ""
        data = ws.get_all_values()
        if len(data) < 2:
            return ""
        memorias = data[1:]  # Skip header
        if not memorias:
            return ""
        lines = "\n".join([f"  [{r[1]}] '{r[2]}' → '{r[3]}'" for r in memorias if len(r) >= 4])
        return f"""

MEMORIA DE APRENDIZAJE (correcciones anteriores del usuario — tené en cuenta estas preferencias):
{lines}
"""
    except Exception as e:
        log.error(f"Error leyendo memoria: {e}")
        return ""

def get_presupuestos_pendientes() -> list:
    """Lee 004 Presupuestos y devuelve proyectos para dar contexto a Claude."""
    try:
        ws   = get_worksheet("004 Presupuestos")
        data = ws.get_all_values()
        if len(data) < 2:
            return []
        headers = data[0]
        try:
            col_exp    = headers.index("Expediente")
            col_cli    = headers.index("Cliente")
            col_proy   = headers.index("Proyecto")
            col_srv    = headers.index("Servicio")
            col_estado = headers.index("Estado cobro")
            col_saldo  = headers.index("Saldo")
            col_monto  = headers.index("Monto")
        except ValueError:
            return []
        resultado = []
        for row in data[1:]:
            exp = row[col_exp].strip() if col_exp < len(row) else ""
            if not exp:
                continue
            resultado.append({
                "expediente": exp,
                "cliente":    row[col_cli].strip()    if col_cli    < len(row) else "",
                "proyecto":   row[col_proy].strip()   if col_proy   < len(row) else "",
                "servicio":   row[col_srv].strip()    if col_srv    < len(row) else "",
                "estado":     row[col_estado].strip() if col_estado < len(row) else "",
                "monto":      row[col_monto].strip()  if col_monto  < len(row) else "",
                "saldo":      row[col_saldo].strip()  if col_saldo  < len(row) else "",
            })
        return resultado[:100]
    except Exception as e:
        log.error(f"Error leyendo presupuestos: {e}")
        return []


def parse_with_claude(message: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%m/%d/%Y")

    # Cargar presupuestos + memoria de aprendizaje
    presupuestos = get_presupuestos_pendientes()
    memoria = get_memoria()
    presup_context = ""
    if presupuestos:
        lines = "\n".join([
            f"  {p['expediente']} | {p['cliente']} | {p['proyecto']} | {p['servicio']} | Estado cobro: {p['estado']} | Monto: {p['monto']} | Saldo: {p['saldo']}"
            for p in presupuestos
        ])
        presup_context = f"""

PRESUPUESTOS EXISTENTES (hoja 004 Presupuestos) — usalos para autocompletar expediente, proyecto y cliente:
{lines}

INSTRUCCIONES DE BÚSQUEDA:
- Si el usuario menciona un cliente (ej: "SBMT", "Leone"), buscá ese cliente en la lista y completá el expediente y proyecto automáticamente.
- Si hay varios expedientes del mismo cliente, elegí el que tenga saldo pendiente o el más reciente.
- Si el usuario ya menciona el expediente, usalo directamente.
- Si no encontrás coincidencia, dejá expediente vacío y confianza < 80.
"""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT + presup_context + memoria,
        messages=[{
            "role": "user",
            "content": USER_PROMPT.format(today=today, message=message)
        }]
    )
    raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ─────────────────────────────────────────────────────────
# FORMATO DEL MENSAJE DE CONFIRMACIÓN
# ─────────────────────────────────────────────────────────

def format_confirmation(parsed: dict) -> str:
    es_in  = parsed["tipo"] == "Ingreso"
    emoji  = "💰" if es_in else "💸"
    imp    = float(parsed.get("importe", 0))
    sign   = "+" if imp >= 0 else ""
    sym    = {"Pesos": "$", "Dolares": "U$D", "Euros": "€"}.get(parsed.get("moneda", "Pesos"), "$")

    lines = [
        f"{emoji} *{parsed['tipo'].upper()} detectado*",
        "",
        f"📅 Fecha:          `{parsed.get('fecha', '—')}`",
        f"💵 Importe:        `{sign}{sym} {abs(imp):,.0f}`",
        f"🏦 Cuenta Origen:  `{parsed.get('cuenta_origen', '—')}`",
        f"📂 Cuenta Destino: `{parsed.get('cuenta_destino', '—')}`",
        f"💳 Moneda:         `{parsed.get('moneda', '—')}`",
        f"🧾 Factura:        `{parsed.get('factura', 'S/Factura')}`",
    ]

    if es_in and parsed.get("cliente"):
        lines.append(f"👤 Cliente:        `{parsed['cliente']}`")
    if not es_in and parsed.get("proveedor"):
        lines.append(f"🏭 Proveedor:      `{parsed['proveedor']}`")
    if parsed.get("expediente"):
        lines.append(f"📁 Expediente:     `{parsed['expediente']}`")
    if parsed.get("proyecto"):
        lines.append(f"🏗️ Proyecto:       `{parsed['proyecto']}`")

    conf = parsed.get("confianza", 100)
    if conf < 80:
        lines += ["", f"⚠️ _Confianza: {conf}% — revisá los datos_"]
    if parsed.get("dudas"):
        lines.append(f"❓ _Duda: {parsed['dudas']}_")

    lines += ["", "¿Lo registro así en la planilla?"]
    return "\n".join(lines)

# ─────────────────────────────────────────────────────────
# HANDLERS DE TELEGRAM
# ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Sky Ingeniería — Bot de Cashflow*\n\n"
        "Mandame una operación y la registro directamente en tu planilla Google Sheets.\n\n"
        "*Ejemplos de mensajes:*\n"
        "• `ingreso 500 usd SBMT anticipo F25031`\n"
        "• `cobré 1.200.000 pesos vivienda unifamiliar Leone Loray saldo`\n"
        "• `egreso salario Federico 800 dólares`\n"
        "• `pagué impuestos ARCA 85000 transferencia`\n"
        "• `gasté 45000 software licencia autocad`\n\n"
        "*Comandos:*\n"
        "/resumen — Balance del mes en USD\n"
        "/cuentas — Ver plan de cuentas\n"
        "/ayuda — Ayuda completa",
        parse_mode="Markdown"
    )

async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Cómo registrar*\n\n"
        "Describí la operación de forma natural:\n"
        "`[tipo] [monto] [moneda] [descripción] [cliente/proveedor]`\n\n"
        "*Palabras clave:*\n"
        "Ingresos: _cobré, ingreso, anticipo, saldo, me pagaron_\n"
        "Egresos: _pagué, egreso, salario, gasto, transferí_\n\n"
        "*Monedas:* pesos / dólares (usd) / euros\n"
        "*Formas de pago:* efectivo / transferencia\n\n"
        "/resumen — Totales del mes\n"
        "/cobrar — Proyectos con saldo pendiente\n"
        "/buscar [término] — Buscar transacciones\n"
        "/aprender [tipo] [original] [correcto] — Enseñarle al bot\n"
        "/memoria — Ver aprendizajes guardados\n"
        "/cuentas — Plan de cuentas completo",
        parse_mode="Markdown"
    )

async def cmd_cuentas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📊 *Plan de Cuentas*\n\n"
        "*CUENTAS ORIGEN (de dónde sale):*\n"
        "`1100` Mostrador AR$ (efectivo $)\n"
        "`1200` Mostrador U$D (efectivo USD)\n"
        "`1400` C.A. Galicia AR$\n"
        "`1500` C.A. Galicia U$D\n\n"
        "*INGRESOS (cuenta destino):*\n"
        "`2100` DE Vivienda Unifamiliar\n"
        "`2200` DE Vivienda Multifamiliar\n"
        "`2300` DE Obras Civiles\n"
        "`2400` DE Reformas\n"
        "`2500` Informe Técnico\n"
        "`2600` Visita a Obra\n"
        "`2900` Otros ingresos\n\n"
        "*EGRESOS (cuenta destino):*\n"
        "`3100` Salarios operativos\n"
        "`4100` Salarios administrativos\n"
        "`4200` Infraestructura de software\n"
        "`4300` Infraestructura física\n"
        "`4400` Marketing y publicidad\n"
        "`4500` Formación\n"
        "`4600` Contador\n"
        "`4700` Impuestos\n"
        "`4800` Comisiones bancarias\n"
        "`4950` Otros gastos"
    )
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_resumen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Consultando planilla...")
    try:
        s   = get_month_summary()
        bal = s["balance"]
        em  = "✅" if bal >= 0 else "🔴"
        await msg.edit_text(
            f"📊 *Resumen {s['mes']}*\n\n"
            f"💰 Ingresos:   `U$D {s['ingresos']:>10,.2f}`\n"
            f"💸 Egresos:    `U$D {s['egresos']:>10,.2f}`\n"
            f"{'─' * 30}\n"
            f"{em} Balance:  `U$D {bal:>10,.2f}`\n\n"
            f"_📋 {s['tx_count']} transacciones registradas este mes_",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Error resumen: {e}")
        await msg.edit_text(
            f"❌ Error al leer la planilla:\n`{e}`\n\n"
            "_Verificá que el bot tenga acceso de editor._",
            parse_mode="Markdown"
        )


async def cmd_aprender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Permite al usuario enseñarle al bot una corrección manual."""
    args = ctx.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "📚 *Cómo enseñarle al bot*\n\n"
            "Formato: `/aprender [tipo] [original] → [correcto]`\n\n"
            "*Ejemplos:*\n"
            "`/aprender cliente sbmt SBMT Arquitectura`\n"
            "`/aprender categoria honorarios Venta de servicio`\n"
            "`/aprender cuenta galicia C.A. Galicia AR$`\n\n"
            "El bot recordará esta corrección en todas las operaciones futuras.",
            parse_mode="Markdown"
        )
        return
    tipo     = args[0]
    original = args[1]
    correcto = " ".join(args[2:])
    guardar_aprendizaje(tipo, original, correcto)
    await update.message.reply_text(
        f"✅ *¡Aprendido!*\n\n"
        f"Tipo: `{tipo}`\n"
        f"Cuando digas `{original}` → lo voy a interpretar como `{correcto}`\n\n"
        f"_Guardado en Bot_Memoria_",
        parse_mode="Markdown"
    )

async def cmd_memoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Muestra todo lo que el bot ha aprendido."""
    try:
        ws   = get_or_create_memoria_sheet()
        data = ws.get_all_values() if ws else []
        if len(data) < 2:
            await update.message.reply_text("📭 El bot aún no tiene aprendizajes guardados.\nUsá `/aprender` para enseñarle.")
            return
        lines = "\n".join([f"• [{r[1]}] `{r[2]}` → `{r[3]}`" for r in data[1:] if len(r) >= 4])
        await update.message.reply_text(
            f"📚 *Memoria del bot ({len(data)-1} aprendizajes)*\n\n{lines}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_cobrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Proyectos con saldo pendiente desde 005 Proyectos por cobrar."""
    msg = await update.message.reply_text("⏳ Consultando proyectos por cobrar...")
    try:
        ws   = get_worksheet("005 Proyectos por cobrar")
        data = ws.get_all_values()
        if len(data) < 2:
            await msg.edit_text("📭 No hay datos en Proyectos por cobrar.")
            return

        # Estructura: Moneda | Aux (Expediente - Proyecto - Cliente) | Presupuesto | Cobrado | Saldo
        pendientes = []
        for row in data[1:]:
            if len(row) < 5:
                continue
            moneda = row[0].strip()
            aux    = row[1].strip()   # "F25077 - YPF Campana - Fontana"
            saldo  = row[4].strip()   # "$1,000,000.00"
            if not aux or not saldo:
                continue
            # Parsear saldo
            saldo_num_str = saldo.replace("$","").replace(".","").replace(",",".").strip()
            try:
                saldo_num = float(saldo_num_str)
                if saldo_num <= 0:
                    continue
            except:
                continue
            # Parsear Aux: "F25077 - YPF Campana - Fontana"
            partes = aux.split(" - ", 2)
            exp    = partes[0].strip() if len(partes) > 0 else ""
            proy   = partes[1].strip() if len(partes) > 1 else ""
            cli    = partes[2].strip() if len(partes) > 2 else ""
            presup = row[2].strip() if len(row) > 2 else ""
            cobrado= row[3].strip() if len(row) > 3 else ""
            pendientes.append({
                "exp": exp, "proy": proy, "cli": cli,
                "moneda": moneda, "presup": presup,
                "cobrado": cobrado, "saldo": saldo
            })

        if not pendientes:
            await msg.edit_text("✅ No hay proyectos con saldo pendiente.")
            return

        total = len(pendientes)
        lines = [f"📋 *Proyectos por cobrar ({total})*\n"]
        for p in pendientes[:20]:
            mon_emoji = "💵" if "USD" in p["moneda"].upper() else "💰"
            lines.append(f"{mon_emoji} `{p['exp']}` — *{p['cli'] or p['proy']}*")
            if p['proy']: lines.append(f"  _{p['proy'][:45]}_")
            lines.append(f"  Presup: {p['presup']} | Cobrado: {p['cobrado']}")
            lines.append(f"  *Saldo: {p['saldo']}* ({p['moneda']})")
        if total > 20:
            lines.append(f"\n_...y {total-20} más_")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error cobrar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")


async def cmd_buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Búsqueda inteligente: acepta expediente, cliente o proyecto.
    Cruza 001 Clientes + 004 Presupuestos + 003 Transacciones para completar info faltante."""
    if not ctx.args:
        await update.message.reply_text(
            "🔍 *Buscar*\n\n"
            "Uso: `/buscar [término]`\n\n"
            "Podés buscar por:\n"
            "• Expediente: `/buscar F25031`\n"
            "• Cliente: `/buscar SBMT`\n"
            "• Proyecto: `/buscar Campana`\n\n"
            "El bot busca en clientes, presupuestos y transacciones automáticamente.",
            parse_mode="Markdown"
        )
        return

    query = " ".join(ctx.args).lower().strip()
    msg   = await update.message.reply_text(f"🔍 Buscando `{query}`...", parse_mode="Markdown")

    try:
        resultado = {}

        # ── 1. Buscar en 001 Clientes ──────────────────────────────
        try:
            ws_cli  = get_worksheet("001 Clientes")
            cli_data = ws_cli.get_all_values()
            for row in cli_data[1:]:
                if any(query in cell.lower() for cell in row if cell):
                    resultado["cliente_id"]     = row[0].strip() if len(row) > 0 else ""
                    resultado["cliente_nombre"]  = row[1].strip() if len(row) > 1 else ""
                    resultado["cliente_rep"]     = row[2].strip() if len(row) > 2 else ""
                    break
        except Exception as e:
            log.warning(f"Clientes lookup error: {e}")

        # ── 2. Buscar en 004 Presupuestos ──────────────────────────
        presupuestos_match = []
        try:
            ws_presup  = get_worksheet("004 Presupuestos")
            presup_data = ws_presup.get_all_values()
            if presup_data:
                ph = presup_data[0]
                def pc(name):
                    try: return ph.index(name)
                    except: return -1
                p_exp    = pc("Expediente")
                p_cli    = pc("Cliente")
                p_proy   = pc("Proyecto")
                p_srv    = pc("Servicio")
                p_estado = pc("Estado cobro")
                p_monto  = pc("Monto")
                p_saldo  = pc("Saldo")
                for row in presup_data[1:]:
                    if any(query in cell.lower() for cell in row if cell):
                        presupuestos_match.append({
                            "exp":    row[p_exp].strip()    if p_exp    >= 0 and p_exp    < len(row) else "",
                            "cli":    row[p_cli].strip()    if p_cli    >= 0 and p_cli    < len(row) else "",
                            "proy":   row[p_proy].strip()   if p_proy   >= 0 and p_proy   < len(row) else "",
                            "srv":    row[p_srv].strip()    if p_srv    >= 0 and p_srv    < len(row) else "",
                            "estado": row[p_estado].strip() if p_estado >= 0 and p_estado < len(row) else "",
                            "monto":  row[p_monto].strip()  if p_monto  >= 0 and p_monto  < len(row) else "",
                            "saldo":  row[p_saldo].strip()  if p_saldo  >= 0 and p_saldo  < len(row) else "",
                        })
        except Exception as e:
            log.warning(f"Presupuestos lookup error: {e}")

        # Si encontramos cliente pero no presupuestos, buscar con el nombre del cliente
        if resultado.get("cliente_nombre") and not presupuestos_match:
            nombre = resultado["cliente_nombre"].lower()
            try:
                for row in presup_data[1:]:
                    if any(nombre in cell.lower() for cell in row if cell):
                        presupuestos_match.append({
                            "exp":    row[p_exp].strip()    if p_exp    >= 0 and p_exp    < len(row) else "",
                            "cli":    row[p_cli].strip()    if p_cli    >= 0 and p_cli    < len(row) else "",
                            "proy":   row[p_proy].strip()   if p_proy   >= 0 and p_proy   < len(row) else "",
                            "srv":    row[p_srv].strip()    if p_srv    >= 0 and p_srv    < len(row) else "",
                            "estado": row[p_estado].strip() if p_estado >= 0 and p_estado < len(row) else "",
                            "monto":  row[p_monto].strip()  if p_monto  >= 0 and p_monto  < len(row) else "",
                            "saldo":  row[p_saldo].strip()  if p_saldo  >= 0 and p_saldo  < len(row) else "",
                        })
            except:
                pass

        # ── 3. Buscar en 003 Transacciones ─────────────────────────
        tx_match = []
        try:
            ws_tx   = get_worksheet("003 Transacciones")
            tx_data = ws_tx.get_all_values()
            if tx_data:
                th = tx_data[0]
                def tc(name):
                    try: return th.index(name)
                    except: return -1
                t_fecha = tc("Fecha")
                t_tipo  = tc("Transacción")
                t_cli   = tc("Cliente")
                t_prv   = tc("Proveedor")
                t_exp   = tc("Expediente")
                t_imp   = tc("Importe USD")

                # Si tenemos expediente de presupuestos, buscar también por eso
                exp_ids = set(p["exp"].lower() for p in presupuestos_match if p["exp"])
                cli_nombre = resultado.get("cliente_nombre","").lower()

                for row in tx_data[1:]:
                    row_text = " ".join(cell.lower() for cell in row if cell)
                    if query in row_text or any(e in row_text for e in exp_ids) or (cli_nombre and cli_nombre in row_text):
                        tx_match.append(row)
        except Exception as e:
            log.warning(f"Transacciones lookup error: {e}")

        # ── Armar respuesta ─────────────────────────────────────────
        if not resultado and not presupuestos_match and not tx_match:
            await msg.edit_text(f"📭 Sin resultados para `{query}`.\nProbá con otro término.", parse_mode="Markdown")
            return

        lines = [f"🔍 *Resultados para '{query}'*\n"]

        # Cliente encontrado
        if resultado.get("cliente_nombre"):
            lines.append(f"👤 *Cliente:* {resultado['cliente_id']} — {resultado['cliente_nombre']}")
            if resultado.get("cliente_rep"):
                lines.append(f"  Contacto: {resultado['cliente_rep']}")
            lines.append("")

        # Presupuestos
        if presupuestos_match:
            lines.append(f"📄 *Presupuestos ({len(presupuestos_match)}):*")
            for p in presupuestos_match[:8]:
                lines.append(f"• `{p['exp']}` — {p['proy'][:40]}")
                lines.append(f"  {p['srv']} | Estado: {p['estado']}")
                lines.append(f"  Monto: {p['monto']} | Saldo: {p['saldo']}")
            if len(presupuestos_match) > 8:
                lines.append(f"  _...y {len(presupuestos_match)-8} más_")
            lines.append("")

        # Transacciones
        if tx_match:
            total_in = total_eg = 0.0
            for row in tx_match:
                tipo = row[t_tipo].strip() if t_tipo >= 0 and t_tipo < len(row) else ""
                try:
                    imp = float(str(row[t_imp]).replace(",",".").replace("U$D","").strip()) if t_imp >= 0 and t_imp < len(row) else 0
                    if tipo == "Ingreso": total_in += imp
                    elif tipo == "Egreso": total_eg += abs(imp)
                except: pass

            lines.append(f"💳 *Transacciones ({len(tx_match)}):*")
            lines.append(f"  💰 Ingresos: `U$D {total_in:,.2f}` | 💸 Egresos: `U$D {total_eg:,.2f}`")
            for row in tx_match[-10:]:  # últimas 10
                fecha = row[t_fecha].strip() if t_fecha >= 0 and t_fecha < len(row) else ""
                tipo  = row[t_tipo].strip()  if t_tipo  >= 0 and t_tipo  < len(row) else ""
                cli   = row[t_cli].strip()   if t_cli   >= 0 and t_cli   < len(row) else ""
                prv   = row[t_prv].strip()   if t_prv   >= 0 and t_prv   < len(row) else ""
                exp   = row[t_exp].strip()   if t_exp   >= 0 and t_exp   < len(row) else ""
                imp   = row[t_imp].strip()   if t_imp   >= 0 and t_imp   < len(row) else ""
                emoji = "💰" if tipo == "Ingreso" else "💸"
                entidad = cli or prv
                lines.append(f"{emoji} `{fecha}` `{exp}` {entidad} — `{imp}`")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        log.error(f"Error buscar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Lee comprobante por foto. Solo extrae valores numéricos.
    El usuario completa el resto en el caption."""
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    caption = update.message.caption or ""
    msg = await update.message.reply_text("📸 Leyendo valores del comprobante...")

    try:
        # Descargar imagen
        photo  = update.message.photo[-1]
        file   = await ctx.bot.get_file(photo.file_id)
        import httpx, base64
        async with httpx.AsyncClient() as client:
            resp = await client.get(file.file_path)
            img_b64 = base64.b64encode(resp.content).decode()

        today = datetime.now().strftime("%m/%d/%Y")

        client_ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp_ai   = client_ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}
                    },
                    {
                        "type": "text",
                        "text": (
                            "Mirá este comprobante y extraé ÚNICAMENTE los valores numéricos que ves. "
                            "No interpretes nada más. Respondé SOLO con este JSON, sin texto adicional:\n"
                            "{\n"
                            '  "monto": número que aparece en el comprobante (el principal, positivo),\n'
                            '  "fecha": "MM/DD/YYYY si aparece, si no usar ' + today + '",\n'
                            '  "moneda": "Pesos" o "Dolares" o "Euros" según el símbolo que veas\n'
                            "}"
                        )
                    }
                ]
            }]
        )

        raw = resp_ai.content[0].text.strip().replace("```json","").replace("```","").strip()
        valores = json.loads(raw)

        # Ahora combinar con el caption del usuario para armar el mensaje completo
        if caption:
            mensaje_completo = caption + f" monto {valores.get('monto',0)} {valores.get('moneda','Pesos')} fecha {valores.get('fecha', today)}"
        else:
            # Sin caption, pedir que complete
            await msg.edit_text(
                f"📸 *Valores leídos del comprobante:*\n\n"
                f"💵 Monto: `{valores.get('monto', '?')}`\n"
                f"💱 Moneda: `{valores.get('moneda', '?')}`\n"
                f"📅 Fecha: `{valores.get('fecha', today)}`\n\n"
                f"Ahora mandame un mensaje con el resto de los datos:\n"
                f"_Ej: `ingreso venta de servicio SBMT anticipo F25031`_",
                parse_mode="Markdown"
            )
            # Guardar valores para combinar con el siguiente mensaje
            ctx.user_data["valores_comprobante"] = valores
            return

        # Con caption: procesar todo junto
        presupuestos = get_presupuestos_pendientes()
        memoria      = get_memoria()
        presup_ctx   = ""
        if presupuestos:
            lines = "\n".join([
                f"  {p['expediente']} | {p['cliente']} | {p['proyecto']} | Estado: {p['estado']} | Saldo: {p['saldo']}"
                for p in presupuestos
            ])
            presup_ctx = f"\n\nPRESUPUESTOS:\n{lines}"

        parsed = parse_with_claude(mensaje_completo)
        # Sobreescribir con los valores exactos del comprobante
        parsed["monto"]  = valores.get("monto", parsed.get("monto", 0))
        parsed["moneda"] = valores.get("moneda", parsed.get("moneda", "Pesos"))
        parsed["fecha"]  = valores.get("fecha", parsed.get("fecha", today))
        if parsed.get("tipo") == "Ingreso":
            parsed["importe"] = parsed["monto"]
        else:
            parsed["importe"] = -abs(parsed["monto"])

        ctx.user_data["pending"] = {"parsed": parsed, "original": f"[foto] {caption}"}
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirmar y guardar", callback_data="confirm"),
             InlineKeyboardButton("❌ Cancelar",            callback_data="cancel")],
            [InlineKeyboardButton("✏️ Corregir",            callback_data="edit")]
        ])
        await msg.edit_text(
            "📸 *Comprobante procesado*\n\n" + format_confirmation(parsed),
            parse_mode="Markdown",
            reply_markup=kb
        )

    except json.JSONDecodeError:
        await msg.edit_text(
            "❌ No pude leer los valores del comprobante.\n"
            "Probá con una foto más nítida, o escribí el monto manualmente."
        )
    except Exception as e:
        log.error(f"Error foto: {e}")
        await msg.edit_text(f"❌ Error al procesar la imagen: `{e}`", parse_mode="Markdown")


async def cmd_aprender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Permite al usuario enseñarle al bot una corrección manual."""
    args = ctx.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "📚 *Cómo enseñarle al bot*\n\n"
            "Formato: `/aprender [tipo] [original] → [correcto]`\n\n"
            "*Ejemplos:*\n"
            "`/aprender cliente sbmt SBMT Arquitectura`\n"
            "`/aprender categoria honorarios Venta de servicio`\n"
            "`/aprender cuenta galicia C.A. Galicia AR$`\n\n"
            "El bot recordará esta corrección en todas las operaciones futuras.",
            parse_mode="Markdown"
        )
        return
    tipo     = args[0]
    original = args[1]
    correcto = " ".join(args[2:])
    guardar_aprendizaje(tipo, original, correcto)
    await update.message.reply_text(
        f"✅ *¡Aprendido!*\n\n"
        f"Tipo: `{tipo}`\n"
        f"Cuando digas `{original}` → lo voy a interpretar como `{correcto}`\n\n"
        f"_Guardado en Bot_Memoria_",
        parse_mode="Markdown"
    )

async def cmd_memoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Muestra todo lo que el bot ha aprendido."""
    try:
        ws   = get_or_create_memoria_sheet()
        data = ws.get_all_values() if ws else []
        if len(data) < 2:
            await update.message.reply_text("📭 El bot aún no tiene aprendizajes guardados.\nUsá `/aprender` para enseñarle.")
            return
        lines = "\n".join([f"• [{r[1]}] `{r[2]}` → `{r[3]}`" for r in data[1:] if len(r) >= 4])
        await update.message.reply_text(
            f"📚 *Memoria del bot ({len(data)-1} aprendizajes)*\n\n{lines}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")


async def cmd_cobrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Proyectos con saldo pendiente desde 005 Proyectos por cobrar."""
    msg = await update.message.reply_text("⏳ Consultando proyectos por cobrar...")
    try:
        ws   = get_worksheet("005 Proyectos por cobrar")
        data = ws.get_all_values()
        if len(data) < 2:
            await msg.edit_text("📭 No hay datos en Proyectos por cobrar.")
            return

        # Estructura: Moneda | Aux (Expediente - Proyecto - Cliente) | Presupuesto | Cobrado | Saldo
        pendientes = []
        for row in data[1:]:
            if len(row) < 5:
                continue
            moneda = row[0].strip()
            aux    = row[1].strip()   # "F25077 - YPF Campana - Fontana"
            saldo  = row[4].strip()   # "$1,000,000.00"
            if not aux or not saldo:
                continue
            # Parsear saldo
            saldo_num_str = saldo.replace("$","").replace(".","").replace(",",".").strip()
            try:
                saldo_num = float(saldo_num_str)
                if saldo_num <= 0:
                    continue
            except:
                continue
            # Parsear Aux: "F25077 - YPF Campana - Fontana"
            partes = aux.split(" - ", 2)
            exp    = partes[0].strip() if len(partes) > 0 else ""
            proy   = partes[1].strip() if len(partes) > 1 else ""
            cli    = partes[2].strip() if len(partes) > 2 else ""
            presup = row[2].strip() if len(row) > 2 else ""
            cobrado= row[3].strip() if len(row) > 3 else ""
            pendientes.append({
                "exp": exp, "proy": proy, "cli": cli,
                "moneda": moneda, "presup": presup,
                "cobrado": cobrado, "saldo": saldo
            })

        if not pendientes:
            await msg.edit_text("✅ No hay proyectos con saldo pendiente.")
            return

        total = len(pendientes)
        lines = [f"📋 *Proyectos por cobrar ({total})*\n"]
        for p in pendientes[:20]:
            mon_emoji = "💵" if "USD" in p["moneda"].upper() else "💰"
            lines.append(f"{mon_emoji} `{p['exp']}` — *{p['cli'] or p['proy']}*")
            if p['proy']: lines.append(f"  _{p['proy'][:45]}_")
            lines.append(f"  Presup: {p['presup']} | Cobrado: {p['cobrado']}")
            lines.append(f"  *Saldo: {p['saldo']}* ({p['moneda']})")
        if total > 20:
            lines.append(f"\n_...y {total-20} más_")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error cobrar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")


async def cmd_buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Búsqueda inteligente: acepta expediente, cliente o proyecto.
    Cruza 001 Clientes + 004 Presupuestos + 003 Transacciones para completar info faltante."""
    if not ctx.args:
        await update.message.reply_text(
            "🔍 *Buscar*\n\n"
            "Uso: `/buscar [término]`\n\n"
            "Podés buscar por:\n"
            "• Expediente: `/buscar F25031`\n"
            "• Cliente: `/buscar SBMT`\n"
            "• Proyecto: `/buscar Campana`\n\n"
            "El bot busca en clientes, presupuestos y transacciones automáticamente.",
            parse_mode="Markdown"
        )
        return

    query = " ".join(ctx.args).lower().strip()
    msg   = await update.message.reply_text(f"🔍 Buscando `{query}`...", parse_mode="Markdown")

    try:
        resultado = {}

        # ── 1. Buscar en 001 Clientes ──────────────────────────────
        try:
            ws_cli  = get_worksheet("001 Clientes")
            cli_data = ws_cli.get_all_values()
            for row in cli_data[1:]:
                if any(query in cell.lower() for cell in row if cell):
                    resultado["cliente_id"]     = row[0].strip() if len(row) > 0 else ""
                    resultado["cliente_nombre"]  = row[1].strip() if len(row) > 1 else ""
                    resultado["cliente_rep"]     = row[2].strip() if len(row) > 2 else ""
                    break
        except Exception as e:
            log.warning(f"Clientes lookup error: {e}")

        # ── 2. Buscar en 004 Presupuestos ──────────────────────────
        presupuestos_match = []
        try:
            ws_presup  = get_worksheet("004 Presupuestos")
            presup_data = ws_presup.get_all_values()
            if presup_data:
                ph = presup_data[0]
                def pc(name):
                    try: return ph.index(name)
                    except: return -1
                p_exp    = pc("Expediente")
                p_cli    = pc("Cliente")
                p_proy   = pc("Proyecto")
                p_srv    = pc("Servicio")
                p_estado = pc("Estado cobro")
                p_monto  = pc("Monto")
                p_saldo  = pc("Saldo")
                for row in presup_data[1:]:
                    if any(query in cell.lower() for cell in row if cell):
                        presupuestos_match.append({
                            "exp":    row[p_exp].strip()    if p_exp    >= 0 and p_exp    < len(row) else "",
                            "cli":    row[p_cli].strip()    if p_cli    >= 0 and p_cli    < len(row) else "",
                            "proy":   row[p_proy].strip()   if p_proy   >= 0 and p_proy   < len(row) else "",
                            "srv":    row[p_srv].strip()    if p_srv    >= 0 and p_srv    < len(row) else "",
                            "estado": row[p_estado].strip() if p_estado >= 0 and p_estado < len(row) else "",
                            "monto":  row[p_monto].strip()  if p_monto  >= 0 and p_monto  < len(row) else "",
                            "saldo":  row[p_saldo].strip()  if p_saldo  >= 0 and p_saldo  < len(row) else "",
                        })
        except Exception as e:
            log.warning(f"Presupuestos lookup error: {e}")

        # Si encontramos cliente pero no presupuestos, buscar con el nombre del cliente
        if resultado.get("cliente_nombre") and not presupuestos_match:
            nombre = resultado["cliente_nombre"].lower()
            try:
                for row in presup_data[1:]:
                    if any(nombre in cell.lower() for cell in row if cell):
                        presupuestos_match.append({
                            "exp":    row[p_exp].strip()    if p_exp    >= 0 and p_exp    < len(row) else "",
                            "cli":    row[p_cli].strip()    if p_cli    >= 0 and p_cli    < len(row) else "",
                            "proy":   row[p_proy].strip()   if p_proy   >= 0 and p_proy   < len(row) else "",
                            "srv":    row[p_srv].strip()    if p_srv    >= 0 and p_srv    < len(row) else "",
                            "estado": row[p_estado].strip() if p_estado >= 0 and p_estado < len(row) else "",
                            "monto":  row[p_monto].strip()  if p_monto  >= 0 and p_monto  < len(row) else "",
                            "saldo":  row[p_saldo].strip()  if p_saldo  >= 0 and p_saldo  < len(row) else "",
                        })
            except:
                pass

        # ── 3. Buscar en 003 Transacciones ─────────────────────────
        tx_match = []
        try:
            ws_tx   = get_worksheet("003 Transacciones")
            tx_data = ws_tx.get_all_values()
            if tx_data:
                th = tx_data[0]
                def tc(name):
                    try: return th.index(name)
                    except: return -1
                t_fecha = tc("Fecha")
                t_tipo  = tc("Transacción")
                t_cli   = tc("Cliente")
                t_prv   = tc("Proveedor")
                t_exp   = tc("Expediente")
                t_imp   = tc("Importe USD")

                # Si tenemos expediente de presupuestos, buscar también por eso
                exp_ids = set(p["exp"].lower() for p in presupuestos_match if p["exp"])
                cli_nombre = resultado.get("cliente_nombre","").lower()

                for row in tx_data[1:]:
                    row_text = " ".join(cell.lower() for cell in row if cell)
                    if query in row_text or any(e in row_text for e in exp_ids) or (cli_nombre and cli_nombre in row_text):
                        tx_match.append(row)
        except Exception as e:
            log.warning(f"Transacciones lookup error: {e}")

        # ── Armar respuesta ─────────────────────────────────────────
        if not resultado and not presupuestos_match and not tx_match:
            await msg.edit_text(f"📭 Sin resultados para `{query}`.\nProbá con otro término.", parse_mode="Markdown")
            return

        lines = [f"🔍 *Resultados para '{query}'*\n"]

        # Cliente encontrado
        if resultado.get("cliente_nombre"):
            lines.append(f"👤 *Cliente:* {resultado['cliente_id']} — {resultado['cliente_nombre']}")
            if resultado.get("cliente_rep"):
                lines.append(f"  Contacto: {resultado['cliente_rep']}")
            lines.append("")

        # Presupuestos
        if presupuestos_match:
            lines.append(f"📄 *Presupuestos ({len(presupuestos_match)}):*")
            for p in presupuestos_match[:8]:
                lines.append(f"• `{p['exp']}` — {p['proy'][:40]}")
                lines.append(f"  {p['srv']} | Estado: {p['estado']}")
                lines.append(f"  Monto: {p['monto']} | Saldo: {p['saldo']}")
            if len(presupuestos_match) > 8:
                lines.append(f"  _...y {len(presupuestos_match)-8} más_")
            lines.append("")

        # Transacciones
        if tx_match:
            total_in = total_eg = 0.0
            for row in tx_match:
                tipo = row[t_tipo].strip() if t_tipo >= 0 and t_tipo < len(row) else ""
                try:
                    imp = float(str(row[t_imp]).replace(",",".").replace("U$D","").strip()) if t_imp >= 0 and t_imp < len(row) else 0
                    if tipo == "Ingreso": total_in += imp
                    elif tipo == "Egreso": total_eg += abs(imp)
                except: pass

            lines.append(f"💳 *Transacciones ({len(tx_match)}):*")
            lines.append(f"  💰 Ingresos: `U$D {total_in:,.2f}` | 💸 Egresos: `U$D {total_eg:,.2f}`")
            for row in tx_match[-10:]:  # últimas 10
                fecha = row[t_fecha].strip() if t_fecha >= 0 and t_fecha < len(row) else ""
                tipo  = row[t_tipo].strip()  if t_tipo  >= 0 and t_tipo  < len(row) else ""
                cli   = row[t_cli].strip()   if t_cli   >= 0 and t_cli   < len(row) else ""
                prv   = row[t_prv].strip()   if t_prv   >= 0 and t_prv   < len(row) else ""
                exp   = row[t_exp].strip()   if t_exp   >= 0 and t_exp   < len(row) else ""
                imp   = row[t_imp].strip()   if t_imp   >= 0 and t_imp   < len(row) else ""
                emoji = "💰" if tipo == "Ingreso" else "💸"
                entidad = cli or prv
                lines.append(f"{emoji} `{fecha}` `{exp}` {entidad} — `{imp}`")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")

    except Exception as e:
        log.error(f"Error buscar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Procesa fotos/comprobantes usando Claude Vision."""
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    msg = await update.message.reply_text("📸 Analizando comprobante con Claude Vision...")
    try:
        # Obtener la foto de mayor resolución
        photo   = update.message.photo[-1]
        file    = await ctx.bot.get_file(photo.file_id)
        img_url = file.file_path

        # Descargar la imagen
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(img_url)
            img_bytes  = resp.content
            img_base64 = __import__('base64').b64encode(img_bytes).decode()

        today  = datetime.now().strftime("%m/%d/%Y")
        presupuestos = get_presupuestos_pendientes()
        memoria      = get_memoria()
        presup_ctx   = ""
        if presupuestos:
            lines = "\n".join([
                f"  {p['expediente']} | {p['cliente']} | {p['proyecto']} | Estado: {p['estado']} | Saldo: {p['saldo']}"
                for p in presupuestos
            ])
            presup_ctx = f"\n\nPRESUPUESTOS PARA CONTEXTUALIZAR:\n{lines}"

        client_ai = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp_ai   = client_ai.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=SYSTEM_PROMPT + presup_ctx + memoria,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_base64,
                        }
                    },
                    {
                        "type": "text",
                        "text": f"Fecha de hoy: {today}\n\nAnalizá este comprobante (transferencia, ticket, factura, captura de banco) y extraé los datos de la transacción.\n\n" + USER_PROMPT.format(today=today, message="[ver imagen adjunta]")
                    }
                ]
            }]
        )
        raw    = resp_ai.content[0].text.strip().replace("```json","").replace("```","").strip()
        parsed = json.loads(raw)

        # Caption como descripción si hay
        if update.message.caption and not parsed.get("proyecto"):
            parsed["proyecto"] = update.message.caption

        ctx.user_data["pending"] = {"parsed": parsed, "original": "[comprobante foto]"}

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirmar y guardar", callback_data="confirm"),
             InlineKeyboardButton("❌ Cancelar",            callback_data="cancel")],
            [InlineKeyboardButton("✏️ Corregir",            callback_data="edit")]
        ])
        await msg.edit_text(
            "📸 *Comprobante leído*\n\n" + format_confirmation(parsed),
            parse_mode="Markdown",
            reply_markup=kb
        )
    except json.JSONDecodeError:
        await msg.edit_text(
            "❌ No pude leer los datos del comprobante.\n"
            "Probá con una foto más nítida o escribí la operación manualmente."
        )
    except Exception as e:
        log.error(f"Error foto: {e}")
        await msg.edit_text(f"❌ Error al procesar la imagen: `{e}`", parse_mode="Markdown")


def enriquecer_transaccion(parsed: dict) -> dict:
    """
    Busca en 001 Clientes y 004 Presupuestos para completar
    expediente, cliente y proyecto si faltan.
    Devuelve parsed enriquecido + lista de campos que completó.
    """
    completados = []
    tiene_exp    = bool(parsed.get("expediente","").strip())
    tiene_cli    = bool(parsed.get("cliente","").strip())
    tiene_proy   = bool(parsed.get("proyecto","").strip())

    # Cargar presupuestos una sola vez
    try:
        ws_presup  = get_worksheet("004 Presupuestos")
        presup_data = ws_presup.get_all_values()
        ph = presup_data[0] if presup_data else []
        def pc(name):
            try: return ph.index(name)
            except: return -1
        p_exp  = pc("Expediente")
        p_cli  = pc("Cliente")
        p_proy = pc("Proyecto")
        p_srv  = pc("Servicio")
        p_saldo= pc("Saldo")
        p_cobro= pc("Estado cobro")
    except Exception as e:
        log.warning(f"No se pudieron cargar presupuestos: {e}")
        presup_data = []

    # Cargar clientes
    try:
        ws_cli   = get_worksheet("001 Clientes")
        cli_data = ws_cli.get_all_values()
    except Exception as e:
        log.warning(f"No se pudieron cargar clientes: {e}")
        cli_data = []

    def buscar_presupuesto(campo, valor):
        """Busca filas en presupuestos donde campo contenga valor."""
        resultados = []
        for row in presup_data[1:]:
            col_idx = p_exp if campo == "exp" else p_cli if campo == "cli" else p_proy
            if col_idx >= 0 and col_idx < len(row):
                if valor.lower() in row[col_idx].lower():
                    resultados.append({
                        "expediente": row[p_exp].strip()  if p_exp  >= 0 and p_exp  < len(row) else "",
                        "cliente":    row[p_cli].strip()  if p_cli  >= 0 and p_cli  < len(row) else "",
                        "proyecto":   row[p_proy].strip() if p_proy >= 0 and p_proy < len(row) else "",
                        "servicio":   row[p_srv].strip()  if p_srv  >= 0 and p_srv  < len(row) else "",
                        "saldo":      row[p_saldo].strip()if p_saldo>= 0 and p_saldo< len(row) else "",
                        "estado":     row[p_cobro].strip()if p_cobro>= 0 and p_cobro< len(row) else "",
                    })
        return resultados

    # ── Si tiene expediente, buscar cliente y proyecto ────────────
    if tiene_exp and (not tiene_cli or not tiene_proy):
        matches = buscar_presupuesto("exp", parsed["expediente"])
        if matches:
            m = matches[0]
            if not tiene_cli and m["cliente"]:
                parsed["cliente"] = m["cliente"]
                completados.append(f"cliente: `{m['cliente']}`")
            if not tiene_proy and m["proyecto"]:
                parsed["proyecto"] = m["proyecto"]
                completados.append(f"proyecto: `{m['proyecto']}`")

    # ── Si tiene cliente, buscar expediente y proyecto ────────────
    elif tiene_cli and not tiene_exp:
        # Primero normalizar cliente buscando en 001 Clientes
        cli_raw  = parsed["cliente"].replace("CLI","").strip()
        cli_norm = ""
        for row in cli_data[1:]:
            if len(row) >= 2:
                if cli_raw.lower() in row[1].lower() or (len(row) > 0 and cli_raw.lower() in row[0].lower()):
                    cli_norm = row[1].strip()
                    break

        buscar_val = cli_norm or cli_raw
        matches = buscar_presupuesto("cli", buscar_val)

        if len(matches) == 1:
            # Solo un presupuesto → completar automáticamente
            m = matches[0]
            if not tiene_exp and m["expediente"]:
                parsed["expediente"] = m["expediente"]
                completados.append(f"expediente: `{m['expediente']}`")
            if not tiene_proy and m["proyecto"]:
                parsed["proyecto"] = m["proyecto"]
                completados.append(f"proyecto: `{m['proyecto']}`")
        elif len(matches) > 1:
            # Múltiples → guardar opciones para preguntar
            parsed["_opciones_presupuesto"] = matches

    # ── Si tiene proyecto, buscar expediente y cliente ────────────
    elif tiene_proy and not tiene_exp:
        matches = buscar_presupuesto("proy", parsed["proyecto"])
        if len(matches) == 1:
            m = matches[0]
            if not tiene_exp and m["expediente"]:
                parsed["expediente"] = m["expediente"]
                completados.append(f"expediente: `{m['expediente']}`")
            if not tiene_cli and m["cliente"]:
                parsed["cliente"] = m["cliente"]
                completados.append(f"cliente: `{m['cliente']}`")
        elif len(matches) > 1:
            parsed["_opciones_presupuesto"] = matches

    parsed["_completados"] = completados
    return parsed

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Handler principal: interpreta con Claude y pide confirmación."""
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("⛔ Sin acceso autorizado.")
        return

    text = update.message.text.strip()
    if not text or text.startswith("/"):
        return

    # Si el usuario está eligiendo entre opciones de presupuesto
    if ctx.user_data.get("awaiting_opcion"):
        ctx.user_data.pop("awaiting_opcion")
        pending  = ctx.user_data.get("pending", {})
        parsed   = pending.get("parsed", {})
        opciones = pending.get("opciones", [])
        original = pending.get("original", text)
        eleccion = text.strip()

        # Intentar por número
        opcion_elegida = None
        if eleccion.isdigit():
            idx = int(eleccion) - 1
            if 0 <= idx < len(opciones):
                opcion_elegida = opciones[idx]
        else:
            # Buscar por expediente o texto
            for op in opciones:
                if eleccion.lower() in op["expediente"].lower() or eleccion.lower() in op["proyecto"].lower():
                    opcion_elegida = op
                    break

        if not opcion_elegida and opciones:
            opcion_elegida = opciones[0]  # Fallback: primera opción

        if opcion_elegida:
            parsed["expediente"] = opcion_elegida["expediente"]
            parsed["cliente"]    = opcion_elegida["cliente"]
            parsed["proyecto"]   = opcion_elegida["proyecto"]

        ctx.user_data["pending"] = {"parsed": parsed, "original": original}
        confirmacion = (
            f"✅ _Expediente seleccionado: `{parsed.get('expediente','')}`_\n\n"
            + format_confirmation(parsed)
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirmar y guardar", callback_data="confirm"),
             InlineKeyboardButton("❌ Cancelar",            callback_data="cancel")],
            [InlineKeyboardButton("✏️ Corregir",            callback_data="edit")]
        ])
        await update.message.reply_text(confirmacion, parse_mode="Markdown", reply_markup=kb)
        return

    # Si hay valores de comprobante pendientes, combinarlos con este mensaje
    if ctx.user_data.get("valores_comprobante"):
        valores = ctx.user_data.pop("valores_comprobante")
        texto_completo = text + f" monto {valores.get('monto',0)} {valores.get('moneda','Pesos')} fecha {valores.get('fecha','')}"
        text = texto_completo

    # Si hay una corrección pendiente, guardala como aprendizaje
    if ctx.user_data.get("awaiting_correction"):
        prev = ctx.user_data.pop("awaiting_correction")
        guardar_aprendizaje("corrección", prev["original"], text)
        log.info(f"Aprendizaje guardado: '{prev['original']}' → '{text}'")

    msg = await update.message.reply_text("🤖 Analizando con Claude IA...")

    try:
        parsed = parse_with_claude(text)
    except json.JSONDecodeError as e:
        log.error(f"JSON error: {e}")
        await msg.edit_text(
            "❌ No pude interpretar ese mensaje.\n\n"
            "*Probá algo como:*\n"
            "`ingreso 500 usd SBMT anticipo F25031`\n"
            "`egreso 85000 pesos salario Federico`",
            parse_mode="Markdown"
        )
        return
    except Exception as e:
        log.error(f"Claude error: {e}")
        await msg.edit_text(f"❌ Error con Claude IA:\n`{e}`", parse_mode="Markdown")
        return

    # ── Enriquecer: buscar campos faltantes en clientes y presupuestos ──
    await msg.edit_text("🔍 Buscando expediente y cliente en la planilla...")
    parsed = enriquecer_transaccion(parsed)

    opciones = parsed.pop("_opciones_presupuesto", None)
    completados = parsed.pop("_completados", [])

    # ── Si hay múltiples presupuestos para el cliente → preguntar cuál ──
    if opciones:
        ctx.user_data["pending"] = {"parsed": parsed, "original": text, "opciones": opciones}
        lines = ["❓ *¿A qué proyecto corresponde?*\n"]
        for i, op in enumerate(opciones[:8]):
            saldo_info = f" | Saldo: {op['saldo']}" if op.get("saldo") else ""
            lines.append(f"`{i+1}.` `{op['expediente']}` — {op['proyecto'][:40]}{saldo_info}")
        lines.append("\nRespondé con el número o escribí el expediente directamente.")
        ctx.user_data["awaiting_opcion"] = True
        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        return

    # ── Guardar y mostrar confirmación con campos completados ──────────
    ctx.user_data["pending"] = {"parsed": parsed, "original": text}

    confirmacion = format_confirmation(parsed)
    if completados:
        confirmacion = f"🔎 _Completé automáticamente: {', '.join(completados)}_\n\n" + confirmacion

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirmar y guardar", callback_data="confirm"),
            InlineKeyboardButton("❌ Cancelar",            callback_data="cancel"),
        ],
        [
            InlineKeyboardButton("✏️ Corregir",            callback_data="edit"),
        ]
    ])

    await msg.edit_text(confirmacion, parse_mode="Markdown", reply_markup=kb)

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Maneja los botones de confirmación/cancelación."""
    query  = update.callback_query
    action = query.data
    await query.answer()

    pending = ctx.user_data.get("pending")
    if not pending:
        await query.edit_message_text("⚠️ No hay operación pendiente. Mandá un nuevo mensaje.")
        return

    parsed   = pending["parsed"]
    original = pending["original"]

    if action == "confirm":
        await query.edit_message_text("⏳ Guardando en Google Sheets...")
        try:
            row_num = await append_to_sheet(parsed)
            es_in   = parsed["tipo"] == "Ingreso"
            emoji   = "💰" if es_in else "💸"
            imp     = float(parsed.get("importe", 0))
            sym     = {"Pesos": "$", "Dolares": "U$D", "Euros": "€"}.get(parsed.get("moneda", "Pesos"), "$")
            sign    = "+" if imp >= 0 else ""
            destino = parsed.get("cuenta_destino", "")
            entidad = parsed.get("cliente", "") or parsed.get("proveedor", "")

            success = (
                f"{emoji} *¡Guardado en fila {row_num}!*\n\n"
                f"`{sign}{sym} {abs(imp):,.0f}` → {destino}\n"
            )
            if entidad:
                success += f"_{entidad}_\n"
            if parsed.get("expediente"):
                success += f"📁 `{parsed['expediente']}`\n"
            success += f"\n✅ Registrado en `003 Transacciones`"

            await query.edit_message_text(success, parse_mode="Markdown")
            ctx.user_data.pop("pending", None)

        except Exception as e:
            log.error(f"Error guardando: {e}")
            await query.edit_message_text(
                f"❌ Error al guardar en la planilla:\n`{e}`\n\n"
                "_Verificá que el bot tenga permiso de *Editor* en la planilla._",
                parse_mode="Markdown"
            )

    elif action == "cancel":
        await query.edit_message_text(
            "❌ Operación cancelada.\n_Mandá un nuevo mensaje cuando quieras._",
            parse_mode="Markdown"
        )
        ctx.user_data.pop("pending", None)

    elif action == "edit":
        ctx.user_data["awaiting_correction"] = {"original": original, "parsed": parsed}
        await query.edit_message_text(
            f"✏️ *Corrección*\n\n"
            f"_Original:_ `{original}`\n\n"
            "Mandame el mensaje corregido. El bot va a aprender de esta corrección automáticamente.",
            parse_mode="Markdown"
        )
        ctx.user_data.pop("pending", None)

# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

def main():
    for val, name in [
        (TELEGRAM_TOKEN,    "TELEGRAM_TOKEN"),
        (ANTHROPIC_API_KEY, "ANTHROPIC_API_KEY"),
        (GOOGLE_SHEET_ID,   "GOOGLE_SHEET_ID"),
    ]:
        if not val:
            raise ValueError(f"❌ Falta {name} en el archivo .env")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("ayuda",   cmd_ayuda))
    app.add_handler(CommandHandler("cuentas", cmd_cuentas))
    app.add_handler(CommandHandler("resumen", cmd_resumen))
    app.add_handler(CommandHandler("aprender", cmd_aprender))
    app.add_handler(CommandHandler("memoria",  cmd_memoria))
    app.add_handler(CommandHandler("cobrar",   cmd_cobrar))
    app.add_handler(CommandHandler("buscar",   cmd_buscar))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("🤖 Sky Ingeniería Bot iniciado — esperando mensajes...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
