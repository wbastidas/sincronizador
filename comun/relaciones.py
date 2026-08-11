# -*- coding: utf-8 -*-
"""
comun.relaciones
================

Remapeo **seguro** de columnas de llave foranea (FK) tras reasignar identidades.

Problema
--------
Cuando se remapea una FK con ``UPDATE hijo SET fk = nuevo WHERE fk = viejo`` valor
por valor, y el conjunto de valores NUEVOS se solapa con el de valores VIEJOS
(caso tipico con OBJECTID, que son enteros correlativos), una fila ya actualizada
puede volver a coincidir con un ``viejo`` posterior y remapearse dos veces. El
resultado son relaciones corruptas.

Solucion (dos fases con centinela)
----------------------------------
1. **Fase 1**: mover cada ``viejo`` a un valor CENTINELA disjunto del espacio de
   valores reales (para OBJECTID: negativo ``-viejo-1``; para GUID: prefijo
   ``TMP~``). Como los centinelas no coinciden con ningun ``viejo`` real, no hay
   cascada.
2. **Fase 2**: mover cada centinela al valor ``nuevo`` definitivo. Como los
   ``nuevo`` no coinciden con ningun centinela, tampoco hay cascada.

Ambas fases usan la misma forma de sentencia (``SET col=:v WHERE col=:v``) y se
ejecutan por lotes con ``executemany``.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

from comun.utiles import trocear


def _centinela(valor, tipo_llave):
    if tipo_llave == "oid":
        return -int(valor) - 1          # espacio negativo, disjunto de OIDs reales
    return u"TMP~%s" % valor             # prefijo improbable en un GUID real


def remapear_fk(db, tabla, columna, mapa, tipo_llave, tamano_lote, log=None):
    """Remapea de forma segura ``tabla.columna`` segun ``mapa`` (viejo -> nuevo).

    :param db:        conexion (comun.oracle_db.ConexionOracle o compatible).
    :param mapa:      dict {valor_viejo: valor_nuevo}.
    :param tipo_llave:'oid' o 'guid'.
    :return:          numero de filas hijas efectivamente remapeadas.
    """
    pares = [(v, n) for v, n in mapa.items() if v != n]
    if not pares:
        return 0

    sql = "UPDATE %s SET %s = :nuevo WHERE %s = :viejo" % (tabla, columna, columna)

    # Fase 1: viejo -> centinela.
    binds1 = [{"nuevo": _centinela(v, tipo_llave), "viejo": v} for v, n in pares]
    for bloque in trocear(binds1, tamano_lote):
        db.ejecutar_muchos(sql, bloque)

    # Fase 2: centinela -> nuevo.
    binds2 = [{"nuevo": n, "viejo": _centinela(v, tipo_llave)} for v, n in pares]
    total = 0
    for bloque in trocear(binds2, tamano_lote):
        total += db.ejecutar_muchos(sql, bloque)

    if log is not None:
        log.info(u"  FK %s.%s: %d filas remapeadas (2 fases, sin colision)",
                 tabla, columna, total)
    return total
