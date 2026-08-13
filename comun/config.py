# -*- coding: utf-8 -*-
"""
comun.config
============

Carga y valida los dos archivos de configuracion en formato JSON:

* ``conexiones.json`` : credenciales/rutas de origen y destino.
* ``tablas.json``     : que tablas sincronizar y sus particularidades.

Se usa JSON (y no un ``.py``) para que un operador pueda ajustarlo sin tocar
codigo y para poder versionar plantillas ``*.example`` sin credenciales.

Todos los valores de texto se devuelven como ``unicode``.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import io
import json
import os


class ErrorConfig(Exception):
    """Error de configuracion (archivo ausente, JSON invalido o campo faltante)."""


def _leer_json(ruta):
    if not os.path.isfile(ruta):
        raise ErrorConfig(u"No existe el archivo de configuracion: %s" % ruta)
    try:
        with io.open(ruta, mode="r", encoding="utf-8") as fh:
            return json.load(fh)
    except ValueError as exc:
        raise ErrorConfig(u"JSON invalido en %s: %s" % (ruta, exc))


def _requerir(dic, clave, contexto):
    if clave not in dic:
        raise ErrorConfig(u"Falta la clave '%s' en %s" % (clave, contexto))
    return dic[clave]


class Conexion(object):
    """Datos de conexion de un extremo (origen o destino).

    Atributos:
        nombre        : etiqueta legible ("ORIGEN"/"DESTINO").
        oracle        : dict con dsn/user/password/nls (para cx_Oracle).
        sde_workspace : ruta al ``.sde`` (solo lo usa el proceso 2 / arcpy).
    """

    def __init__(self, nombre, datos):
        self.nombre = nombre
        self.oracle = _requerir(datos, "oracle", u"conexion '%s'" % nombre)
        # sde_workspace es opcional (proceso 1 no lo necesita).
        self.sde_workspace = datos.get("sde_workspace")

        # Validacion minima del bloque oracle.
        for campo in ("dsn", "user", "password"):
            _requerir(self.oracle, campo, u"conexion.oracle '%s'" % nombre)
        # Codificacion del cliente Oracle; por defecto UTF-8 para tildes/enies.
        self.oracle.setdefault("nls_lang", "SPANISH_SPAIN.AL32UTF8")
        self.oracle.setdefault("encoding", "UTF-8")


class ConfigTabla(object):
    """Configuracion de una tabla/feature class a sincronizar."""

    def __init__(self, datos):
        self.nombre = _requerir(datos, "nombre", u"tabla")
        # Llave de negocio estable entre bases (por defecto GLOBALID).
        self.llave_negocio = datos.get("llave_negocio", "GLOBALID")
        # Columna de geometria (None para tablas no espaciales).
        self.columna_geometria = datos.get("columna_geometria", "SHAPE")
        # Columnas a IGNORAR en la comparacion (ademas de las de sistema).
        self.ignorar = [c.upper() for c in datos.get("ignorar", [])]
        # Marca si la tabla participa en la red geometrica (opcional en proc.1).
        self.red_geometrica = bool(datos.get("red_geometrica", False))
        # Filtro WHERE opcional (p. ej. por empresa/provincia) para particionar.
        self.filtro = datos.get("filtro")
        # Politica de borrado: 'solo_migradas' (default) borra solo filas cuyo
        # MIGUID apunta a un origen ya inexistente; 'todas' borra cualquier
        # fila que no exista en origen; 'ninguna' nunca borra.
        self.politica_borrado = datos.get("politica_borrado", "solo_migradas")
        # Estrategia de asignacion de OBJECTID en proceso 1: 'max' o 'secuencia'.
        self.estrategia_objectid = datos.get("estrategia_objectid", "max")
        self.secuencia_objectid = datos.get("secuencia_objectid")


class ConfigRelacion(object):
    """Describe una relacion para el remapeo de llaves en el proceso 2.

    Cuando arcpy inserta y ArcGIS asigna un GLOBALID nuevo, hay que reparar las
    columnas hijas que apuntaban al GLOBALID (o al OBJECTID) del origen.
    """

    def __init__(self, datos):
        self.nombre = _requerir(datos, "nombre", u"relacion")
        self.tabla_origen = _requerir(datos, "tabla_origen", u"relacion")
        self.tabla_destino = _requerir(datos, "tabla_destino", u"relacion")
        # Tipo de llave: 'guid' (GLOBALID->FK) o 'oid' (OBJECTID->FK).
        self.tipo_llave = datos.get("tipo_llave", "guid")
        # Columna en la tabla hija que almacena la FK a remapear.
        self.columna_fk = _requerir(datos, "columna_fk", u"relacion")


class Configuracion(object):
    """Contenedor de toda la configuracion cargada."""

    def __init__(self, conexiones_json, tablas_json):
        datos_con = _leer_json(conexiones_json)
        datos_tab = _leer_json(tablas_json)

        self.origen = Conexion("ORIGEN", _requerir(datos_con, "origen", u"conexiones"))
        self.destino = Conexion("DESTINO", _requerir(datos_con, "destino", u"conexiones"))

        self.tablas = [ConfigTabla(t) for t in datos_tab.get("tablas", [])]
        self.relaciones = [ConfigRelacion(r) for r in datos_tab.get("relaciones", [])]

        # Opciones globales.
        opciones = datos_tab.get("opciones", {})
        self.incluir_red_geometrica = bool(opciones.get("incluir_red_geometrica", False))
        self.sincronizar_dominios = bool(opciones.get("sincronizar_dominios", True))
        self.tamano_lote = int(opciones.get("tamano_lote", 500))
        self.modo_simulacion = bool(opciones.get("modo_simulacion", False))

    def tablas_a_procesar(self):
        """Devuelve las tablas segun la opcion de red geometrica.

        Las tablas marcadas como ``red_geometrica`` solo se incluyen si
        ``incluir_red_geometrica`` esta activo (son OPCIONALES en el proceso 1).
        """
        for tabla in self.tablas:
            if tabla.red_geometrica and not self.incluir_red_geometrica:
                continue
            yield tabla


def cargar(conexiones_json, tablas_json):
    """Atajo de conveniencia para cargar la configuracion completa."""
    return Configuracion(conexiones_json, tablas_json)
