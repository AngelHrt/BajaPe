"""Descargas HTTP tolerantes a problemas con los certificados TLS.

En algunos equipos Windows rechaza la cadena de certificados de GitHub porque un
antivirus o un proxy de la red inspecciona HTTPS, porque la fecha del sistema es
incorrecta o porque el almacén de certificados raíz está desactualizado. Python
falla entonces con ``CERTIFICATE_VERIFY_FAILED`` y la aplicación no puede
descargar sus herramientas.

Este módulo intenta, en orden:

1. ``urllib`` usando el almacén de certificados de Windows (ignorando variables
   de entorno como ``SSL_CERT_FILE`` que pueden apuntar a un paquete incompleto),
   más los certificados de ``certifi`` y un ``ca-bundle.pem`` opcional del usuario.
2. ``curl.exe``, que en Windows usa el almacén del sistema mediante Schannel.
3. ``urllib`` sin verificar el certificado, solo si el usuario lo autoriza
   expresamente (ajuste de la ventana de preparación o variable de entorno).
"""

from __future__ import annotations

import logging
import os
import shutil
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable

from . import paths

logger = logging.getLogger("bajape.net")

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BajaPe/1.0"
CA_BUNDLE_ENV = "BAJAPE_CA_BUNDLE"
INSECURE_ENV = "BAJAPE_ALLOW_INSECURE_DOWNLOADS"
CHUNK_SIZE = 256 * 1024

ProgressCallback = Callable[[int, int], None]
CancelCallback = Callable[[], None]
_TRUTHY = {"1", "true", "yes", "si", "sí", "on"}


class DownloadError(RuntimeError):
    """Falló una descarga HTTP."""


class CertificateError(DownloadError):
    """No se pudo verificar el certificado TLS del servidor."""


def insecure_allowed() -> bool:
    """Indica si el usuario autorizó descargas sin verificar el certificado."""
    return os.environ.get(INSECURE_ENV, "").strip().lower() in _TRUTHY


def ca_bundle_path() -> Path | None:
    """Certificado o paquete de certificados extra indicado por el usuario."""
    override = os.environ.get(CA_BUNDLE_ENV, "").strip()
    if override:
        path = Path(os.path.expandvars(override)).expanduser()
        if path.is_file():
            return path
        logger.warning("%s apunta a un archivo inexistente: %s", CA_BUNDLE_ENV, path)
    default = paths.app_data_dir() / "ca-bundle.pem"
    return default if default.is_file() else None


