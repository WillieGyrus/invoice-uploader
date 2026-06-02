#!/usr/bin/env python3
"""
invoice_uploader.py
-------------------
Recupera facturas etiquetadas en Gmail y las sube a Google Drive.

Uso:
    python invoice_uploader.py <mes_numerico>
    python invoice_uploader.py 5        # Mayo
    python invoice_uploader.py 5 --dry-run  # Simula sin subir nada

Requisitos:
    pip install google-auth google-auth-oauthlib google-api-python-client

Credenciales:
    Coloca credentials.json (OAuth 2.0 Desktop App) en el mismo directorio.
    En el primer uso abrirá el navegador para autenticarte.
"""

import argparse
import base64
import logging
import os
import sys
from datetime import datetime, date
from email import message_from_bytes
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaInMemoryUpload

# ---------------------------------------------------------------------------
# CONFIGURACIÓN
# ---------------------------------------------------------------------------

MY_EMAIL = "guillermo.rodriguez@gyrusds.io"

GMAIL_LABEL = "invoices"

DRIVE_ROOT_PATH = "GYRUS DATA SOLUTIONS/CONTABILIDAD/DOCUMENTACIÓN CONTABLE"

# Scopes necesarios
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/gmail.send",
]

CREDENTIALS_FILE = Path(__file__).parent / "credentials.json"
TOKEN_FILE = Path(__file__).parent / "token.json"

# ---------------------------------------------------------------------------
# LOGGING
# ---------------------------------------------------------------------------

LOG_DIR = Path(__file__).parent / "logs"

def setup_logging(verbose: bool = False) -> logging.Logger:
    LOG_DIR.mkdir(exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    log_file = LOG_DIR / f"invoice_uploader_{timestamp}.log"

    fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(fmt)

    logger = logging.getLogger("invoice_uploader")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    logger.info("Log file: %s", log_file)
    return logger

log = logging.getLogger("invoice_uploader")

# ---------------------------------------------------------------------------
# TABLA DE PROVEEDORES
# ---------------------------------------------------------------------------
# Campos:
#   name          : nombre legible
#   emails        : lista de posibles remitentes (from: o reply-to:)
#   reply_to      : si True, buscar también en Reply-To (Lenovo)
#   tipo          : "Servicios Informáticos" | "Alquiler Espacios"
#   carpeta       : nombre de la carpeta objetivo en Drive
#   arrival_days  : tupla (dia_min, dia_max) del mes de llegada,
#                   o None si puede llegar cualquier día del mes actual.
#                   Cuando llegan días 1-3 del mes actual, se contabilizan
#                   como el mes ANTERIOR.

PROVEEDORES = [
    {
        "name": "AWS",
        "emails": [
            "invoicing@aws.com",
            "no-reply@tax-and-invoicing.us-east-1.amazonaws.com",
        ],
        "reply_to": True,
        "tipo": "Servicios Informáticos",
        "carpeta": "6.AWS",
        "arrival_days": None,
        "arrival_month": "current",
    },
    {
        "name": "Basecamp",
        "emails": ["billing@basecamp.com"],
        "reply_to": False,
        "tipo": "Servicios Informáticos",
        "carpeta": "4.BASECAMP",
        "arrival_days": None,
        "arrival_month": "current",
    },
    {
        "name": "Vercel",
        "emails": ["invoice+statements@vercel.com"],
        "reply_to": False,
        "tipo": "Servicios Informáticos",
        "carpeta": "5.VERCEL",
        "arrival_days": None,
        "arrival_month": "current",
        "attachment_filter": "Invoice-",  # ignorar Receipt-*, solo Invoice-*
    },
    {
        "name": "Ionos",
        "emails": ["noreply@ionos.es"],
        "reply_to": False,
        "tipo": "Servicios Informáticos",
        "carpeta": "5.IONOS",
        "arrival_days": None,        # cualquier día del mes actual
        "arrival_month": "current",
    },
    {
        "name": "Supabase",
        "emails": ["billing-support@supabase.com"],
        "reply_to": True,
        "tipo": "Servicios Informáticos",
        "carpeta": "8.SUPABASE",
        "arrival_days": None,    # llega días 26-28 → mes ACTUAL
        "arrival_month": "current",
        "attachment_filter": "Invoice-",  # ignorar Receipt-*, solo Invoice-*
    },
    {
        "name": "Microsoft",
        "emails": ["microsoft-noreply@microsoft.com"],
        "reply_to": False,
        "tipo": "Servicios Informáticos",
        "carpeta": "3.MICROSOFT",
        "arrival_days": None,
        "arrival_month": "current",
    },
    {
        "name": "Github",
        "emails": ["noreply@github.com"],
        "reply_to": False,
        "tipo": "Servicios Informáticos",
        "carpeta": "7.GITHUB",
        "arrival_days": None,
        "arrival_month": "current",
    },
    {
        "name": "Lenovo",
        "emails": ["no-reply@directinvoices.dllgroup.com"],
        "reply_to": True,   # buscar en Reply-To además de From
        "tipo": "Servicios Informáticos",
        "carpeta": "2.LENOVO",
        "arrival_days": None,
        "arrival_month": "current",
    },
    {
        "name": "Jerónimo",
        "emails": ["no-reply@mail.nexudus.com"],
        "reply_to": False,   # buscar en Reply-To además de From
        "tipo": "Alquiler espacios",
        "carpeta": "COWORK MADRID",
        "arrival_days": None,
        "arrival_month": "current",
    },
        {
        "name": "Alse",
        "emails": ["noreply.alsepark@gmail.com"],
        "reply_to": False,   # buscar en Reply-To además de From
        "tipo": "Alquiler espacios",
        "carpeta": "PARKING MADRID",
        "arrival_days": None,
        "arrival_month": "previous",  # llega primeros días del mes siguiente, pero contabiliza en mes anterior
    },
]

# ---------------------------------------------------------------------------
# HELPERS DE FECHA Y RUTA
# ---------------------------------------------------------------------------

MESES_ES = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL",
    5: "MAYO", 6: "JUNIO", 7: "JULIO", 8: "AGOSTO",
    9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE",
}

