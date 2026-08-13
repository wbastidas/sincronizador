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

# Permite ejecutar tanto como modulo (python -m herramientas.reporte_diferencias)
# como script directo (python herramientas/reporte_diferencias.py) agregando la
# raiz del proyecto al path para poder importar el paquete `comun`.
_RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

from comun import config as cfg_mod
from comun import dominios as dom_mod
from comun import modelo
from comun.comparador import comparar
from comun.log import obtener_logger
from comun.oracle_db import ConexionOracle
from comun.reporte import EscritorCSV, escribir_excel, openpyxl_disponible
from comun.utiles import normalizar_guid


class GeneradorReporte(object):
    def __init__(self, configuracion, log, con_geometria=False,
                 modo_verificacion=False, llave_destino=None):
        self.cfg = configuracion
        self.log = log
        self.con_geometria = con_geometria
        # Llave del destino cuando difiere de la del origen (verificacion del
        # proceso 2: origen por GLOBALID, destino por MIGUID).
        self.llave_destino = llave_destino
        # En modo verificacion el reporte se interpreta como control POST-carga:
        # el resultado esperado es CERO diferencias.
        self.modo_verificacion = modo_verificacion
        self.filas_resumen = []   # para xlsx
        self.filas_detalle = []
        self.filas_dominios = []
        self.filas_verificacion = []  # (TABLA, DIFERENCIAS, ESTADO)
        self.total_diferencias = 0    # suma global (tablas + dominios)

    def ejecutar(self, carpeta_salida):
        titulo = ("Verificacion POST-sincronizacion (esperado: 0 diferencias)"
                  if self.modo_verificacion
                  else "Reporte de diferencias (vista previa, sin cambios)")
        self.log.info(u"=== %s ===", titulo)
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
                        self.filas_verificacion.append(
                            [cfg_tabla.nombre, "", "ERROR"])

            self._reportar_dominios(origen, destino, carpeta_salida)

        if self.modo_verificacion:
            self._escribir_verificacion(carpeta_salida)

        # Libro Excel combinado (opcional).
        self._escribir_excel(carpeta_salida)
        self.log.info(u"Reporte generado en: %s", carpeta_salida)
        return self.total_diferencias

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
        # Excluir de la comparacion las FK remapeadas por relaciones (quedan con
        # la identidad del destino tras el remapeo).
        fks = modelo.columnas_fk_de_tabla(nombre, self.cfg.relaciones)
        comparables = modelo.columnas_comparables(comunes, cfg_tabla,
                                                  extra_ignorar=fks)

        llave = cfg_tabla.llave_negocio.upper()
        llave_dest = (self.llave_destino or cfg_tabla.llave_negocio).upper()
        seleccion = list(comparables)
        for extra in (llave, llave_dest, "OBJECTID"):
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
                       firma_geometria=firma_geom,
                       llave_destino=self.llave_destino)

        w_res.fila([nombre, len(res.nuevos), len(res.modificados),
                    len(res.eliminados), res.iguales, len(filas_o), len(filas_d)])
        self.filas_resumen.append([nombre, len(res.nuevos), len(res.modificados),
                                   len(res.eliminados), res.iguales,
                                   len(filas_o), len(filas_d)])
        self.log.info(res.resumen())

        # Acumulado para la verificacion post-sincronizacion.
        dif_tabla = len(res.nuevos) + len(res.modificados) + len(res.eliminados)
        self.total_diferencias += dif_tabla
        self.filas_verificacion.append(
            [nombre, dif_tabla, "OK" if dif_tabla == 0 else "CON_DIFERENCIAS"])

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
        # Cada valor de dominio distinto cuenta como una diferencia.
        dif_dom = sum(len(d.codigos_agregar) + len(d.codigos_quitar) +
                      len(d.codigos_cambiar) for d in difs)
        self.total_diferencias += dif_dom
        self.filas_verificacion.append(
            ["(DOMINIOS)", dif_dom, "OK" if dif_dom == 0 else "CON_DIFERENCIAS"])
        self.log.info(u"Dominios con diferencias: %d (valores: %d)",
                      len(difs), dif_dom)

    def _fila_dom(self, w, nombre, estado, accion, cod, vo, vd):
        w.fila([nombre, estado, accion, cod, vo, vd])
        self.filas_dominios.append([nombre, estado, accion, cod, vo, vd])

    # ------------------------------------------------------------------ #
    def _escribir_verificacion(self, carpeta):
        """Escribe verificacion.csv con el estado por tabla y el veredicto global."""
        with EscritorCSV(os.path.join(carpeta, "verificacion.csv"),
                         ["TABLA", "DIFERENCIAS", "ESTADO"]) as w:
            for fila in self.filas_verificacion:
                w.fila(fila)
            veredicto = "SINCRONIZADO" if self.total_diferencias == 0 \
                else "PENDIENTE"
            w.fila(["=== VEREDICTO GLOBAL ===", self.total_diferencias, veredicto])

        if self.total_diferencias == 0:
            self.log.info(u"VERIFICACION OK: ORIGEN y DESTINO sin diferencias. "
                          u"Bases SINCRONIZADAS.")
        else:
            self.log.warning(
                u"VERIFICACION: quedan %d diferencia(s). Bases AUN NO iguales; "
                u"revise verificacion.csv y detalle.csv.", self.total_diferencias)

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
        if self.modo_verificacion:
            hojas.append(("Verificacion",
                          ["TABLA", "DIFERENCIAS", "ESTADO"],
                          self.filas_verificacion))
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
    parser.add_argument("--verificar", action="store_true",
                        help="Modo verificacion POST-sincronizacion: agrega "
                             "verificacion.csv con veredicto global y devuelve "
                             "codigo de salida 1 si quedan diferencias.")
    parser.add_argument("--llave-destino", default=None,
                        help="Columna llave del destino cuando difiere de la del "
                             "origen. Para verificar el proceso 2 use MIGUID "
                             "(el destino se indexa por MIGUID = GLOBALID origen).")
    args = parser.parse_args(argv)

    log = obtener_logger("reporte_diferencias")
    try:
        configuracion = cfg_mod.cargar(args.conexiones, args.tablas)
    except cfg_mod.ErrorConfig as exc:
        log.error(u"Configuracion invalida: %s", exc)
        return 2
    if args.incluir_red:
        configuracion.incluir_red_geometrica = True

    total = GeneradorReporte(configuracion, log, con_geometria=args.con_geometria,
                             modo_verificacion=args.verificar,
                             llave_destino=args.llave_destino).ejecutar(args.salida)

    # En verificacion, el codigo de salida refleja el resultado (0 = sin
    # diferencias, apto para automatizacion/CI post-carga).
    if args.verificar:
        return 0 if total == 0 else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
