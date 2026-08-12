# -*- coding: utf-8 -*-
"""
entorno_prueba.generar_datos
============================

Puebla las tablas de prueba (creadas con ``sql/01_esquema.sql``) en las bases
ORIGEN y DESTINO usando **cx_Oracle**, generando una divergencia controlada
(nuevos / modificados / eliminados) para validar los procesos en un servidor
real. Escalable a 20.000+ elementos.

Incluye datos con **caracteres especiales** (tildes, enies, comas) para probar
el traspaso sin corrupcion.

Uso::

    python entorno_prueba/generar_datos.py \\
        --conexiones config/conexiones.json \\
        --n 20000 --para p1        # p1 = destino con GLOBALID copiado
    #   --para p2                  # p2 = destino con identidad en MIGUID

Reparto por defecto (sobre N estructuras; PUNTOCARGA=2N, CONEXION=3N):
    60% iguales, 25% modificados, 15% nuevos, +10% extra en destino (eliminados).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import sys
import uuid

from comun import config as cfg_mod
from comun.log import obtener_logger
from comun.oracle_db import ConexionOracle


def guid():
    return "{%s}" % uuid.uuid4().hex.upper()


def texto(prefijo, i):
    return u"%s_%d Muñoz, Ñandú áéíóú" % (prefijo, i)


TABLAS = ["ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR"]


def _truncar(db, log):
    for t in TABLAS:
        try:
            db.ejecutar("DELETE FROM %s" % t)
        except Exception as exc:
            log.warning(u"No se pudo limpiar %s: %s", t, exc)
    db.commit()


def generar_origen(db, n, log):
    """Inserta N estructuras, 2N puntos de carga, 3N conexiones en el origen.

    Devuelve las listas de GLOBALID generados por tabla (para construir destino).
    """
    _truncar(db, log)
    gids = {t: [] for t in TABLAS}

    # ESTRUCTURAANIVEL
    filas = []
    for i in range(n):
        g = guid()
        gids["ESTRUCTURAANIVEL"].append(g)
        filas.append({"oid": i + 1, "gid": g, "nom": texto(u"Estr", i), "emp": "001"})
    db.ejecutar_muchos(
        "INSERT INTO ESTRUCTURAANIVEL (OBJECTID, GLOBALID, NOMBRE, CODIGOEMPRESA) "
        "VALUES (:oid, :gid, :nom, :emp)", filas)

    # PUNTOCARGA (2N), FK a estructura por GLOBALID
    filas = []
    for i in range(2 * n):
        g = guid()
        gids["PUNTOCARGA"].append(g)
        est = gids["ESTRUCTURAANIVEL"][i % n]
        filas.append({"oid": i + 1, "gid": g, "fk": est,
                      "nom": texto(u"PC", i), "carga": i * 1.5})
    db.ejecutar_muchos(
        "INSERT INTO PUNTOCARGA (OBJECTID, GLOBALID, ESTRUCTURANIVELGLOBALID, "
        "NOMBRE, CARGA) VALUES (:oid, :gid, :fk, :nom, :carga)", filas)

    # CONEXIONCONSUMIDOR (3N), FK a puntocarga por GLOBALID
    filas = []
    for i in range(3 * n):
        g = guid()
        gids["CONEXIONCONSUMIDOR"].append(g)
        pc = gids["PUNTOCARGA"][i % (2 * n)]
        filas.append({"oid": i + 1, "gid": g, "fk": pc,
                      "cod": "CLI%08d" % i, "nom": texto(u"Cliente", i)})
    db.ejecutar_muchos(
        "INSERT INTO CONEXIONCONSUMIDOR (OBJECTID, GLOBALID, PUNTOCARGAGLOBALID, "
        "CODIGOCLIENTE, NOMBRECLIENTE) VALUES (:oid, :gid, :fk, :cod, :nom)", filas)

    db.commit()
    log.info(u"ORIGEN poblado: %d estructuras, %d puntos, %d conexiones",
             n, 2 * n, 3 * n)
    return gids


def generar_destino(db_origen, db_destino, para, log):
    """Construye el destino divergente leyendo el origen.

    :param para: 'p1' (GLOBALID copiado) o 'p2' (identidad en MIGUID).
    """
    _truncar(db_destino, log)
    columnas = {
        "ESTRUCTURAANIVEL": ["OBJECTID", "GLOBALID", "NOMBRE", "CODIGOEMPRESA",
                             "MIOID", "MIGUID"],
        "PUNTOCARGA": ["OBJECTID", "GLOBALID", "ESTRUCTURANIVELGLOBALID",
                       "NOMBRE", "CARGA", "MIOID", "MIGUID"],
        "CONEXIONCONSUMIDOR": ["OBJECTID", "GLOBALID", "PUNTOCARGAGLOBALID",
                               "CODIGOCLIENTE", "NOMBRECLIENTE", "MIOID", "MIGUID"],
    }
    campo_texto = {"ESTRUCTURAANIVEL": "NOMBRE", "PUNTOCARGA": "NOMBRE",
                   "CONEXIONCONSUMIDOR": "NOMBRECLIENTE"}

    for tabla in TABLAS:
        filas_o = db_origen.consultar("SELECT * FROM %s ORDER BY OBJECTID" % tabla)
        n = len(filas_o)
        corte_igual = int(n * 0.60)
        corte_mod = int(n * 0.85)   # 60-85 modificadas; 85-100 ausentes (nuevos)
        doid = 1
        destino_filas = []
        for k, fo in enumerate(filas_o):
            if k >= corte_mod:
                continue  # ausente -> se insertara como NUEVO por el proceso
            fila = {c: fo.get(c) for c in columnas[tabla]}
            if para == "p2":
                fila["MIGUID"] = fo["GLOBALID"]
                fila["MIOID"] = fo["OBJECTID"]
                fila["GLOBALID"] = guid()       # identidad local distinta
                fila["OBJECTID"] = doid
            else:  # p1: GLOBALID copiado, sin espejo
                fila["MIGUID"] = None
                fila["MIOID"] = None
                fila["OBJECTID"] = doid
            doid += 1
            if corte_igual <= k < corte_mod:    # modificadas: valor viejo
                ct = campo_texto[tabla]
                fila[ct] = u"VIEJO " + (fo.get(ct) or u"")
            destino_filas.append(fila)

        # Extras que sobran en destino -> ELIMINADOS por el proceso.
        for j in range(int(n * 0.10)):
            fila = {c: None for c in columnas[tabla]}
            fila["OBJECTID"] = doid
            doid += 1
            fila["GLOBALID"] = guid()
            if para == "p2":
                fila["MIGUID"] = "{DEL-%s-%08d}" % (tabla[:3], j)
            else:
                fila["GLOBALID"] = "{DEL-%s-%08d}" % (tabla[:3], j)
            destino_filas.append(fila)

        cols = columnas[tabla]
        binds = ", ".join(":%s" % c for c in cols)
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (tabla, ", ".join(cols), binds)
        db_destino.ejecutar_muchos(sql, [
            {c: f.get(c) for c in cols} for f in destino_filas])
        db_destino.commit()
        log.info(u"DESTINO[%s] (%s): %d filas sembradas", tabla, para,
                 len(destino_filas))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera datos de prueba en Oracle.")
    parser.add_argument("--conexiones", default="config/conexiones.json")
    parser.add_argument("--n", type=int, default=1000,
                        help="Numero de estructuras (PUNTOCARGA=2N, CONEXION=3N).")
    parser.add_argument("--para", choices=["p1", "p2"], default="p1")
    args = parser.parse_args(argv)

    log = obtener_logger("generar_datos")
    cfg = cfg_mod.cargar(args.conexiones, args.conexiones)  # solo se usa 'origen/destino'

    with ConexionOracle(cfg.origen.oracle) as origen, \
            ConexionOracle(cfg.destino.oracle) as destino:
        generar_origen(origen, args.n, log)
        generar_destino(origen, destino, args.para, log)
    log.info(u"Datos generados. Ejecute el reporte y luego el proceso %s.", args.para)
    return 0


if __name__ == "__main__":
    sys.exit(main())
