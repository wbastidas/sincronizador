# -*- coding: utf-8 -*-
"""
entorno_prueba.crear_conexiones_sde
===================================

Crea los archivos de conexion **.sde** que necesita el PROCESO 2 (arcpy), y
opcionalmente un **dominio de prueba** en la geodatabase destino para validar la
sincronizacion de dominios.

Se ejecuta con el **Python 2.7 de ArcGIS Desktop 10.8.1** (trae arcpy)::

    "C:\\Python27\\ArcGIS10.8\\python.exe" entorno_prueba/crear_conexiones_sde.py ^
        --carpeta C:\\conexiones ^
        --host-origen SRVORIGEN --servicio-origen ORCL --usuario-origen sde --clave-origen ***  ^
        --host-destino SRVDESTINO --servicio-destino ORCL --usuario-destino sde --clave-destino ***

Genera ``origen.sde`` y ``destino.sde`` en la carpeta indicada. Use esas rutas en
``config/conexiones.json`` (campo ``sde_workspace``).

Nota: las feature class con geometria (ST_GEOMETRY) y la red geometrica deben
existir en la geodatabase. Este script no las crea (dependen de su diseno);
para las pruebas de atributos y relaciones basta con las tablas de
``sql/01_esquema.sql`` registradas en la geodatabase.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import os
import sys


def crear_sde(arcpy, carpeta, nombre, host, servicio, usuario, clave):
    ruta = os.path.join(carpeta, nombre)
    if arcpy.Exists(ruta):
        arcpy.Delete_management(ruta)
    # instance para Oracle Easy Connect: "sde:oracle11g:HOST:PORT/SERVICIO" o
    # simplemente el TNS/EZConnect segun su cliente.
    instancia = "sde:oracle11g:%s/%s" % (host, servicio)
    arcpy.CreateDatabaseConnection_management(
        out_folder_path=carpeta,
        out_name=nombre,
        database_platform="ORACLE",
        instance=instancia,
        account_authentication="DATABASE_AUTH",
        username=usuario,
        password=clave,
        save_user_pass="SAVE_USERNAME",
    )
    print("Creado: %s" % ruta)
    return ruta


def crear_dominio_prueba(arcpy, sde_destino):
    """Crea un dominio de valores codificados de ejemplo en el destino."""
    nombre = "EstadoConexionPrueba"
    try:
        arcpy.CreateDomain_management(sde_destino, nombre, "Estado (prueba)",
                                      "TEXT", "CODED")
        for cod, desc in [("A", u"Activo"), ("S", u"Suspendido"),
                          ("C", u"Cortado")]:
            arcpy.AddCodedValueToDomain_management(sde_destino, nombre, cod, desc)
        print("Dominio de prueba creado en destino: %s" % nombre)
    except Exception as exc:
        print("No se pudo crear el dominio de prueba: %s" % exc)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crea archivos .sde de conexion.")
    parser.add_argument("--carpeta", required=True)
    parser.add_argument("--host-origen", required=True)
    parser.add_argument("--servicio-origen", required=True)
    parser.add_argument("--usuario-origen", required=True)
    parser.add_argument("--clave-origen", required=True)
    parser.add_argument("--host-destino", required=True)
    parser.add_argument("--servicio-destino", required=True)
    parser.add_argument("--usuario-destino", required=True)
    parser.add_argument("--clave-destino", required=True)
    parser.add_argument("--crear-dominio", action="store_true")
    args = parser.parse_args(argv)

    try:
        import arcpy
    except ImportError:
        print("arcpy no disponible. Ejecute con el Python de ArcGIS Desktop 10.8.1.")
        return 2

    if not os.path.isdir(args.carpeta):
        os.makedirs(args.carpeta)

    crear_sde(arcpy, args.carpeta, "origen.sde", args.host_origen,
              args.servicio_origen, args.usuario_origen, args.clave_origen)
    destino = crear_sde(arcpy, args.carpeta, "destino.sde", args.host_destino,
                        args.servicio_destino, args.usuario_destino, args.clave_destino)

    if args.crear_dominio:
        crear_dominio_prueba(arcpy, destino)

    print("Listo. Configure estas rutas .sde en config/conexiones.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
