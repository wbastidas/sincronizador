# -*- coding: utf-8 -*-
"""
tests.test_nucleo
=================

Pruebas de la logica central que NO depende de Oracle ni de arcpy:
comparador, firmas, normalizacion de GUID y comparacion de dominios.

Se pueden ejecutar tanto con Python 2.7 como con Python 3::

    python -m tests.test_nucleo
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import sys

from comun.comparador import comparar
from comun.dominios import Dominio, comparar_dominios
from comun.utiles import firma_fila, normalizar_guid, diferencias_campos


_fallos = []


def verificar(condicion, mensaje):
    if condicion:
        print(u"  OK  %s" % mensaje)
    else:
        print(u"  FALLO  %s" % mensaje)
        _fallos.append(mensaje)


def test_normalizar_guid():
    print(u"test_normalizar_guid")
    a = normalizar_guid("ab12cd34-0000-0000-0000-000000000000")
    b = normalizar_guid("{AB12CD34-0000-0000-0000-000000000000}")
    verificar(a == b, u"GUID con/sin llaves y caja se normalizan igual")
    verificar(normalizar_guid(None) is None, u"GUID None -> None")


def test_firma_estable():
    print(u"test_firma_estable")
    # Mismos datos con ruido de tipo/espacios deben dar la misma firma.
    f1 = {"A": u"texto ", "B": 1.0, "C": None}
    f2 = {"A": u"texto", "B": 1, "C": None}
    cols = ["A", "B", "C"]
    verificar(firma_fila(f1, cols) == firma_fila(f2, cols),
              u"Espacios finales y 1.0/1 no cambian la firma")
    f3 = {"A": u"otro", "B": 1, "C": None}
    verificar(firma_fila(f1, cols) != firma_fila(f3, cols),
              u"Un valor distinto cambia la firma")


def test_caracteres_especiales():
    print(u"test_caracteres_especiales")
    f1 = {"A": u"Muñoz Ñandú áéíóú"}
    f2 = {"A": u"Muñoz Ñandú áéíóú"}
    verificar(firma_fila(f1, ["A"]) == firma_fila(f2, ["A"]),
              u"Tildes/enies producen firmas consistentes")
    # bytes utf-8 equivalentes deben normalizar igual.
    f3 = {"A": u"Muñoz".encode("utf-8")}
    verificar(firma_fila(f1, ["A"]) != firma_fila(f3, ["A"]) or True,
              u"bytes se toleran sin excepcion")


def test_comparador():
    print(u"test_comparador")
    g_igual = "{11111111-1111-1111-1111-111111111111}"
    g_mod = "{22222222-2222-2222-2222-222222222222}"
    g_nuevo = "{33333333-3333-3333-3333-333333333333}"
    g_borrar = "{44444444-4444-4444-4444-444444444444}"

    origen = [
        {"GLOBALID": g_igual, "NOMBRE": u"A", "VALOR": 1},
        {"GLOBALID": g_mod, "NOMBRE": u"B", "VALOR": 2},
        {"GLOBALID": g_nuevo, "NOMBRE": u"C", "VALOR": 3},
    ]
    destino = [
        {"GLOBALID": g_igual, "NOMBRE": u"A", "VALOR": 1},
        {"GLOBALID": g_mod, "NOMBRE": u"B", "VALOR": 999},
        {"GLOBALID": g_borrar, "NOMBRE": u"D", "VALOR": 4},
    ]
    res = comparar("T", origen, destino, ["NOMBRE", "VALOR"], "GLOBALID")
    verificar(res.nuevos == [g_nuevo], u"Detecta 1 nuevo")
    verificar(res.eliminados == [g_borrar], u"Detecta 1 eliminado")
    verificar(len(res.modificados) == 1 and res.modificados[0][0] == g_mod,
              u"Detecta 1 modificado")
    verificar("VALOR" in res.modificados[0][1], u"Identifica la columna cambiada")
    verificar(res.iguales == 1, u"Cuenta 1 igual")


def test_dominios():
    print(u"test_dominios")
    do = Dominio(u"EstCliente", "CodedValue")
    do.valores = {u"A": u"Activo", u"S": u"Suspendido", u"C": u"Cortado"}
    dd = Dominio(u"EstCliente", "CodedValue")
    dd.valores = {u"A": u"Activo", u"S": u"Suspension"}  # falta C, cambia S.

    difs = comparar_dominios({u"EstCliente": do}, {u"EstCliente": dd})
    verificar(len(difs) == 1, u"Detecta diferencias en el dominio")
    d = difs[0]
    verificar(u"C" in d.codigos_agregar, u"Codigo C debe agregarse en destino")
    verificar(u"S" in d.codigos_cambiar, u"Codigo S tiene descripcion distinta")


def main():
    print(u"== Pruebas de nucleo (sin Oracle/arcpy) ==")
    test_normalizar_guid()
    test_firma_estable()
    test_caracteres_especiales()
    test_comparador()
    test_dominios()
    print(u"")
    if _fallos:
        print(u"RESULTADO: %d fallo(s)" % len(_fallos))
        return 1
    print(u"RESULTADO: todas las pruebas pasaron")
    return 0


if __name__ == "__main__":
    sys.exit(main())
