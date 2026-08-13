# -*- coding: utf-8 -*-
"""
tests.dobles
============

Dobles de prueba (test doubles) que emulan las interfaces de **cx_Oracle** y de
**arcpy** para poder ejecutar el CODIGO REAL de orquestacion de los dos procesos
sin tener Oracle ni ArcGIS instalados.

* :class:`Almacen`    - tablas en memoria (compartidas entre "Oracle" y "arcpy"
                        del mismo extremo, como ocurre en la realidad: es la
                        misma base).
* :class:`FakeOracle` - implementa el subconjunto de ConexionOracle que usan los
                        procesos (columnas_tabla, consultar, ejecutar,
                        ejecutar_muchos, siguiente_objectid, contexto).
* :class:`FakeArcpy`  - implementa arcpy.env, arcpy.Exists, arcpy.ListDatasets y
                        arcpy.da (SearchCursor/InsertCursor/UpdateCursor/Editor).

No pretenden ser un motor SQL completo: reconocen exactamente las sentencias que
generan los procesos (INSERT/UPDATE/DELETE/SELECT con formato conocido).
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os
import re
import uuid


# ---------------------------------------------------------------------------- #
# Almacen en memoria
# ---------------------------------------------------------------------------- #
class TablaMem(object):
    def __init__(self, columnas):
        self.columnas = [c.upper() for c in columnas]
        self.filas = []           # lista de dict (claves en MAYUSCULA)
        self._siguiente_oid = 1

    def nuevo_oid(self):
        v = self._siguiente_oid
        self._siguiente_oid += 1
        return v

    def max_oid(self):
        if not self.filas:
            return 0
        return max(f.get("OBJECTID", 0) or 0 for f in self.filas)


class Almacen(object):
    """Conjunto de tablas en memoria de un extremo (origen o destino)."""

    def __init__(self):
        self.tablas = {}

    def crear(self, nombre, columnas):
        t = TablaMem(columnas)
        self.tablas[nombre.upper()] = t
        return t

    def tabla(self, nombre):
        return self.tablas[nombre.upper()]


def nuevo_guid():
    return "{%s}" % uuid.uuid4().hex.upper()


# ---------------------------------------------------------------------------- #
# Doble de cx_Oracle
# ---------------------------------------------------------------------------- #
_RE_INSERT = re.compile(r"INSERT INTO (\w+) \((.*?)\) VALUES \((.*?)\)", re.I | re.S)
_RE_UPDATE = re.compile(r"UPDATE (\w+) SET (.*?) WHERE (.*)", re.I | re.S)
_RE_DELETE = re.compile(r"DELETE FROM (\w+) WHERE (\w+) IN \((.*?)\)", re.I | re.S)
_RE_FROM = re.compile(r"FROM (\w+)", re.I)
_RE_ASIGNA = re.compile(r"(\w+)\s*=\s*:(\w+)")


class FakeOracle(object):
    """Emula comun.oracle_db.ConexionOracle sobre un :class:`Almacen`."""

    def __init__(self, almacen):
        self.almacen = almacen
        self._commits = 0

    # --- ciclo de vida ---
    def conectar(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def cerrar(self):
        pass

    def commit(self):
        self._commits += 1

    def rollback(self):
        pass

    # --- metadatos ---
    def columnas_tabla(self, tabla, esquema=None):
        t = self.almacen.tablas.get(tabla.upper())
        if t is None:
            return []
        return [{"NOMBRE": c, "TIPO": "VARCHAR2", "LONGITUD": 100,
                 "ESCALA": None, "NULABLE": True} for c in t.columnas]

    def siguiente_objectid(self, tabla, columna="OBJECTID"):
        return self.almacen.tabla(tabla).max_oid() + 1

    def valor_secuencia(self, secuencia):
        return 1

    # --- consultas ---
    def consultar(self, sql, binds=None, tamano_arraysize=1000):
        m = _RE_FROM.search(sql)
        if not m:
            return []
        nombre = m.group(1).upper()
        t = self.almacen.tablas.get(nombre)
        if t is None:
            return []
        # Devolver copias para que el codigo no mute el almacen por accidente.
        return [dict(f) for f in t.filas]

    def iterar(self, sql, binds=None, tamano_arraysize=2000):
        for f in self.consultar(sql, binds):
            yield f

    # --- DML ---
    def ejecutar(self, sql, binds=None):
        return self._aplicar(sql, [binds or {}])

    def ejecutar_muchos(self, sql, lista_binds):
        return self._aplicar(sql, list(lista_binds))

    def _aplicar(self, sql, lista_binds):
        m = _RE_INSERT.search(sql)
        if m:
            return self._insert(m, lista_binds)
        m = _RE_UPDATE.search(sql)
        if m:
            return self._update(m, lista_binds)
        m = _RE_DELETE.search(sql)
        if m:
            return self._delete(m, lista_binds)
        raise AssertionError(u"FakeOracle: sentencia no reconocida: %s" % sql)

    def _insert(self, m, lista_binds):
        nombre = m.group(1).upper()
        cols = [c.strip().upper() for c in m.group(2).split(",")]
        t = self.almacen.tabla(nombre)
        n = 0
        for binds in lista_binds:
            fila = {}
            for c in cols:
                fila[c] = binds.get(c)
            # Completar columnas faltantes de la tabla con None.
            for c in t.columnas:
                fila.setdefault(c, None)
            t.filas.append(fila)
            n += 1
        return n

    def _update(self, m, lista_binds):
        nombre = m.group(1).upper()
        t = self.almacen.tabla(nombre)
        asign = _RE_ASIGNA.findall(m.group(2))            # [(col, bind), ...]
        cond = _RE_ASIGNA.findall(m.group(3))             # WHERE col = :bind
        col_cond, bind_cond = cond[0]
        col_cond = col_cond.upper()
        n = 0
        for binds in lista_binds:
            objetivo = binds.get(bind_cond)
            for fila in t.filas:
                if fila.get(col_cond) == objetivo:
                    for col, bind in asign:
                        fila[col.upper()] = binds.get(bind)
                    n += 1
        return n

    def _delete(self, m, lista_binds):
        nombre = m.group(1).upper()
        col = m.group(2).upper()
        binds_names = re.findall(r":(\w+)", m.group(3))
        t = self.almacen.tabla(nombre)
        n = 0
        for binds in lista_binds:
            valores = set(binds.get(b) for b in binds_names)
            antes = len(t.filas)
            t.filas = [f for f in t.filas if f.get(col) not in valores]
            n += antes - len(t.filas)
        return n


# ---------------------------------------------------------------------------- #
# Doble de arcpy
# ---------------------------------------------------------------------------- #
class _Env(object):
    def __init__(self):
        self.workspace = None
        self.overwriteOutput = True
        self.maintainAttachments = True


class _SearchCursor(object):
    def __init__(self, tabla, campos):
        self._tabla = tabla
        self._campos = campos

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        for fila in list(self._tabla.filas):
            yield tuple(self._valor(fila, c) for c in self._campos)

    @staticmethod
    def _valor(fila, campo):
        if campo in ("OID@", "OID@OBJECTID"):
            return fila.get("OBJECTID")
        if campo == "SHAPE@WKB":
            return fila.get("SHAPE@WKB")
        return fila.get(campo.upper())


class _InsertCursor(object):
    def __init__(self, tabla, campos):
        self._tabla = tabla
        self._campos = campos

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def insertRow(self, valores):
        fila = {}
        for campo, valor in zip(self._campos, valores):
            if campo == "SHAPE@WKB":
                fila["SHAPE@WKB"] = valor
            else:
                fila[campo.upper()] = valor
        oid = self._tabla.nuevo_oid()
        fila["OBJECTID"] = oid
        # arcpy asigna un GLOBALID NUEVO si no se provee (no se puede escribir).
        if not fila.get("GLOBALID"):
            fila["GLOBALID"] = nuevo_guid()
        for c in self._tabla.columnas:
            fila.setdefault(c, None)
        self._tabla.filas.append(fila)
        return oid


class _UpdateCursor(object):
    def __init__(self, tabla, campos):
        self._tabla = tabla
        self._campos = campos
        self._i = -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def __iter__(self):
        self._i = -1
        for idx in range(len(self._tabla.filas)):
            self._i = idx
            fila = self._tabla.filas[idx]
            yield [_SearchCursor._valor(fila, c) for c in self._campos]

    def updateRow(self, valores):
        fila = self._tabla.filas[self._i]
        for campo, valor in zip(self._campos, valores):
            if campo == "SHAPE@WKB":
                fila["SHAPE@WKB"] = valor
            elif campo in ("OID@",):
                fila["OBJECTID"] = valor
            else:
                fila[campo.upper()] = valor

    def deleteRow(self):
        self._tabla.filas[self._i] = None  # marca; se limpia al final del for

    def _limpiar(self):
        self._tabla.filas = [f for f in self._tabla.filas if f is not None]


class _Editor(object):
    def __init__(self, workspace):
        self.workspace = workspace

    def startEditing(self, con_deshacer, multiusuario):
        pass

    def startOperation(self):
        pass

    def stopOperation(self):
        pass

    def stopEditing(self, guardar):
        pass

    def abortOperation(self):
        pass


class _DA(object):
    def __init__(self, arcpy):
        self._arcpy = arcpy

    def SearchCursor(self, ruta, campos):
        return _SearchCursor(self._arcpy._tabla_de(ruta), campos)

    def InsertCursor(self, ruta, campos):
        return _InsertCursor(self._arcpy._tabla_de(ruta), campos)

    def UpdateCursor(self, ruta, campos):
        cur = _UpdateCursor(self._arcpy._tabla_de(ruta), campos)
        # Envolver el iterador para limpiar filas borradas al terminar.
        return _UpdateCursorAutolimpia(cur)

    def Editor(self, workspace):
        return _Editor(workspace)


class _UpdateCursorAutolimpia(object):
    """Envuelve _UpdateCursor para compactar filas borradas al salir del with."""

    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self._cur

    def __exit__(self, *a):
        self._cur._limpiar()
        return False


class FakeArcpy(object):
    """Emula el modulo arcpy sobre uno o varios :class:`Almacen`.

    Puede recibir un unico almacen (con nombre de workspace) o un diccionario
    ``{nombre_workspace: Almacen}`` para modelar origen y destino por separado
    (necesario para leer geometria del origen y escribir en el destino).
    """

    def __init__(self, almacen=None, workspace="MEM", workspaces=None):
        if workspaces is not None:
            self._workspaces = {k.upper(): v for k, v in workspaces.items()}
        else:
            self._workspaces = {workspace.upper(): almacen}
        self.env = _Env()
        self.da = _DA(self)

    # arcpy.Exists / ListDatasets / Describe / verificaciones
    def Exists(self, ruta):
        ws = os.path.basename(os.path.dirname(ruta)).upper()
        base = os.path.basename(ruta).upper()
        alm = self._workspaces.get(ws)
        if alm is not None:
            return base in alm.tablas
        # Buscar en cualquier workspace.
        return any(base in a.tablas for a in self._workspaces.values())

    def ListDatasets(self, comodin="*", tipo=None):
        return []  # sin feature datasets ni redes en el doble

    def Describe(self, ruta):
        class _D(object):
            featureClassNames = []
        return _D()

    def VerifyAndRepairGeometricNetworkConnectivity_management(self, *a, **k):
        pass

    def RebuildGeometricNetwork_management(self, *a, **k):
        pass

    def CreateDomain_management(self, *a, **k):
        pass

    def AddCodedValueToDomain_management(self, *a, **k):
        pass

    def DeleteCodedValueFromDomain_management(self, *a, **k):
        pass

    def SetValueForRangeDomain_management(self, *a, **k):
        pass

    def _tabla_de(self, ruta):
        ws = os.path.basename(os.path.dirname(ruta)).upper()
        base = os.path.basename(ruta)
        alm = self._workspaces.get(ws)
        if alm is not None and base.upper() in alm.tablas:
            return alm.tabla(base)
        # Buscar en cualquier workspace por nombre de tabla.
        for a in self._workspaces.values():
            if base.upper() in a.tablas:
                return a.tabla(base)
        raise KeyError(u"Tabla no encontrada en ningun workspace: %s" % ruta)
