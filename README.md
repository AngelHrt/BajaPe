# BajaPe

Descargador de video y música de YouTube para Windows, con interfaz de escritorio
oscura, cola de descargas simultáneas e historial de lo descargado.

BajaPe se apoya en el ejecutable oficial de [yt-dlp](https://github.com/yt-dlp/yt-dlp)
y en `ffmpeg`. La primera vez que se abre, la aplicación descarga sola todo lo que
necesita y lo guarda en tu carpeta de usuario: **no hay que instalar nada a mano**.

---

## Características

- **Interfaz nativa de escritorio** (CustomTkinter) con tema oscuro.
- **Video MP4** con calidad seleccionable: 360p, 480p, 720p, 1080p, 1440p, 2160p (4K)
  o "Mejor disponible".
- **Audio MP3** con bitrate seleccionable: 128, 192, 256 o 320 kbps.
- **Variés enlaces a la vez**: pega varias URLs (una por línea) y se encolan.
- **Playlists y canales**, con límite opcional de elementos (`0` = todos).
- **Cola de descargas** con progreso, velocidad, tiempo restante y cancelación.
- **Varias descargas en paralelo** (1 a 4, por defecto 3).
- **Historial** en JSON, con búsqueda y acceso rápido a cada archivo.
- **Elegir carpeta de destino** y opción de abrirla al terminar.
- **Cookies del navegador** (Chrome, Edge, Firefox, Brave) para videos con
  restricción de edad o privados.
- **Actualización de yt-dlp** desde la propia aplicación.

---

## Requisitos

- Windows 10 u 11 (64 bits).
- Conexión a internet la primera vez (la aplicación descarga sus herramientas).
- Para **ejecutar desde el código**: Python 3.10 o superior.

> No necesitas instalar yt-dlp, ffmpeg ni Node.js/Deno: BajaPe los descarga y los
> gestiona por ti.

---

## Ejecutar desde el código

```powershell
# 1. Crear el entorno virtual
python -m venv .venv

# 2. Instalar dependencias
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 3. Arrancar
.venv\Scripts\python.exe app.py
```

En la primera ejecución aparecerá una ventana de **Preparación inicial** que descargará
yt-dlp (unos 17 MB), ffmpeg (unos 110 MB) y, si no encuentra Node.js/Deno en el sistema,
un runtime de JavaScript (Deno).

---

## Crear el ejecutable (.exe)

Requiere las dependencias de desarrollo:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Luego usa el script de compilación:

```powershell
# Carpeta portable: dist\BajaPe\BajaPe.exe  (recomendado, arranca más rápido)
powershell -ExecutionPolicy Bypass -File build.ps1

# Un único archivo: dist\BajaPe.exe  (más cómodo de compartir, arranca más lento)
powershell -ExecutionPolicy Bypass -File build.ps1 -OneFile
```

El script crea el entorno virtual si falta, instala dependencias, regenera el icono y
compila con PyInstaller. Para distribuir, comprime la carpeta `dist\BajaPe` completa
(o copia `dist\BajaPe.exe`).

---

## Dónde se guardan las cosas

| Qué | Ubicación |
| --- | --- |
| Herramientas (yt-dlp, ffmpeg, Deno) | `%LOCALAPPDATA%\BajaPe\bin` |
| Ajustes | `%LOCALAPPDATA%\BajaPe\settings.json` |
| Historial de descargas | `%LOCALAPPDATA%\BajaPe\history.json` |
| Registro (log) | `%LOCALAPPDATA%\BajaPe\bajape.log` |
| Descargas por defecto | `%USERPROFILE%\Downloads\BajaPe` |

Los archivos temporales de cada descarga se crean en `<carpeta destino>\.bajape-tmp`
y se borran al terminar (o al cancelar).

Los nombres de archivo siguen la plantilla `Título [ID].ext`, con caracteres válidos
para Windows y sin sobrescribir archivos existentes.

---

## Solución de problemas

**Faltan calidades o formatos.**
YouTube necesita un runtime de JavaScript (Deno o Node.js). BajaPe lo detecta
automáticamente; si no lo encuentra, lo intenta descargar en la preparación inicial.
Puedes revisar el estado en la barra inferior de la ventana. Si aparece
"sin runtime JS", instala Node.js o Deno y vuelve a abrir la aplicación.

**Errores al descargar / "sign in to confirm" / videos con restricción de edad.**
Abre **Ajustes → Cookies del navegador** y elige tu navegador. Si usas Chrome o Edge,
ciérralos antes de descargar (Windows bloquea la lectura de las cookies mientras el
navegador está abierto).

**YouTube cambió y las descargas fallan.**
Es habitual. Pulsa **Actualizar yt-dlp** (abajo a la derecha). Requiere que no haya
descargas en curso.

**El antivirus bloquea la aplicación o las descargas.**
BajaPe descarga ejecutables (yt-dlp, ffmpeg, Deno) desde fuentes oficiales. Algunos
antivirus marcan ese comportamiento como sospechoso. Añade una exclusión para
`%LOCALAPPDATA%\BajaPe` o para la carpeta donde tengas el `.exe`.

**No se actualiza yt-dlp.**
Cierra cualquier descarga en curso: el archivo está en uso mientras se ejecuta.

**¿Dónde está el detalle del error?**
En `%LOCALAPPDATA%\BajaPe\bajape.log`.

---

## Aviso legal

BajaPe es una herramienta para uso personal. Descarga únicamente contenido sobre el
que tengas derechos o permiso, y respeta los Términos de servicio de YouTube y la
legislación de derechos de autor de tu país. El autor de esta aplicación no se
responsabiliza del uso que se le dé.
