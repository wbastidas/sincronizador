# -*- coding: utf-8 -*-
"""
comun.dominios
==============

Comparacion y sincronizacion de **dominios** (listas de valores codificados y
rangos) entre las dos geodatabases.

Hay dos "capas" de dominios en el modelo SIGELEC:

1. **Dominios reales de la geodatabase** (CodedValueDomain / RangeDomain).  Su
   definicion vive en el repositorio SDE, tabla ``GDB_ITEMS`` (o la vista
   ``GDB_ITEMS_VIEW``), como un XML en la columna ``Definition``.

2. Una **tabla de aplicacion** ``DOMINIOS`` (DOMINIO, CODE, VALUE...) que es una
   copia "de negocio" de esos valores codificados, usada por aplicaciones
   externas.

Estrategia por proceso:

* **Proceso 1 (Oracle puro):** LEE los dominios reales desde ``GDB_ITEMS`` en
  ambas bases y REPORTA las diferencias (dominios/valores nuevos, eliminados o
  cambiados).  La tabla de aplicacion ``DOMINIOS`` se sincroniza como una tabla
  mas (via el motor de diff), porque eso si es DML seguro.  **No** reescribe el
  XML de ``GDB_ITEMS`` directamente (es arriesgado sin ArcGIS); para aplicar
  cambios en los dominios reales se recomienda el proceso 2.

* **Proceso 2 (arcpy):** compara los dominios con ``arcpy.da.ListDomains`` y los
  aplica con las herramientas oficiales (``AddCodedValueToDomain``,
  ``DeleteCodedValueFromDomain``, ``CreateDomain``, etc.), que mantienen la
  integridad del repositorio.

Este modulo provee la parte comun: parseo del XML de ``GDB_ITEMS`` y el calculo
de diferencias entre dos diccionarios de dominios.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import xml.etree.ElementTree as ET

from comun.utiles import to_unicode


class Dominio(object):
    """Representacion neutra de un dominio (sirve para coded value y range)."""

    def __init__(self, nombre, tipo, tipo_campo=None):
        self.nombre = nombre
        self.tipo = tipo            # 'CodedValue' | 'Range'
        self.tipo_campo = tipo_campo
        # Para CodedValue: dict {codigo(unicode): descripcion(unicode)}.
        self.valores = {}
        # Para Range: (minimo, maximo).
        self.rango = None

    def __repr__(self):
        return "<Dominio %s (%s) n=%d>" % (
            self.nombre.encode("ascii", "replace"), self.tipo, len(self.valores))


# ---------------------------------------------------------------------------- #
# Lectura desde GDB_ITEMS (Oracle puro)
# ---------------------------------------------------------------------------- #
def leer_dominios_gdb(db, vista="SDE.GDB_ITEMS_VIEW"):
    """Lee los dominios reales de la geodatabase desde ``GDB_ITEMS``.

    :param db:    instancia de comun.oracle_db.ConexionOracle ya conectada.
    :param vista: nombre de la vista/tabla de items (ajustable segun el esquema
                  SDE: ``SDE.GDB_ITEMS_VIEW`` o ``SDE.GDB_ITEMS``).
    :return:      dict {nombre_dominio: Dominio}.

    La consulta filtra por el tipo de item "Domain".  El GUID del tipo de
    dominio en ArcGIS es constante:
    ``{8C368B12-A12E-4C7E-9638-C9C64E69E98F}`` (Coded/Range comparten padre
    "Domain"); por robustez tambien filtramos por Definition que contenga
    ``Domain`` en la raiz del XML.
    """
    sql = (
        "SELECT i.Name AS NOMBRE, i.Definition AS DEFINICION "
        "FROM %s i "
        "WHERE i.Definition IS NOT NULL "
        "  AND (DBMS_LOB.INSTR(i.Definition, 'GPCodedValueDomain2') > 0 "
        "       OR DBMS_LOB.INSTR(i.Definition, 'GPRangeDomain2') > 0)"
    ) % vista

    dominios = {}
    for fila in db.consultar(sql):
        definicion = fila.get("DEFINICION")
        if not definicion:
            continue
        dom = _parsear_xml_dominio(to_unicode(definicion))
        if dom is not None:
            dominios[dom.nombre] = dom
    return dominios


def _texto(nodo):
    return to_unicode(nodo.text) if nodo is not None and nodo.text is not None else None


def _parsear_xml_dominio(xml_texto):
    """Convierte el XML ``Definition`` de un dominio en un objeto :class:`Dominio`."""
    try:
        raiz = ET.fromstring(xml_texto.encode("utf-8"))
    except Exception:
        return None

    # El nombre y tipo pueden venir con o sin namespaces; buscamos por sufijo.
    def buscar(tag):
        for elem in raiz.iter():
            if elem.tag.split("}")[-1] == tag:
                return elem
        return None

    nombre = _texto(buscar("DomainName"))
    if not nombre:
        return None

    tipo_xsi = raiz.get("{http://www.w3.org/2001/XMLSchema-instance}type") or ""
    es_rango = "Range" in tipo_xsi or buscar("MaxValue") is not None
    tipo = "Range" if es_rango else "CodedValue"
    tipo_campo = _texto(buscar("FieldType"))

    dom = Dominio(nombre, tipo, tipo_campo)

    if es_rango:
        minimo = _texto(buscar("MinValue"))
        maximo = _texto(buscar("MaxValue"))
        dom.rango = (minimo, maximo)
    else:
        # CodedValues -> lista de <CodedValue><Name/><Code/></CodedValue>.
        for cv in raiz.iter():
            if cv.tag.split("}")[-1] != "CodedValue":
                continue
            nombre_cv = None
            codigo_cv = None
            for hijo in cv:
                etiqueta = hijo.tag.split("}")[-1]
                if etiqueta == "Name":
                    nombre_cv = _texto(hijo)
                elif etiqueta == "Code":
                    codigo_cv = _texto(hijo)
            if codigo_cv is not None:
                dom.valores[u"%s" % codigo_cv] = nombre_cv or u""
    return dom


# ---------------------------------------------------------------------------- #
# Comparacion de dominios (comun a los dos procesos)
# ---------------------------------------------------------------------------- #
class DiferenciaDominio(object):
    def __init__(self, nombre):
        self.nombre = nombre
        self.estado = None          # 'nuevo' | 'eliminado' | 'modificado'
        self.codigos_agregar = {}   # codigo -> descripcion (existen en origen)
        self.codigos_quitar = {}    # codigo -> descripcion (sobran en destino)
        self.codigos_cambiar = {}   # codigo -> (desc_origen, desc_destino)
        self.rango_origen = None
        self.rango_destino = None

    def hay_cambios(self):
        return bool(self.codigos_agregar or self.codigos_quitar or
                    self.codigos_cambiar or
                    (self.rango_origen != self.rango_destino and
                     self.estado != "sin_cambios"))


def comparar_dominios(dom_origen, dom_destino):
    """Compara dos diccionarios de dominios {nombre: Dominio}.

    :return: lista de :class:`DiferenciaDominio` (solo las que tienen cambios).
    """
    diferencias = []
    nombres = set(dom_origen) | set(dom_destino)

    for nombre in sorted(nombres):
        do = dom_origen.get(nombre)
        dd = dom_destino.get(nombre)
        dif = DiferenciaDominio(nombre)

        if do is not None and dd is None:
            dif.estado = "nuevo"
            dif.codigos_agregar = dict(do.valores)
            dif.rango_origen = do.rango
            diferencias.append(dif)
            continue

        if do is None and dd is not None:
            dif.estado = "eliminado"
            dif.codigos_quitar = dict(dd.valores)
            dif.rango_destino = dd.rango
            diferencias.append(dif)
            continue

        # Existe en ambos: comparar contenido.
        dif.estado = "modificado"
        dif.rango_origen = do.rango
        dif.rango_destino = dd.rango

        codigos_o = set(do.valores)
        codigos_d = set(dd.valores)
        for c in (codigos_o - codigos_d):
            dif.codigos_agregar[c] = do.valores[c]
        for c in (codigos_d - codigos_o):
            dif.codigos_quitar[c] = dd.valores[c]
        for c in (codigos_o & codigos_d):
            if do.valores[c] != dd.valores[c]:
                dif.codigos_cambiar[c] = (do.valores[c], dd.valores[c])

        if dif.hay_cambios():
            diferencias.append(dif)

    return diferencias
