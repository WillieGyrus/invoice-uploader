/**
 * Invoice Uploader — Google Apps Script
 *
 * SETUP (one time):
 *  1. Go to https://script.google.com and create a new project.
 *  2. Paste this entire file.
 *  3. Click "Services" (+ icon on the left) → add "Drive API" (v3).
 *  4. Run `runMonthlyInvoiceUploader` once manually — Google will ask for
 *     permissions. Click "Allow". That's the only time you'll need a browser.
 *  5. Set up the automatic trigger:
 *       Triggers (clock icon) → Add Trigger
 *         Function:    runMonthlyInvoiceUploader
 *         Event type:  Time-driven → Day timer → between 18:00 and 19:00
 *     The script checks internally whether today is the last day of the month
 *     and exits immediately if it isn't.
 */

// ============================================================
// CONFIG
// ============================================================

const MY_EMAIL = "guillermo.rodriguez@gyrusds.io";
const GMAIL_LABEL = "invoices";
const DRIVE_ROOT_PATH = "GYRUS DATA SOLUTIONS/CONTABILIDAD/DOCUMENTACIÓN CONTABLE";

const MESES_ES = {
  1: "ENERO",  2: "FEBRERO",   3: "MARZO",      4: "ABRIL",
  5: "MAYO",   6: "JUNIO",     7: "JULIO",       8: "AGOSTO",
  9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE"
};

const PROVEEDORES = [
  {
    name: "AWS",
    emails: ["invoicing@aws.com", "no-reply@tax-and-invoicing.us-east-1.amazonaws.com"],
    reply_to: true,
    tipo: "Servicios Informáticos",
    carpeta: "6.AWS",
    arrival_month: "current"
  },
  {
    name: "Basecamp",
    emails: ["billing@basecamp.com"],
    reply_to: false,
    tipo: "Servicios Informáticos",
    carpeta: "4.BASECAMP",
    arrival_month: "current"
  },
  {
    name: "Vercel",
    emails: ["invoice+statements@vercel.com"],
    reply_to: false,
    tipo: "Servicios Informáticos",
    carpeta: "5.VERCEL",
    arrival_month: "current",
    attachment_filter: "Invoice-"
  },
  {
    name: "Ionos",
    emails: ["noreply@ionos.es"],
    reply_to: false,
    tipo: "Servicios Informáticos",
    carpeta: "5.IONOS",
    arrival_month: "current"
  },
  {
    name: "Supabase",
    emails: ["billing-support@supabase.com"],
    reply_to: true,
    tipo: "Servicios Informáticos",
    carpeta: "8.SUPABASE",
    arrival_month: "current",
    attachment_filter: "Invoice-"
  },
  {
    name: "Microsoft",
    emails: ["microsoft-noreply@microsoft.com"],
    reply_to: false,
    tipo: "Servicios Informáticos",
    carpeta: "3.MICROSOFT",
    arrival_month: "current"
  },
  {
    name: "Github",
    emails: ["noreply@github.com"],
    reply_to: false,
    tipo: "Servicios Informáticos",
    carpeta: "7.GITHUB",
    arrival_month: "current"
  },
  {
    name: "Lenovo",
    emails: ["no-reply@directinvoices.dllgroup.com"],
    reply_to: true,
    tipo: "Servicios Informáticos",
    carpeta: "2.LENOVO",
    arrival_month: "current"
  },
  {
    name: "Jerónimo",
    emails: ["no-reply@mail.nexudus.com"],
    reply_to: false,
    tipo: "Alquiler espacios",
    carpeta: "COWORK MADRID",
    arrival_month: "current"
  },
  {
    name: "Alse",
    emails: ["noreply.alsepark@gmail.com"],
    reply_to: false,
    tipo: "Alquiler espacios",
    carpeta: "PARKING MADRID",
    arrival_month: "previous"
  }
];

// ============================================================
// DATE HELPERS
// ============================================================

function isLastDayOfMonth(date) {
  const tomorrow = new Date(date);
  tomorrow.setDate(date.getDate() + 1);
  return tomorrow.getDate() === 1;
}

function lastDayOfMonth(year, month) {
  return new Date(year, month, 0).getDate();
}

function trimestre(mes) {
  return `${Math.floor((mes - 1) / 3) + 1}T`;
}

