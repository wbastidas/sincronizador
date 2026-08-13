# -*- coding: utf-8 -*-
"""
entorno_prueba.crear_fc_geometrica
==================================

Crea y puebla una **feature class de puntos con geometria ST_GEOMETRY**
(``POSTEPRUEBA``) en las geodatabases ORIGEN y DESTINO, para validar la
sincronizacion de geometria del PROCESO 2 en un servidor real.

Se ejecuta con el **Python 2.7 de ArcGIS Desktop 10.8.1**::

    "C:\\Python27\\ArcGIS10.8\\python.exe" entorno_prueba/crear_fc_geometrica.py ^
        --sde-origen  C:\\conexiones\\origen.sde ^
        --sde-destino C:\\conexiones\\destino.sde ^
        --n 500 --srid 4326

Deja:
  * ORIGEN.POSTEPRUEBA con N puntos (la "verdad").
  * DESTINO.POSTEPRUEBA con un subconjunto divergente (identidad de origen en
    MIGUID/MIOID, GLOBALID propio, geometria distinta en algunos) para que el
    proceso 2 detecte nuevos/modificados/eliminados y transfiera la geometria.

Campos: OBJECTID (auto), SHAPE (ST_GEOMETRY punto), GLOBALID (auto),
NOMBRE, CODIGOEMPRESA, MIOID (LONG), MIGUID (GUID).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import os
import sys

NOMBRE_FC = "POSTEPRUEBA"


def _texto(prefijo, i):
    return u"%s_%d Muñoz, Ñandú áéíóú" % (prefijo, i)


def crear_fc(arcpy, sde, srid):
    ruta = os.path.join(sde, NOMBRE_FC)
    if arcpy.Exists(ruta):
        arcpy.Delete_management(ruta)
    sr = arcpy.SpatialReference(srid)
    arcpy.CreateFeatureclass_management(
        out_path=sde, out_name=NOMBRE_FC, geometry_type="POINT",
        spatial_reference=sr)
    arcpy.AddField_management(ruta, "NOMBRE", "TEXT", field_length=255)
    arcpy.AddField_management(ruta, "CODIGOEMPRESA", "TEXT", field_length=10)
    arcpy.AddField_management(ruta, "MIOID", "LONG")
    arcpy.AddField_management(ruta, "MIGUID", "GUID")
    # GlobalID gestionado por ArcGIS (no se puede escribir en el INSERT).
    arcpy.AddGlobalIDs_management(ruta)
    print("Creada FC: %s" % ruta)
    return ruta


def poblar_origen(arcpy, ruta, n):
    campos = ["SHAPE@XY", "NOMBRE", "CODIGOEMPRESA"]
    with arcpy.da.InsertCursor(ruta, campos) as cur:
        for i in range(n):
            x = -79.0 + (i % 100) * 0.001      # coordenadas de ejemplo (Ecuador)
            y = -2.0 + (i // 100) * 0.001
            cur.insertRow([(x, y), _texto(u"Poste", i), "001"])
    print("ORIGEN.%s poblado con %d puntos" % (NOMBRE_FC, n))


def poblar_destino(arcpy, ruta_origen, ruta_destino, n):
    """Siembra el destino divergente leyendo el origen (identidad en MIGUID)."""
    # Leer origen: GLOBALID, OBJECTID, geometria y atributos.
    origen = []
    with arcpy.da.SearchCursor(
            ruta_origen, ["GLOBALID", "OID@", "SHAPE@XY", "NOMBRE",
                          "CODIGOEMPRESA"]) as cur:
        for gid, oid, xy, nombre, emp in cur:
            origen.append((gid, oid, xy, nombre, emp))

    total = len(origen)
    ci, cm = int(total * 0.60), int(total * 0.85)
    campos = ["SHAPE@XY", "NOMBRE", "CODIGOEMPRESA", "MIOID", "MIGUID"]
    with arcpy.da.InsertCursor(ruta_destino, campos) as cur:
        for k, (gid, oid, xy, nombre, emp) in enumerate(origen):
            if k >= cm:
                continue  # ausente -> el proceso 2 lo insertara (NUEVO)
            x, y = xy
            nom = nombre
            if ci <= k < cm:               # atributo modificado
                nom = u"VIEJO " + (nombre or u"")
            if 50 <= k < 60:               # geometria modificada (desplazada)
                x, y = x + 0.01, y + 0.01
            cur.insertRow([(x, y), nom, emp, oid, gid])
    print("DESTINO.%s sembrado (divergente) desde %d puntos de origen"
          % (NOMBRE_FC, total))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Crea y puebla la FC geometrica POSTEPRUEBA (origen/destino).")
    parser.add_argument("--sde-origen", required=True)
    parser.add_argument("--sde-destino", required=True)
    parser.add_argument("--n", type=int, default=500)
    parser.add_argument("--srid", type=int, default=4326)
    args = parser.parse_args(argv)

    try:
        import arcpy
    except ImportError:
        print("arcpy no disponible. Ejecute con el Python de ArcGIS Desktop 10.8.1.")
        return 2
    arcpy.env.overwriteOutput = True

    ruta_o = crear_fc(arcpy, args.sde_origen, args.srid)
    ruta_d = crear_fc(arcpy, args.sde_destino, args.srid)
    poblar_origen(arcpy, ruta_o, args.n)
    poblar_destino(arcpy, ruta_o, ruta_d, args.n)

    print("Listo. Agregue POSTEPRUEBA (columna_geometria='SHAPE') a la config y "
          "ejecute el proceso 2; luego verifique con --llave-destino MIGUID.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
