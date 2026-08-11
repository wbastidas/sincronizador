# -*- coding: utf-8 -*-
"""
tests.test_carga_masiva
=======================

Prueba de carga con **20.000 elementos** (nuevos + modificados + eliminados) para
verificar que los DOS escenarios funcionan de extremo a extremo, ejecutando el
CODIGO REAL de orquestacion contra los dobles de :mod:`tests.dobles`
(FakeOracle / FakeArcpy), sin necesidad de Oracle ni ArcGIS.

Reparto de las 20.000 diferencias:
    * 8.000 NUEVOS       (existen en origen, faltan en destino)  -> INSERT
    * 7.000 MODIFICADOS  (existen en ambos, cambia un valor)     -> UPDATE
    * 5.000 ELIMINADOS   (faltan en origen, sobran en destino)   -> DELETE
  (+ 2.000 IGUALES para asegurar que NO se tocan)

Escenario 1: proceso 1 (Oracle directo) + remapeo de relacion por OBJECTID.
Escenario 2: proceso 2 (arcpy) + identidad MIOID/MIGUID + remapeo por GLOBALID.

Ejecutar::

    python -m tests.test_carga_masiva
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sys

from comun import modelo
from comun.config import ConfigTabla, ConfigRelacion
from comun.utiles import firma_fila, normalizar_guid
from tests.dobles import Almacen, FakeOracle, FakeArcpy, nuevo_guid

_fallos = []

# Conteos del escenario.
N_NUEVOS = 8000
N_MOD = 7000
N_ELIM = 5000
N_IGUAL = 2000
N_HIJOS = 1000


def verificar(cond, msg):
    if cond:
        print(u"  OK  %s" % msg)
    else:
        print(u"  FALLO  %s" % msg)
        _fallos.append(msg)


def guid_elem(i):
    return "{ELEM-%08d}" % i


def guid_del(i):
    return "{DEL-%08d}" % i


def guid_hijo(i):
    return "{HIJO-%08d}" % i


# --------------------------------------------------------------------------- #
# Configuracion minima (sin JSON)
# --------------------------------------------------------------------------- #
class _Conexion(object):
    def __init__(self, fake, ws="MEM"):
        self.oracle = {"_fake": fake}
        self.sde_workspace = ws


class MiniConfig(object):
    def __init__(self, origen_fake, destino_fake, tablas, relaciones):
        self.origen = _Conexion(origen_fake)
        self.destino = _Conexion(destino_fake)
        self.tablas = tablas
        self.relaciones = relaciones
        self.incluir_red_geometrica = False
        self.sincronizar_dominios = False
        self.tamano_lote = 1000
        self.modo_simulacion = False

    def tablas_a_procesar(self):
        for t in self.tablas:
            if t.red_geometrica and not self.incluir_red_geometrica:
                continue
            yield t


def _fabrica_fake(params, autocommit=False):
    """Reemplaza a ConexionOracle: devuelve el FakeOracle marcado en params."""
    return params["_fake"]


def _diffs(idx_origen, idx_destino, comparables):
    """Cuenta diferencias (nuevos/modif/elim) entre dos indices ya construidos."""
    lo, ld = set(idx_origen), set(idx_destino)
    nuevos = len(lo - ld)
    elim = len(ld - lo)
    mod = 0
    for k in (lo & ld):
        if firma_fila(idx_origen[k], comparables) != firma_fila(idx_destino[k], comparables):
            mod += 1
    return nuevos, mod, elim


# =========================================================================== #
# ESCENARIO 1: Proceso 1 (Oracle directo)
# =========================================================================== #
def test_proceso1_oracle_20k():
    print(u"test_proceso1_oracle_20k")
    import proceso1_oracle.sincronizar_oracle as p1

    origen = Almacen()
    destino = Almacen()
    cols_elem = ["OBJECTID", "GLOBALID", "NOMBRE", "VALOR"]
    to = origen.crear("ELEMENTO", cols_elem)
    td = destino.crear("ELEMENTO", cols_elem)
    # Tabla hija con FK por OBJECTID (para probar el remapeo OID del proceso 1).
    cols_hijo = ["OBJECTID", "GLOBALID", "NOMBRE", "PADRE_OID"]
    ho = origen.crear("HIJO", cols_hijo)
    hd = destino.crear("HIJO", cols_hijo)

    # --- Poblar ORIGEN (la verdad) ---
    oid = 1
    # iguales
    for i in range(N_IGUAL):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Igual_%d ñ" % i, "VALOR": i})
        oid += 1
    # modificados
    for i in range(N_IGUAL, N_IGUAL + N_MOD):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Mod_%d áé" % i, "VALOR": i})
        oid += 1
    # nuevos
    oid_nuevos = {}
    for i in range(N_IGUAL + N_MOD, N_IGUAL + N_MOD + N_NUEVOS):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Nuevo_%d üï" % i, "VALOR": i})
        oid_nuevos[guid_elem(i)] = oid
        oid += 1

    # --- Poblar DESTINO (divergente) ---
    doid = 1
    for i in range(N_IGUAL):  # iguales identicos
        td.filas.append({"OBJECTID": doid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Igual_%d ñ" % i, "VALOR": i})
        doid += 1
    for i in range(N_IGUAL, N_IGUAL + N_MOD):  # modificados con valor viejo
        td.filas.append({"OBJECTID": doid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Mod_%d áé" % i, "VALOR": -1})
        doid += 1
    for j in range(N_ELIM):  # eliminados (solo en destino)
        td.filas.append({"OBJECTID": doid, "GLOBALID": guid_del(j),
                         "NOMBRE": u"Borrar_%d" % j, "VALOR": j})
        doid += 1

    # Hijos: existen en origen apuntando (por OBJECTID) a parents "nuevos".
    parents_nuevos = list(oid_nuevos.items())[:N_HIJOS]
    for k, (gid_padre, oid_padre) in enumerate(parents_nuevos):
        ho.filas.append({"OBJECTID": k + 1, "GLOBALID": guid_hijo(k),
                         "NOMBRE": u"Hijo_%d" % k, "PADRE_OID": oid_padre})

    tablas = [
        ConfigTabla({"nombre": "ELEMENTO", "llave_negocio": "GLOBALID",
                     "columna_geometria": None}),
        ConfigTabla({"nombre": "HIJO", "llave_negocio": "GLOBALID",
                     "columna_geometria": None}),
    ]
    relaciones = [ConfigRelacion({"nombre": "ELEM_HIJO", "tabla_origen": "ELEMENTO",
                                  "tabla_destino": "HIJO", "tipo_llave": "oid",
                                  "columna_fk": "PADRE_OID"})]
    cfg = MiniConfig(FakeOracle(origen), FakeOracle(destino), tablas, relaciones)

    # Inyectar el doble de Oracle en el modulo del proceso.
    original = p1.ConexionOracle
    p1.ConexionOracle = _fabrica_fake
    try:
        log = _log_silencioso()
        p1.SincronizadorOracle(cfg, log).ejecutar()
    finally:
        p1.ConexionOracle = original

    # --- Verificaciones ---
    verificar(len(td.filas) == len(to.filas),
              u"ELEMENTO: destino y origen tienen igual cantidad (%d)" % len(to.filas))

    idx_o = {normalizar_guid(f["GLOBALID"]): f for f in to.filas}
    idx_d = {normalizar_guid(f["GLOBALID"]): f for f in td.filas}
    comparables = modelo.columnas_comparables(["NOMBRE", "VALOR"], tablas[0])
    n, m, e = _diffs(idx_o, idx_d, comparables)
    verificar((n, m, e) == (0, 0, 0),
              u"ELEMENTO: sin diferencias tras sincronizar (n=%d m=%d e=%d)" % (n, m, e))

    # GLOBALID copiado tal cual (misma identidad) para un nuevo.
    g = guid_elem(N_IGUAL + N_MOD)
    verificar(normalizar_guid(idx_d[g]["GLOBALID"]) == normalizar_guid(g),
              u"ELEMENTO: el GLOBALID del origen se copia al destino (proceso 1)")

    # Remapeo OID: el hijo apunta al OBJECTID de destino del padre correcto.
    hijos_d = {normalizar_guid(f["GLOBALID"]): f for f in hd.filas}
    ok_remap = True
    for k, (gid_padre, _oid_o) in enumerate(parents_nuevos):
        padre_d = idx_d[normalizar_guid(gid_padre)]
        hijo = hijos_d.get(normalizar_guid(guid_hijo(k)))
        if hijo is None or hijo["PADRE_OID"] != padre_d["OBJECTID"]:
            ok_remap = False
            break
    verificar(ok_remap,
              u"HIJO: FK por OBJECTID remapeada al OBJECTID de destino del padre")


# =========================================================================== #
# ESCENARIO 2: Proceso 2 (arcpy)
# =========================================================================== #
def test_proceso2_arcpy_20k():
    print(u"test_proceso2_arcpy_20k")
    import proceso2_arcpy.sincronizar_arcpy as p2

    origen = Almacen()
    destino = Almacen()
    cols_elem_o = ["OBJECTID", "GLOBALID", "NOMBRE", "VALOR"]
    cols_elem_d = ["OBJECTID", "GLOBALID", "NOMBRE", "VALOR", "MIOID", "MIGUID"]
    to = origen.crear("ELEMENTO", cols_elem_o)
    td = destino.crear("ELEMENTO", cols_elem_d)
    cols_hijo_o = ["OBJECTID", "GLOBALID", "NOMBRE", "PADREGUID"]
    cols_hijo_d = ["OBJECTID", "GLOBALID", "NOMBRE", "PADREGUID", "MIOID", "MIGUID"]
    ho = origen.crear("HIJO", cols_hijo_o)
    destino.crear("HIJO", cols_hijo_d)

    # --- ORIGEN ---
    oid = 1
    for i in range(N_IGUAL):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Igual_%d ñ" % i, "VALOR": i})
        oid += 1
    for i in range(N_IGUAL, N_IGUAL + N_MOD):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Mod_%d áé" % i, "VALOR": i})
        oid += 1
    guids_nuevos = []
    for i in range(N_IGUAL + N_MOD, N_IGUAL + N_MOD + N_NUEVOS):
        to.filas.append({"OBJECTID": oid, "GLOBALID": guid_elem(i),
                         "NOMBRE": u"Nuevo_%d üï" % i, "VALOR": i})
        guids_nuevos.append(guid_elem(i))
        oid += 1

    # --- DESTINO (arcpy) con identidad propia y MIGUID=guid de origen ---
    doid = 500000
    for i in range(N_IGUAL):
        td.filas.append({"OBJECTID": doid, "GLOBALID": nuevo_guid(),
                         "NOMBRE": u"Igual_%d ñ" % i, "VALOR": i,
                         "MIOID": i + 1, "MIGUID": guid_elem(i)})
        doid += 1
    for idx_i, i in enumerate(range(N_IGUAL, N_IGUAL + N_MOD)):
        td.filas.append({"OBJECTID": doid, "GLOBALID": nuevo_guid(),
                         "NOMBRE": u"Mod_%d áé" % i, "VALOR": -1,   # valor viejo
                         "MIOID": N_IGUAL + idx_i + 1, "MIGUID": guid_elem(i)})
        doid += 1
    for j in range(N_ELIM):  # sobran en destino -> deben borrarse
        td.filas.append({"OBJECTID": doid, "GLOBALID": nuevo_guid(),
                         "NOMBRE": u"Borrar_%d" % j, "VALOR": j,
                         "MIOID": None, "MIGUID": guid_del(j)})
        doid += 1

    # Hijos en origen apuntando (por GLOBALID) a parents nuevos; en destino son
    # nuevos y se insertaran copiando PADREGUID=guid de origen, luego remapeado.
    parents_hijo = guids_nuevos[:N_HIJOS]
    for k, gid_padre in enumerate(parents_hijo):
        ho.filas.append({"OBJECTID": k + 1, "GLOBALID": guid_hijo(k),
                         "NOMBRE": u"Hijo_%d" % k, "PADREGUID": gid_padre})

    tablas = [
        ConfigTabla({"nombre": "ELEMENTO", "llave_negocio": "GLOBALID",
                     "columna_geometria": None}),
        ConfigTabla({"nombre": "HIJO", "llave_negocio": "GLOBALID",
                     "columna_geometria": None}),
    ]
    relaciones = [ConfigRelacion({"nombre": "ELEM_HIJO", "tabla_origen": "ELEMENTO",
                                  "tabla_destino": "HIJO", "tipo_llave": "guid",
                                  "columna_fk": "PADREGUID"})]
    cfg = MiniConfig(FakeOracle(origen), FakeOracle(destino), tablas, relaciones)

    # Inyectar arcpy y el doble de Oracle.
    fake_arcpy = FakeArcpy(destino, workspace="MEM")
    sys.modules["arcpy"] = fake_arcpy
    original = p2.ConexionOracle
    p2.ConexionOracle = _fabrica_fake
    try:
        log = _log_silencioso()
        codigo = p2.SincronizadorArcpy(cfg, log).ejecutar()
    finally:
        p2.ConexionOracle = original
        sys.modules.pop("arcpy", None)

    verificar(codigo == 0, u"Proceso 2 finaliza sin error (codigo=%s)" % codigo)

    # Cantidad final = cantidad de origen (nuevos insertados, eliminados fuera).
    verificar(len(td.filas) == len(to.filas),
              u"ELEMENTO: destino y origen igualan cantidad (%d)" % len(to.filas))

    # Indexar destino por MIGUID (identidad de origen) y comparar.
    idx_o = {normalizar_guid(f["GLOBALID"]): f for f in to.filas}
    idx_d = {normalizar_guid(f["MIGUID"]): f for f in td.filas}
    comparables = modelo.columnas_comparables(cols_elem_o, tablas[0])
    n, m, e = _diffs(idx_o, idx_d, comparables)
    verificar((n, m, e) == (0, 0, 0),
              u"ELEMENTO: sin diferencias tras sincronizar (n=%d m=%d e=%d)" % (n, m, e))

    # Identidad: un nuevo debe tener MIOID/MIGUID del origen y GLOBALID NUEVO.
    gnuevo = guids_nuevos[0]
    fila_d = idx_d[normalizar_guid(gnuevo)]
    verificar(normalizar_guid(fila_d["MIGUID"]) == normalizar_guid(gnuevo),
              u"INSERT: MIGUID guarda el GLOBALID del origen")
    verificar(fila_d["MIOID"] == idx_o[normalizar_guid(gnuevo)]["OBJECTID"],
              u"INSERT: MIOID guarda el OBJECTID del origen")
    verificar(normalizar_guid(fila_d["GLOBALID"]) != normalizar_guid(gnuevo),
              u"INSERT: el destino recibe un GLOBALID NUEVO (arcpy)")

    # Eliminados: ninguna fila de destino con MIGUID de la serie DEL.
    hay_del = any(normalizar_guid(f["MIGUID"]) == normalizar_guid(guid_del(0))
                  for f in td.filas)
    verificar(not hay_del, u"DELETE: los elementos sobrantes fueron eliminados")

    # Remapeo por GLOBALID: el hijo apunta al GLOBALID de destino del padre.
    hd = destino.tabla("HIJO")
    idx_hijo_d = {normalizar_guid(f["MIGUID"]): f for f in hd.filas}
    ok_remap = True
    for k, gid_padre in enumerate(parents_hijo):
        padre_d = idx_d[normalizar_guid(gid_padre)]
        hijo = idx_hijo_d.get(normalizar_guid(guid_hijo(k)))
        if hijo is None or normalizar_guid(hijo["PADREGUID"]) != \
                normalizar_guid(padre_d["GLOBALID"]):
            ok_remap = False
            break
    verificar(ok_remap,
              u"HIJO: FK por GLOBALID remapeada al GLOBALID de destino del padre")


# --------------------------------------------------------------------------- #
def _log_silencioso():
    import logging
    log = logging.getLogger("carga_masiva")
    if not getattr(log, "_ok", False):
        log.addHandler(logging.NullHandler())
        log.setLevel(logging.CRITICAL)
        log._ok = True
    return log


def main():
    print(u"== Prueba de carga masiva (20.000 elementos) ==")
    print(u"   nuevos=%d modificados=%d eliminados=%d iguales=%d" % (
        N_NUEVOS, N_MOD, N_ELIM, N_IGUAL))
    test_proceso1_oracle_20k()
    test_proceso2_arcpy_20k()
    print(u"")
    if _fallos:
        print(u"RESULTADO: %d fallo(s)" % len(_fallos))
        return 1
    print(u"RESULTADO: ambos escenarios OK con 20.000 elementos")
    return 0


if __name__ == "__main__":
    sys.exit(main())