def trimestre(mes: int) -> str:
    return f"{(mes - 1) // 3 + 1}T"

def build_drive_path(year: int, mes: int, tipo: str, carpeta: str) -> str:
    """Construye la ruta completa en Drive."""
    return (
        f"{DRIVE_ROOT_PATH}/"
        f"{year}/"
        f"{trimestre(mes)}/"
        f"{MESES_ES[mes]}/"
        f"RECIBIDAS/"
        f"{tipo}/"
        f"{carpeta}"
    )

def get_accounting_month(proveedor: dict, email_date: date, target_month: int, target_year: int):
    """
    Determina el mes/año contable al que pertenece una factura.

    - Si arrival_month == 'previous': la factura llega los primeros días
      del mes actual pero contabiliza en el mes ANTERIOR.
    - Si arrival_month == 'current': contabiliza en el mes de llegada.

    El script recibe un `target_month` (mes que queremos procesar).
    Devuelve True si esta factura debe incluirse en ese mes, junto con
    el (year, month) contable real.
    """
    if proveedor["arrival_month"] == "previous":
        # Email llega en el mes anterior → sube a la carpeta del mes indicado en el script
        prev_month = target_month - 1 if target_month > 1 else 12
        prev_year = target_year if target_month > 1 else target_year - 1
        accounting_month = target_month
        accounting_year = target_year
        belongs = (email_date.month == prev_month and email_date.year == prev_year)
    else:
        accounting_month = email_date.month
        accounting_year = email_date.year
        belongs = (accounting_month == target_month and accounting_year == target_year)

    return belongs, accounting_year, accounting_month

# ---------------------------------------------------------------------------
# AUTENTICACIÓN GOOGLE
# ---------------------------------------------------------------------------

def get_credentials():
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.debug("Token caducado, refrescando credenciales.")
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                log.error(
                    "No se encontró %s. Descarga las credenciales OAuth 2.0 "
                    "(tipo 'Desktop App') desde https://console.cloud.google.com/apis/credentials "
                    "y guárdalas como credentials.json junto a este script.",
                    CREDENTIALS_FILE,
                )
                sys.exit(1)
            log.info("Iniciando flujo OAuth — se abrirá el navegador.")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())
    return creds

# ---------------------------------------------------------------------------
# GMAIL: buscar correos y extraer adjuntos
# ---------------------------------------------------------------------------

