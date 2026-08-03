# -*- coding: utf-8 -*-
"""
comun.log
=========

Configuracion centralizada de logging para los dos procesos.

Objetivos:

* Escribir simultaneamente a consola y a un archivo con rotacion por fecha.
* Manejar **Unicode de forma segura** (nombres, direcciones y observaciones en
  la base contienen tildes, enies y caracteres especiales).  En Python 2.7 el
  `StreamHandler` por defecto falla con `UnicodeEncodeError` cuando la consola
  no es UTF-8; aqui forzamos la codificacion.
* Ofrecer un unico punto de entrada :func:`obtener_logger`.

Uso tipico::

    from comun.log import obtener_logger
    log = obtener_logger("proceso1", carpeta="logs")
    log.info(u"Sincronizando tabla %s", nombre)
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import io
import logging
import os
import sys
from datetime import datetime

# Formato homogeneo para todos los mensajes.
_FORMATO = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_FECHA = "%Y-%m-%d %H:%M:%S"


class _EscritorUnicode(object):
    """Envuelve un stream de bytes (p. ej. `sys.stdout` en Py2) y codifica a UTF-8.

    Evita que un caracter especial en un mensaje aborte todo el proceso.
    """

    def __init__(self, stream, encoding="utf-8"):
        self._stream = stream
        self._encoding = encoding

    def write(self, texto):
        try:
            if isinstance(texto, bytes):
                self._stream.write(texto)
            else:
                self._stream.write(texto.encode(self._encoding, "replace"))
        except Exception:
            # Ultimo recurso: nunca dejar que el logging tumbe el proceso.
            try:
                self._stream.write(repr(texto))
            except Exception:
                pass

    def flush(self):
        try:
            self._stream.flush()
        except Exception:
            pass


def obtener_logger(nombre, carpeta="logs", nivel=logging.INFO):
    """Devuelve un logger configurado (idempotente).

    :param nombre:  Nombre logico del proceso (define el archivo de log).
    :param carpeta: Carpeta donde se depositan los archivos ``.log``.
    :param nivel:   Nivel minimo (``logging.INFO`` por defecto).
    :return:        Instancia de :class:`logging.Logger`.
    """
    log = logging.getLogger(nombre)
    if getattr(log, "_configurado", False):
        # Ya fue configurado en esta ejecucion; no duplicar handlers.
        return log

    log.setLevel(nivel)
    log.propagate = False

    formato = logging.Formatter(_FORMATO, _FECHA)

    # --- Handler de consola (con envoltura Unicode) ---------------------------
    stream = getattr(sys.stdout, "buffer", sys.stdout)  # Py3 usa .buffer; Py2 no.
    consola = logging.StreamHandler(_EscritorUnicode(stream))
    consola.setFormatter(formato)
    consola.setLevel(nivel)
    log.addHandler(consola)

    # --- Handler de archivo ---------------------------------------------------
    try:
        if not os.path.isdir(carpeta):
            os.makedirs(carpeta)
        marca = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta = os.path.join(carpeta, "%s_%s.log" % (nombre, marca))
        # io.open garantiza escritura UTF-8 tanto en Py2 como Py3.
        archivo = logging.StreamHandler(io.open(ruta, mode="a", encoding="utf-8"))
        archivo.setFormatter(formato)
        archivo.setLevel(logging.DEBUG)  # el archivo guarda todo el detalle.
        log.addHandler(archivo)
        log.debug(u"Archivo de log: %s", ruta)
    except Exception as exc:  # pragma: no cover - problemas de permisos, etc.
        log.warning(u"No se pudo crear el archivo de log: %s", exc)

    log._configurado = True
    return log
