# -*- coding: utf-8 -*-
"""
proceso1_oracle.sincronizar_oracle
==================================

PROCESO 1 - Sincronizacion directa entre dos bases Oracle 11g (sin ArcGIS).

Objetivo
--------
Dejar la base DESTINO identica a la base ORIGEN para el conjunto de tablas
configurado: detectar y aplicar **nuevos**, **eliminados** y **modificados**,
de modo que no queden diferencias.  Ademas compara los **dominios** y, para la
tabla de aplicacion ``DOMINIOS``, los sincroniza; para los dominios reales de la
geodatabase, reporta las diferencias.

Caracteristicas clave
----------------------
* **Llave de negocio = GLOBALID.**  Como GLOBALID es un GUID global, al insertar
  en destino se **copia tal cual** desde el origen: asi todas las relaciones que
  referencian GLOBALID quedan intactas sin necesidad de remapeo.  (Esta es la
  gran ventaja de la via Oracle directa frente a arcpy.)
* **OBJECTID** es local a cada base; se asigna en destino con la estrategia
  configurada (``max`` o ``secuencia``).  Las relaciones M:N basadas en OBJECTID
  se reparan al final con el mapa OBJECTID_origen -> OBJECTID_destino.
* **Red geometrica: OPCIONAL.**  Las tablas marcadas ``red_geometrica`` solo se
  procesan si ``opciones.incluir_red_geometrica`` es verdadero.  Se advierte que
  la via directa NO reconstruye la conectividad logica de la red (para eso esta
  el proceso 2).
* **Caracteres especiales:** toda lectura llega como ``unicode`` y toda escritura
  usa *bind variables* (nunca interpolacion de texto), evitando problemas de
  comillas y codificacion.
* **Transaccional:** cada tabla se aplica en su propia transaccion; si algo
  falla se hace rollback de esa tabla.  Con ``modo_simulacion`` no se escribe
  nada, solo se reporta.

Ejecucion (compatible con Python 2.7)::

    python -m proceso1_oracle.sincronizar_oracle \\
        --conexiones config/conexiones.json \\
        --tablas config/tablas.json
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import sys

from comun import config as cfg_mod
from comun import dominios as dom_mod
from comun import modelo
from comun.comparador import comparar
from comun.log import obtener_logger
from comun.oracle_db import ConexionOracle
from comun.utiles import normalizar_guid, trocear


class SincronizadorOracle(object):
    """Orquesta la sincronizacion tabla por tabla entre dos bases Oracle."""

    def __init__(self, configuracion, log):
        self.cfg = configuracion
        self.log = log
        # Mapa por tabla: {objectid_origen: objectid_destino} para remapeo M:N.
        self.mapa_oid = {}

    # ------------------------------------------------------------------ #
    # Entrada principal
    # ------------------------------------------------------------------ #
    def ejecutar(self):
        self.log.info(u"=== PROCESO 1: sincronizacion Oracle directa ===")
        if self.cfg.modo_simulacion:
            self.log.warning(u"MODO SIMULACION activo: no se escribira en destino.")

        with ConexionOracle(self.cfg.origen.oracle) as origen, \
                ConexionOracle(self.cfg.destino.oracle) as destino:

            # 1) Dominios (reporte + sincronizacion de la tabla DOMINIOS).
            if self.cfg.sincronizar_dominios:
                self._sincronizar_dominios(origen, destino)

            # 2) Tablas de negocio.
            for cfg_tabla in self.cfg.tablas_a_procesar():
                try:
                    self._sincronizar_tabla(origen, destino, cfg_tabla)
                    if not self.cfg.modo_simulacion:
                        destino.commit()
                except Exception as exc:
                    destino.rollback()
                    self.log.error(u"Tabla %s: ERROR, rollback aplicado: %s",
                                   cfg_tabla.nombre, exc)

            # 3) Reparacion de relaciones M:N basadas en OBJECTID.
            self._reparar_relaciones_oid(destino)
            if not self.cfg.modo_simulacion:
                destino.commit()

        self.log.info(u"=== PROCESO 1 finalizado ===")

    # ------------------------------------------------------------------ #
    # Dominios
    # ------------------------------------------------------------------ #
    def _sincronizar_dominios(self, origen, destino):
        self.log.info(u"--- Comparando dominios de la geodatabase (GDB_ITEMS) ---")
        try:
            dom_o = dom_mod.leer_dominios_gdb(origen)
            dom_d = dom_mod.leer_dominios_gdb(destino)
        except Exception as exc:
            self.log.warning(u"No se pudieron leer dominios de GDB_ITEMS: %s", exc)
            return

        diferencias = dom_mod.comparar_dominios(dom_o, dom_d)
        if not diferencias:
            self.log.info(u"Dominios: sin diferencias.")
        for d in diferencias:
            self.log.warning(
                u"Dominio '%s' [%s]: +%d valores, -%d valores, ~%d cambios",
                d.nombre, d.estado, len(d.codigos_agregar),
                len(d.codigos_quitar), len(d.codigos_cambiar))
            for cod, desc in sorted(d.codigos_agregar.items()):
                self.log.warning(u"    AGREGAR  %s = %s", cod, desc)
            for cod, desc in sorted(d.codigos_quitar.items()):
                self.log.warning(u"    QUITAR   %s = %s", cod, desc)
            for cod, (o, dd) in sorted(d.codigos_cambiar.items()):
                self.log.warning(u"    CAMBIAR  %s: '%s' -> '%s'", cod, dd, o)
        self.log.info(
            u"NOTA: los dominios REALES de la geodatabase no se reescriben por "
            u"SQL (riesgo en GDB_ITEMS). Use el proceso 2 (arcpy) para aplicarlos.")

    # ------------------------------------------------------------------ #
    # Tablas
    # ------------------------------------------------------------------ #
    def _sincronizar_tabla(self, origen, destino, cfg_tabla):
        nombre = cfg_tabla.nombre
        self.log.info(u"--- Tabla %s ---", nombre)

        # Metadatos de columnas (interseccion origen/destino para robustez).
        cols_o = [c["NOMBRE"] for c in origen.columnas_tabla(nombre)]
        cols_d = [c["NOMBRE"] for c in destino.columnas_tabla(nombre)]
        if not cols_o or not cols_d:
            self.log.warning(u"Tabla %s no existe en una de las bases; se omite.",
                             nombre)
            return
        columnas_comunes = [c for c in cols_o if c in set(cols_d)]

        # Auto-deteccion de red geometrica (si no se configuro explicitamente).
        if not cfg_tabla.red_geometrica and modelo.afecta_red_geometrica(columnas_comunes):
            if not self.cfg.incluir_red_geometrica:
                self.log.warning(
                    u"Tabla %s parece afectar la red geometrica y la opcion "
                    u"incluir_red_geometrica esta desactivada; se omite.", nombre)
                return

        comparables = modelo.columnas_comparables(columnas_comunes, cfg_tabla)
        # Columnas a copiar: en proceso 1 SI se incluye GLOBALID (via directa).
        copiables = modelo.columnas_copiables(columnas_comunes, cfg_tabla,
                                              incluir_globalid=True)

        tiene_geometria = ("SHAPE" in {c.upper() for c in columnas_comunes}
                           and cfg_tabla.columna_geometria)

        # Lectura de filas (con filtro opcional para particionar por empresa, etc.)
        where = (" WHERE " + cfg_tabla.filtro) if cfg_tabla.filtro else ""
        # Evitar duplicar columnas en el SELECT.
        vistas = []
        seen = set()
        for c in (copiables + [cfg_tabla.llave_negocio.upper(), "OBJECTID"]):
            if c not in seen:
                vistas.append(c)
                seen.add(c)
        sql_base = "SELECT %s FROM %s%s" % (", ".join(vistas), nombre, where)

        filas_origen = origen.consultar(sql_base)
        filas_destino = destino.consultar(sql_base)

        # Firmas de geometria (opcional): comparadas via WKT.
        firma_geom = None
        if tiene_geometria and self._geometria_habilitada(cfg_tabla):
            firma_geom = self._firmas_geometria(origen, destino, cfg_tabla, where)

        resultado = comparar(
            nombre, filas_origen, filas_destino, comparables,
            cfg_tabla.llave_negocio, firma_geometria=firma_geom)
        self.log.info(resultado.resumen())

        if self.cfg.modo_simulacion:
            self._detallar_simulacion(resultado)
            # Aun en simulacion, registrar el mapa OID para reporte de relaciones.
            return

        # Indexar filas por llave para acceder a los datos completos.
        idx_o = self._indexar(filas_origen, cfg_tabla.llave_negocio)
        idx_d = self._indexar(filas_destino, cfg_tabla.llave_negocio)

        self._aplicar_inserts(destino, cfg_tabla, resultado, idx_o, copiables,
                              tiene_geometria, origen, where)
        self._aplicar_updates(destino, cfg_tabla, resultado, idx_o, copiables,
                             tiene_geometria, origen)
        self._aplicar_deletes(destino, cfg_tabla, resultado, idx_d)

    # ------------------------------------------------------------------ #
    # Geometria (opcional, via WKT)
    # ------------------------------------------------------------------ #
    def _geometria_habilitada(self, cfg_tabla):
        # La geometria se compara/copia solo si hay columna configurada.
        return bool(cfg_tabla.columna_geometria)

    def _expr_wkt(self, columna):
        """Expresion SQL para obtener WKT de la geometria (ST_GEOMETRY).

        Ajuste segun el almacenamiento:
          * ST_GEOMETRY: ``SDE.ST_ASTEXT(col)``  (por defecto)
          * SDO_GEOMETRY: ``SDO_UTIL.TO_WKTGEOMETRY(col)``
        """
        return "SDE.ST_ASTEXT(%s)" % columna

    def _firmas_geometria(self, origen, destino, cfg_tabla, where):
        """Devuelve {llave: (hash_geom_origen, hash_geom_destino)}."""
        import hashlib
        col = cfg_tabla.columna_geometria
        llave = cfg_tabla.llave_negocio.upper()
        sql = "SELECT %s AS K, %s AS WKT FROM %s%s" % (
            llave, self._expr_wkt(col), cfg_tabla.nombre, where)

        def cargar(db):
            m = {}
            try:
                for f in db.iterar(sql):
                    k = normalizar_guid(f["K"])
                    wkt = f.get("WKT") or u""
                    m[k] = hashlib.md5(wkt.encode("utf-8")).hexdigest()
            except Exception as exc:
                self.log.warning(u"No se pudo leer geometria (%s): %s",
                                 cfg_tabla.nombre, exc)
            return m

        mo = cargar(origen)
        md = cargar(destino)
        combinado = {}
        for k in set(mo) | set(md):
            combinado[k] = (mo.get(k), md.get(k))
        return combinado

    # ------------------------------------------------------------------ #
    # DML: INSERT / UPDATE / DELETE
    # ------------------------------------------------------------------ #
    def _aplicar_inserts(self, destino, cfg_tabla, resultado, idx_o, copiables,
                         tiene_geometria, origen, where):
        if not resultado.nuevos:
            return
        nombre = cfg_tabla.nombre

        # Asignacion de OBJECTID en destino.
        siguiente = self._inicio_objectid(destino, cfg_tabla)

        # Preparar geometria de origen (WKT) si aplica.
        wkt_origen = {}
        if tiene_geometria:
            wkt_origen = self._wkt_por_llave(origen, cfg_tabla, where)

        col_geom = cfg_tabla.columna_geometria
        columnas_insert = list(copiables)
        placeholders = [":%s" % c for c in columnas_insert]
        # OBJECTID explicito.
        columnas_insert_sql = ["OBJECTID"] + columnas_insert
        placeholders_sql = [":OBJECTID"] + placeholders
        if tiene_geometria:
            columnas_insert_sql.append(col_geom)
            # SRID se toma de una fila existente; se parametriza el WKT.
            placeholders_sql.append("SDE.ST_GEOMETRY(:WKTGEOM, :SRID)")

        sql = "INSERT INTO %s (%s) VALUES (%s)" % (
            nombre, ", ".join(columnas_insert_sql), ", ".join(placeholders_sql))

        srid = self._srid(destino, nombre, col_geom) if tiene_geometria else None

        lote = []
        insertados = 0
        for llave in resultado.nuevos:
            fila = idx_o[llave]
            binds = {}
            for c in columnas_insert:
                binds[c] = fila.get(c)
            oid_origen = fila.get("OBJECTID")
            binds["OBJECTID"] = siguiente
            # Registrar el mapa OID origen->destino para relaciones M:N.
            self.mapa_oid.setdefault(nombre.upper(), {})[oid_origen] = siguiente
            siguiente += 1
            if tiene_geometria:
                binds["WKTGEOM"] = wkt_origen.get(llave)
                binds["SRID"] = srid
            lote.append(binds)
            if len(lote) >= self.cfg.tamano_lote:
                insertados += self._ejecutar_lote_insert(destino, sql, lote,
                                                         tiene_geometria)
                lote = []
        if lote:
            insertados += self._ejecutar_lote_insert(destino, sql, lote,
                                                    tiene_geometria)
        self.log.info(u"  INSERT %d filas en %s", insertados, nombre)

    def _ejecutar_lote_insert(self, destino, sql, lote, tiene_geometria):
        if tiene_geometria:
            # ST_GEOMETRY dentro de executemany puede no soportarse en todos los
            # clientes; se ejecuta fila a fila para maxima compatibilidad.
            n = 0
            for binds in lote:
                n += destino.ejecutar(sql, binds)
            return n
        return destino.ejecutar_muchos(sql, lote)

    def _aplicar_updates(self, destino, cfg_tabla, resultado, idx_o, copiables,
                        tiene_geometria, origen):
        if not resultado.modificados:
            return
        nombre = cfg_tabla.nombre
        llave = cfg_tabla.llave_negocio.upper()

        # No se actualiza la propia llave de negocio.
        cols_set = [c for c in copiables if c != llave]
        set_sql = ", ".join(["%s = :%s" % (c, c) for c in cols_set])
        sql = "UPDATE %s SET %s WHERE %s = :LLAVE" % (nombre, set_sql, llave)

        actualizados = 0
        for k, _cambios in resultado.modificados:
            fila = idx_o[k]
            binds = {}
            for c in cols_set:
                binds[c] = fila.get(c)
            binds["LLAVE"] = fila.get(llave)
            actualizados += destino.ejecutar(sql, binds)

            if tiene_geometria and "SHAPE" in [x.upper() for x in _cambios]:
                self._actualizar_geometria(destino, origen, cfg_tabla, fila.get(llave))
        self.log.info(u"  UPDATE %d filas en %s", actualizados, nombre)

    def _actualizar_geometria(self, destino, origen, cfg_tabla, valor_llave):
        col = cfg_tabla.columna_geometria
        llave = cfg_tabla.llave_negocio.upper()
        wkt = origen.consultar(
            "SELECT %s AS WKT FROM %s WHERE %s = :k" % (
                self._expr_wkt(col), cfg_tabla.nombre, llave),
            {"k": valor_llave})
        if not wkt:
            return
        srid = self._srid(destino, cfg_tabla.nombre, col)
        destino.ejecutar(
            "UPDATE %s SET %s = SDE.ST_GEOMETRY(:w, :s) WHERE %s = :k" % (
                cfg_tabla.nombre, col, llave),
            {"w": wkt[0]["WKT"], "s": srid, "k": valor_llave})

    def _aplicar_deletes(self, destino, cfg_tabla, resultado, idx_d):
        if not resultado.eliminados:
            return
        if cfg_tabla.politica_borrado == "ninguna":
            self.log.info(u"  DELETE omitido (politica 'ninguna'): %d candidatos",
                          len(resultado.eliminados))
            return
        nombre = cfg_tabla.nombre
        llave = cfg_tabla.llave_negocio.upper()
        borrados = 0
        # Borrado por lotes usando IN (:1,:2,...) de a 1000 (limite Oracle).
        for bloque in trocear(resultado.eliminados, 1000):
            binds = {}
            marcadores = []
            for i, valor in enumerate(bloque):
                clave = "b%d" % i
                binds[clave] = idx_d[valor].get(llave)
                marcadores.append(":%s" % clave)
            sql = "DELETE FROM %s WHERE %s IN (%s)" % (
                nombre, llave, ", ".join(marcadores))
            borrados += destino.ejecutar(sql, binds)
        self.log.info(u"  DELETE %d filas en %s", borrados, nombre)

    # ------------------------------------------------------------------ #
    # Relaciones M:N por OBJECTID
    # ------------------------------------------------------------------ #
    def _reparar_relaciones_oid(self, destino):
        relaciones_oid = [r for r in self.cfg.relaciones if r.tipo_llave == "oid"]
        if not relaciones_oid:
            return
        self.log.info(u"--- Reparando relaciones M:N basadas en OBJECTID ---")
        for rel in relaciones_oid:
            mapa = self.mapa_oid.get(rel.tabla_origen.upper(), {})
            if not mapa:
                continue
            actualizados = 0
            for oid_origen, oid_destino in mapa.items():
                sql = "UPDATE %s SET %s = :nuevo WHERE %s = :viejo" % (
                    rel.tabla_destino, rel.columna_fk, rel.columna_fk)
                actualizados += destino.ejecutar(
                    sql, {"nuevo": oid_destino, "viejo": oid_origen})
            self.log.info(u"  Relacion %s: %d FKs remapeadas en %s.%s",
                          rel.nombre, actualizados, rel.tabla_destino, rel.columna_fk)

    # ------------------------------------------------------------------ #
    # Auxiliares
    # ------------------------------------------------------------------ #
    def _inicio_objectid(self, destino, cfg_tabla):
        if cfg_tabla.estrategia_objectid == "secuencia" and cfg_tabla.secuencia_objectid:
            return destino.valor_secuencia(cfg_tabla.secuencia_objectid)
        return destino.siguiente_objectid(cfg_tabla.nombre)

    def _wkt_por_llave(self, origen, cfg_tabla, where):
        col = cfg_tabla.columna_geometria
        llave = cfg_tabla.llave_negocio.upper()
        sql = "SELECT %s AS K, %s AS WKT FROM %s%s" % (
            llave, self._expr_wkt(col), cfg_tabla.nombre, where)
        m = {}
        for f in origen.iterar(sql):
            m[normalizar_guid(f["K"])] = f.get("WKT")
        return m

    def _srid(self, db, tabla, col):
        try:
            r = db.consultar(
                "SELECT SDE.ST_SRID(%s) AS S FROM %s WHERE %s IS NOT NULL "
                "AND ROWNUM = 1" % (col, tabla, col))
            return int(r[0]["S"]) if r else None
        except Exception:
            return None

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

    def _detallar_simulacion(self, resultado):
        for k in resultado.nuevos[:20]:
            self.log.debug(u"    [SIM] INSERT %s", k)
        for k, cambios in resultado.modificados[:20]:
            self.log.debug(u"    [SIM] UPDATE %s -> %s", k, ", ".join(cambios))
        for k in resultado.eliminados[:20]:
            self.log.debug(u"    [SIM] DELETE %s", k)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Proceso 1: sincronizacion directa Oracle 11g -> Oracle 11g")
    parser.add_argument("--conexiones", default="config/conexiones.json",
                        help="Ruta al JSON de conexiones.")
    parser.add_argument("--tablas", default="config/tablas.json",
                        help="Ruta al JSON de tablas/relaciones.")
    parser.add_argument("--simular", action="store_true",
                        help="No escribe en destino; solo reporta diferencias.")
    parser.add_argument("--incluir-red", action="store_true",
                        help="Incluye las tablas de la red geometrica (opcional).")
    args = parser.parse_args(argv)

    log = obtener_logger("proceso1_oracle")
    try:
        configuracion = cfg_mod.cargar(args.conexiones, args.tablas)
    except cfg_mod.ErrorConfig as exc:
        log.error(u"Configuracion invalida: %s", exc)
        return 2

    if args.simular:
        configuracion.modo_simulacion = True
    if args.incluir_red:
        configuracion.incluir_red_geometrica = True

    SincronizadorOracle(configuracion, log).ejecutar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
