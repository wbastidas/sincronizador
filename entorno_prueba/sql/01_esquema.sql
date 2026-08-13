-- ============================================================================
-- 01_esquema.sql
-- Crea un subconjunto representativo del modelo SIGELEC para pruebas.
-- Ejecutar CONECTADO como el usuario ORIGEN y, por separado, como el DESTINO.
--
--   sqlplus origen/clave@ORIGEN  @01_esquema.sql
--   sqlplus destino/clave@DESTINO @01_esquema.sql
--
-- Nota: son tablas Oracle "planas" (sin registrar en SDE) suficientes para
-- probar el PROCESO 1 (Oracle directo) sobre atributos y relaciones. Para
-- probar geometria (ST_GEOMETRY) y la red geometrica se necesitan feature
-- classes registradas en la geodatabase: use 03_bootstrap_arcpy.py.
-- ============================================================================

-- Limpieza (ignore errores si no existen).
BEGIN EXECUTE IMMEDIATE 'DROP TABLE CONEXIONCONSUMIDOR'; EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'DROP TABLE PUNTOCARGA'; EXCEPTION WHEN OTHERS THEN NULL; END;
/
BEGIN EXECUTE IMMEDIATE 'DROP TABLE ESTRUCTURAANIVEL'; EXCEPTION WHEN OTHERS THEN NULL; END;
/

-- Padre: ESTRUCTURAANIVEL --------------------------------------------------
CREATE TABLE ESTRUCTURAANIVEL (
  OBJECTID                    NUMBER(38)      NOT NULL,
  GLOBALID                    VARCHAR2(38)    NOT NULL,
  NOMBRE                      NVARCHAR2(255),
  CODIGOEMPRESA               VARCHAR2(10),
  FECHAMODIFICACIONREGISTRO   DATE,
  USUARIOMODIFICACIONREGISTRO VARCHAR2(50),
  MIOID                       NUMBER(38),
  MIGUID                      VARCHAR2(38),
  CONSTRAINT PK_ESTRUCTURAANIVEL PRIMARY KEY (OBJECTID)
);
CREATE UNIQUE INDEX UX_ESTRUCTURAANIVEL_GID ON ESTRUCTURAANIVEL (GLOBALID);

-- Hijo de estructura / padre de conexiones: PUNTOCARGA ---------------------
CREATE TABLE PUNTOCARGA (
  OBJECTID                    NUMBER(38)      NOT NULL,
  GLOBALID                    VARCHAR2(38)    NOT NULL,
  ESTRUCTURANIVELGLOBALID     VARCHAR2(38),   -- FK -> ESTRUCTURAANIVEL.GLOBALID
  NOMBRE                      NVARCHAR2(255),
  CARGA                       NUMBER(18,4),
  FECHAMODIFICACIONREGISTRO   DATE,
  MIOID                       NUMBER(38),
  MIGUID                      VARCHAR2(38),
  CONSTRAINT PK_PUNTOCARGA PRIMARY KEY (OBJECTID)
);
CREATE UNIQUE INDEX UX_PUNTOCARGA_GID ON PUNTOCARGA (GLOBALID);
CREATE INDEX IX_PUNTOCARGA_FK ON PUNTOCARGA (ESTRUCTURANIVELGLOBALID);

-- Hijo: CONEXIONCONSUMIDOR --------------------------------------------------
CREATE TABLE CONEXIONCONSUMIDOR (
  OBJECTID                    NUMBER(38)      NOT NULL,
  GLOBALID                    VARCHAR2(38)    NOT NULL,
  PUNTOCARGAGLOBALID          VARCHAR2(38),   -- FK -> PUNTOCARGA.GLOBALID
  CODIGOCLIENTE               VARCHAR2(30),
  NOMBRECLIENTE               NVARCHAR2(255), -- admite tildes/enies/comas
  FECHAMODIFICACIONREGISTRO   DATE,
  MIOID                       NUMBER(38),
  MIGUID                      VARCHAR2(38),
  CONSTRAINT PK_CONEXIONCONSUMIDOR PRIMARY KEY (OBJECTID)
);
CREATE UNIQUE INDEX UX_CONEXIONCONSUMIDOR_GID ON CONEXIONCONSUMIDOR (GLOBALID);
CREATE INDEX IX_CONEXIONCONSUMIDOR_FK ON CONEXIONCONSUMIDOR (PUNTOCARGAGLOBALID);

COMMIT;
-- Fin del esquema.
