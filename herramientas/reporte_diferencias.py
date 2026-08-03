# -*- coding: utf-8 -*-
"""
herramientas.reporte_diferencias
================================

Genera un **reporte de diferencias entre ORIGEN y DESTINO sin aplicar ningun
cambio** (vista previa). Usa la misma logica de comparacion que los procesos de
sincronizacion, leyendo por Oracle (rapido, no requiere arcpy).

Produce:

* ``resumen.csv``   : una fila por tabla con los conteos (nuevos, modificados,
                      eliminados, iguales, totales).
* ``detalle.csv``   : una fila por diferencia (tipo, llave, columnas cambiadas).
* ``dominios.csv``  : diferencias de dominios (valores a agregar/quitar/cambiar).
* ``reporte_diferencias.xlsx`` : el mismo contenido en un libro de varias hojas,
                      solo si ``openpyxl`` esta instalado.

Todos los CSV se escriben en UTF-8 con BOM para que Excel muestre bien los
caracteres especiales.

Uso (Python 2.7 o 3)::

    python herramientas/reporte_diferencias.py \\
        --conexiones config/conexiones.json \\
        --tablas     config/tablas.json \\
        --salida     reportes/2026-08-03
    # opcionales:  --con-geometria   (compara tambien la geometria, mas lento)
    #              --incluir-red     (incluye las tablas de red geometrica)
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import hashlib
import os
import sys

from comun import config as cfg_mod
from comun import dominios as dom_mod
from comun import modelo
from comun.comparador import comparar
from comun.log import obtener_logger
from comun.oracle_db import ConexionOracle
from comun.reporte import EscritorCSV, escribir_excel, openpyxl_disponible
from comun.utiles import normalizar_guid


class GeneradorReporte(object):
    def __init__(self, configuracion, log, con_geometria=False):
        self.cfg = configuracion
        self.log = log
        self.con_geometria = con_geometria
        self.filas_resumen = []   # para xlsx
        self.filas_detalle = []
        self.filas_dominios = []

    def ejecutar(self, carpeta_salida):
        self.log.info(u"=== Reporte de diferencias (vista previa, sin cambios) ===")
        with ConexionOracle(self.cfg.origen.oracle) as origen, \
                ConexionOracle(self.cfg.destino.oracle) as destino:

            with EscritorCSV(os.path.join(carpeta_salida, "resumen.csv"),
                             ["TABLA", "NUEVOS", "MODIFICADOS", "ELIMINADOS",
                              "IGUALES", "TOTAL_ORIGEN", "TOTAL_DESTINO"]) as w_res, \
                 EscritorCSV(os.path.join(carpeta_salida, "detalle.csv"),
                             ["TABLA", "TIPO", "LLAVE", "COLUMNAS_CAMBIADAS"]) as w_det:

                for cfg_tabla in self.cfg.tablas_a_procesar():
                    try:
                        self._reportar_tabla(origen, destino, cfg_tabla,
                                             w_res, w_det)
                    except Exception as exc:
                        self.log.error(u"Tabla %s: error en el reporte: %s",
                                       cfg_tabla.nombre, exc)
                        w_res.fila([cfg_tabla.nombre, "ERROR", "", "", "", "", ""])

            self._reportar_dominios(origen, destino, carpeta_salida)

        # Libro Excel combinado (opcional).
        self._escribir_excel(carpeta_salida)
        self.log.info(u"Reporte generado en: %s", carpeta_salida)

    # ------------------------------------------------------------------ #
    def _reportar_tabla(self, origen, destino, cfg_tabla, w_res, w_det):
        nombre = cfg_tabla.nombre
        cols_o = [c["NOMBRE"] for c in origen.columnas_tabla(nombre)]
        cols_d = [c["NOMBRE"] for c in destino.columnas_tabla(nombre)]
        if not cols_o or not cols_d:
            self.log.warning(u"Tabla %s no existe en ambas bases; se omite.", nombre)
            w_res.fila([nombre, "NO_EXISTE", "", "", "", len(cols_o), len(cols_d)])
            return
        comunes = [c for c in cols_o if c in set(cols_d)]
        comparables = modelo.columnas_comparables(comunes, cfg_tabla)

        llave = cfg_tabla.llave_negocio.upper()
        seleccion = list(comparables)
        for extra in (llave, "OBJECTID"):
            if extra not in seleccion:
                seleccion.append(extra)
        where = (" WHERE " + cfg_tabla.filtro) if cfg_tabla.filtro else ""
        sql = "SELECT %s FROM %s%s" % (", ".join(seleccion), nombre, where)

        filas_o = origen.consultar(sql)
        filas_d = destino.consultar(sql)

        firma_geom = None
        if self.con_geometria and cfg_tabla.columna_geometria and \
                "SHAPE" in {c.upper() for c in comunes}:
            firma_geom = self._firmas_geometria(origen, destino, cfg_tabla, where)

        res = comparar(nombre, filas_o, filas_d, comparables, llave,
                       firma_geometria=firma_geom)

        w_res.fila([nombre, len(res.nuevos), len(res.modificados),
                    len(res.eliminados), res.iguales, len(filas_o), len(filas_d)])
        self.filas_resumen.append([nombre, len(res.nuevos), len(res.modificados),
                                   len(res.eliminados), res.iguales,
                                   len(filas_o), len(filas_d)])
        self.log.info(res.resumen())

        for k in res.nuevos:
            self._detalle(w_det, nombre, "NUEVO", k, "")
        for k in res.eliminados:
            self._detalle(w_det, nombre, "ELIMINADO", k, "")
        for k, cambios in res.modificados:
            self._detalle(w_det, nombre, "MODIFICADO", k, ", ".join(cambios))

    def _detalle(self, w_det, tabla, tipo, llave, columnas):
        w_det.fila([tabla, tipo, llave, columnas])
        self.filas_detalle.append([tabla, tipo, llave, columnas])

    def _firmas_geometria(self, origen, destino, cfg_tabla, where):
        col = cfg_tabla.columna_geometria
        llave = cfg_tabla.llave_negocio.upper()
        # ST_GEOMETRY: WKT via SDE.ST_ASTEXT.
        sql = "SELECT %s AS K, SDE.ST_ASTEXT(%s) AS WKT FROM %s%s" % (
            llave, col, cfg_tabla.nombre, where)

        def cargar(db):
            m = {}
            try:
                for f in db.iterar(sql):
                    k = normalizar_guid(f["K"])
                    wkt = f.get("WKT") or u""
                    m[k] = hashlib.md5(wkt.encode("utf-8")).hexdigest()
            except Exception as exc:
                self.log.warning(u"Geometria %s no comparada: %s",
                                 cfg_tabla.nombre, exc)
            return m

        mo, md = cargar(origen), cargar(destino)
        return {k: (mo.get(k), md.get(k)) for k in set(mo) | set(md)}

    # ------------------------------------------------------------------ #
    def _reportar_dominios(self, origen, destino, carpeta):
        if not self.cfg.sincronizar_dominios:
            return
        self.log.info(u"--- Comparando dominios ---")
        try:
            dom_o = dom_mod.leer_dominios_gdb(origen)
            dom_d = dom_mod.leer_dominios_gdb(destino)
        except Exception as exc:
            self.log.warning(u"No se pudieron leer dominios: %s", exc)
            return
        difs = dom_mod.comparar_dominios(dom_o, dom_d)
        with EscritorCSV(os.path.join(carpeta, "dominios.csv"),
                         ["DOMINIO", "ESTADO", "ACCION", "CODIGO",
                          "VALOR_ORIGEN", "VALOR_DESTINO"]) as w:
            for d in difs:
                for cod, desc in sorted(d.codigos_agregar.items()):
                    self._fila_dom(w, d.nombre, d.estado, "AGREGAR", cod, desc, "")
                for cod, desc in sorted(d.codigos_quitar.items()):
                    self._fila_dom(w, d.nombre, d.estado, "QUITAR", cod, "", desc)
                for cod, (o, dd) in sorted(d.codigos_cambiar.items()):
                    self._fila_dom(w, d.nombre, d.estado, "CAMBIAR", cod, o, dd)
        self.log.info(u"Dominios con diferencias: %d", len(difs))

    def _fila_dom(self, w, nombre, estado, accion, cod, vo, vd):
        w.fila([nombre, estado, accion, cod, vo, vd])
        self.filas_dominios.append([nombre, estado, accion, cod, vo, vd])

    # ------------------------------------------------------------------ #
    def _escribir_excel(self, carpeta):
        if not openpyxl_disponible():
            self.log.info(u"openpyxl no disponible: se generaron solo los CSV "
                          u"(Excel los abre directamente).")
            return
        ruta = os.path.join(carpeta, "reporte_diferencias.xlsx")
        hojas = [
            ("Resumen",
             ["TABLA", "NUEVOS", "MODIFICADOS", "ELIMINADOS", "IGUALES",
              "TOTAL_ORIGEN", "TOTAL_DESTINO"], self.filas_resumen),
            ("Detalle",
             ["TABLA", "TIPO", "LLAVE", "COLUMNAS_CAMBIADAS"], self.filas_detalle),
            ("Dominios",
             ["DOMINIO", "ESTADO", "ACCION", "CODIGO", "VALOR_ORIGEN",
              "VALOR_DESTINO"], self.filas_dominios),
        ]
        if escribir_excel(ruta, hojas):
            self.log.info(u"Libro Excel: %s", ruta)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Reporte de diferencias ORIGEN vs DESTINO (sin aplicar cambios)")
    parser.add_argument("--conexiones", default="config/conexiones.json")
    parser.add_argument("--tablas", default="config/tablas.json")
    parser.add_argument("--salida", default="reportes")
    parser.add_argument("--con-geometria", action="store_true",
                        help="Compara tambien la geometria (mas lento).")
    parser.add_argument("--incluir-red", action="store_true",
                        help="Incluye las tablas de la red geometrica.")
    args = parser.parse_args(argv)

    log = obtener_logger("reporte_diferencias")
    try:
        configuracion = cfg_mod.cargar(args.conexiones, args.tablas)
    except cfg_mod.ErrorConfig as exc:
        log.error(u"Configuracion invalida: %s", exc)
        return 2
    if args.incluir_red:
        configuracion.incluir_red_geometrica = True

    GeneradorReporte(configuracion, log, con_geometria=args.con_geometria) \
        .ejecutar(args.salida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
