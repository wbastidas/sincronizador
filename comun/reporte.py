# -*- coding: utf-8 -*-
"""
comun.reporte
=============

Escritura de reportes de diferencias en **CSV** (siempre) y **Excel .xlsx**
(si esta disponible ``openpyxl``).

El CSV se escribe con **BOM UTF-8** para que Microsoft Excel muestre
correctamente tildes, enies y caracteres especiales al abrirlo con doble clic.

La clase :class:`EscritorCSV` abstrae las diferencias entre Python 2.7 (el modulo
``csv`` trabaja con *bytes*) y Python 3 (trabaja con *texto*).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import csv
import io
import os
import sys

from comun.utiles import to_unicode

_PY2 = sys.version_info[0] == 2


class EscritorCSV(object):
    """Escribe filas (listas de valores) a un CSV UTF-8 con BOM.

    Uso::

        with EscritorCSV(ruta, ["col1", "col2"]) as w:
            w.fila([valor1, valor2])
    """

    def __init__(self, ruta, encabezados, separador=","):
        self.ruta = ruta
        carpeta = os.path.dirname(ruta)
        if carpeta and not os.path.isdir(carpeta):
            os.makedirs(carpeta)
        if _PY2:
            self._fh = open(ruta, "wb")
            self._fh.write(b"\xef\xbb\xbf")  # BOM UTF-8
            self._writer = csv.writer(self._fh, delimiter=str(separador))
        else:
            self._fh = io.open(ruta, "w", encoding="utf-8-sig", newline="")
            self._writer = csv.writer(self._fh, delimiter=separador)
        self.fila(encabezados)

    def fila(self, valores):
        celdas = []
        for v in valores:
            texto = u"" if v is None else to_unicode(v)
            if _PY2:
                celdas.append(texto.encode("utf-8"))
            else:
                celdas.append(texto)
        self._writer.writerow(celdas)

    def cerrar(self):
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.cerrar()


def openpyxl_disponible():
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def escribir_excel(ruta, hojas):
    """Escribe un libro .xlsx con varias hojas si ``openpyxl`` esta disponible.

    :param hojas: lista de tuplas ``(nombre_hoja, encabezados, filas)`` donde
                  ``filas`` es una lista de listas de valores.
    :return: True si se escribio, False si openpyxl no esta disponible.
    """
    if not openpyxl_disponible():
        return False
    import openpyxl
    from openpyxl.styles import Font

    libro = openpyxl.Workbook()
    # Quitar la hoja por defecto que crea openpyxl.
    libro.remove(libro.active)

    for nombre, encabezados, filas in hojas:
        hoja = libro.create_sheet(title=nombre[:31])  # Excel limita a 31 chars.
        hoja.append([to_unicode(c) for c in encabezados])
        for celda in hoja[1]:
            celda.font = Font(bold=True)
        for fila in filas:
            hoja.append([u"" if v is None else to_unicode(v) for v in fila])
        # Congelar la fila de encabezados.
        hoja.freeze_panes = "A2"

    carpeta = os.path.dirname(ruta)
    if carpeta and not os.path.isdir(carpeta):
        os.makedirs(carpeta)
    libro.save(ruta)
    return True
