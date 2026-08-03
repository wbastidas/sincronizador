# -*- coding: utf-8 -*-
"""
herramientas.generar_config
===========================

Genera ``config/tablas.json`` a partir de los dos archivos del modelo:

* el diccionario de datos enriquecido (tablas, tipo y campos), y
* el archivo de relaciones (relationship classes).

Clasifica automaticamente cada elemento para dejar una configuracion completa,
lista para sincronizar **todos los elementos**:

* ``columna_geometria`` = "SHAPE" si la tabla tiene un campo Geometry, si no null.
* ``red_geometrica``    = true si la tabla tiene columnas propias de la red
  (ENABLED, ELECTRICTRACEWEIGHT, FDRMGRNONTRACEABLE, ANCILLARYROLE,
  PARENTCIRCUITSOURCEGUID, CIRCUITSOURCEGUID, FEEDERID, FEEDERINFO...).
* ``llave_negocio``     = "GLOBALID" si la tabla lo tiene; si no, "OBJECTID"
  (se marca con comentario porque OBJECTID es local y su sincronizacion entre
  bases es menos fiable: normalmente son tablas intermedias M:N gestionadas via
  el bloque de relaciones).

Las relaciones 1:1 y 1:M (por GLOBALID) se emiten como entradas activas. Las
M:N (por OBJECTID o por GUID en tabla intermedia) se emiten en un bloque aparte,
marcadas para revision del nombre de la tabla intermedia.

Uso::

    python herramientas/generar_config.py \\
        --diccionario ruta/al/diccionario.md \\
        --relaciones  ruta/al/relaciones.md \\
        --salida      config/tablas.json
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import argparse
import io
import json
import re
import sys

# Columnas que delatan participacion en la red geometrica.
COLUMNAS_RED = {
    "ENABLED", "ELECTRICTRACEWEIGHT", "FDRMGRNONTRACEABLE", "ANCILLARYROLE",
    "FEEDERID", "FEEDERINFO", "FEEDERID2", "PARENTCIRCUITSOURCEGUID",
    "CIRCUITSOURCEGUID",
}

_RE_TABLA = re.compile(r"^###\s+TABLA:\s+(.+?)\s*$")
_RE_TIPO = re.compile(r"^\*\s+\*\*Tipo:\*\*\s*(.+?)\s*$")
_RE_CAMPO = re.compile(r"^\s*-\s+([A-Za-z0-9_\.]+)\s*\(([^)]+)\)")

_RE_REL = re.compile(r"^###\s+RELACION:\s+(.+?)\s*$")
_RE_ORIGEN = re.compile(r"^\*\s+\*\*Origen:\*\*\s*(.+?)\s*$")
_RE_DESTINO = re.compile(r"^\*\s+\*\*Destino:\*\*\s*(.+?)\s*$")
_RE_CARD = re.compile(r"^\*\s+\*\*Cardinalidad:\*\*\s*(.+?)\s*$")
_RE_LLAVE_O = re.compile(r"^\s*-\s+Llave Origen \(PK\):\s*(.+?)\s*$")
_RE_LLAVE_D = re.compile(r"^\s*-\s+Llave Destino \(FK\):\s*(.+?)\s*$")


def parsear_diccionario(ruta):
    """Devuelve lista de dicts: {nombre, tipo, campos:[(NOMBRE,TIPO)], upper:set}."""
    tablas = []
    actual = None
    with io.open(ruta, mode="r", encoding="utf-8") as fh:
        for linea in fh:
            m = _RE_TABLA.match(linea)
            if m:
                if actual:
                    tablas.append(actual)
                actual = {"nombre": m.group(1), "tipo": None, "campos": []}
                continue
            if actual is None:
                continue
            m = _RE_TIPO.match(linea)
            if m:
                actual["tipo"] = m.group(1)
                continue
            m = _RE_CAMPO.match(linea)
            if m:
                actual["campos"].append((m.group(1).upper(), m.group(2).strip()))
        if actual:
            tablas.append(actual)
    for t in tablas:
        t["upper"] = {c for c, _tipo in t["campos"]}
        t["tipos"] = {c: tp for c, tp in t["campos"]}
    return tablas


def parsear_relaciones(ruta):
    rels = []
    actual = None
    with io.open(ruta, mode="r", encoding="utf-8") as fh:
        for linea in fh:
            m = _RE_REL.match(linea)
            if m:
                if actual:
                    rels.append(actual)
                actual = {"nombre": m.group(1), "origen": None, "destino": None,
                          "card": None, "llaves_o": [], "llaves_d": []}
                continue
            if actual is None:
                continue
            for regex, clave in ((_RE_ORIGEN, "origen"), (_RE_DESTINO, "destino"),
                                 (_RE_CARD, "card")):
                m = regex.match(linea)
                if m:
                    actual[clave] = m.group(1)
                    break
            else:
                m = _RE_LLAVE_O.match(linea)
                if m:
                    actual["llaves_o"].append(m.group(1))
                    continue
                m = _RE_LLAVE_D.match(linea)
                if m:
                    actual["llaves_d"].append(m.group(1))
        if actual:
            rels.append(actual)
    return rels


def construir_tabla(t):
    """Convierte una tabla parseada en su entrada de configuracion."""
    upper = t["upper"]
    tiene_geom = any(tp == "Geometry" for _c, tp in t["campos"])
    tiene_globalid = "GLOBALID" in upper
    es_red = bool(upper & COLUMNAS_RED)

    entrada = {
        "nombre": t["nombre"],
        "llave_negocio": "GLOBALID" if tiene_globalid else "OBJECTID",
        "columna_geometria": "SHAPE" if tiene_geom else None,
        "red_geometrica": es_red,
        "politica_borrado": "solo_migradas",
    }
    if not tiene_globalid:
        entrada["_comentario"] = (
            "Sin GLOBALID: se usa OBJECTID (local). Verifique una llave de "
            "negocio estable o sincronicela via el bloque de relaciones.")
    return entrada


def construir_relaciones(rels):
    """Separa relaciones 1:1/1:M (guid) de las M:N (revision)."""
    activas = []
    revisar = []
    for r in rels:
        card = (r["card"] or "").lower()
        es_mn = "manytomany" in card or bool(r["llaves_d"])

        if not es_mn:
            # 1:1 o 1:M -> la FK vive en el destino; es la 2a llave de origen.
            fk = r["llaves_o"][1] if len(r["llaves_o"]) >= 2 else None
            pk = r["llaves_o"][0] if r["llaves_o"] else "GLOBALID"
            tipo = "oid" if (pk.upper() == "OBJECTID" or
                             (fk or "").upper().endswith("OID")) else "guid"
            if fk:
                activas.append({
                    "nombre": r["nombre"],
                    "tabla_origen": r["origen"],
                    "tabla_destino": r["destino"],
                    "tipo_llave": tipo,
                    "columna_fk": fk,
                })
            continue

        # M:N -> tabla intermedia con dos FKs. Se emiten dos entradas (una por
        # extremo) y se marca para confirmar el nombre de la tabla intermedia.
        pk_o = r["llaves_o"][0] if r["llaves_o"] else "OBJECTID"
        fk_o = r["llaves_o"][1] if len(r["llaves_o"]) >= 2 else None
        pk_d = r["llaves_d"][0] if r["llaves_d"] else "OBJECTID"
        fk_d = r["llaves_d"][1] if len(r["llaves_d"]) >= 2 else None
        tipo = "guid" if pk_o.upper() in ("GLOBALID", "GUID") else "oid"
        base = {
            "_comentario": ("M:N: 'tabla_destino' asume la convencion de ArcGIS "
                            "(tabla intermedia = nombre de la relationship class). "
                            "Ajuste el nombre si su esquema difiere."),
            "tabla_destino": r["nombre"],
            "tipo_llave": tipo,
        }
        if fk_o:
            e = dict(base)
            e["nombre"] = r["nombre"] + "_extremoOrigen"
            e["tabla_origen"] = r["origen"]
            e["columna_fk"] = fk_o
            revisar.append(e)
        if fk_d:
            e = dict(base)
            e["nombre"] = r["nombre"] + "_extremoDestino"
            e["tabla_origen"] = r["destino"]
            e["columna_fk"] = fk_d
            revisar.append(e)
    return activas, revisar


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera config/tablas.json completo.")
    parser.add_argument("--diccionario", required=True)
    parser.add_argument("--relaciones", required=True)
    parser.add_argument("--salida", default="config/tablas.json")
    args = parser.parse_args(argv)

    tablas = parsear_diccionario(args.diccionario)
    rels = parsear_relaciones(args.relaciones)

    entradas_tabla = [construir_tabla(t) for t in tablas]
    activas, mn = construir_relaciones(rels)
    # Todas las relaciones quedan activas: las M:N asumen tabla intermedia =
    # nombre de la relationship class (convencion de ArcGIS), marcada por su
    # _comentario para ajuste si el esquema difiere.
    relaciones = activas + mn

    config = {
        "_comentario": (
            "Generado por herramientas/generar_config.py a partir del modelo. "
            "Incluye TODOS los elementos. Las relaciones M:N (con _comentario) "
            "asumen tabla intermedia = nombre de la relacion; ajuste si difiere. "
            "Revise tambien las tablas sin GLOBALID antes de la 1a corrida."),
        "opciones": {
            "incluir_red_geometrica": False,
            "sincronizar_dominios": True,
            "tamano_lote": 500,
            "modo_simulacion": False,
        },
        "tablas": entradas_tabla,
        "relaciones": relaciones,
    }

    with io.open(args.salida, mode="w", encoding="utf-8") as fh:
        fh.write(json.dumps(config, ensure_ascii=False, indent=2,
                            sort_keys=False))
        fh.write(u"\n")

    n_geom = sum(1 for e in entradas_tabla if e["columna_geometria"])
    n_red = sum(1 for e in entradas_tabla if e["red_geometrica"])
    n_sin_gid = sum(1 for e in entradas_tabla if e["llave_negocio"] == "OBJECTID")
    print(u"Tablas: %d (con geometria=%d, red=%d, sin GLOBALID=%d)" % (
        len(entradas_tabla), n_geom, n_red, n_sin_gid))
    print(u"Relaciones 1:1 / 1:M (guid/oid): %d" % len(activas))
    print(u"Relaciones M:N activadas: %d" % len(mn))
    print(u"Relaciones totales: %d" % len(relaciones))
    print(u"Salida: %s" % args.salida)
    return 0


if __name__ == "__main__":
    sys.exit(main())
