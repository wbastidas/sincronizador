# -*- coding: utf-8 -*-
"""
proceso2_arcpy.dominios_arcpy
=============================

Aplicacion de diferencias de dominios usando las herramientas oficiales de
``arcpy`` (unica via segura para modificar los dominios reales de la
geodatabase en ArcGIS Desktop 10.8.1).

Se apoya en :func:`comun.dominios.comparar_dominios` para saber QUE cambiar y
aqui se decide COMO aplicarlo:

* Dominio **nuevo**  -> ``CreateDomain`` + ``AddCodedValueToDomain`` por valor.
* Dominio **modificado**:
    - valores a agregar  -> ``AddCodedValueToDomain``
    - valores a cambiar  -> ``AddCodedValueToDomain`` (sobrescribe la descripcion)
    - valores a quitar   -> ``DeleteCodedValueFromDomain``
* Dominio **eliminado** por completo -> **solo se reporta** (borrar un dominio
  asignado a campos es peligroso; se deja a decision manual).
"""

from __future__ import absolute_import, division, print_function, unicode_literals


# Mapa de tipo de campo del XML de GDB_ITEMS al parametro field_type de
# CreateDomain_management.
_TIPO_CAMPO = {
    "esriFieldTypeString": "TEXT",
    "esriFieldTypeSmallInteger": "SHORT",
    "esriFieldTypeInteger": "LONG",
    "esriFieldTypeSingle": "FLOAT",
    "esriFieldTypeDouble": "DOUBLE",
    "esriFieldTypeDate": "DATE",
}


def aplicar_dominios(arcpy, workspace, diferencias, dominios_origen, log,
                     simular=False):
    """Aplica las diferencias de dominios sobre ``workspace`` (destino).

    :param diferencias:      lista de comun.dominios.DiferenciaDominio.
    :param dominios_origen:  dict {nombre: comun.dominios.Dominio} del origen
                             (para conocer tipo de campo al crear dominios).
    :param simular:          si True, solo registra lo que haria.
    """
    if not diferencias:
        log.info(u"Dominios (arcpy): sin diferencias que aplicar.")
        return

    for dif in diferencias:
        try:
            if dif.estado == "eliminado":
                log.warning(
                    u"Dominio '%s' existe en destino y no en origen. NO se borra "
                    u"automaticamente (puede estar asignado a campos). Revise "
                    u"manualmente.", dif.nombre)
                continue

            if dif.estado == "nuevo":
                _crear_dominio(arcpy, workspace, dif, dominios_origen, log, simular)

            # Agregar / actualizar valores (aplica a nuevo y modificado).
            for cod, desc in sorted(dif.codigos_agregar.items()):
                _add_coded(arcpy, workspace, dif.nombre, cod, desc, log, simular)
            for cod, (o, _d) in sorted(dif.codigos_cambiar.items()):
                _add_coded(arcpy, workspace, dif.nombre, cod, o, log, simular)
            for cod, desc in sorted(dif.codigos_quitar.items()):
                _del_coded(arcpy, workspace, dif.nombre, cod, log, simular)

        except Exception as exc:  # pragma: no cover - depende del entorno arcpy
            log.error(u"Error aplicando dominio '%s': %s", dif.nombre, exc)


def _crear_dominio(arcpy, workspace, dif, dominios_origen, log, simular):
    dom = dominios_origen.get(dif.nombre)
    tipo_campo = "TEXT"
    tipo_dominio = "CODED"
    if dom is not None:
        tipo_campo = _TIPO_CAMPO.get(dom.tipo_campo, "TEXT")
        tipo_dominio = "RANGE" if dom.tipo == "Range" else "CODED"
    log.info(u"CREATE DOMAIN '%s' (campo=%s, tipo=%s)",
             dif.nombre, tipo_campo, tipo_dominio)
    if simular:
        return
    arcpy.CreateDomain_management(
        workspace, dif.nombre, dif.nombre, tipo_campo, tipo_dominio)
    if tipo_dominio == "RANGE" and dif.rango_origen and None not in dif.rango_origen:
        arcpy.SetValueForRangeDomain_management(
            workspace, dif.nombre, dif.rango_origen[0], dif.rango_origen[1])


def _add_coded(arcpy, workspace, nombre, codigo, descripcion, log, simular):
    log.info(u"  DOMINIO %s: set %s = %s", nombre, codigo, descripcion)
    if simular:
        return
    arcpy.AddCodedValueToDomain_management(
        workspace, nombre, codigo, descripcion or codigo)


def _del_coded(arcpy, workspace, nombre, codigo, log, simular):
    log.info(u"  DOMINIO %s: quitar %s", nombre, codigo)
    if simular:
        return
    arcpy.DeleteCodedValueFromDomain_management(workspace, nombre, codigo)