def build_gmail_query(proveedor: dict, year: int, month: int) -> str:
    """
    Construye la query de Gmail para encontrar los correos del proveedor
    en la ventana de fechas relevante.
    """
    import calendar

    label = f"label:{GMAIL_LABEL}"

    # Rango de fechas de búsqueda:
    # - Para facturas que llegan días 1-3 del mes SIGUIENTE al que procesamos,
    #   buscamos en el mes siguiente también.
    # - Para el resto buscamos en el mes indicado.
    if proveedor["arrival_month"] == "previous":
        # Buscar en el mes anterior al que procesamos (mes completo)
        search_month = month - 1 if month > 1 else 12
        search_year = year if month > 1 else year - 1
        after_day = 1
        before_day = 32  # mes completo, se recortará al último día
    else:
        search_month = month
        search_year = year
        if proveedor["arrival_days"]:
            after_day = proveedor["arrival_days"][0]
            before_day = proveedor["arrival_days"][1] + 1
        else:
            after_day = 1
            before_day = calendar.monthrange(search_year, search_month)[1] + 1

    # Formato Gmail: after:YYYY/MM/DD before:YYYY/MM/DD
    after_str = f"{search_year}/{search_month:02d}/{after_day:02d}"
    # Calculamos before conservando límites de mes
    import calendar as cal
    last_day = cal.monthrange(search_year, search_month)[1]
    clamped_before = min(before_day, last_day + 1)
    if clamped_before > last_day:
        # Primer día del mes siguiente
        if search_month == 12:
            before_str = f"{search_year + 1}/01/01"
        else:
            before_str = f"{search_year}/{search_month + 1:02d}/01"
    else:
        before_str = f"{search_year}/{search_month:02d}/{clamped_before:02d}"

    # Construir filtro de remitentes.
    # Si reply_to=True las direcciones aparecen en Reply-To, no en From,
    # así que buscamos sin prefijo (Gmail busca en todos los headers).
    if proveedor.get("reply_to"):
        addr_parts = " OR ".join(e.strip() for e in proveedor["emails"])
    else:
        addr_parts = " OR ".join(f"from:{e.strip()}" for e in proveedor["emails"])
    if len(proveedor["emails"]) > 1:
        addr_parts = f"({addr_parts})"

    query = f"{label} {addr_parts} has:attachment after:{after_str} before:{before_str}"
    return query

def get_email_date(msg_payload) -> date:
    """Extrae la fecha del mensaje a partir de los headers."""
    from email.utils import parsedate_to_datetime
    headers = {h["name"]: h["value"] for h in msg_payload.get("headers", [])}
    date_str = headers.get("Date", "")
    try:
        return parsedate_to_datetime(date_str).date()
    except Exception:
        return date.today()

def get_reply_to(msg_payload) -> str:
    headers = {h["name"].lower(): h["value"] for h in msg_payload.get("headers", [])}
    return headers.get("reply-to", "")

def fetch_attachments(gmail_service, msg_id: str):
    """Devuelve lista de (filename, bytes) con los adjuntos del mensaje."""
    msg = gmail_service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()

    attachments = []
    parts = msg.get("payload", {}).get("parts", [])
    _extract_parts(gmail_service, msg_id, parts, attachments)
    return msg["payload"], attachments

def _extract_parts(gmail_service, msg_id, parts, result):
    for part in parts:
        # Recursión para multipart
        if part.get("parts"):
            _extract_parts(gmail_service, msg_id, part["parts"], result)
        filename = part.get("filename", "")
        body = part.get("body", {})
        if filename and body.get("attachmentId"):
            att = gmail_service.users().messages().attachments().get(
                userId="me", messageId=msg_id, id=body["attachmentId"]
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            result.append((filename, data))

# ---------------------------------------------------------------------------
# DRIVE: gestión de carpetas y subida
# ---------------------------------------------------------------------------

def get_or_create_folder(drive_service, name: str, parent_id: str) -> str:
    """Obtiene o crea una carpeta en Drive bajo parent_id. Devuelve folder_id."""
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        f"and '{parent_id}' in parents and trashed=false"
    )
    results = drive_service.files().list(
        q=query, fields="files(id, name)", spaces="drive",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    # Crear
    metadata = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_id],
    }
    folder = drive_service.files().create(
        body=metadata, fields="id", supportsAllDrives=True,
    ).execute()
    return folder["id"]

