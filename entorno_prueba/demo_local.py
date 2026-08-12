# -*- coding: utf-8 -*-
"""
entorno_prueba.demo_local
=========================

Demostracion ejecutable **de extremo a extremo** que NO requiere Oracle ni
ArcGIS: usa los dobles de :mod:`tests.dobles` (FakeOracle / FakeArcpy) como si
fueran las dos bases, con un subconjunto realista del modelo SIGELEC:

    ESTRUCTURAANIVEL (padre)
        └─(GLOBALID → ESTRUCTURANIVELGLOBALID)─ PUNTOCARGA
                                                    └─(GLOBALID → PUNTOCARGAGLOBALID)─ CONEXIONCONSUMIDOR

Ejecuta el flujo completo real que se usaria en produccion:

    1. Reporte de diferencias PREVIO (CSV).
    2. Proceso 1 (Oracle directo)  -> Verificacion (debe dar SINCRONIZADO).
    3. Proceso 2 (arcpy)           -> Verificacion por MIGUID (SINCRONIZADO).

Incluye datos con **caracteres especiales** (tildes, enies, comas) y una mezcla
de nuevos / modificados / eliminados. Genera los CSV en ``entorno_prueba/salidas``.

Ejecutar::

    python -m entorno_prueba.demo_local
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import io
import os
import sys

from comun.config import ConfigTabla, ConfigRelacion
from comun.log import obtener_logger
from tests.dobles import Almacen, FakeOracle, FakeArcpy, nuevo_guid

import herramientas.reporte_diferencias as R
import proceso1_oracle.sincronizar_oracle as P1
import proceso2_arcpy.sincronizar_arcpy as P2

SALIDAS = os.path.join(os.path.dirname(__file__), "salidas")

# Tamanos del subconjunto (ajustables). Con estos valores los CSV son legibles.
N_EST = 200         # estructuras
N_PC = 400          # puntos de carga
N_CX = 600          # conexiones


# --------------------------------------------------------------------------- #
class _Con(object):
    def __init__(self, fake):
        self.oracle = {"_fake": fake}
        self.sde_workspace = "MEM"


class Cfg(object):
    def __init__(self, fo, fd):
        self.origen = _Con(fo)
        self.destino = _Con(fd)
        self.tablas = [
            ConfigTabla({"nombre": "ESTRUCTURAANIVEL", "columna_geometria": None}),
            ConfigTabla({"nombre": "PUNTOCARGA", "columna_geometria": None}),
            ConfigTabla({"nombre": "CONEXIONCONSUMIDOR", "columna_geometria": None}),
        ]
        self.relaciones = [
            ConfigRelacion({"nombre": "EstrucNivel_PuntoCarga",
                            "tabla_origen": "ESTRUCTURAANIVEL",
                            "tabla_destino": "PUNTOCARGA", "tipo_llave": "guid",
                            "columna_fk": "ESTRUCTURANIVELGLOBALID"}),
            ConfigRelacion({"nombre": "PuntoCarga_ConexConsumidor",
                            "tabla_origen": "PUNTOCARGA",
                            "tabla_destino": "CONEXIONCONSUMIDOR", "tipo_llave": "guid",
                            "columna_fk": "PUNTOCARGAGLOBALID"}),
        ]
        self.incluir_red_geometrica = False
        self.sincronizar_dominios = False
        self.tamano_lote = 500
        self.modo_simulacion = False

    def tablas_a_procesar(self):
        for t in self.tablas:
            yield t


def _fabrica(params, autocommit=False):
    return params["_fake"]


# --------------------------------------------------------------------------- #
# Construccion de datos
# --------------------------------------------------------------------------- #
def _texto(prefijo, i):
    # Incluye caracteres especiales a proposito.
    return u"%s_%d Muñoz, Ñandú áéíóú" % (prefijo, i)


def construir_origen():
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
        est_gid = "{EST-%06d}" % (i % N_EST)
        pc.filas.append({"OBJECTID": i + 1, "GLOBALID": "{PC-%06d}" % i,
                         "ESTRUCTURANIVELGLOBALID": est_gid,
                         "NOMBRE": _texto(u"PC", i), "CARGA": i * 1.5})
    for i in range(N_CX):
        pc_gid = "{PC-%06d}" % (i % N_PC)
        cx.filas.append({"OBJECTID": i + 1, "GLOBALID": "{CX-%06d}" % i,
                         "PUNTOCARGAGLOBALID": pc_gid,
                         "CODIGOCLIENTE": "CLI%06d" % i,
                         "NOMBRECLIENTE": _texto(u"Cliente", i)})
    return a


def construir_destino_p1(origen):
    """Destino divergente para el proceso 1 (identidad por GLOBALID copiado).

    - primeras 60% filas: iguales
    - siguiente 25%: modificadas (cambia un valor)
    - ultimo 15% de origen: ausentes (seran NUEVOS)
    - se agregan filas extra que no existen en origen (seran ELIMINADOS)
    """
    d = Almacen()
    for nombre in ("ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR"):
        to = origen.tabla(nombre)
        td = d.crear(nombre, list(to.columnas))
        n = len(to.filas)
        corte_igual = int(n * 0.60)
        corte_mod = int(n * 0.85)  # 60-85% modificadas; 85-100% ausentes (nuevos)
        for k, fila in enumerate(to.filas):
            if k < corte_igual:
                td.filas.append(dict(fila))
            elif k < corte_mod:
                copia = dict(fila)
                # Modificar un campo de texto/numero segun la tabla.
                if "NOMBRE" in copia:
                    copia["NOMBRE"] = u"VIEJO " + (copia["NOMBRE"] or u"")
                elif "NOMBRECLIENTE" in copia:
                    copia["NOMBRECLIENTE"] = u"VIEJO " + (copia["NOMBRECLIENTE"] or u"")
                td.filas.append(copia)
            # else: ausente -> nuevo
        # Extras que sobran en destino -> eliminados.
        base = 900000
        for j in range(int(n * 0.10)):
            fila = {c: None for c in td.columnas}
            fila["OBJECTID"] = base + j
            fila["GLOBALID"] = "{DEL-%s-%06d}" % (nombre[:3], j)
            td.filas.append(fila)
    return d


def construir_destino_p2(origen):
    """Destino divergente para el proceso 2 (identidad del origen en MIGUID)."""
    d = Almacen()
    for nombre in ("ESTRUCTURAANIVEL", "PUNTOCARGA", "CONEXIONCONSUMIDOR"):
        to = origen.tabla(nombre)
        cols = list(to.columnas) + ["MIOID", "MIGUID"]
        td = d.crear(nombre, cols)
        n = len(to.filas)
        corte_igual = int(n * 0.60)
        corte_mod = int(n * 0.85)
        doid = 700000
        for k, fila in enumerate(to.filas):
            if k >= corte_mod:
                continue  # ausente -> nuevo
            copia = dict(fila)
            copia["MIGUID"] = fila["GLOBALID"]       # identidad de origen
            copia["MIOID"] = fila["OBJECTID"]
            copia["GLOBALID"] = nuevo_guid()          # identidad local distinta
            copia["OBJECTID"] = doid
            doid += 1
            if corte_igual <= k < corte_mod:          # modificadas
                if "NOMBRE" in copia:
                    copia["NOMBRE"] = u"VIEJO " + (fila["NOMBRE"] or u"")
                elif "NOMBRECLIENTE" in copia:
                    copia["NOMBRECLIENTE"] = u"VIEJO " + (fila["NOMBRECLIENTE"] or u"")
            td.filas.append(copia)
        # Sobrantes -> eliminados (MIGUID que no existe en origen).
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
    print(u"\n  --- %s (%s) ---" % (titulo, ruta))
    if not os.path.isfile(ruta):
        print(u"    (no generado)")
        return
    with io.open(ruta, encoding="utf-8-sig") as fh:
        for i, linea in enumerate(fh):
            if i >= maximo:
                print(u"    ...")
                break
            print(u"    " + linea.rstrip())


def main():
    log = obtener_logger("demo_local", carpeta=SALIDAS)
    print(u"=========================================================")
    print(u" DEMO ENTORNO DE PRUEBA (subconjunto real SIGELEC)")
    print(u" estructuras=%d puntos_carga=%d conexiones=%d" % (N_EST, N_PC, N_CX))
    print(u"=========================================================")

    # ---------------- Proceso 1 ----------------
    origen = construir_origen()
    dest1 = construir_destino_p1(origen)
    cfg1 = Cfg(FakeOracle(origen), FakeOracle(dest1))

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

    # ---------------- Proceso 2 ----------------
    dest2 = construir_destino_p2(origen)
    cfg2 = Cfg(FakeOracle(origen), FakeOracle(dest2))
    fake_arcpy = FakeArcpy(dest2, workspace="MEM")

    print(u"\n[4] Ejecutando PROCESO 2 (arcpy)")
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

    print(u"\n=========================================================")
    ok1 = (total1 == 0)
    ok2 = (codigo2 == 0 and total2 == 0)
    print(u" PROCESO 1: %s (diferencias restantes=%d)" % (
        "SINCRONIZADO" if ok1 else "PENDIENTE", total1))
    print(u" PROCESO 2: %s (diferencias restantes=%d)" % (
        "SINCRONIZADO" if ok2 else "PENDIENTE", total2))
    print(u"=========================================================")
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    sys.exit(main())
