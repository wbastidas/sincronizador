# -*- coding: utf-8 -*-
"""
entorno_prueba.demo_local
=========================

Demostracion ejecutable **de extremo a extremo** que NO requiere Oracle ni
ArcGIS: usa los dobles de :mod:`tests.dobles` (FakeOracle / FakeArcpy) como si
fueran las dos bases, con un subconjunto realista del modelo SIGELEC:

    ESTRUCTURAANIVEL (padre)
        └─(GLOBALID→ESTRUCTURANIVELGLOBALID)─ PUNTOCARGA
                                                └─(GLOBALID→PUNTOCARGAGLOBALID)─ CONEXIONCONSUMIDOR
    POSTEPRUEBA (feature class con geometria SHAPE / ST_GEOMETRY)

Ejecuta el flujo completo real:

    1. Reporte de diferencias PREVIO (CSV).
    2. Proceso 1 (Oracle directo)  -> Verificacion (SINCRONIZADO).
    3. Proceso 2 (arcpy, con GEOMETRIA) -> Verificacion por MIGUID (SINCRONIZADO)
       + comprobacion de que la geometria (SHAPE) se transfirio correctamente.

Datos con **caracteres especiales**. CSV en ``entorno_prueba/salidas``.

Ejecutar::

    python -m entorno_prueba.demo_local
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import hashlib
import io
import os
import sys

from comun.config import ConfigTabla, ConfigRelacion
from comun.log import obtener_logger
from comun.utiles import normalizar_guid
from tests.dobles import Almacen, FakeOracle, FakeArcpy, nuevo_guid

import herramientas.reporte_diferencias as R
import proceso1_oracle.sincronizar_oracle as P1
import proceso2_arcpy.sincronizar_arcpy as P2

SALIDAS = os.path.join(os.path.dirname(__file__), "salidas")

N_EST = 200
N_PC = 400
N_CX = 600
N_POSTE = 120


def _fabrica(params, autocommit=False):
    return params["_fake"]


class _Con(object):
    def __init__(self, fake, ws):
        self.oracle = {"_fake": fake}
        self.sde_workspace = ws


class Cfg(object):
    def __init__(self, fo, fd, tablas, relaciones, ws_origen="ORIG", ws_destino="DEST"):
        self.origen = _Con(fo, ws_origen)
        self.destino = _Con(fd, ws_destino)
        self.tablas = tablas
        self.relaciones = relaciones
        self.incluir_red_geometrica = False
        self.sincronizar_dominios = False
        self.tamano_lote = 500
        self.modo_simulacion = False

    def tablas_a_procesar(self):
        for t in self.tablas:
            yield t


RELACIONES = [
    ConfigRelacion({"nombre": "EstrucNivel_PuntoCarga",
                    "tabla_origen": "ESTRUCTURAANIVEL",
                    "tabla_destino": "PUNTOCARGA", "tipo_llave": "guid",
                    "columna_fk": "ESTRUCTURANIVELGLOBALID"}),
    ConfigRelacion({"nombre": "PuntoCarga_ConexConsumidor",
                    "tabla_origen": "PUNTOCARGA",
                    "tabla_destino": "CONEXIONCONSUMIDOR", "tipo_llave": "guid",
                    "columna_fk": "PUNTOCARGAGLOBALID"}),
]


def _texto(prefijo, i):
    return u"%s_%d Muñoz, Ñandú áéíóú" % (prefijo, i)


def _wkb(i, variante=0):
    # Geometria opaca para el doble (en arcpy real seria WKB binario).
    return ("POINT|%d|%d" % (i, variante)).encode("utf-8")


# --------------------------------------------------------------------------- #
def construir_origen(con_geometria=False):
    a = Almacen()
    est = a.crear("ESTRUCTURAANIVEL",
                  ["OBJECTID", "GLOBALID", "NOMBRE", "CODIGOEMPRESA"])
    pc = a.crear("PUNTOCARGA",
                 ["OBJECTID", "GLOBALID", "ESTRUCTURANIVELGLOBALID", "NOMBRE", "CARGA"])
    cx = a.crear("CONEXIONCONSUMIDOR",
                 ["OBJECTID", "GLOBALID", "PUNTOCARGAGLOBALID", "CODIGOCLIENTE", "NOMBRECLIENTE"])
    for i in range(N_EST):
        est.filas.append({"OBJECTID": i + 1, "GLOBALID": "{EST-%06d}" % i,
                          "NOMBRE": _texto(u"Estr", i), "CODIGOEMPRESA": "001"})
    for i in range(N_PC):
        pc.filas.append({"OBJECTID": i + 1, "GLOBALID": "{PC-%06d}" % i,
                         "ESTRUCTURANIVELGLOBALID": "{EST-%06d}" % (i % N_EST),
                         "NOMBRE": _texto(u"PC", i), "CARGA": i * 1.5})
    for i in range(N_CX):
        cx.filas.append({"OBJECTID": i + 1, "GLOBALID": "{CX-%06d}" % i,
                         "PUNTOCARGAGLOBALID": "{PC-%06d}" % (i % N_PC),
                         "CODIGOCLIENTE": "CLI%06d" % i,
                         "NOMBRECLIENTE": _texto(u"Cliente", i)})
    if con_geometria:
        po = a.crear("POSTEPRUEBA",
                     ["OBJECTID", "GLOBALID", "NOMBRE", "CODIGOEMPRESA", "SHAPE"])
        for i in range(N_POSTE):
            po.filas.append({"OBJECTID": i + 1, "GLOBALID": "{POS-%06d}" % i,
                             "NOMBRE": _texto(u"Poste", i), "CODIGOEMPRESA": "001",
                             "SHAPE": None, "SHAPE@WKB": _wkb(i)})
    return a


def construir_destino_p1(origen):
    d = Almacen()
    for nombre in ("ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR"):
        to = origen.tabla(nombre)
        td = d.crear(nombre, list(to.columnas))
        n = len(to.filas)
        ci, cm = int(n * 0.60), int(n * 0.85)
        for k, fila in enumerate(to.filas):
            if k < ci:
                td.filas.append(dict(fila))
            elif k < cm:
                copia = dict(fila)
                campo = "NOMBRE" if "NOMBRE" in copia else "NOMBRECLIENTE"
                copia[campo] = u"VIEJO " + (copia[campo] or u"")
                td.filas.append(copia)
        base = 900000
        for j in range(int(n * 0.10)):
            fila = {c: None for c in td.columnas}
            fila["OBJECTID"] = base + j
            fila["GLOBALID"] = "{DEL-%s-%06d}" % (nombre[:3], j)
            td.filas.append(fila)
    return d


def construir_destino_p2(origen):
    d = Almacen()
    tablas = ["ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR", "POSTEPRUEBA"]
    for nombre in tablas:
        to = origen.tabla(nombre)
        td = d.crear(nombre, list(to.columnas) + ["MIOID", "MIGUID"])
        n = len(to.filas)
        ci, cm = int(n * 0.60), int(n * 0.85)
        doid = 700000
        for k, fila in enumerate(to.filas):
            if k >= cm:
                continue  # ausente -> nuevo
            copia = dict(fila)
            copia["MIGUID"] = fila["GLOBALID"]
            copia["MIOID"] = fila["OBJECTID"]
            copia["GLOBALID"] = nuevo_guid()
            copia["OBJECTID"] = doid
            doid += 1
            if nombre == "POSTEPRUEBA":
                # Geometria: para [ci:cm) igual; para [50:60) forzamos geometria
                # DISTINTA (variante 9) -> debe detectarse como modificado y
                # corregirse a la del origen.
                if 50 <= k < 60:
                    copia["SHAPE@WKB"] = _wkb(k, variante=9)
                else:
                    copia["SHAPE@WKB"] = fila.get("SHAPE@WKB")
            if ci <= k < cm:
                campo = ("NOMBRECLIENTE" if nombre == "CONEXIONCONSUMIDOR"
                         else "NOMBRE")
                copia[campo] = u"VIEJO " + (fila.get(campo) or u"")
            td.filas.append(copia)
        for j in range(int(n * 0.10)):
            fila = {c: None for c in td.columnas}
            fila["OBJECTID"] = doid
            doid += 1
            fila["GLOBALID"] = nuevo_guid()
            fila["MIGUID"] = "{DEL-%s-%06d}" % (nombre[:3], j)
            td.filas.append(fila)
    return d


# --------------------------------------------------------------------------- #
def reporte(cfg, carpeta, log, verificar=False, llave_destino=None):
    original = R.ConexionOracle
    R.ConexionOracle = _fabrica
    try:
        return R.GeneradorReporte(cfg, log, modo_verificacion=verificar,
                                  llave_destino=llave_destino).ejecutar(carpeta)
    finally:
        R.ConexionOracle = original


def mostrar_csv(ruta, titulo, maximo=8):
    print(u"\n  --- %s ---" % titulo)
    if not os.path.isfile(ruta):
        print(u"    (no generado)")
        return
    with io.open(ruta, encoding="utf-8-sig") as fh:
        for i, linea in enumerate(fh):
            if i >= maximo:
                print(u"    ...")
                break
            print(u"    " + linea.rstrip())


def verificar_geometria(origen, destino, log):
    """Comprueba que la geometria (SHAPE@WKB) del destino iguala a la del origen."""
    to = origen.tabla("POSTEPRUEBA")
    td = destino.tabla("POSTEPRUEBA")
    geo_o = {normalizar_guid(f["GLOBALID"]): f.get("SHAPE@WKB") for f in to.filas}
    idx_d = {normalizar_guid(f["MIGUID"]): f for f in td.filas}
    total = 0
    iguales = 0
    for gid, wkb_o in geo_o.items():
        fd = idx_d.get(gid)
        if fd is None:
            continue
        total += 1
        h_o = hashlib.md5(wkb_o).hexdigest() if wkb_o else None
        wkb_d = fd.get("SHAPE@WKB")
        h_d = hashlib.md5(wkb_d).hexdigest() if wkb_d else None
        if h_o == h_d:
            iguales += 1
    log.info(u"POSTEPRUEBA geometria: %d/%d con SHAPE identico al origen",
             iguales, total)
    return total > 0 and iguales == total


def main():
    log = obtener_logger("demo_local", carpeta=SALIDAS)
    print(u"=========================================================")
    print(u" DEMO ENTORNO DE PRUEBA (subconjunto real SIGELEC)")
    print(u" estructuras=%d puntos=%d conexiones=%d postes(geom)=%d" % (
        N_EST, N_PC, N_CX, N_POSTE))
    print(u"=========================================================")

    origen = construir_origen(con_geometria=True)

    tablas_p1 = [ConfigTabla({"nombre": n, "columna_geometria": None})
                 for n in ("ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR")]
    tablas_p2 = list(tablas_p1) + [
        ConfigTabla({"nombre": "POSTEPRUEBA", "columna_geometria": "SHAPE"})]

    # ---------------- Proceso 1 ----------------
    dest1 = construir_destino_p1(origen)
    cfg1 = Cfg(FakeOracle(origen), FakeOracle(dest1), tablas_p1, RELACIONES,
               ws_origen="ORIG", ws_destino="DEST")

    print(u"\n[1] Reporte PREVIO (proceso 1)")
    reporte(cfg1, os.path.join(SALIDAS, "p1_previo"), log)
    mostrar_csv(os.path.join(SALIDAS, "p1_previo", "resumen.csv"), "resumen previo")

    print(u"\n[2] Ejecutando PROCESO 1 (Oracle directo)")
    orig = P1.ConexionOracle
    P1.ConexionOracle = _fabrica
    try:
        P1.SincronizadorOracle(cfg1, log).ejecutar()
    finally:
        P1.ConexionOracle = orig

    print(u"\n[3] VERIFICACION proceso 1")
    total1 = reporte(cfg1, os.path.join(SALIDAS, "p1_verificacion"), log,
                     verificar=True)
    mostrar_csv(os.path.join(SALIDAS, "p1_verificacion", "verificacion.csv"),
                "verificacion p1")

    # ---------------- Proceso 2 (con geometria) ----------------
    dest2 = construir_destino_p2(origen)
    cfg2 = Cfg(FakeOracle(origen), FakeOracle(dest2), tablas_p2, RELACIONES,
               ws_origen="ORIG", ws_destino="DEST")
    # Un unico FakeArcpy con DOS workspaces: ORIG (lectura de geometria) y DEST.
    fake_arcpy = FakeArcpy(workspaces={"ORIG": origen, "DEST": dest2})

    print(u"\n[4] Ejecutando PROCESO 2 (arcpy, con geometria)")
    sys.modules["arcpy"] = fake_arcpy
    orig2 = P2.ConexionOracle
    P2.ConexionOracle = _fabrica
    try:
        codigo2 = P2.SincronizadorArcpy(cfg2, log).ejecutar()
    finally:
        P2.ConexionOracle = orig2
        sys.modules.pop("arcpy", None)

    print(u"\n[5] VERIFICACION proceso 2 (llave destino = MIGUID)")
    total2 = reporte(cfg2, os.path.join(SALIDAS, "p2_verificacion"), log,
                     verificar=True, llave_destino="MIGUID")
    mostrar_csv(os.path.join(SALIDAS, "p2_verificacion", "verificacion.csv"),
                "verificacion p2")
    geom_ok = verificar_geometria(origen, dest2, log)

    print(u"\n=========================================================")
    ok1 = (total1 == 0)
    ok2 = (codigo2 == 0 and total2 == 0 and geom_ok)
    print(u" PROCESO 1: %s (diferencias restantes=%d)" % (
        "SINCRONIZADO" if ok1 else "PENDIENTE", total1))
    print(u" PROCESO 2: %s (diferencias=%d, geometria=%s)" % (
        "SINCRONIZADO" if ok2 else "PENDIENTE", total2,
        "OK" if geom_ok else "FALLO"))
    print(u"=========================================================")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
