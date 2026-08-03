# -*- coding: utf-8 -*-
"""
comun.oracle_db
===============

Envoltura ligera sobre **cx_Oracle** con foco en tres cosas:

1. **Codificacion correcta** de caracteres especiales (tildes, enies, simbolos)
   fijando ``NLS_LANG`` y el ``encoding``/``nencoding`` de la conexion a UTF-8.
2. **Consultas parametrizadas** (bind variables) en todo momento: nunca se
   interpola texto en el SQL, lo que evita inyeccion y los problemas de comillas
   con datos que contienen ``'`` o caracteres raros.
3. Utilidades de **metadatos** (columnas, tipos, PK) y lectura de definiciones
   de **dominios** desde el repositorio de la geodatabase (``GDB_ITEMS``).

Compatible con Oracle 11g y Python 2.7.  `cx_Oracle` solo se importa al
instanciar la clase, de modo que los tests y el proceso 2 (que puede no tener
cx_Oracle) importen el modulo sin fallar.
"""

from __future__ import absolute_import, division, print_function, unicode_literals

import os

from comun.utiles import to_unicode


class ErrorOracle(Exception):
    """Cualquier error originado en la capa de acceso a Oracle."""


class ConexionOracle(object):
    """Gestiona una conexion cx_Oracle y expone helpers de alto nivel.

    Uso recomendado como contexto::

        with ConexionOracle(cfg.origen.oracle) as db:
            filas = db.consultar("SELECT * FROM barra")
    """

    def __init__(self, parametros, autocommit=False):
        """:param parametros: dict con dsn/user/password/nls_lang/encoding."""
        self._param = parametros
        self._autocommit = autocommit
        self._con = None
        self._cx = None

    # ------------------------------------------------------------------ #
    # Ciclo de vida de la conexion
    # ------------------------------------------------------------------ #
    def conectar(self):
        if self._con is not None:
            return self._con
        try:
            import cx_Oracle  # import diferido: solo se necesita aqui.
        except ImportError as exc:  # pragma: no cover
            raise ErrorOracle(
                u"cx_Oracle no esta instalado. Instale una version compatible "
                u"con su cliente Oracle y Python 2.7 (p. ej. cx_Oracle 5.3 / 6.x)."
            )
        self._cx = cx_Oracle

        # NLS_LANG debe fijarse ANTES de abrir la conexion para que el cliente
        # Oracle convierta correctamente los caracteres especiales.
        nls = self._param.get("nls_lang")
        if nls:
            os.environ["NLS_LANG"] = str(nls)

        try:
            self._con = cx_Oracle.connect(
                to_unicode(self._param["user"]),
                to_unicode(self._param["password"]),
                to_unicode(self._param["dsn"]),
                encoding=str(self._param.get("encoding", "UTF-8")),
                nencoding=str(self._param.get("encoding", "UTF-8")),
            )
        except Exception as exc:
            raise ErrorOracle(u"No se pudo conectar a Oracle: %s" % to_unicode(exc))

        self._con.autocommit = self._autocommit
        return self._con

    def __enter__(self):
        self.conectar()
        return self

    def __exit__(self, tipo, valor, traza):
        if tipo is None:
            self.commit()
        else:
            self.rollback()
        self.cerrar()

    def cerrar(self):
        if self._con is not None:
            try:
                self._con.close()
            finally:
                self._con = None

    def commit(self):
        if self._con is not None:
            self._con.commit()

    def rollback(self):
        if self._con is not None:
            self._con.rollback()

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def _cursor(self):
        if self._con is None:
            self.conectar()
        return self._con.cursor()

    def consultar(self, sql, binds=None, tamano_arraysize=1000):
        """Ejecuta un SELECT y devuelve una lista de ``dict`` (columna->valor).

        Las claves de cada dict son los nombres de columna en MAYUSCULA.
        Los valores de texto se devuelven ya como ``unicode``.
        """
        cur = self._cursor()
        cur.arraysize = tamano_arraysize
        try:
            cur.execute(sql, binds or {})
            columnas = [d[0].upper() for d in cur.description]
            filas = []
            for registro in cur:
                fila = {}
                for i, col in enumerate(columnas):
                    fila[col] = self._normalizar_lectura(registro[i])
                filas.append(fila)
            return filas
        except Exception as exc:
            raise ErrorOracle(u"Error en consulta: %s\nSQL: %s" % (to_unicode(exc), sql))
        finally:
            cur.close()

    def iterar(self, sql, binds=None, tamano_arraysize=2000):
        """Igual que :meth:`consultar` pero como generador (no carga todo en RAM).

        Recomendado para tablas muy grandes.
        """
        cur = self._cursor()
        cur.arraysize = tamano_arraysize
        try:
            cur.execute(sql, binds or {})
            columnas = [d[0].upper() for d in cur.description]
            for registro in cur:
                fila = {}
                for i, col in enumerate(columnas):
                    fila[col] = self._normalizar_lectura(registro[i])
                yield fila
        except Exception as exc:
            raise ErrorOracle(u"Error iterando: %s\nSQL: %s" % (to_unicode(exc), sql))
        finally:
            cur.close()

    def ejecutar(self, sql, binds=None):
        """Ejecuta un DML puntual (INSERT/UPDATE/DELETE). Devuelve filas afectadas."""
        cur = self._cursor()
        try:
            cur.execute(sql, binds or {})
            return cur.rowcount
        except Exception as exc:
            raise ErrorOracle(u"Error en DML: %s\nSQL: %s" % (to_unicode(exc), sql))
        finally:
            cur.close()

    def ejecutar_muchos(self, sql, lista_binds):
        """Ejecuta DML en lote con ``executemany`` (mucho mas rapido).

        :param lista_binds: lista de dicts con los binds de cada fila.
        """
        if not lista_binds:
            return 0
        cur = self._cursor()
        try:
            # batcherrors=False -> si una fila falla, aborta el lote y hace
            # rollback en la transaccion global (integridad todo-o-nada).
            cur.executemany(sql, lista_binds)
            return cur.rowcount
        except Exception as exc:
            raise ErrorOracle(
                u"Error en DML por lote: %s\nSQL: %s" % (to_unicode(exc), sql))
        finally:
            cur.close()

    def _normalizar_lectura(self, valor):
        """Convierte LOBs a texto y bytes a unicode al leer."""
        if valor is None:
            return None
        # cx_Oracle LOB -> leer contenido completo.
        if self._cx is not None and isinstance(valor, self._cx.LOB):
            valor = valor.read()
        if isinstance(valor, bytes):
            return to_unicode(valor)
        return valor

    # ------------------------------------------------------------------ #
    # Metadatos
    # ------------------------------------------------------------------ #
    def columnas_tabla(self, tabla, esquema=None):
        """Devuelve lista de dicts con metadatos de columnas de la tabla.

        Cada dict: {NOMBRE, TIPO, LONGITUD, ESCALA, NULABLE}.
        """
        binds = {"t": tabla.upper()}
        filtro_esquema = ""
        if esquema:
            binds["o"] = esquema.upper()
            filtro_esquema = " AND owner = :o"
        sql = (
            "SELECT column_name, data_type, data_length, data_scale, nullable "
            "FROM all_tab_columns "
            "WHERE table_name = :t" + filtro_esquema +
            " ORDER BY column_id"
        )
        cols = []
        for f in self.consultar(sql, binds):
            cols.append({
                "NOMBRE": f["COLUMN_NAME"].upper(),
                "TIPO": f["DATA_TYPE"],
                "LONGITUD": f["DATA_LENGTH"],
                "ESCALA": f["DATA_SCALE"],
                "NULABLE": f["NULLABLE"] == "Y",
            })
        return cols

    def existe_tabla(self, tabla, esquema=None):
        binds = {"t": tabla.upper()}
        filtro = ""
        if esquema:
            binds["o"] = esquema.upper()
            filtro = " AND owner = :o"
        sql = "SELECT COUNT(*) AS N FROM all_tables WHERE table_name = :t" + filtro
        return self.consultar(sql, binds)[0]["N"] > 0

    def siguiente_objectid(self, tabla, columna="OBJECTID"):
        """Calcula ``MAX(OBJECTID)+1`` (estrategia simple para el proceso 1).

        Nota: para tablas SDE versionadas o de red geometrica lo correcto es
        usar el proceso 2 (arcpy) o una secuencia registrada.  Aqui se ofrece
        como estrategia por defecto para datos no versionados.
        """
        sql = "SELECT NVL(MAX(%s),0)+1 AS N FROM %s" % (columna, tabla)
        return int(self.consultar(sql)[0]["N"])

    def valor_secuencia(self, secuencia):
        """Obtiene ``secuencia.NEXTVAL``."""
        sql = "SELECT %s.NEXTVAL AS N FROM DUAL" % secuencia
        return int(self.consultar(sql)[0]["N"])
