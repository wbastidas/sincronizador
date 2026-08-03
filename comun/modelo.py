# -*- coding: utf-8 -*-
"""
comun.modelo
============

Conocimiento del modelo de datos SIGELEC que es independiente de cada tabla
concreta.  Encapsula las reglas generales observadas en el diccionario de datos:

* Casi toda tabla tiene ``OBJECTID`` (gestionado por SDE, **local** a cada base)
  y la mayoria ``GLOBALID`` (GUID **global**, estable entre bases).
* Las feature class tienen ``SHAPE`` y campos calculados
  ``SHAPE_LENGTH`` / ``SHAPE_AREA`` que NO deben compararse ni copiarse.
* Muchas tablas tienen ``MIOID`` (Integer) y ``MIGUID`` (Guid): campos "espejo"
  donde el proceso 2 guarda el OBJECTID/GLOBALID del origen.
* Las tablas de la **red geometrica** se reconocen por campos como ``ENABLED``,
  ``ELECTRICTRACEWEIGHT``, ``FDRMGRNONTRACEABLE`` o ``ANCILLARYROLE``.

Este modulo decide, para una tabla dada, que columnas entran en la comparacion
y cuales se excluyen por ser de sistema, calculadas o volatiles.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

# Columnas que NUNCA se comparan ni se copian tal cual entre bases porque son
# locales de cada geodatabase o se recalculan solas.
COLUMNAS_SISTEMA = {
    "OBJECTID",        # id local gestionado por SDE.
    "SHAPE",           # geometria: se maneja aparte (token SHAPE@ en arcpy).
    "SHAPE_LENGTH",    # calculada por ArcGIS.
    "SHAPE_AREA",      # calculada por ArcGIS.
    "SHAPE.LEN",       # alias que a veces expone SDE.
    "SHAPE.AREA",
}

# Campos "espejo" que gestiona el proceso 2 (no forman parte del dato de negocio
# y por tanto se excluyen de la firma de comparacion).
COLUMNAS_ESPEJO = {"MIOID", "MIGUID"}

# Indicios de que una tabla participa en la red geometrica.  Si una tabla tiene
# alguna de estas columnas, se considera "afecta a la red" salvo configuracion
# explicita en contrario.
COLUMNAS_RED = {
    "ENABLED",
    "ELECTRICTRACEWEIGHT",
    "FDRMGRNONTRACEABLE",
    "ANCILLARYROLE",
    "FEEDERID",
    "FEEDERINFO",
    "FEEDERID2",
    "PARENTCIRCUITSOURCEGUID",
    "CIRCUITSOURCEGUID",
}

# Campos de auditoria.  Se comparan por defecto, pero se pueden ignorar via
# configuracion (``ignorar``) si el operador no quiere que un simple cambio de
# fecha de modificacion dispare un UPDATE.
COLUMNAS_AUDITORIA = {
    "USUARIOREGISTRO",
    "FECHAREGISTRO",
    "FECHAMODIFICACIONREGISTRO",
    "USUARIOMODIFICACIONREGISTRO",
}


def es_columna_sistema(nombre):
    return nombre.upper() in COLUMNAS_SISTEMA


def afecta_red_geometrica(columnas):
    """Heuristica: ¿la tabla participa en la red geometrica?

    :param columnas: iterable de nombres de columna (en cualquier caja).
    """
    conjunto = {c.upper() for c in columnas}
    return bool(conjunto & COLUMNAS_RED)


def columnas_comparables(todas_las_columnas, cfg_tabla):
    """Devuelve la lista ORDENADA de columnas que entran en la comparacion.

    Se excluyen:
      * columnas de sistema (OBJECTID, SHAPE, SHAPE_LENGTH, SHAPE_AREA),
      * columnas espejo (MIOID, MIGUID),
      * las indicadas explicitamente en ``cfg_tabla.ignorar``.

    La geometria (SHAPE) se compara aparte (ver comparador/arcpy), no aqui.

    :param todas_las_columnas: lista de nombres de columna de la tabla.
    :param cfg_tabla:          instancia de comun.config.ConfigTabla.
    """
    ignorar = set(cfg_tabla.ignorar) | COLUMNAS_SISTEMA | COLUMNAS_ESPEJO
    # La llave de negocio SI se incluye en la comparacion como ancla, salvo que
    # sea OBJECTID (local): en ese caso no se compara como valor.
    resultado = []
    for col in todas_las_columnas:
        cu = col.upper()
        if cu in ignorar:
            continue
        resultado.append(cu)
    # Orden estable para que la firma sea reproducible.
    return sorted(resultado)


def columnas_copiables(todas_las_columnas, cfg_tabla, incluir_globalid=True):
    """Columnas que se INSERTAN/ACTUALIZAN en destino.

    En el proceso 1 (Oracle directo) se puede escribir GLOBALID, por lo que se
    copia tal cual desde el origen (preserva relaciones sin remapeo).

    En el proceso 2 (arcpy) NO se puede escribir OBJECTID ni GLOBALID en el
    INSERT: por eso ``incluir_globalid=False`` los excluye y en su lugar se
    rellenan MIOID/MIGUID.
    """
    excluir = set(cfg_tabla.ignorar) | {"OBJECTID", "SHAPE_LENGTH", "SHAPE_AREA"}
    if not incluir_globalid:
        excluir.add("GLOBALID")
    resultado = []
    for col in todas_las_columnas:
        cu = col.upper()
        if cu in excluir:
            continue
        if cu == "SHAPE":
            # La geometria se maneja con token especial, no como columna comun.
            continue
        resultado.append(cu)
    return resultado
