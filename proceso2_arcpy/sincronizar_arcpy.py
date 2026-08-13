# -*- coding: utf-8 -*-
"""
proceso2_arcpy.sincronizar_arcpy
================================

PROCESO 2 - Sincronizacion con **arcpy** para ArcGIS Desktop 10.8.1.

Escenario
---------
Igual objetivo que el proceso 1 (dejar el destino identico al origen), pero la
ESCRITURA se hace con la libreria ``arcpy`` para respetar el comportamiento de
la **red geometrica** y demas reglas de la geodatabase.  La LECTURA de atributos
se hace desde **Oracle** (mas rapido); la **geometria** se lee con arcpy (via
mas fiable para SDE).

Modelo de identidad (lo esencial del requerimiento)
---------------------------------------------------
Al insertar con arcpy NO se puede escribir OBJECTID ni GLOBALID.  Por eso:

1. Se inserta el feature copiando atributos + geometria y guardando:
      - ``MIOID``  = OBJECTID del origen
      - ``MIGUID`` = GLOBALID del origen
2. Tras insertar, ArcGIS asigna un GLOBALID/OBJECTID NUEVOS en destino.  Se leen
   y se arman los mapas ``guid_origen->guid_destino`` y ``oid_origen->oid_destino``.
3. Con esos mapas se **reparan las relaciones via Oracle** (rapido): cada columna
   hija que apuntaba al GLOBALID/OBJECTID del origen se actualiza al valor de
   destino, para que **no se pierda la relacion**.

Ademas se comparan y aplican los **dominios** con las herramientas de arcpy y se
maneja la **red geometrica** dentro de una sesion de edicion.

Ejecucion (con el Python 2.7 de ArcGIS Desktop 10.8.1)::

    "C:\\Python27\\ArcGIS10.8\\python.exe" -m proceso2_arcpy.sincronizar_arcpy \\
        --conexiones config/conexiones.json --tablas config/tablas.json
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import hashlib
import os
import sys

from comun import config as cfg_mod
from comun import dominios as dom_mod
from comun import modelo
from comun.log import obtener_logger
from comun.oracle_db import ConexionOracle
from comun.relaciones import remapear_fk
from comun.utiles import firma_fila, diferencias_campos, normalizar_guid

from proceso2_arcpy import arcpy_io
from proceso2_arcpy import dominios_arcpy
from proceso2_arcpy.red_geometrica import RedGeometrica, SesionEdicion


class SincronizadorArcpy(object):
    def __init__(self, configuracion, log):
        self.cfg = configuracion
        self.log = log
        self._arcpy = None
        # Mapas globales de identidad para reparar relaciones al final.
        self.mapa_guid = {}   # {tabla: {guid_origen: guid_destino}}
        self.mapa_oid = {}    # {tabla: {oid_origen: oid_destino}}

    # ------------------------------------------------------------------ #
    def ejecutar(self):
        self.log.info(u"=== PROCESO 2: sincronizacion con arcpy (Desktop 10.8.1) ===")
        try:
            import arcpy
        except ImportError:
            self.log.error(u"arcpy no disponible. Ejecute con el Python de ArcGIS "
                           u"Desktop 10.8.1.")
            return 2
        self._arcpy = arcpy
        arcpy.env.overwriteOutput = True
        # Mantener los GLOBALID/atributos tal cual (no recalcular) donde aplique.
        arcpy.env.maintainAttachments = True

        ws_destino = self.cfg.destino.sde_workspace
        ws_origen = self.cfg.origen.sde_workspace
        if not ws_destino:
            self.log.error(u"Falta 'sde_workspace' de destino en conexiones.json")
            return 2

        red = RedGeometrica(arcpy, ws_destino, self.log)
        clases_red = red.clases_en_red()

        escritor = arcpy_io.EscritorArcpy(arcpy, self.log)

        with ConexionOracle(self.cfg.origen.oracle) as ora_origen, \
                ConexionOracle(self.cfg.destino.oracle) as ora_destino:

            # 1) Dominios (comparacion via GDB_ITEMS, aplicacion via arcpy).
            if self.cfg.sincronizar_dominios:
                self._sincronizar_dominios(ora_origen, ora_destino, ws_destino)

            # 2) Tablas dentro de una sesion de edicion (obligatoria por la red).
            with SesionEdicion(arcpy, ws_destino, self.log) as sesion:
                for cfg_tabla in self.cfg.tablas_a_procesar():
                    try:
                        self._sincronizar_tabla(
                            cfg_tabla, ora_origen, ws_origen, ws_destino,
                            escritor, clases_red)
                    except Exception as exc:
                        self.log.error(u"Tabla %s: ERROR: %s", cfg_tabla.nombre, exc)
                        raise  # aborta la sesion completa para no dejar a medias.
                sesion.marcar_ok()

            # 3) Reparacion de relaciones via Oracle (rapido y transaccional).
            self._reparar_relaciones(ora_destino)
            if not self.cfg.modo_simulacion:
                ora_destino.commit()

            # 4) Verificar/reparar la conectividad de la red geometrica.
            if self.cfg.incluir_red_geometrica or clases_red:
                red.verificar_y_reparar()

        self.log.info(u"=== PROCESO 2 finalizado ===")
        return 0

    # ------------------------------------------------------------------ #
    # Dominios
    # ------------------------------------------------------------------ #
    def _sincronizar_dominios(self, ora_origen, ora_destino, ws_destino):
        self.log.info(u"--- Sincronizando dominios (arcpy) ---")
        try:
            dom_o = dom_mod.leer_dominios_gdb(ora_origen)
            dom_d = dom_mod.leer_dominios_gdb(ora_destino)
        except Exception as exc:
            self.log.warning(u"No se pudieron leer dominios: %s", exc)
            return
        diferencias = dom_mod.comparar_dominios(dom_o, dom_d)
        dominios_arcpy.aplicar_dominios(
            self._arcpy, ws_destino, diferencias, dom_o, self.log,
            simular=self.cfg.modo_simulacion)

    # ------------------------------------------------------------------ #
    # Sincronizacion de una tabla
    # ------------------------------------------------------------------ #
    def _sincronizar_tabla(self, cfg_tabla, ora_origen, ws_origen, ws_destino,
                           escritor, clases_red):
        nombre = cfg_tabla.nombre
        self.log.info(u"--- Tabla %s ---", nombre)

        ruta_origen = os.path.join(ws_origen, nombre) if ws_origen else None
        ruta_destino = self._ruta_fc(ws_destino, nombre)

        # Metadatos de columnas comunes.
        cols_o = [c["NOMBRE"] for c in ora_origen.columnas_tabla(nombre)]
        if not cols_o:
            self.log.warning(u"Tabla %s no existe en origen; se omite.", nombre)
            return
        # Excluir de la comparacion las FK remapeadas por relaciones (quedan con
        # la identidad del destino tras el remapeo via Oracle).
        fks = modelo.columnas_fk_de_tabla(nombre, self.cfg.relaciones)
        comparables = modelo.columnas_comparables(cols_o, cfg_tabla,
                                                  extra_ignorar=fks)
        # En proceso 2 NO se escriben OBJECTID ni GLOBALID en el INSERT.
        copiables = modelo.columnas_copiables(cols_o, cfg_tabla,
                                              incluir_globalid=False)
        # Quitar columnas espejo de las copiables (se rellenan explicitamente).
        copiables = [c for c in copiables if c not in ("MIOID", "MIGUID")]

        es_red = nombre.upper() in clases_red or cfg_tabla.red_geometrica
        if es_red and not self.cfg.incluir_red_geometrica:
            self.log.warning(
                u"Tabla %s participa en la red geometrica y la opcion esta "
                u"desactivada; se omite (opcional).", nombre)
            return
        if es_red:
            self.log.info(u"  (Tabla de red geometrica: edicion dentro de sesion.)")

        tiene_geometria = "SHAPE" in {c.upper() for c in cols_o} and \
            cfg_tabla.columna_geometria

        # --- Lectura del ORIGEN: atributos desde Oracle (rapido) -------------
        where = (" WHERE " + cfg_tabla.filtro) if cfg_tabla.filtro else ""
        vistas = self._columnas_select(copiables, comparables, cfg_tabla)
        sql = "SELECT %s FROM %s%s" % (", ".join(vistas), nombre, where)
        filas_origen = ora_origen.consultar(sql)
        idx_origen = self._indexar(filas_origen, cfg_tabla.llave_negocio)

        # --- Geometria del ORIGEN: via arcpy (WKB) ---------------------------
        geom_origen = {}
        if tiene_geometria and ruta_origen:
            geom_origen = arcpy_io.leer_geometria_origen(
                self._arcpy, ruta_origen, cfg_tabla.llave_negocio)

        # --- Lectura del DESTINO: indexado por MIGUID ------------------------
        idx_destino, nativas = escritor.leer_destino_indexado(
            ruta_destino, comparables, tiene_geometria)

        # --- Diff (por el GLOBALID de origen) --------------------------------
        nuevos, modificados, eliminados = self._diferenciar(
            idx_origen, idx_destino, comparables, tiene_geometria, geom_origen)

        self.log.info(u"[%s] nuevos=%d modificados=%d eliminados=%d",
                      nombre, len(nuevos), len(modificados), len(eliminados))

        if self.cfg.modo_simulacion:
            return

        # --- INSERT ----------------------------------------------------------
        if nuevos:
            filas_nuevas = [idx_origen[k] for k in nuevos]
            mapas = escritor.insertar(
                ruta_destino, filas_nuevas, copiables, tiene_geometria,
                geom_origen, cfg_tabla.llave_negocio)
            self.mapa_guid.setdefault(nombre.upper(), {}).update(mapas["guid"])
            self.mapa_oid.setdefault(nombre.upper(), {}).update(mapas["oid"])

        # --- UPDATE ----------------------------------------------------------
        if modificados:
            cambios = {}
            for k, cols in modificados:
                cambios[k] = (idx_origen[k], cols)
            escritor.actualizar(ruta_destino, cambios, copiables,
                                tiene_geometria, geom_origen)

        # --- Registro de identidad de las filas EXISTENTES (iguales + modif) --
        # Su GLOBALID/OBJECTID de destino ya existe y no cambia; se registra el
        # mapa (identidad_origen -> identidad_destino) para reparar relaciones,
        # incluso cuando un hijo nuevo apunta a un padre que no cambio.
        for k in (set(idx_origen) & set(idx_destino)):
            fd = idx_destino[k]
            gid_dest = fd.get("GLOBALID")
            if gid_dest:
                self.mapa_guid.setdefault(nombre.upper(), {})[k] = \
                    normalizar_guid(gid_dest)
            oid_dest = fd.get("OBJECTID")
            oid_orig = idx_origen[k].get("OBJECTID")
            if oid_dest is not None and oid_orig is not None:
                self.mapa_oid.setdefault(nombre.upper(), {})[oid_orig] = oid_dest

        # --- DELETE ----------------------------------------------------------
        if eliminados and cfg_tabla.politica_borrado != "ninguna":
            escritor.eliminar(ruta_destino, eliminados)

    # ------------------------------------------------------------------ #
    # Diff especifico del proceso 2 (origen por GLOBALID, destino por MIGUID)
    # ------------------------------------------------------------------ #
    def _diferenciar(self, idx_origen, idx_destino, comparables, tiene_geometria,
                     geom_origen):
        nuevos, modificados = [], []
        llaves_o = set(idx_origen)
        llaves_d = set(idx_destino)

        for k in (llaves_o - llaves_d):
            nuevos.append(k)

        eliminados = list(llaves_d - llaves_o)

        for k in (llaves_o & llaves_d):
            fo = idx_origen[k]
            fd = idx_destino[k]
            if firma_fila(fo, comparables) != firma_fila(fd, comparables):
                cambios = diferencias_campos(fo, fd, comparables)
                modificados.append((k, cambios))
            elif tiene_geometria:
                # Comparar geometria por hash WKB.
                wkb_o = geom_origen.get(k)
                wkb_d = fd.get("SHAPE@WKB")
                if self._hash_wkb(wkb_o) != self._hash_wkb(wkb_d):
                    modificados.append((k, ["SHAPE"]))

        nuevos.sort()
        eliminados.sort()
        modificados.sort(key=lambda x: x[0])
        return nuevos, modificados, eliminados

    @staticmethod
    def _hash_wkb(wkb):
        if wkb is None:
            return None
        if isinstance(wkb, memoryview):
            wkb = wkb.tobytes()
        if isinstance(wkb, bytearray):
            wkb = bytes(wkb)
        try:
            return hashlib.md5(wkb).hexdigest()
        except Exception:
            return hashlib.md5(repr(wkb).encode("utf-8")).hexdigest()

    # ------------------------------------------------------------------ #
    # Reparacion de relaciones via Oracle
    # ------------------------------------------------------------------ #
    def _reparar_relaciones(self, ora_destino):
        if not self.cfg.relaciones:
            return
        self.log.info(u"--- Reparando relaciones via Oracle ---")
        for rel in self.cfg.relaciones:
            if rel.tipo_llave == "guid":
                mapa = self.mapa_guid.get(rel.tabla_origen.upper(), {})
            else:
                mapa = self.mapa_oid.get(rel.tabla_origen.upper(), {})
            if not mapa:
                continue
            self._remapear_fk(ora_destino, rel, mapa)
        if not self.cfg.modo_simulacion:
            ora_destino.commit()

    def _remapear_fk(self, ora_destino, rel, mapa):
        """Actualiza la columna FK de la tabla hija: viejo_valor -> nuevo_valor.

        Usa el remapeo seguro en dos fases (comun.relaciones), a prueba de
        colisiones entre el espacio de valores viejos y nuevos, por lotes.
        """
        remapear_fk(ora_destino, rel.tabla_destino, rel.columna_fk, mapa,
                    rel.tipo_llave, self.cfg.tamano_lote, self.log)

    # ------------------------------------------------------------------ #
    # Auxiliares
    # ------------------------------------------------------------------ #
    def _ruta_fc(self, workspace, nombre):
        """Resuelve la ruta a la feature class/tabla dentro del workspace.

        Busca primero como tabla suelta y, si no, dentro de feature datasets
        (necesario para las clases que estan en el dataset de la red).
        """
        arcpy = self._arcpy
        directa = os.path.join(workspace, nombre)
        if arcpy.Exists(directa):
            return directa
        # Buscar dentro de feature datasets.
        entorno = arcpy.env.workspace
        try:
            arcpy.env.workspace = workspace
            for fds in arcpy.ListDatasets("*", "Feature") or []:
                candidata = os.path.join(workspace, fds, nombre)
                if arcpy.Exists(candidata):
                    return candidata
        finally:
            arcpy.env.workspace = entorno
        return directa  # se devuelve la directa aunque no exista (dara error claro).

    def _columnas_select(self, copiables, comparables, cfg_tabla):
        llave = cfg_tabla.llave_negocio.upper()
        cols = list(copiables)
        for extra in comparables + [llave, "OBJECTID"]:
            if extra not in cols:
                cols.append(extra)
        return cols

    def _indexar(self, filas, llave_negocio):
        llave = llave_negocio.upper()
        es_guid = llave in ("GLOBALID", "GUID", "MIGUID")
        idx = {}
        for f in filas:
            v = f.get(llave)
            v = normalizar_guid(v) if es_guid else (None if v is None else u"%s" % v)
            if v is not None:
                idx[v] = f
        return idx


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Proceso 2: sincronizacion con arcpy (ArcGIS Desktop 10.8.1)")
    parser.add_argument("--conexiones", default="config/conexiones.json")
    parser.add_argument("--tablas", default="config/tablas.json")
    parser.add_argument("--simular", action="store_true")
    parser.add_argument("--incluir-red", action="store_true")
    args = parser.parse_args(argv)

    log = obtener_logger("proceso2_arcpy")
    try:
        configuracion = cfg_mod.cargar(args.conexiones, args.tablas)
    except cfg_mod.ErrorConfig as exc:
        log.error(u"Configuracion invalida: %s", exc)
        return 2

    if args.simular:
        configuracion.modo_simulacion = True
    if args.incluir_red:
        configuracion.incluir_red_geometrica = True

    return SincronizadorArcpy(configuracion, log).ejecutar()


if __name__ == "__main__":
    sys.exit(main())
