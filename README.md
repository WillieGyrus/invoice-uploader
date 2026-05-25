# 📄 invoice_uploader.py — Guía de configuración

Script que recupera facturas etiquetadas en Gmail y las sube automáticamente
a las carpetas correctas de Google Drive, enviando un resumen por correo.

---

## 1. Requisitos

- Python 3.8 o superior
- Una cuenta Google con acceso a Gmail y Drive

Instala las dependencias:

```bash
pip install google-auth google-auth-oauthlib google-api-python-client
```

---

## 2. Crear credenciales en Google Cloud Console

> Solo hay que hacerlo una vez.

1. Ve a https://console.cloud.google.com/
2. Crea un proyecto nuevo (o usa uno existente).
3. Ve a **APIs y servicios → Biblioteca** y activa:
   - **Gmail API**
   - **Google Drive API**
4. Ve a **APIs y servicios → Credenciales**.
5. Pulsa **Crear credenciales → ID de cliente OAuth 2.0**.
6. Tipo de aplicación: **App de escritorio**.
7. Descarga el JSON y **renómbralo a `credentials.json`**.
8. Coloca `credentials.json` en la misma carpeta que `invoice_uploader.py`.

### Configurar la pantalla de consentimiento OAuth

En **APIs y servicios → Pantalla de consentimiento de OAuth**:
- Tipo de usuario: **Externo** (o Interno si tienes Google Workspace).
- Rellena nombre de app y correo de soporte.
- En **Usuarios de prueba**, añade `guillermo.rodriguez@gyrusds.io`.

---

## 3. Primer uso — Autorización

La primera vez que ejecutes el script se abrirá el navegador para que
autorices el acceso. Acepta los permisos (Gmail lectura, Drive, envío de correo).

Se creará un archivo `token.json` que guarda la sesión. Las siguientes
ejecuciones no pedirán autorización.

---

## 4. Uso

```bash
# Procesar las facturas de Mayo
python invoice_uploader.py 5

# Procesar Diciembre (si estamos en enero, cogerá el año anterior)
python invoice_uploader.py 12

# Simular sin subir nada ni enviar correo (prueba)
python invoice_uploader.py 5 --dry-run
```

---

## 5. Estructura de carpetas en Drive

Las facturas se suben a:

```
GYRUS DATA SOLUTIONS/
└── CONTABILIDAD/
    └── DOCUMENTACIÓN CONTABLE/
        └── {AÑO}/
            └── {NT}/           ← Ej: 2T
                └── {MES}/      ← Ej: MAYO
                    └── RECIBIDAS/
                        └── Servicios Informáticos/
                            ├── 2.LENOVO/
                            ├── 3.MICROSOFT/
                            ├── 4.BASECAMP/
                            ├── 5.IONOS/
                            ├── 5.VERCEL/
                            ├── 6.AWS/
                            ├── 7.GITHUB/
                            └── 8.SUPABASE/
```

Las carpetas se crean automáticamente si no existen.

---

## 6. Lógica de fechas

| Proveedor  | Llega        | Contabiliza en |
|------------|--------------|----------------|
| AWS        | Días 1–3     | Mes anterior   |
| Basecamp   | Días 21–23   | Mes actual     |
| Vercel     | Días 17–19   | Mes actual     |
| Ionos      | Cualquier día| Mes actual     |
| Supabase   | Días 26–28   | Mes actual     |
| Microsoft  | Cualquier día| Mes actual     |
| Github     | Cualquier día| Mes actual     |
| Lenovo     | Cualquier día| Mes actual     |

**AWS** es especial: sus facturas llegan los primeros días del mes siguiente
al que corresponden. Si procesas Mayo (`python invoice_uploader.py 5`),
el script buscará el correo de AWS que llega entre el 1 y el 3 de Junio.

---

## 7. Resumen por correo

Al finalizar, el script envía un correo a `guillermo.rodriguez@gyrusds.io`
con el resultado: facturas subidas, omitidas (ya existían) y errores.

---

## 8. Notas de seguridad

- `credentials.json` y `token.json` contienen acceso a tu cuenta.
  **No los subas a Git ni los compartas.**
- Añádelos al `.gitignore`:
  ```
  credentials.json
  token.json
  ```