function buildDrivePath(year, mes, tipo, carpeta) {
  return `${DRIVE_ROOT_PATH}/${year}/${trimestre(mes)}/${MESES_ES[mes]}/RECIBIDAS/${tipo}/${carpeta}`;
}

function fmtDate(year, month, day) {
  return `${year}/${String(month).padStart(2, "0")}/${String(day).padStart(2, "0")}`;
}

// ============================================================
// GMAIL QUERY BUILDER
// ============================================================

function buildGmailQuery(proveedor, year, month) {
  let searchMonth, searchYear;

  if (proveedor.arrival_month === "previous") {
    searchMonth = month > 1 ? month - 1 : 12;
    searchYear  = month > 1 ? year : year - 1;
  } else {
    searchMonth = month;
    searchYear  = year;
  }

  const afterStr = fmtDate(searchYear, searchMonth, 1);
  const beforeStr = searchMonth === 12
    ? fmtDate(searchYear + 1, 1, 1)
    : fmtDate(searchYear, searchMonth + 1, 1);

  let addrParts = proveedor.reply_to
    ? proveedor.emails.join(" OR ")
    : proveedor.emails.map(e => `from:${e.trim()}`).join(" OR ");

  if (proveedor.emails.length > 1) addrParts = `(${addrParts})`;

  return `label:${GMAIL_LABEL} ${addrParts} has:attachment after:${afterStr} before:${beforeStr}`;
}

// ============================================================
// DRIVE HELPERS  (require Drive API v3 advanced service)
// ============================================================

function getOrCreateFolder(name, parentId) {
  const q = `name='${name}' and mimeType='application/vnd.google-apps.folder' and '${parentId}' in parents and trashed=false`;
  const res = Drive.Files.list({
    q,
    fields: "files(id)",
    supportsAllDrives: true,
    includeItemsFromAllDrives: true
  });
  if (res.files && res.files.length > 0) return res.files[0].id;

  const folder = Drive.Files.create(
    { name, mimeType: "application/vnd.google-apps.folder", parents: [parentId] },
    null,
    { supportsAllDrives: true }
  );
  return folder.id;
}

function resolveDrivePath(path) {
  const parts = path.split("/").filter(Boolean);
  const rootName = parts[0];

  const res = Drive.Files.list({
    q: `name='${rootName}' and mimeType='application/vnd.google-apps.folder' and trashed=false and sharedWithMe=true`,
    fields: "files(id)",
    supportsAllDrives: true,
    includeItemsFromAllDrives: true
  });
  if (!res.files || res.files.length === 0) {
    throw new Error(`Root shared folder '${rootName}' not found.`);
  }

  let currentId = res.files[0].id;
  for (let i = 1; i < parts.length; i++) {
    currentId = getOrCreateFolder(parts[i], currentId);
  }
  return currentId;
}

function fileExistsInFolder(folderId, filename) {
  const res = Drive.Files.list({
    q: `name='${filename}' and '${folderId}' in parents and trashed=false`,
    fields: "files(id)",
    supportsAllDrives: true,
    includeItemsFromAllDrives: true
  });
  return res.files && res.files.length > 0;
}

// ============================================================
// MAIN LOGIC
// ============================================================

