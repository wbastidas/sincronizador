# -*- coding: utf-8 -*-
"""
Paquete `comun`
===============

Contiene los modulos compartidos por los dos procesos de sincronizacion:

* :mod:`comun.config`      - Carga y validacion de los archivos de configuracion.
* :mod:`comun.log`         - Registro (logging) con manejo seguro de Unicode.
* :mod:`comun.utiles`      - Utilidades de normalizacion, firmas de fila y GUID.
* :mod:`comun.oracle_db`   - Envoltura de cx_Oracle (conexion, metadatos, DML).
* :mod:`comun.modelo`      - Metadatos del modelo (llaves, geometria, red, relaciones).
* :mod:`comun.dominios`    - Comparacion y sincronizacion de dominios.
* :mod:`comun.comparador`  - Motor de diferencias (nuevos, eliminados, modificados).

Todo el codigo es compatible con **Python 2.7** (que es el interprete que embebe
ArcGIS Desktop 10.8.1) y evita en lo posible dependencias externas mas alla de
`cx_Oracle` (proceso 1) y `arcpy` (proceso 2).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

__all__ = [
    "config",
    "log",
    "utiles",
    "oracle_db",
    "modelo",
    "dominios",
    "comparador",
]

__version__ = "1.0.0"