def resolve_drive_path(drive_service, path: str) -> str:
    """
    Resuelve una ruta completa de Drive (separada por /) y devuelve el folder_id
    del último segmento, creando las carpetas intermedias si no existen.
    La carpeta raíz se busca en las unidades compartidas con el usuario.
    """
    parts = [p for p in path.split("/") if p]

    # El primer segmento es una carpeta compartida — buscar fuera de "Mi unidad"
    root_name = parts[0]
    results = drive_service.files().list(
        q=(
            f"name='{root_name}' and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false and sharedWithMe=true"
        ),
        fields="files(id, name)",
        includeItemsFromAllDrives=True,
        supportsAllDrives=True,
    ).execute()
    shared_files = results.get("files", [])
    if not shared_files:
        raise FileNotFoundError(
            f"No se encontró la carpeta raíz compartida '{root_name}'. "
            "Asegúrate de que está compartida contigo en Google Drive."
        )
    current_id = shared_files[0]["id"]

    for part in parts[1:]:
        current_id = get_or_create_folder(drive_service, part, current_id)
    return current_id

def file_exists_in_folder(drive_service, folder_id: str, filename: str) -> bool:
    """Comprueba si ya existe un archivo con ese nombre en la carpeta."""
    query = (
        f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    )
    results = drive_service.files().list(
        q=query, fields="files(id)",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()
    return bool(results.get("files"))

def upload_file(drive_service, folder_id: str, filename: str, data: bytes) -> str:
    """Sube un archivo a Drive. Devuelve el file_id."""
    import mimetypes
    mime_type, _ = mimetypes.guess_type(filename)
    mime_type = mime_type or "application/octet-stream"

    media = MediaInMemoryUpload(data, mimetype=mime_type, resumable=False)
    metadata = {"name": filename, "parents": [folder_id]}
    f = drive_service.files().create(
        body=metadata, media_body=media, fields="id", supportsAllDrives=True,
    ).execute()
    return f["id"]

# ---------------------------------------------------------------------------
# GMAIL: enviar resumen por correo
# ---------------------------------------------------------------------------

def send_summary_email(gmail_service, uploaded: list, skipped: list, errors: list,
                        target_month: int, target_year: int, dry_run: bool):
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    mes_str = MESES_ES[target_month]
    subject = f"[Facturas] Resumen subida {mes_str} {target_year}"
    if dry_run:
        subject = "[DRY RUN] " + subject

    counts: dict = {p["name"]: {"subidas": 0, "omitidas": 0, "errores": 0} for p in PROVEEDORES}
    for item in uploaded:
        counts[item["proveedor"]]["subidas"] += 1
    for item in skipped:
        counts[item["proveedor"]]["omitidas"] += 1
    for item in errors:
        counts[item["proveedor"]]["errores"] += 1

    total_up = sum(c["subidas"] for c in counts.values())
    total_sk = sum(c["omitidas"] for c in counts.values())
    total_er = sum(c["errores"] for c in counts.values())

    # --- HTML ---
    def td(val, bold=False, color=None):
        style = "padding:6px 14px;text-align:center;"
        if color:
            style += f"color:{color};font-weight:600;"
        if bold:
            style += "font-weight:700;"
        return f'<td style="{style}">{val}</td>'

    def td_name(val, bold=False):
        style = "padding:6px 14px;text-align:left;"
        if bold:
            style += "font-weight:700;"
        return f'<td style="{style}">{val}</td>'

    summary_rows = ""
    for prov in sorted(counts.keys()):
        c = counts[prov]
        up_color = "#16a34a" if c["subidas"] else "#6b7280"
        sk_color = "#d97706" if c["omitidas"] else "#6b7280"
        er_color = "#dc2626" if c["errores"] else "#6b7280"
        summary_rows += (
            f"<tr>"
            f"{td_name(prov)}"
            f"{td(c['subidas'], color=up_color)}"
            f"{td(c['omitidas'], color=sk_color)}"
            f"{td(c['errores'], color=er_color)}"
            f"</tr>"
        )

    up_color_total = "#16a34a" if total_up else "#6b7280"
    sk_color_total = "#d97706" if total_sk else "#6b7280"
    er_color_total = "#dc2626" if total_er else "#6b7280"
    total_row = (
        f'<tr style="border-top:2px solid #e5e7eb;background:#f9fafb;">'
        f'{td_name("TOTAL", bold=True)}'
        f'{td(total_up, color=up_color_total)}'
        f'{td(total_sk, color=sk_color_total)}'
        f'{td(total_er, color=er_color_total)}'
        f"</tr>"
    )

    th_style = "padding:8px 14px;background:#1e293b;color:#f8fafc;font-weight:600;text-align:center;"
    th_name_style = "padding:8px 14px;background:#1e293b;color:#f8fafc;font-weight:600;text-align:left;"

    summary_table = f"""
    <table style="border-collapse:collapse;font-family:sans-serif;font-size:14px;margin-bottom:24px;min-width:400px;">
      <thead>
        <tr>
          <th style="{th_name_style}">Proveedor</th>
          <th style="{th_style}">✅ Subidas</th>
          <th style="{th_style}">⏭ Omitidas</th>
          <th style="{th_style}">❌ Errores</th>
        </tr>
      </thead>
      <tbody>
        {summary_rows}
        {total_row}
      </tbody>
    </table>
    """ if counts else "<p style='color:#6b7280;'>No se encontraron facturas para este mes.</p>"

    def section(title, items, color, key_fn):
        if not items:
            return ""
        rows = "".join(
            f"<tr><td style='padding:4px 10px;font-weight:600;'>{i['proveedor']}</td>"
            f"<td style='padding:4px 10px;'>{i.get('filename','N/A')}</td>"
            f"<td style='padding:4px 10px;color:#6b7280;font-size:12px;'>{key_fn(i)}</td></tr>"
            for i in items
        )
        return f"""
        <h3 style="color:{color};margin-top:24px;">{title} ({len(items)})</h3>
        <table style="border-collapse:collapse;font-family:sans-serif;font-size:13px;width:100%;">
          <thead><tr>
            <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Proveedor</th>
            <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Archivo</th>
            <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Ruta / Error</th>
          </tr></thead>
          <tbody>{rows}</tbody>
        </table>"""

    detail_html = (
        section("✅ Subidas correctamente", uploaded, "#16a34a", lambda i: i["drive_path"]) +
        section("⏭ Omitidas — ya existían en Drive", skipped, "#d97706", lambda i: i["drive_path"]) +
        section("❌ Errores", errors, "#dc2626", lambda i: i.get("error", ""))
    )

    dry_run_banner = """
    <div style="margin-top:24px;padding:12px 16px;background:#fef9c3;border-left:4px solid #ca8a04;
                font-family:sans-serif;font-size:13px;color:#713f12;">
      ⚠️ <strong>Modo DRY RUN</strong> — no se ha subido nada realmente.
    </div>""" if dry_run else ""

    no_invoices = "" if (uploaded or skipped or errors) else \
        "<p style='font-family:sans-serif;color:#6b7280;'>No se encontraron facturas para este mes.</p>"

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f1f5f9;">
<div style="max-width:700px;margin:0 auto;background:#ffffff;border-radius:8px;
            padding:32px;box-shadow:0 1px 4px rgba(0,0,0,.08);">
  <h2 style="margin-top:0;font-family:sans-serif;color:#1e293b;">
    Facturas — {mes_str} {target_year}
  </h2>
  {summary_table}
  {detail_html}
  {no_invoices}
  {dry_run_banner}
</div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["To"] = MY_EMAIL
    msg["From"] = MY_EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    gmail_service.users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()
    log.info("Resumen enviado a %s", MY_EMAIL)

# ---------------------------------------------------------------------------
# LÓGICA PRINCIPAL
# ---------------------------------------------------------------------------

def process_invoices(target_month: int, dry_run: bool = False):
    today = date.today()
    # Inferir el año: si el mes pedido es mayor que el mes actual, es el año anterior
    if target_month > today.month:
        target_year = today.year - 1
    else:
        target_year = today.year

    log.info("Procesando facturas: %s %d%s", MESES_ES[target_month], target_year,
             "  [DRY RUN]" if dry_run else "")

    creds = get_credentials()
    gmail_service = build("gmail", "v1", credentials=creds)
    drive_service = build("drive", "v3", credentials=creds)

    uploaded = []
    skipped = []
    errors = []

    for proveedor in PROVEEDORES:
        log.info("Buscando: %s", proveedor["name"])
        query = build_gmail_query(proveedor, target_year, target_month)
        log.debug("Query Gmail: %s", query)

        try:
            results = gmail_service.users().messages().list(
                userId="me", q=query, maxResults=20
            ).execute()
        except HttpError as e:
            log.error("[%s] Error en Gmail: %s", proveedor["name"], e)
            errors.append({"proveedor": proveedor["name"], "error": str(e)})
            continue

        messages = results.get("messages", [])
        if not messages:
            log.info("[%s] Sin correos encontrados.", proveedor["name"])
            continue

        log.info("[%s] %d correo(s) encontrado(s).", proveedor["name"], len(messages))

        for msg_meta in messages:
            msg_id = msg_meta["id"]
            try:
                payload, attachments = fetch_attachments(gmail_service, msg_id)
            except HttpError as e:
                log.error("[%s] Error al leer mensaje %s: %s", proveedor["name"], msg_id, e)
                errors.append({"proveedor": proveedor["name"], "error": str(e)})
                continue

            if not attachments:
                log.warning("[%s] Mensaje %s sin adjuntos, omitiendo.", proveedor["name"], msg_id)
                continue

            email_date = get_email_date(payload)

            # Verificar Reply-To para Lenovo
            if proveedor["reply_to"]:
                reply_to = get_reply_to(payload)
                match = any(e.strip().lower() in reply_to.lower() for e in proveedor["emails"])
                if not match:
                    log.warning("[%s] Reply-To no coincide (%s), omitiendo.", proveedor["name"], reply_to)
                    continue

            # Determinar mes contable
            belongs, acc_year, acc_month = get_accounting_month(
                proveedor, email_date, target_month, target_year
            )
            if not belongs:
                log.warning(
                    "[%s] Correo de %s no corresponde a %s %d, omitiendo.",
                    proveedor["name"], email_date, MESES_ES[target_month], target_year,
                )
                continue

            drive_path = build_drive_path(
                acc_year, acc_month, proveedor["tipo"], proveedor["carpeta"]
            )
            log.debug("[%s] Ruta Drive: %s", proveedor["name"], drive_path)

            attachment_filter = proveedor.get("attachment_filter")
            for filename, data in attachments:
                if attachment_filter and not filename.lower().startswith(attachment_filter.lower()):
                    log.debug("[%s] Omitiendo %s (no coincide con filtro '%s')", proveedor["name"], filename, attachment_filter)
                    continue

                # Solo subir PDFs (y también XML, ZIP si existen)
                ext = Path(filename).suffix.lower()
                if ext not in (".pdf", ".xml", ".zip", ".csv"):
                    log.debug("[%s] Omitiendo %s (extensión %s)", proveedor["name"], filename, ext)
                    continue

                log.info("[%s] Adjunto: %s (%s bytes)", proveedor["name"], filename, f"{len(data):,}")

                if dry_run:
                    log.info("[DRY RUN] Se subiría %s → %s", filename, drive_path)
                    uploaded.append({
                        "proveedor": proveedor["name"],
                        "filename": filename,
                        "drive_path": drive_path,
                    })
                    continue

                try:
                    folder_id = resolve_drive_path(drive_service, drive_path)

                    if file_exists_in_folder(drive_service, folder_id, filename):
                        log.info("[%s] %s ya existe en Drive, omitiendo.", proveedor["name"], filename)
                        skipped.append({
                            "proveedor": proveedor["name"],
                            "filename": filename,
                            "drive_path": drive_path,
                        })
                        continue

                    upload_file(drive_service, folder_id, filename, data)
                    log.info("[%s] %s subida correctamente.", proveedor["name"], filename)
                    uploaded.append({
                        "proveedor": proveedor["name"],
                        "filename": filename,
                        "drive_path": drive_path,
                    })

                except HttpError as e:
                    log.error("[%s] Error al subir %s: %s", proveedor["name"], filename, e)
                    errors.append({
                        "proveedor": proveedor["name"],
                        "filename": filename,
                        "error": str(e),
                    })

    log.info("RESUMEN FINAL — Subidas: %d | Omitidas: %d | Errores: %d",
             len(uploaded), len(skipped), len(errors))

    # Enviar resumen por correo
    try:
        send_summary_email(
            gmail_service, uploaded, skipped, errors,
            target_month, target_year, dry_run
        )
    except Exception as e:
        log.warning("No se pudo enviar el correo de resumen: %s", e)

# ---------------------------------------------------------------------------
# ENTRADA
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Sube facturas de Gmail a Google Drive.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python invoice_uploader.py 5          # Procesa Mayo del año en curso
  python invoice_uploader.py 12         # Procesa Diciembre (año anterior si ya es enero)
  python invoice_uploader.py 3 --dry-run  # Simula sin subir nada
        """,
    )
    parser.add_argument(
        "mes",
        type=int,
        choices=range(1, 13),
        metavar="MES",
        help="Mes a procesar (1-12)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula la ejecución sin subir archivos ni enviar correo real.",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Muestra mensajes de debug (queries Gmail, rutas Drive, etc.).",
    )
    args = parser.parse_args()
    setup_logging(verbose=args.verbose)
    process_invoices(args.mes, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