function processInvoices(targetMonth, targetYear) {
  Logger.log(`Processing invoices: ${MESES_ES[targetMonth]} ${targetYear}`);

  const uploaded = [], skipped = [], errors = [];

  for (const proveedor of PROVEEDORES) {
    Logger.log(`Searching: ${proveedor.name}`);
    const query = buildGmailQuery(proveedor, targetYear, targetMonth);
    Logger.log(`Gmail query: ${query}`);

    let threads;
    try {
      threads = GmailApp.search(query, 0, 20);
    } catch (e) {
      Logger.log(`[${proveedor.name}] Gmail error: ${e}`);
      errors.push({ proveedor: proveedor.name, error: String(e) });
      continue;
    }

    if (threads.length === 0) {
      Logger.log(`[${proveedor.name}] No emails found.`);
      continue;
    }
    Logger.log(`[${proveedor.name}] ${threads.length} thread(s) found.`);

    for (const thread of threads) {
      for (const message of thread.getMessages()) {
        const attachments = message.getAttachments();
        if (attachments.length === 0) continue;

        // Reply-To filter
        if (proveedor.reply_to) {
          const replyTo = message.getReplyTo() || "";
          const match = proveedor.emails.some(e => replyTo.toLowerCase().includes(e.toLowerCase()));
          if (!match) {
            Logger.log(`[${proveedor.name}] Reply-To mismatch (${replyTo}), skipping.`);
            continue;
          }
        }

        const emailDate  = message.getDate();
        const emailMonth = emailDate.getMonth() + 1;
        const emailYear  = emailDate.getFullYear();

        // Determine accounting month/year and whether this email belongs to targetMonth
        let accMonth, accYear, belongs;
        if (proveedor.arrival_month === "previous") {
          const prevMonth = targetMonth > 1 ? targetMonth - 1 : 12;
          const prevYear  = targetMonth > 1 ? targetYear : targetYear - 1;
          accMonth = targetMonth;
          accYear  = targetYear;
          belongs  = (emailMonth === prevMonth && emailYear === prevYear);
        } else {
          accMonth = emailMonth;
          accYear  = emailYear;
          belongs  = (accMonth === targetMonth && accYear === targetYear);
        }

        if (!belongs) {
          Logger.log(`[${proveedor.name}] Date ${emailDate.toDateString()} outside ${MESES_ES[targetMonth]} ${targetYear}, skipping.`);
          continue;
        }

        const drivePath = buildDrivePath(accYear, accMonth, proveedor.tipo, proveedor.carpeta);

        for (const attachment of attachments) {
          const filename = attachment.getName();

          if (proveedor.attachment_filter && !filename.toLowerCase().startsWith(proveedor.attachment_filter.toLowerCase())) {
            Logger.log(`[${proveedor.name}] Skipping ${filename} (filter: ${proveedor.attachment_filter})`);
            continue;
          }

          const ext = filename.split(".").pop().toLowerCase();
          if (!["pdf", "xml", "zip", "csv"].includes(ext)) {
            Logger.log(`[${proveedor.name}] Skipping ${filename} (extension .${ext})`);
            continue;
          }

          Logger.log(`[${proveedor.name}] Attachment: ${filename}`);

          try {
            const folderId = resolveDrivePath(drivePath);

            if (fileExistsInFolder(folderId, filename)) {
              Logger.log(`[${proveedor.name}] ${filename} already exists, skipping.`);
              skipped.push({ proveedor: proveedor.name, filename, drive_path: drivePath });
              continue;
            }

            Drive.Files.create(
              { name: filename, parents: [folderId] },
              attachment.copyBlob(),
              { supportsAllDrives: true }
            );
            Logger.log(`[${proveedor.name}] ${filename} uploaded.`);
            uploaded.push({ proveedor: proveedor.name, filename, drive_path: drivePath });

          } catch (e) {
            Logger.log(`[${proveedor.name}] Error uploading ${filename}: ${e}`);
            errors.push({ proveedor: proveedor.name, filename, error: String(e) });
          }
        }
      }
    }
  }

  Logger.log(`SUMMARY — Uploaded: ${uploaded.length} | Skipped: ${skipped.length} | Errors: ${errors.length}`);
  sendSummaryEmail(uploaded, skipped, errors, targetMonth, targetYear);
}

// ============================================================
// SUMMARY EMAIL
// ============================================================

