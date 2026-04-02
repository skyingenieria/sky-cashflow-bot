"""
Bot de Telegram — Sky Ingeniería Cashflow
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Usa Claude IA para interpretar mensajes y registrarlos en
Google Sheets (hoja: 003 Transacciones) con la estructura exacta
de SKY-FNN-DOC-001-Cash Flow_BETA.

CAMBIOS v2:
- Fuzzy matching de clientes y proyectos (difflib)
- Búsqueda de expediente lee TODOS los presupuestos (no solo 100)
- Prioridad: saldo pendiente > más reciente
- 2950-IVA Facturado como cuenta destino
- Split automático de IVA para Factura A en CC Galicia
- Columnas de Transacciones actualizadas (Proveedor=H, Cliente=I, Expediente=J, Proyecto/Desc=K)
- Clientes leídos dinámicamente desde 001 Clientes (no hardcodeados)
"""

import os, json, logging
from datetime import datetime, date
from difflib import SequenceMatcher
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
import anthropic
import httpx
import gspread
from google.oauth2.service_account import Credentials

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
# CUENTAS
# ─────────────────────────────────────────────────────────

CUENTAS_ORIGEN = {
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
    "2350": "2350-DE Local Comercial",
    "2400": "2400-DE Reformas",
    "2500": "2500-Informe Técnico",
    "2600": "2600-Visita a Obra",
    "2900": "2900-Otros ingresos",
    "2950": "2950-IVA Facturado",   # IVA discriminado en Factura A
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
# GOOGLE SHEETS
# ─────────────────────────────────────────────────────────

def get_gc():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_json = os.getenv("GOOGLE_CREDS_JSON")
    if creds_json:
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    return gspread.authorize(creds)

def get_worksheet(sheet_name="003 Transacciones"):
    gc = get_gc()
    sh = gc.open_by_key(GOOGLE_SHEET_ID)
    return sh.worksheet(sheet_name)

def excel_date(dt: date) -> int:
    return (dt - date(1899, 12, 30)).days

# ─────────────────────────────────────────────────────────
# FUZZY MATCHING
# ─────────────────────────────────────────────────────────

def similarity(a: str, b: str) -> float:
    """Ratio de similitud entre dos strings, case-insensitive."""
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

def token_score(query_tokens: list[str], target: str) -> float:
    """
    Calcula el mejor score entre los tokens del query y el target.
    Estrategia multi-nivel:
    1. Si algún token del query es substring del target → 0.95
    2. Si algún token del target es substring de algún token del query → 0.90
    3. Mejor score de similitud token-a-token → valor numérico
    """
    t_lower = target.lower()
    t_tokens = t_lower.split()
    best = 0.0

    for q_tok in query_tokens:
        if len(q_tok) < 3:          # ignorar tokens muy cortos (preposiciones, etc.)
            continue
        # substring directo: "canevari" en "lucas canevari"
        if q_tok in t_lower:
            return 0.95
        # token del target dentro del query token
        for t_tok in t_tokens:
            if len(t_tok) >= 3 and t_tok in q_tok:
                best = max(best, 0.90)
        # similitud fuzzy token a token
        for t_tok in t_tokens:
            best = max(best, similarity(q_tok, t_tok))

    return best

def fuzzy_find_cliente(query: str, clientes: list[dict], threshold: float = 0.72) -> dict | None:
    """
    Busca el cliente más parecido al query usando matching por tokens.
    Funciona bien con queries parciales como "canevari" → "Lucas Canevari".
    """
    # Normalizar y tokenizar el query (ignorar palabras vacías comunes)
    STOPWORDS = {"de", "del", "la", "el", "los", "las", "y", "e", "o", "un", "una",
                 "ingreso", "egreso", "pago", "cobro", "proyecto", "obra", "civil",
                 "cc", "ca", "galicia", "transferencia", "efectivo", "anticipo", "saldo"}
    q_tokens = [t for t in query.lower().split() if t not in STOPWORDS and len(t) >= 3]

    if not q_tokens:
        return None

    best_score = 0.0
    best = None

    for c in clientes:
        targets = [
            c.get("nombre", ""),
            c.get("representante", ""),
        ]
        for t in targets:
            if not t:
                continue
            score = token_score(q_tokens, t)
            if score > best_score:
                best_score = score
                best = c

    return best if best_score >= threshold else None


def fuzzy_find_top_clientes(query: str, clientes: list[dict], n: int = 5) -> list[tuple[float, dict]]:
    """Retorna los top N clientes con sus scores, sin umbral mínimo."""
    STOPWORDS = {"de", "del", "la", "el", "los", "las", "y", "e", "o", "un", "una",
                 "ingreso", "egreso", "pago", "cobro", "proyecto", "obra", "civil",
                 "cc", "ca", "galicia", "transferencia", "efectivo", "anticipo", "saldo"}
    q_tokens = [t for t in query.lower().split() if t not in STOPWORDS and len(t) >= 3]
    if not q_tokens:
        return []
    scored = []
    for c in clientes:
        targets = [c.get("nombre", ""), c.get("representante", "")]
        best = max((token_score(q_tokens, t) for t in targets if t), default=0.0)
        if best > 0:
            scored.append((best, c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:n]


def find_best_client_from_message(message: str, n: int = 5) -> tuple[float, list[dict]]:
    """
    Escanea todo el mensaje y retorna (mejor_score, top_N_candidatos).
    Prueba ventanas de 1, 2 y 3 tokens para encontrar el mejor match.
    """
    clientes = get_clientes_from_sheet()
    tokens = message_to_tokens(message)
    if not tokens:
        return 0.0, []

    candidate_scores: dict[str, tuple[float, dict]] = {}
    for i in range(len(tokens)):
        for window in [tokens[i:i+1], tokens[i:i+2], tokens[i:i+3]]:
            if not window:
                continue
            query = " ".join(window)
            for score, c in fuzzy_find_top_clientes(query, clientes, n=n):
                cid = c["id"]
                if cid not in candidate_scores or score > candidate_scores[cid][0]:
                    candidate_scores[cid] = (score, c)

    if not candidate_scores:
        return 0.0, []

    sorted_cands = sorted(candidate_scores.values(), key=lambda x: x[0], reverse=True)
    best_score = sorted_cands[0][0]
    top_clients = [c for _, c in sorted_cands[:n]]
    return best_score, top_clients


def fuzzy_find_proyecto(cliente_nombre: str, proyecto_tokens: list[str], presupuestos: list[dict], threshold: float = 0.72) -> dict | None:
    """
    Busca el presupuesto más parecido filtrando primero por cliente (token matching),
    luego por similitud con el nombre del proyecto.
    Prioriza: saldo pendiente > match más alto > expediente más reciente.
    proyecto_tokens: lista de tokens ya limpiados del mensaje del usuario.
    """
    # Filtrar candidatos por cliente usando token matching
    cli_tokens = [t for t in cliente_nombre.lower().split() if len(t) >= 3]
    candidatos = []
    for p in presupuestos:
        cli_score = token_score(cli_tokens, p.get("cliente", ""))
        if cli_score >= 0.72:
            candidatos.append(p)

    if not candidatos:
        candidatos = presupuestos  # fallback: buscar en todos

    # Scorer por proyecto
    scored = []
    for p in candidatos:
        proy_score = token_score(proyecto_tokens, p.get("proyecto", ""))
        if proy_score >= threshold:
            scored.append((proy_score, p))

    if not scored:
        return None

    def sort_key(item):
        score, p = item
        try:
            saldo = float(str(p.get("saldo", "0")).replace(",", ".").replace("$", "").strip() or 0)
        except:
            saldo = 0
        return (1 if saldo > 0 else 0, score)

    scored.sort(key=sort_key, reverse=True)
    return scored[0][1]

# ─────────────────────────────────────────────────────────
# CLIENTES — lectura dinámica desde 001 Clientes
# ─────────────────────────────────────────────────────────

_clientes_cache: list[dict] = []
_clientes_cache_ts: datetime | None = None
CLIENTES_CACHE_TTL = 300  # 5 minutos

def get_clientes_from_sheet() -> list[dict]:
    """
    Lee 001 Clientes y devuelve lista de dicts.
    Columnas actualizadas: A=ID, B=Estudio/Cliente, C=Representante,
    D=Contacto, E=WhatsApp Link, F=Estado, G=Fecha Alta, H=Fuente,
    I=Cant.Proy, J=LTV, K=Satisfacción, L=Ref
    """
    global _clientes_cache, _clientes_cache_ts
    now = datetime.now()
    if _clientes_cache and _clientes_cache_ts and (now - _clientes_cache_ts).seconds < CLIENTES_CACHE_TTL:
        return _clientes_cache

    try:
        ws = get_worksheet("001 Clientes")
        data = ws.get_all_values()
        if len(data) < 2:
            return []
        result = []
        for row in data[1:]:
            if len(row) < 2 or not row[0] or not row[1]:
                continue
            cid   = row[0].strip()
            nombre= row[1].strip()
            rep   = row[2].strip() if len(row) > 2 else ""
            estado= row[5].strip() if len(row) > 5 else "Activo"
            ref   = row[11].strip() if len(row) > 11 else f"{cid} - {nombre}"
            if not nombre or nombre in ("Estudio/Cliente", "SKY"):
                continue
            result.append({
                "id": cid,
                "nombre": nombre,
                "representante": rep,
                "estado": estado,
                "ref": ref,
            })
        _clientes_cache = result
        _clientes_cache_ts = now
        log.info(f"Clientes cargados desde sheet: {len(result)}")
        return result
    except Exception as e:
        log.error(f"Error leyendo clientes: {e}")
        return _clientes_cache  # devolver cache vieja si falla

def clientes_as_text(clientes: list[dict]) -> str:
    """Formatea la lista de clientes para incluir en el prompt de Claude."""
    lines = []
    for c in clientes:
        estado = c.get("estado", "")
        if estado in ("Activo", "Prospecto", ""):
            lines.append(f'{c["id"]}={c["nombre"]}')
    return ", ".join(lines)

# ─────────────────────────────────────────────────────────
# PRESUPUESTOS — lectura completa con fuzzy
# ─────────────────────────────────────────────────────────

_presupuestos_cache: list[dict] = []
_presupuestos_cache_ts: datetime | None = None
PRESUPUESTOS_CACHE_TTL = 300

def get_presupuestos_from_sheet() -> list[dict]:
    """Lee TODOS los presupuestos de 004 Presupuestos."""
    global _presupuestos_cache, _presupuestos_cache_ts
    now = datetime.now()
    if _presupuestos_cache and _presupuestos_cache_ts and (now - _presupuestos_cache_ts).seconds < PRESUPUESTOS_CACHE_TTL:
        return _presupuestos_cache

    try:
        ws = get_worksheet("004 Presupuestos")
        data = ws.get_all_values()
        if len(data) < 2:
            return []
        headers = data[0]

        def col(name):
            try: return headers.index(name)
            except: return -1

        c_exp   = col("Expediente")
        c_cli   = col("Cliente")
        c_proy  = col("Proyecto")
        c_srv   = col("Servicio")
        c_estado= col("Estado cobro")
        c_monto = col("Monto")
        c_saldo = col("Saldo")
        c_aux   = col("Aux")

        result = []
        for row in data[1:]:
            exp = row[c_exp].strip() if c_exp >= 0 and c_exp < len(row) else ""
            if not exp:
                continue
            result.append({
                "expediente": exp,
                "cliente":    row[c_cli].strip()   if c_cli >= 0   and c_cli < len(row)   else "",
                "proyecto":   row[c_proy].strip()  if c_proy >= 0  and c_proy < len(row)  else "",
                "servicio":   row[c_srv].strip()   if c_srv >= 0   and c_srv < len(row)   else "",
                "estado":     row[c_estado].strip()if c_estado >= 0 and c_estado < len(row) else "",
                "monto":      row[c_monto].strip() if c_monto >= 0 and c_monto < len(row) else "",
                "saldo":      row[c_saldo].strip() if c_saldo >= 0 and c_saldo < len(row) else "",
                "aux":        row[c_aux].strip()   if c_aux >= 0   and c_aux < len(row)   else "",
            })

        _presupuestos_cache = result
        _presupuestos_cache_ts = now
        log.info(f"Presupuestos cargados: {len(result)}")
        return result
    except Exception as e:
        log.error(f"Error leyendo presupuestos: {e}")
        return _presupuestos_cache

STOPWORDS_BOT = {
    "de", "del", "la", "el", "los", "las", "y", "e", "o", "un", "una",
    "ingreso", "egreso", "pago", "cobro", "proyecto", "obra", "civil",
    "cc", "ca", "galicia", "transferencia", "efectivo", "anticipo", "saldo",
    "pesos", "dolares", "usd", "factura", "sin", "con", "por", "para",
    "cobre", "pague", "gaste", "me", "le", "al",
    "cliente", "proveedor", "monto", "importe", "fecha", "tipo",
}

def message_to_tokens(message: str) -> list:
    import re
    words = re.split(r"[\s.,;:!?/\-]+", message.lower())
    return [w for w in words if len(w) >= 3 and w not in STOPWORDS_BOT]

def find_expediente(mensaje_completo: str) -> dict | None:
    """
    Recibe el mensaje completo y encuentra el mejor expediente.
    1. Tokeniza el mensaje
    2. Prueba cada token como posible cliente
    3. Con el cliente encontrado, busca el proyecto con los demás tokens
    4. Fallback: presupuesto con mayor saldo de ese cliente
    """
    clientes     = get_clientes_from_sheet()
    presupuestos = get_presupuestos_from_sheet()

    tokens = message_to_tokens(mensaje_completo)
    if not tokens:
        return None

    # Paso 1: mejor cliente probando ventanas de 1, 2 y 3 tokens
    best_cli_score = 0.0
    best_cli = None

    for i in range(len(tokens)):
        for window in [tokens[i:i+1], tokens[i:i+2], tokens[i:i+3]]:
            if not window:
                continue
            query = " ".join(window)
            match = fuzzy_find_cliente(query, clientes, threshold=0.72)
            if match:
                score = token_score(window, match["nombre"])
                if score > best_cli_score:
                    best_cli_score = score
                    best_cli = match

    cliente_nombre = best_cli["nombre"] if best_cli else ""

    # Paso 2: buscar proyecto con todos los tokens del mensaje
    proy_tokens = [t for t in tokens if len(t) >= 3]
    presup = None
    if proy_tokens:
        presup = fuzzy_find_proyecto(cliente_nombre or mensaje_completo, proy_tokens, presupuestos)

    # Paso 3: fallback al mas reciente con saldo del cliente
    if not presup and best_cli:
        cli_tokens = [t for t in best_cli["nombre"].lower().split() if len(t) >= 3]
        candidatos = [
            p for p in presupuestos
            if token_score(cli_tokens, p.get("cliente", "")) >= 0.72
        ]
        def saldo_num(p):
            try:
                return float(str(p.get("saldo", "0")).replace(",", ".").replace("$", "").strip() or 0)
            except:
                return 0
        candidatos.sort(key=lambda p: (saldo_num(p), p["expediente"]), reverse=True)
        presup = candidatos[0] if candidatos else None

    return presup


# ─────────────────────────────────────────────────────────
# CONSTRUCCIÓN DE FILA — columnas actualizadas v2
# ─────────────────────────────────────────────────────────
# Estructura actual de 003 Transacciones:
# A=Fecha | B=Transacción | C=Cuenta Origen | D=Cuenta Destino | E=Moneda |
# F=Importe | G=Factura | H=Proveedor | I=Cliente | J=Expediente |
# K=Proyecto/Descripción (fórmula VLOOKUP en ingresos, texto en egresos) |
# L=Dolar | M=EUR/USD | N=Importe USD | O=ID Fecha | P=Year | Q=Quarter | R=Month | S=YYYY-MM

def build_row(parsed: dict, dolar_blue: float = 1390.0) -> list:
    tx_date = datetime.strptime(parsed["fecha"], "%m/%d/%Y")
    moneda  = parsed.get("moneda", "Pesos")
    importe = float(parsed.get("importe", 0))
    tipo    = parsed.get("tipo", "Ingreso")
    expediente = parsed.get("expediente", "")

    if moneda == "Pesos":
        importe_usd = importe / dolar_blue
    elif moneda == "Dolares":
        importe_usd = importe
    else:
        importe_usd = importe

    year    = tx_date.year
    month   = tx_date.month
    quarter = (month - 1) // 3 + 1
    yyyy_mm = f"{year}-{month}"

    # Columna K: fórmula VLOOKUP si es ingreso con expediente, texto si es egreso
    if tipo == "Ingreso" and expediente:
        # Buscar en qué fila se va a insertar para armar la fórmula
        # Usamos placeholder; se reemplaza en append_to_sheet
        col_k = f'=IFERROR(VLOOKUP(J{{ROW}},"\'004 Presupuestos\'!D:G",4,0),"")'
    else:
        col_k = parsed.get("proyecto", "")

    return [
        parsed["fecha"],                                    # A Fecha
        tipo,                                               # B Transacción
        parsed.get("cuenta_origen", "1100-Mostrador AR$"), # C Cuenta Origen
        parsed.get("cuenta_destino", ""),                   # D Cuenta Destino
        moneda,                                             # E Moneda
        importe,                                            # F Importe
        parsed.get("factura", "S/Factura"),                 # G Factura
        parsed.get("proveedor", ""),                        # H Proveedor
        parsed.get("cliente", ""),                          # I Cliente
        expediente,                                         # J Expediente
        col_k,                                              # K Proyecto/Descripción
        dolar_blue,                                         # L Dolar
        1.0,                                                # M EUR/USD
        round(importe_usd, 4),                              # N Importe USD
        excel_date(tx_date.date()),                         # O ID Fecha
        year,                                               # P Year
        quarter,                                            # Q Quarter
        month,                                              # R Month
        yyyy_mm,                                            # S YYYY-MM
    ]

async def append_to_sheet(parsed: dict) -> int:
    """Inserta la fila (o dos filas si hay split IVA) y devuelve el nro de fila."""
    dolar = await get_dolar_blue()
    ws    = get_worksheet()

    rows_to_insert = [parsed]

    # ── Split IVA ────────────────────────────────────────────
    # Si es ingreso por CC Galicia con Factura A → separar honorarios + IVA
    cuenta_origen  = parsed.get("cuenta_origen", "")
    factura        = parsed.get("factura", "")
    es_cc_galicia  = "1600" in cuenta_origen
    es_factura_a   = factura == "Factura A"

    if parsed.get("tipo") == "Ingreso" and es_cc_galicia and es_factura_a:
        importe_bruto = float(parsed.get("importe", 0))
        honorarios    = round(importe_bruto / 1.21, 2)
        iva           = round(importe_bruto - honorarios, 2)

        # Fila 1: honorarios netos
        fila_honorarios = dict(parsed)
        fila_honorarios["importe"] = honorarios

        # Fila 2: IVA
        fila_iva = dict(parsed)
        fila_iva["importe"]        = iva
        fila_iva["cuenta_destino"] = "2950-IVA Facturado"
        fila_iva["cliente"]        = ""
        fila_iva["proveedor"]      = "PRV007 - ARCA / ARBA"
        fila_iva["expediente"]     = ""
        fila_iva["proyecto"]       = f"IVA s/Factura A - {parsed.get('expediente','')}"

        rows_to_insert = [fila_honorarios, fila_iva]

    # ── Insertar filas ───────────────────────────────────────
    last_row = len(ws.col_values(1))
    for i, row_data in enumerate(rows_to_insert):
        row = build_row(row_data, dolar_blue=dolar)
        # Reemplazar placeholder {ROW} en fórmula VLOOKUP
        next_row = last_row + 1 + i
        row[10] = row[10].replace("{ROW}", str(next_row)) if isinstance(row[10], str) else row[10]
        ws.append_row(row, value_input_option="USER_ENTERED")

    return last_row + len(rows_to_insert)

# ─────────────────────────────────────────────────────────
# DÓLAR BLUE
# ─────────────────────────────────────────────────────────

async def get_dolar_blue() -> float:
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
# ─────────────────────────────────────────────────────────

def get_or_create_memoria_sheet():
    try:
        gc = get_gc()
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
    try:
        ws = get_or_create_memoria_sheet()
        if ws:
            ws.append_row([datetime.now().strftime("%m/%d/%Y"), tipo, original, corregido])
    except Exception as e:
        log.error(f"Error guardando aprendizaje: {e}")

def get_memoria() -> str:
    try:
        ws = get_or_create_memoria_sheet()
        if not ws:
            return ""
        data = ws.get_all_values()
        if len(data) < 2:
            return ""
        lines = "\n".join([f"  [{r[1]}] '{r[2]}' → '{r[3]}'" for r in data[1:] if len(r) >= 4])
        return f"\nMEMORIA DE APRENDIZAJE (correcciones anteriores):\n{lines}\n"
    except Exception as e:
        log.error(f"Error leyendo memoria: {e}")
        return ""

# ─────────────────────────────────────────────────────────
# CLAUDE — SYSTEM PROMPT
# ─────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Sos el asistente financiero de Sky Ingeniería, empresa de ingeniería estructural argentina.
Interpretás mensajes y extraés datos para registrar en la planilla de cashflow (hoja: 003 Transacciones).

ESTRUCTURA DE LA HOJA (19 columnas, en este orden):
1. Fecha — DD/MM/YYYY
2. Transacción — "Ingreso" o "Egreso"
3. Cuenta Origen — de dónde sale el dinero
4. Cuenta Destino — hacia dónde va
5. Moneda — "Pesos", "Dolares" o "Euros"
6. Importe — positivo para ingresos, NEGATIVO para egresos
7. Factura — "S/Factura", "Factura C" o "Factura A"
8. Proveedor — nombre del proveedor (solo egresos)
9. Cliente — nombre del cliente exactamente como lo mencionó el usuario (solo ingresos)
10. Expediente — dejar vacío siempre
11. Proyecto/Descripción — nombre del proyecto exactamente como lo mencionó el usuario
12-19. Calculados automáticamente

CUENTAS ORIGEN:
- 1100-Mostrador AR$ | 1200-Mostrador U$D | 1300-Mostrador EU
- 1400-C.A. Galicia AR$ | 1500-C.A. Galicia U$D
- 1600-C.C. Galicia AR$ | 1700-C.C. Galicia U$D
- 1800-C.A. Wise EU | 1900-C.A. Prex UY U$D

CUENTAS DESTINO INGRESOS:
- 2100-DE Vivienda Unifamiliar | 2200-DE Vivienda Multifamiliar
- 2300-DE Obras Civiles | 2350-DE Local Comercial | 2400-DE Reformas
- 2500-Informe Técnico | 2600-Visita a Obra | 2900-Otros ingresos
- 2950-IVA Facturado → SOLO para la porción IVA de una Factura A (el bot la separa automáticamente)

CUENTAS DESTINO EGRESOS:
- 3100-Salarios operativos | 4100-Salarios administrativos
- 4200-Infraestructura de software | 4300-Infraestructura física
- 4400-Marketing y publicidad | 4500-Formación | 4600-Contador
- 4700-Impuestos | 4800-Comisiones bancarias | 4900-Devoluciones
- 4950-Otros gastos | 5100-Dividendos pagados

REGLAS DE INFERENCIA:
- "cobré / ingreso / anticipo / saldo / me pagaron" → Ingreso
- "pagué / egreso / salario / sueldo / gasto" → Egreso
- Sin moneda + monto grande → Pesos
- Efectivo → Mostrador AR$ (pesos) o Mostrador U$D (dólares)
- Transferencia / banco sin aclarar → C.A. Galicia AR$
- "cc galicia" → 1600-C.C. Galicia AR$ | "ca galicia" → 1400-C.A. Galicia AR$
- Vivienda sin aclarar → 2100-DE Vivienda Unifamiliar
- Obra civil / industrial → 2300-DE Obras Civiles
- Importe NEGATIVO para egresos

IMPORTANTE — CLIENTE Y PROYECTO:
Usá exactamente el nombre que mencionó el usuario. No busques códigos ni hagas matching.
Si dice "Cliente: Aranda" → cliente = "Aranda". Si dice "Proyecto: San Sebastián L261" → proyecto = "San Sebastián L261".
Si no menciona cliente, dejá vacío. Si no menciona proyecto, dejá vacío.

IMPORTANTE — FACTURAS:
- "sin factura" / "s/factura" → "S/Factura"
- "factura c" / "con factura" / "c/factura" → "Factura C" (sin split de IVA)
- "factura a" → "Factura A" (CON split de IVA automático)

IMPORTANTE — IVA EN CC GALICIA (SOLO FACTURA A):
Si es un ingreso por 1600-C.C. Galicia AR$ con "Factura A", el bot automáticamente
va a generar dos filas: una por honorarios netos y otra por el IVA (2950-IVA Facturado).
Factura C NO genera split de IVA. Vos solo registrá el importe bruto total.

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
  "factura": "S/Factura", "Factura C" o "Factura A",
  "cliente": "nombre exacto del cliente o ''",
  "proveedor": "nombre exacto del proveedor o ''",
  "expediente": "",
  "proyecto": "nombre exacto del proyecto o ''",
  "confianza": 0-100,
  "dudas": "qué no quedó claro" o null
}}"""

# ─────────────────────────────────────────────────────────
# PARSE CON CLAUDE
# ─────────────────────────────────────────────────────────

def parse_with_claude(message: str) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    today  = datetime.now().strftime("%m/%d/%Y")
    memoria = get_memoria()

    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system=SYSTEM_PROMPT + "\n" + memoria,
        messages=[{
            "role": "user",
            "content": USER_PROMPT.format(
                today=today,
                message=message,
            )
        }]
    )

    raw = resp.content[0].text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(raw)

# ─────────────────────────────────────────────────────────
# FORMATO CONFIRMACIÓN
# ─────────────────────────────────────────────────────────

def format_confirmation(parsed: dict, es_split_iva: bool = False) -> str:
    es_in  = parsed["tipo"] == "Ingreso"
    emoji  = "💰" if es_in else "💸"
    imp    = float(parsed.get("importe", 0))
    sign   = "+" if imp >= 0 else ""
    sym    = {"Pesos": "$", "Dolares": "U$D", "Euros": "€"}.get(parsed.get("moneda", "Pesos"), "$")

    # Calcular si habrá split de IVA
    cuenta_origen = parsed.get("cuenta_origen", "")
    factura       = parsed.get("factura", "")
    split_iva = (
        es_in and
        "1600" in cuenta_origen and
        factura == "Factura A"
    )

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

    # Advertir del split de IVA
    if split_iva:
        honorarios = round(imp / 1.21, 0)
        iva_monto  = round(imp - honorarios, 0)
        lines += [
            "",
            f"⚡ _CC Galicia + Factura A detectados_",
            f"   _Se registrarán 2 filas:_",
            f"   _• {sym} {honorarios:,.0f} → honorarios netos_",
            f"   _• {sym} {iva_monto:,.0f} → 2950-IVA Facturado (PRV007-ARCA)_",
        ]

    conf = parsed.get("confianza", 100)
    if conf < 80:
        lines += ["", f"⚠️ _Confianza: {conf}% — revisá los datos_"]
        if parsed.get("dudas"):
            lines.append(f"❓ _Duda: {parsed['dudas']}_")

    lines += ["", "¿Lo registro así en la planilla?"]
    return "\n".join(lines)

def get_month_summary() -> dict:
    ws   = get_worksheet()
    data = ws.get_all_values()
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
            if tipo == "Ingreso":   total_in += val
            elif tipo == "Egreso":  total_eg += abs(val)
            count += 1
        except (ValueError, IndexError):
            continue

    return {
        "ingresos":  round(total_in, 2),
        "egresos":   round(total_eg, 2),
        "balance":   round(total_in - total_eg, 2),
        "tx_count":  count,
        "mes":       f"{now.month}/{now.year}",
    }

# ─────────────────────────────────────────────────────────
# HANDLERS DE TELEGRAM
# ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Sky Ingeniería — Bot de Cashflow v2*\n\n"
        "Mandame una operación y la registro en tu planilla.\n\n"
        "*Ejemplos:*\n"
        "• `ingreso cc galicia. canevari. teatro lujan. 270000. obra civil`\n"
        "• `cobré 1.200.000 pesos vivienda Leone Loray saldo`\n"
        "• `egreso salario Federico 800 dólares`\n"
        "• `pagué impuestos ARCA 85000 transferencia`\n\n"
        "*Comandos:*\n"
        "/resumen — Balance del mes\n"
        "/cobrar — Proyectos con saldo pendiente\n"
        "/buscar [término] — Buscar en planilla\n"
        "/cuentas — Plan de cuentas\n"
        "/ayuda — Ayuda completa",
        parse_mode="Markdown"
    )

async def cmd_ayuda(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Ayuda — Cómo registrar*\n\n"
        "Describí la operación de forma natural.\n"
        "El bot busca el expediente automáticamente en la planilla de presupuestos.\n\n"
        "*Palabras clave:*\n"
        "Ingresos: _cobré, ingreso, anticipo, saldo, me pagaron_\n"
        "Egresos: _pagué, egreso, salario, gasto, transferí_\n\n"
        "*IVA automático:*\n"
        "Si registrás un ingreso por CC Galicia con factura, el bot\n"
        "separa automáticamente honorarios + IVA en dos filas.\n\n"
        "/resumen — Totales del mes\n"
        "/cobrar — Proyectos pendientes\n"
        "/buscar [término] — Buscar\n"
        "/aprender [tipo] [original] [correcto] — Enseñarle al bot\n"
        "/memoria — Ver aprendizajes",
        parse_mode="Markdown"
    )

async def cmd_cuentas(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📊 *Plan de Cuentas*\n\n"
        "*ORIGEN (de dónde sale):*\n"
        "`1100` Mostrador AR$ · `1200` Mostrador U$D · `1300` Mostrador EU\n"
        "`1400` C.A. Galicia AR$ · `1500` C.A. Galicia U$D\n"
        "`1600` C.C. Galicia AR$ · `1700` C.C. Galicia U$D\n"
        "`1900` C.A. Prex UY U$D\n\n"
        "*INGRESOS (destino):*\n"
        "`2100` DE Vivienda Unifamiliar · `2200` Multifamiliar\n"
        "`2300` Obras Civiles · `2350` Local Comercial · `2400` Reformas\n"
        "`2500` Informe Técnico · `2600` Visita a Obra\n"
        "`2900` Otros ingresos · `2950` IVA Facturado\n\n"
        "*EGRESOS (destino):*\n"
        "`3100` Salarios operativos · `4100` Salarios admin\n"
        "`4200` Software · `4300` Infra física · `4400` Marketing\n"
        "`4500` Formación · `4600` Contador · `4700` Impuestos\n"
        "`4800` Comisiones · `4950` Otros gastos · `5100` Dividendos",
        parse_mode="Markdown"
    )

async def cmd_resumen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Consultando planilla...")
    try:
        s   = get_month_summary()
        bal = s["balance"]
        em  = "✅" if bal >= 0 else "🔴"
        await msg.edit_text(
            f"📊 *Resumen {s['mes']}*\n\n"
            f"💰 Ingresos: `U$D {s['ingresos']:>10,.2f}`\n"
            f"💸 Egresos:  `U$D {s['egresos']:>10,.2f}`\n"
            f"{'─' * 30}\n"
            f"{em} Balance: `U$D {bal:>10,.2f}`\n\n"
            f"_📋 {s['tx_count']} transacciones este mes_",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.error(f"Error resumen: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")

async def cmd_aprender(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if not args or len(args) < 3:
        await update.message.reply_text(
            "📚 *Cómo enseñarle al bot*\n\n"
            "Formato: `/aprender [tipo] [original] [correcto]`\n\n"
            "*Ejemplos:*\n"
            "`/aprender cliente canevari Lucas Canevari`\n"
            "`/aprender proyecto teatro teatro lujan`",
            parse_mode="Markdown"
        )
        return
    tipo     = args[0]
    original = args[1]
    correcto = " ".join(args[2:])
    guardar_aprendizaje(tipo, original, correcto)
    await update.message.reply_text(
        f"✅ *¡Aprendido!*\n`{original}` → `{correcto}`\n_Guardado en Bot_Memoria_",
        parse_mode="Markdown"
    )

async def cmd_memoria(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        ws   = get_or_create_memoria_sheet()
        data = ws.get_all_values() if ws else []
        if len(data) < 2:
            await update.message.reply_text("📭 Sin aprendizajes aún. Usá `/aprender`.")
            return
        lines = "\n".join([f"• [{r[1]}] `{r[2]}` → `{r[3]}`" for r in data[1:] if len(r) >= 4])
        await update.message.reply_text(
            f"📚 *Memoria del bot ({len(data)-1} aprendizajes)*\n\n{lines}",
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Error: {e}")

async def cmd_cobrar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Consultando proyectos por cobrar...")
    try:
        ws   = get_worksheet("005 Proyectos por cobrar")
        data = ws.get_all_values()
        if len(data) < 2:
            await msg.edit_text("📭 No hay datos en Proyectos por cobrar.")
            return

        pendientes = []
        for row in data[1:]:
            if len(row) < 5:
                continue
            moneda = row[0].strip()
            aux    = row[1].strip()
            saldo  = row[4].strip()
            if not aux or not saldo:
                continue
            try:
                saldo_num = float(saldo.replace("$","").replace(".","").replace(",",".").strip())
                if saldo_num <= 0:
                    continue
            except:
                continue
            partes = aux.split(" - ", 2)
            pendientes.append({
                "exp":    partes[0].strip() if len(partes) > 0 else "",
                "proy":   partes[1].strip() if len(partes) > 1 else "",
                "cli":    partes[2].strip() if len(partes) > 2 else "",
                "moneda": moneda,
                "presup": row[2].strip() if len(row) > 2 else "",
                "cobrado":row[3].strip() if len(row) > 3 else "",
                "saldo":  saldo,
            })

        if not pendientes:
            await msg.edit_text("✅ No hay proyectos con saldo pendiente.")
            return

        lines = [f"📋 *Proyectos por cobrar ({len(pendientes)})*\n"]
        for p in pendientes[:20]:
            mon_emoji = "💵" if "USD" in p["moneda"].upper() else "💰"
            lines.append(f"{mon_emoji} `{p['exp']}` — *{p['cli'] or p['proy']}*")
            if p['proy']: lines.append(f"  _{p['proy'][:45]}_")
            lines.append(f"  Presup: {p['presup']} | Cobrado: {p['cobrado']}")
            lines.append(f"  *Saldo: {p['saldo']}* ({p['moneda']})")
        if len(pendientes) > 20:
            lines.append(f"\n_...y {len(pendientes)-20} más_")

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error cobrar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")

async def cmd_buscar(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "🔍 *Buscar*\n\nUso: `/buscar [término]`\n"
            "Podés buscar por expediente, cliente o proyecto.",
            parse_mode="Markdown"
        )
        return

    query = " ".join(ctx.args).lower().strip()
    msg   = await update.message.reply_text(f"🔍 Buscando `{query}`...", parse_mode="Markdown")

    try:
        clientes     = get_clientes_from_sheet()
        presupuestos = get_presupuestos_from_sheet()

        # Buscar cliente
        cli_match = fuzzy_find_cliente(query, clientes, threshold=0.4)

        # Buscar presupuestos
        presup_matches = []
        for p in presupuestos:
            fields = [p["expediente"], p["cliente"], p["proyecto"], p["servicio"]]
            if any(query in f.lower() for f in fields):
                presup_matches.append(p)
        if not presup_matches and cli_match:
            nombre = cli_match["nombre"].lower()
            presup_matches = [
                p for p in presupuestos
                if nombre in p["cliente"].lower() or similarity(nombre, p["cliente"].lower()) >= 0.5
            ]

        lines = [f"🔍 *Resultados para '{query}'*\n"]

        if cli_match:
            lines.append(f"👤 *Cliente:* {cli_match['id']} — {cli_match['nombre']}")
            if cli_match.get("representante"):
                lines.append(f"  Contacto: {cli_match['representante']}")
            lines.append("")

        if presup_matches:
            lines.append(f"📄 *Presupuestos ({len(presup_matches)}):*")
            for p in presup_matches[:8]:
                lines.append(f"• `{p['expediente']}` — {p['proyecto'][:40]}")
                lines.append(f"  {p['servicio']} | Estado: {p['estado']}")
                lines.append(f"  Monto: {p['monto']} | Saldo: {p['saldo']}")
            if len(presup_matches) > 8:
                lines.append(f"  _...y {len(presup_matches)-8} más_")

        if not cli_match and not presup_matches:
            await msg.edit_text(f"📭 Sin resultados para `{query}`.", parse_mode="Markdown")
            return

        await msg.edit_text("\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        log.error(f"Error buscar: {e}")
        await msg.edit_text(f"❌ Error: `{e}`", parse_mode="Markdown")

# ─────────────────────────────────────────────────────────
# HANDLER PRINCIPAL DE MENSAJES
# ─────────────────────────────────────────────────────────

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    text = update.message.text.strip()
    msg  = await update.message.reply_text("🤔 Analizando...")

    try:
        parsed = parse_with_claude(text)
        conf_text = format_confirmation(parsed)

        ctx.user_data["pending"] = parsed
        ctx.user_data["original_message"] = text

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Registrar", callback_data="confirm"),
                InlineKeyboardButton("✏️ Corregir", callback_data="edit"),
                InlineKeyboardButton("❌ Cancelar", callback_data="cancel"),
            ]
        ])
        await msg.edit_text(conf_text, parse_mode="Markdown", reply_markup=keyboard)

    except Exception as e:
        log.error(f"Error procesando mensaje: {e}")
        await msg.edit_text(
            f"❌ No pude interpretar el mensaje.\n`{e}`\n\n"
            "_Intentá con más detalle: monto, cuenta, cliente, proyecto._",
            parse_mode="Markdown"
        )

async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        return

    caption = update.message.caption or ""
    msg     = await update.message.reply_text("📸 Leyendo valores del comprobante...")

    try:
        photo  = update.message.photo[-1]
        file   = await ctx.bot.get_file(photo.file_id)
        import base64
        async with httpx.AsyncClient() as client:
            resp    = await client.get(file.file_path)
            img_b64 = base64.b64encode(resp.content).decode()

        today      = datetime.now().strftime("%m/%d/%Y")
        client_ai  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp_ai    = client_ai.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": (
                        "Extraé ÚNICAMENTE los valores numéricos del comprobante. "
                        "Respondé SOLO con JSON:\n"
                        '{"monto": número, "fecha": "MM/DD/YYYY o ' + today + '", "moneda": "Pesos/Dolares/Euros"}'
                    )}
                ]
            }]
        )
        raw    = resp_ai.content[0].text.strip().replace("```json","").replace("```","").strip()
        valores = json.loads(raw)

        if caption:
            mensaje_completo = caption + f" monto {valores.get('monto',0)} {valores.get('moneda','Pesos')} fecha {valores.get('fecha', today)}"
            update.message.text = mensaje_completo
            await handle_message(update, ctx)
        else:
            await msg.edit_text(
                f"📸 *Valores leídos:*\n\n"
                f"💵 Monto: `{valores.get('monto', '?')}`\n"
                f"💱 Moneda: `{valores.get('moneda', '?')}`\n"
                f"📅 Fecha: `{valores.get('fecha', today)}`\n\n"
                "_Ahora mandame el detalle: cliente, proyecto, cuenta._",
                parse_mode="Markdown"
            )
            ctx.user_data["comprobante_valores"] = valores

    except Exception as e:
        log.error(f"Error foto: {e}")
        await msg.edit_text(f"❌ No pude leer el comprobante: `{e}`", parse_mode="Markdown")

async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query  = update.callback_query
    await query.answer()
    action = query.data
    parsed = ctx.user_data.get("pending")

    if action == "confirm" and parsed:
        loading = await query.edit_message_text("⏳ Registrando en planilla...")
        try:
            fila = await append_to_sheet(parsed)

            # Determinar si hubo split IVA para el mensaje de éxito
            cuenta_origen = parsed.get("cuenta_origen", "")
            factura       = parsed.get("factura", "")
            split_iva = (
                parsed.get("tipo") == "Ingreso" and
                "1600" in cuenta_origen and
                factura == "Factura A"
            )

            if split_iva:
                imp   = float(parsed.get("importe", 0))
                hon   = round(imp / 1.21, 0)
                iva_m = round(imp - hon, 0)
                await loading.edit_text(
                    f"✅ *Registrado — 2 filas (split IVA)*\n\n"
                    f"📁 Expediente: `{parsed.get('expediente','—')}`\n"
                    f"💰 Honorarios netos: `$ {hon:,.0f}`\n"
                    f"🧾 IVA Facturado:    `$ {iva_m:,.0f}`\n"
                    f"_Filas {fila-1} y {fila} en la planilla_",
                    parse_mode="Markdown"
                )
            else:
                await loading.edit_text(
                    f"✅ *Registrado en fila {fila}*\n\n"
                    f"📁 Expediente: `{parsed.get('expediente','—')}`\n"
                    f"👤 Cliente:    `{parsed.get('cliente','—')}`\n"
                    f"💵 Importe:    `{abs(float(parsed.get('importe',0))):,.0f}`",
                    parse_mode="Markdown"
                )
            ctx.user_data.pop("pending", None)

        except Exception as e:
            log.error(f"Error registrando: {e}")
            await loading.edit_text(f"❌ Error al registrar:\n`{e}`", parse_mode="Markdown")

    elif action == "edit":
        await query.edit_message_text(
            "✏️ Mandame el mensaje corregido y lo proceso de nuevo.\n\n"
            "_Tip: si el expediente no quedó bien, podés usar `/buscar [cliente]` para encontrarlo._",
            parse_mode="Markdown"
        )
        ctx.user_data.pop("pending", None)

    elif action == "cancel":
        await query.edit_message_text("❌ Operación cancelada.")
        ctx.user_data.pop("pending", None)


# ─────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────

def main():
    if not TELEGRAM_TOKEN:
        raise ValueError("TELEGRAM_TOKEN no configurado")
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY no configurado")

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("ayuda",    cmd_ayuda))
    app.add_handler(CommandHandler("help",     cmd_ayuda))
    app.add_handler(CommandHandler("cuentas",  cmd_cuentas))
    app.add_handler(CommandHandler("resumen",  cmd_resumen))
    app.add_handler(CommandHandler("cobrar",   cmd_cobrar))
    app.add_handler(CommandHandler("buscar",   cmd_buscar))
    app.add_handler(CommandHandler("aprender", cmd_aprender))
    app.add_handler(CommandHandler("memoria",  cmd_memoria))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO,                  handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    log.info("Bot iniciado")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