def is_certificate_error(error: BaseException) -> bool:
    """Detecta errores de validación TLS dentro de la cadena de excepciones."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        text = str(current).lower()
        if "certificate_verify_failed" in text or "certificate verify failed" in text:
            return True
        for attribute in ("reason", "__cause__", "__context__"):
            nested = getattr(current, attribute, None)
            if isinstance(nested, BaseException):
                pending.append(nested)
    return False


def certificate_error_message(name: str, detail: BaseException | None = None) -> str:
    """Mensaje accionable para la interfaz."""
    message = (
        f"No se pudo verificar el certificado de seguridad al descargar {name}. "
        "Revisa la fecha de Windows, actualiza el sistema o desactiva la inspección "
        "HTTPS del antivirus/proxy. Si usas un proxy, guarda su certificado en "
        f"{paths.app_data_dir()}\\ca-bundle.pem y pulsa Reintentar."
    )
    if detail is not None:
        logger.error("Error de certificado al descargar %s: %s", name, detail)
    return message


def verified_context() -> ssl.SSLContext:
    """Contexto TLS con los certificados del sistema, certifi y el bundle extra."""
    context = _windows_context() or ssl.create_default_context()
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    except (AttributeError, ValueError):
        pass
    _load_certifi(context)
    bundle = ca_bundle_path()
    if bundle is not None:
        try:
            context.load_verify_locations(cafile=str(bundle))
            logger.info("Usando certificados adicionales de %s", bundle)
        except (OSError, ssl.SSLError, ValueError) as error:
            logger.warning("No se pudo leer el paquete de certificados %s: %s", bundle, error)
    return context


def unverified_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _windows_context() -> ssl.SSLContext | None:
    """Carga explícitamente el almacén de certificados de Windows.

    Se hace a mano para no depender de ``SSL_CERT_FILE``/``SSL_CERT_DIR``, que
    muchas herramientas dejan definidas apuntando a archivos incompletos.
    """
    if os.name != "nt" or not hasattr(ssl, "enum_certificates"):
        return None
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    loaded = 0
    for store in ("ROOT", "CA"):
        try:
            certificates = ssl.enum_certificates(store)
        except (OSError, AttributeError):
            continue
        for cert_der, encoding, trust in certificates:
            if encoding != "x509_asn":
                continue
            if trust is False:
                continue
            if isinstance(trust, set) and "1.3.6.1.5.5.7.3.1" not in trust:
                continue
            try:
                context.load_verify_locations(cadata=cert_der)
                loaded += 1
            except (ssl.SSLError, ValueError):
                continue
    if not loaded:
        return None
    return context


def _load_certifi(context: ssl.SSLContext) -> None:
    try:
        import certifi  # noqa: PLC0415 - dependencia opcional
    except ImportError:
        return
    try:
        context.load_verify_locations(cafile=certifi.where())
    except (OSError, ssl.SSLError, ValueError) as error:
        logger.warning("No se pudo cargar certifi: %s", error)


def download(
    url: str,
    destination: Path,
    *,
    name: str,
    on_progress: ProgressCallback | None = None,
    check_cancel: CancelCallback | None = None,
    allow_insecure: bool = False,
    timeout: float = 45.0,
) -> None:
    """Descarga ``url`` en ``destination`` probando varios métodos."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    partial.unlink(missing_ok=True)

    try:
        try:
            _download_urllib(url, partial, on_progress, check_cancel, verified_context(), timeout)
        except BaseException as error:
            if not is_certificate_error(error):
                raise
            logger.warning("Certificado TLS no verificable al descargar %s: %s", url, error)
            partial.unlink(missing_ok=True)
            try:
                _download_with_curl(url, partial, on_progress, check_cancel)
            except DownloadError as curl_error:
                logger.warning("La descarga con curl también falló: %s", curl_error)
                partial.unlink(missing_ok=True)
                if not allow_insecure:
                    raise CertificateError(certificate_error_message(name, error)) from error
                logger.warning(
                    "Descargando %s sin verificar el certificado por indicación del usuario", url
                )
                _download_urllib(
                    url,
                    partial,
                    on_progress,
                    check_cancel,
                    unverified_context(),
                    timeout,
                )

        if destination.exists():
            destination.unlink()
        os.replace(partial, destination)
        if on_progress is not None:
            try:
                size = destination.stat().st_size
                on_progress(size, size)
            except OSError:
                pass
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _download_urllib(
    url: str,
    destination: Path,
    on_progress: ProgressCallback | None,
    check_cancel: CancelCallback | None,
    context: ssl.SSLContext,
    timeout: float,
) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout, context=context) as response, open(
        destination, "wb"
    ) as output:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            if check_cancel is not None:
                check_cancel()
            chunk = response.read(CHUNK_SIZE)
            if not chunk:
                break
            output.write(chunk)
            done += len(chunk)
            if on_progress is not None:
                on_progress(done, total)


def _download_with_curl(
    url: str,
    destination: Path,
    on_progress: ProgressCallback | None,
    check_cancel: CancelCallback | None,
) -> None:
    curl = shutil.which("curl.exe") or shutil.which("curl")
    if not curl:
        raise DownloadError("curl no está disponible en este equipo")

    destination.unlink(missing_ok=True)
    command = [
        curl,
        "--location",
        "--fail",
        "--silent",
        "--show-error",
        "--retry",
        "2",
        "--retry-delay",
        "1",
        "--connect-timeout",
        "30",
        "--user-agent",
        USER_AGENT,
        "--output",
        str(destination),
        url,
    ]
    logger.info("Reintentando la descarga con curl: %s", url)
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=paths.CREATE_NO_WINDOW,
        )
    except OSError as error:
        raise DownloadError(f"No se pudo iniciar curl: {error}") from error

    try:
        while process.poll() is None:
            if check_cancel is not None:
                check_cancel()
            _report_curl_progress(destination, on_progress)
            time.sleep(0.2)
        stderr = (process.communicate(timeout=10)[1] or "").strip()
    except BaseException:
        _kill(process)
        raise

    if process.returncode != 0:
        detail = stderr or f"código {process.returncode}"
        raise DownloadError(f"curl falló: {detail}")
    if not destination.is_file() or destination.stat().st_size == 0:
        raise DownloadError("curl terminó sin descargar datos")
    _report_curl_progress(destination, on_progress)


def _report_curl_progress(
    destination: Path, on_progress: ProgressCallback | None
) -> None:
    if on_progress is None:
        return
    try:
        size = destination.stat().st_size if destination.exists() else 0
    except OSError:
        size = 0
    on_progress(size, 0)


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