function sendSummaryEmail(uploaded, skipped, errors, targetMonth, targetYear) {
  const mesStr  = MESES_ES[targetMonth];
  const subject = `[Facturas] Resumen subida ${mesStr} ${targetYear}`;

  const counts = {};
  for (const p of PROVEEDORES) counts[p.name] = { subidas: 0, omitidas: 0, errores: 0 };
  uploaded.forEach(i => counts[i.proveedor].subidas++);
  skipped.forEach(i =>  counts[i.proveedor].omitidas++);
  errors.forEach(i =>   { if (counts[i.proveedor]) counts[i.proveedor].errores++; });

  const totalUp = Object.values(counts).reduce((s, c) => s + c.subidas,  0);
  const totalSk = Object.values(counts).reduce((s, c) => s + c.omitidas, 0);
  const totalEr = Object.values(counts).reduce((s, c) => s + c.errores,  0);

  const thS  = "padding:8px 14px;background:#1e293b;color:#f8fafc;font-weight:600;text-align:center;";
  const thNS = "padding:8px 14px;background:#1e293b;color:#f8fafc;font-weight:600;text-align:left;";

  const rows = Object.keys(counts).sort().map(prov => {
    const c = counts[prov];
    const uc = c.subidas  ? "#16a34a" : "#6b7280";
    const sc = c.omitidas ? "#d97706" : "#6b7280";
    const ec = c.errores  ? "#dc2626" : "#6b7280";
    return `<tr>
      <td style="padding:6px 14px;">${prov}</td>
      <td style="padding:6px 14px;text-align:center;color:${uc};font-weight:600;">${c.subidas}</td>
      <td style="padding:6px 14px;text-align:center;color:${sc};font-weight:600;">${c.omitidas}</td>
      <td style="padding:6px 14px;text-align:center;color:${ec};font-weight:600;">${c.errores}</td>
    </tr>`;
  }).join("");

  const totalRow = `<tr style="border-top:2px solid #e5e7eb;background:#f9fafb;">
    <td style="padding:6px 14px;font-weight:700;">TOTAL</td>
    <td style="padding:6px 14px;text-align:center;color:${totalUp ? "#16a34a" : "#6b7280"};font-weight:700;">${totalUp}</td>
    <td style="padding:6px 14px;text-align:center;color:${totalSk ? "#d97706" : "#6b7280"};font-weight:700;">${totalSk}</td>
    <td style="padding:6px 14px;text-align:center;color:${totalEr ? "#dc2626" : "#6b7280"};font-weight:700;">${totalEr}</td>
  </tr>`;

  function section(title, items, color, keyFn) {
    if (!items.length) return "";
    const rowsHtml = items.map(i => `<tr>
      <td style="padding:4px 10px;font-weight:600;">${i.proveedor}</td>
      <td style="padding:4px 10px;">${i.filename || "N/A"}</td>
      <td style="padding:4px 10px;color:#6b7280;font-size:12px;">${keyFn(i)}</td>
    </tr>`).join("");
    return `
    <h3 style="color:${color};margin-top:24px;">${title} (${items.length})</h3>
    <table style="border-collapse:collapse;font-family:sans-serif;font-size:13px;width:100%;">
      <thead><tr>
        <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Proveedor</th>
        <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Archivo</th>
        <th style="text-align:left;padding:4px 10px;border-bottom:1px solid #e5e7eb;">Ruta / Error</th>
      </tr></thead>
      <tbody>${rowsHtml}</tbody>
    </table>`;
  }

  const detailHtml =
    section("✅ Subidas correctamente", uploaded, "#16a34a", i => i.drive_path) +
    section("⏭ Omitidas — ya existían en Drive", skipped, "#d97706", i => i.drive_path) +
    section("❌ Errores", errors, "#dc2626", i => i.error || "");

  const noInvoices = (!uploaded.length && !skipped.length && !errors.length)
    ? "<p style='font-family:sans-serif;color:#6b7280;'>No se encontraron facturas para este mes.</p>"
    : "";

  const html = `<!DOCTYPE html>
<html><body style="margin:0;padding:24px;background:#f1f5f9;">
<div style="max-width:700px;margin:0 auto;background:#fff;border-radius:8px;padding:32px;box-shadow:0 1px 4px rgba(0,0,0,.08);">
  <h2 style="margin-top:0;font-family:sans-serif;color:#1e293b;">Facturas — ${mesStr} ${targetYear}</h2>
  <table style="border-collapse:collapse;font-family:sans-serif;font-size:14px;margin-bottom:24px;min-width:400px;">
    <thead><tr>
      <th style="${thNS}">Proveedor</th>
      <th style="${thS}">✅ Subidas</th>
      <th style="${thS}">⏭ Omitidas</th>
      <th style="${thS}">❌ Errores</th>
    </tr></thead>
    <tbody>${rows}${totalRow}</tbody>
  </table>
  ${detailHtml}
  ${noInvoices}
</div>
</body></html>`;

  GmailApp.sendEmail(MY_EMAIL, subject, "", { htmlBody: html });
  Logger.log(`Summary email sent to ${MY_EMAIL}`);
}

// ============================================================
// ENTRY POINTS
// ============================================================

function runMonthlyInvoiceUploader() {
  const today = new Date();
  if (!isLastDayOfMonth(today)) {
    Logger.log("Not the last day of the month. Exiting.");
    return;
  }
  processInvoices(today.getMonth() + 1, today.getFullYear());
}

// Manual test: run this from the editor to process a specific month
function runForMonth() {
  const month = 5;  // change as needed
  const year  = 2026;
  processInvoices(month, year);
}
