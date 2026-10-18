#!/usr/bin/env python3
"""
Tarea 1 — PostGIS de punta a punta (Santiago centro)
Script único de carga y ETL reproducible (Parte A y Parte B).

Uso en máquina limpia:
    docker compose up -d
    python3 cargar.py
"""
import csv
import glob
import os
import subprocess
import sys
import time
import zipfile

AQUI = os.path.dirname(os.path.abspath(__file__))
DATOS = os.path.join(AQUI, "datos")
FUENTE = os.path.join(AQUI, "datos_fuente")
GTFS_DIR = os.path.join(DATOS, "gtfs")

ZONA = "santiago-centro"
BBOX = (-70.75, -33.52, -70.52, -33.36)  # xmin, ymin, xmax, ymax (WGS 84)
PG_CONN = "PG:host=localhost dbname=postgres user=postgres password=pmd2026"


def banner(titulo):
    print(f"\n{'=' * 72}\n▶ {titulo}\n{'=' * 72}")


def compose(*args, mostrar=False, check=True):
    """Ejecuta `docker compose <args>` desde la raíz del repositorio."""
    r = subprocess.run(
        ["docker", "compose", *args],
        cwd=AQUI,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and r.returncode != 0:
        sys.stderr.write(f"\n⛔ Error en docker compose {' '.join(args)}:\n{r.stderr}\n")
        sys.exit(1)
    if mostrar and r.stdout.strip():
        print(r.stdout.strip())
    return r.stdout


def psql(sql, mostrar=True):
    """Ejecuta un bloque SQL dentro del contenedor PostGIS."""
    args = [
        "exec", "-T", "db",
        "psql", "-q", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "postgres",
        "-c", sql,
    ]
    return compose(*args, mostrar=mostrar)


def psql_archivo(ruta_contenedor):
    """Ejecuta un archivo .sql montado dentro del contenedor."""
    args = [
        "exec", "-T", "db",
        "psql", "-q", "-v", "ON_ERROR_STOP=1",
        "-U", "postgres", "-d", "postgres",
        "-f", ruta_contenedor,
    ]
    return compose(*args, mostrar=False)


def asegurar_contenedor():
    """Verifica que el contenedor esté arriba y que PostgreSQL responda."""
    banner("0. Verificando contenedor PostGIS y preparando entorno")
    compose("up", "-d", "--wait")
    for i in range(30):
        r = subprocess.run(
            ["docker", "compose", "exec", "-T", "db", "pg_isready", "-U", "postgres", "-d", "postgres"],
            cwd=AQUI,
            capture_output=True,
            text=True,
        )
        if r.returncode == 0:
            print(f"✅ Contenedor PostGIS listo y aceptando conexiones (intento {i + 1}).")
            return
        time.sleep(1)
    sys.stderr.write("⛔ PostgreSQL no respondió a tiempo.\n")
    sys.exit(1)


def asegurar_insumos():
    """Verifica el GeoPackage de OSM, comunas_cl.sql y el ZIP del GTFS."""
    gpkg_local = os.path.join(DATOS, f"osm_{ZONA}.gpkg")
    if not os.path.exists(gpkg_local):
        print(f"⚙️  No existe osm_{ZONA}.gpkg en datos/. Ejecutando preparar_datos.py...")
        r = subprocess.run([sys.executable, "preparar_datos.py", "--zona", ZONA], cwd=AQUI)
        if r.returncode != 0 or not os.path.exists(gpkg_local):
            sys.stderr.write("⛔ Falló la generación del GeoPackage de OSM.\n")
            sys.exit(1)
    else:
        mb = os.path.getsize(gpkg_local) / 1e6
        print(f"✅ GeoPackage OSM encontrado: datos/osm_{ZONA}.gpkg ({mb:.1f} MB)")

    # Buscar comunas_cl.sql en datos/ o datos_fuente/
    if os.path.exists(os.path.join(DATOS, "comunas_cl.sql")):
        ruta_comunas = "/datos/comunas_cl.sql"
    elif os.path.exists(os.path.join(FUENTE, "comunas_cl.sql")):
        ruta_comunas = "/datos_fuente/comunas_cl.sql"
    else:
        sys.stderr.write("⛔ No se encontró comunas_cl.sql ni en datos/ ni en datos_fuente/.\n")
        sys.exit(1)
    print(f"✅ Archivo DPA encontrado: {ruta_comunas}")

    # Buscar GTFS (*.zip) en datos_fuente/ o datos/
    zips = sorted(glob.glob(os.path.join(FUENTE, "*GTFS*.zip")) + glob.glob(os.path.join(DATOS, "*GTFS*.zip")) +
                  glob.glob(os.path.join(FUENTE, "*.zip")) + glob.glob(os.path.join(DATOS, "*.zip")))
    archivos_gtfs = ("stops.txt", "routes.txt", "trips.txt", "shapes.txt", "stop_times.txt")

    os.makedirs(GTFS_DIR, exist_ok=True)
    if zips:
        gtfs_zip = zips[0]
        print(f"✅ Archivo GTFS encontrado: {os.path.basename(gtfs_zip)} ({os.path.getsize(gtfs_zip) / 1e6:.1f} MB)")
        with zipfile.ZipFile(gtfs_zip, "r") as zf:
            nombres_zip = {os.path.basename(n): n for n in zf.namelist()}
            for req in archivos_gtfs:
                dest = os.path.join(GTFS_DIR, req)
                if not os.path.exists(dest):
                    if req not in nombres_zip:
                        sys.stderr.write(f"⛔ El ZIP del GTFS no contiene {req}.\n")
                        sys.exit(1)
                    print(f"   Extrayendo {req} -> datos/gtfs/{req}...")
                    with zf.open(nombres_zip[req]) as src, open(dest, "wb") as out:
                        out.write(src.read())
    else:
        # Verificar si ya estaban descomprimidos en datos/gtfs/ o datos/
        for req in archivos_gtfs:
            if not os.path.exists(os.path.join(GTFS_DIR, req)):
                sys.stderr.write(f"⛔ No se encontró el ZIP de GTFS ni {req} en datos/gtfs/.\n")
                sys.exit(1)
        print("✅ Archivos TXT del GTFS encontrados en datos/gtfs/.")

    return ruta_comunas


def cargar_dpa(ruta_comunas):
    """Paso 1: Extensiones, carga de comunas_cl y creación de comunas_sub con ST_Subdivide."""
    banner("1. Cargando División Política Administrativa (DPA) y ST_Subdivide")
    t0 = time.perf_counter()
    psql("""
        CREATE EXTENSION IF NOT EXISTS postgis;
        CREATE EXTENSION IF NOT EXISTS hstore;
        DROP TABLE IF EXISTS comunas_cl, comunas_sub CASCADE;
    """, mostrar=False)

    psql_archivo(ruta_comunas)

    psql("""

        CREATE TABLE comunas_sub AS
        SELECT gid, comuna, cut_com, cut_prov, cut_reg, ST_Subdivide(geom, 128) AS geom
        FROM comunas_cl;

        CREATE INDEX idx_comunas_sub_geom ON comunas_sub USING GIST (geom);
        ANALYZE comunas_cl;
        ANALYZE comunas_sub;
    """, mostrar=False)

    psql("""
        SELECT
            (SELECT count(*) FROM comunas_cl) AS comunas_cl_filas,
            (SELECT count(*) FROM comunas_sub) AS comunas_sub_filas,
            (SELECT max(ST_SRID(geom)) FROM comunas_cl) AS srid;
    """)
    print(f"⏱️  DPA cargada y subdividida en {time.perf_counter() - t0:.1f} s")


def cargar_osm_crudo():
    """Paso 2: Carga de las 3 capas del GeoPackage de OSM mediante ogr2ogr."""
    banner("2. Cargando capas crudas de OpenStreetMap con ogr2ogr (SRID 5361)")
    capas = {
        "points": "osm_puntos",
        "lines": "osm_lineas",
        "multipolygons": "osm_poligonos",
    }
    for capa, tabla in capas.items():
        t0 = time.perf_counter()
        compose(
            "exec", "-T", "db",
            "ogr2ogr", "-f", "PostgreSQL", PG_CONN,
            f"/datos/osm_{ZONA}.gpkg", capa,
            "-nln", tabla, "-overwrite",
            "-lco", "GEOMETRY_NAME=geom",
            "-lco", "SPATIAL_INDEX=GIST",
            "-lco", "COLUMN_TYPES=other_tags=hstore",
            "-lco", "FID=id",
            mostrar=False,
        )
        print(f"   {capa:>14} → {tabla:<15} ({time.perf_counter() - t0:5.1f} s)")


def reparar_osm():
    """Paso 3: Diagnóstico y reparación de geometrías inválidas en OSM con ST_MakeValid."""
    banner("3. Auditoría y reparación de geometrías inválidas en OSM (ST_MakeValid)")
    print("--- Diagnóstico ANTES de reparar (osm_poligonos) ---")
    psql("""
        SELECT
            count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalidos_antes,
            count(*) AS total_poligonos,
            round(coalesce(sum(ST_Area(geom)) FILTER (WHERE NOT ST_IsValid(geom)), 0)) AS area_invalida_m2
        FROM osm_poligonos;

        SELECT split_part(ST_IsValidReason(geom), '[', 1) AS razon_invalidez, count(*) AS cantidad
        FROM osm_poligonos
        WHERE NOT ST_IsValid(geom)
        GROUP BY 1
        ORDER BY 2 DESC
        LIMIT 10;
    """)

    t0 = time.perf_counter()
    psql("""
        UPDATE osm_poligonos
        SET geom = ST_Multi(ST_CollectionExtract(ST_MakeValid(geom), 3))
        WHERE NOT ST_IsValid(geom);

        UPDATE osm_lineas
        SET geom = ST_CollectionExtract(ST_MakeValid(geom), 2)
        WHERE NOT ST_IsValid(geom);
    """, mostrar=False)

    print("--- Diagnóstico DESPUÉS de reparar (osm_poligonos) ---")
    psql("""
        SELECT
            count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalidos_despues,
            count(*) FILTER (WHERE ST_IsEmpty(geom)) AS vacios_excluidos
        FROM osm_poligonos;
    """)
    print(f"⏱️  Geometrías reparadas en {time.perf_counter() - t0:.1f} s")


def derivar_capas_osm():
    """Paso 4: Creación de tablas derivadas de OSM en SRID 5361 con índices GiST."""
    banner("4. Derivando capas de esquema propio desde OSM (SRID 5361)")
    t0 = time.perf_counter()
    psql("""
        DROP TABLE IF EXISTS calles, edificios, paraderos_osm, amenidades CASCADE;

        -- 4.1 Calles
        CREATE TABLE calles AS
        SELECT
            id,
            osm_id,
            name,
            highway,
            other_tags -> 'maxspeed' AS maxspeed,
            other_tags -> 'oneway' AS oneway,
            other_tags -> 'surface' AS surface,
            ST_Length(geom) AS largo_m,
            geom
        FROM osm_lineas
        WHERE highway IS NOT NULL AND NOT ST_IsEmpty(geom);
        CREATE INDEX idx_calles_geom ON calles USING GIST (geom);

        -- 4.2 Edificios (polígono + punto interior garantizado con ST_PointOnSurface)
        CREATE TABLE edificios AS
        SELECT
            id,
            coalesce(osm_id, osm_way_id) AS osm_id,
            name,
            building,
            other_tags -> 'building:levels' AS pisos,
            ST_Area(geom) AS area_m2,
            geom,
            ST_PointOnSurface(geom)::geometry(Point, 5361) AS punto
        FROM osm_poligonos
        WHERE building IS NOT NULL AND NOT ST_IsEmpty(geom);
        CREATE INDEX idx_edificios_geom  ON edificios USING GIST (geom);
        CREATE INDEX idx_edificios_punto ON edificios USING GIST (punto);

        -- 4.3 Paraderos OSM (highway = 'bus_stop')
        CREATE TABLE paraderos_osm AS
        SELECT
            id,
            osm_id,
            name,
            other_tags -> 'ref' AS codigo_ref,
            other_tags -> 'network' AS red,
            other_tags -> 'shelter' AS refugio,
            geom
        FROM osm_puntos
        WHERE highway = 'bus_stop' AND NOT ST_IsEmpty(geom);
        CREATE INDEX idx_paraderos_osm_geom ON paraderos_osm USING GIST (geom);

        -- 4.4 Amenidades (unión de puntos y polígonos representados por su punto interior)
        CREATE TABLE amenidades AS
        SELECT
            id,
            osm_id,
            name,
            other_tags -> 'amenity' AS amenity,
            geom::geometry(Point, 5361) AS geom
        FROM osm_puntos
        WHERE other_tags ? 'amenity' AND NOT ST_IsEmpty(geom)
        UNION ALL
        SELECT
            id,
            coalesce(osm_id, osm_way_id) AS osm_id,
            name,
            amenity,
            ST_PointOnSurface(geom)::geometry(Point, 5361) AS geom
        FROM osm_poligonos
        WHERE amenity IS NOT NULL AND NOT ST_IsEmpty(geom);
        CREATE INDEX idx_amenidades_geom ON amenidades USING GIST (geom);

        ANALYZE calles, edificios, paraderos_osm, amenidades;
    """, mostrar=False)

    psql("""
        SELECT 'calles' AS tabla, count(*) AS filas FROM calles
        UNION ALL SELECT 'edificios', count(*) FROM edificios
        UNION ALL SELECT 'paraderos_osm', count(*) FROM paraderos_osm
        UNION ALL SELECT 'amenidades', count(*) FROM amenidades;
    """)
    print(f"⏱️  Capas OSM derivadas e indexadas en {time.perf_counter() - t0:.1f} s")


def cargar_gtfs():
    """Paso 5: Carga masiva de GTFS con COPY y construcción de geometrías en SRID 5361."""
    banner("5. Cargando GTFS con COPY y construyendo geometrías (SRID 5361)")
    tablas_csv = {
        "gtfs_stops_raw": "stops.txt",
        "gtfs_routes_raw": "routes.txt",
        "gtfs_trips_raw": "trips.txt",
        "gtfs_shapes_raw": "shapes.txt",
        "gtfs_stop_times_raw": "stop_times.txt",
    }

    for tabla, archivo in tablas_csv.items():
        t0 = time.perf_counter()
        ruta_local = os.path.join(GTFS_DIR, archivo)
        with open(ruta_local, "r", encoding="utf-8-sig") as f:
            columnas = [c.strip() for c in next(csv.reader(f))]
        defs_col = ", ".join(f'"{col}" text' for col in columnas)

        psql(f"""
            DROP TABLE IF EXISTS {tabla} CASCADE;
            CREATE UNLOGGED TABLE {tabla} ({defs_col});
            COPY {tabla} FROM '/datos/gtfs/{archivo}' WITH (FORMAT csv, HEADER true, ENCODING 'UTF8');
        """, mostrar=False)
        print(f"   COPY {archivo:<16} → {tabla:<20} ({time.perf_counter() - t0:5.1f} s)")

    print("\n--- Conteo de filas crudas cargadas vía COPY desde GTFS ---")
    psql("""
        SELECT 'stops.txt' AS archivo, count(*) AS filas_crudas FROM gtfs_stops_raw
        UNION ALL SELECT 'routes.txt', count(*) FROM gtfs_routes_raw
        UNION ALL SELECT 'trips.txt', count(*) FROM gtfs_trips_raw
        UNION ALL SELECT 'shapes.txt', count(*) FROM gtfs_shapes_raw
        UNION ALL SELECT 'stop_times.txt', count(*) FROM gtfs_stop_times_raw;
    """)

    t0 = time.perf_counter()
    xmin, ymin, xmax, ymax = BBOX
    psql(f"""
        DROP TABLE IF EXISTS paraderos_gtfs, recorridos, gtfs_frecuencia_paraderos CASCADE;

        -- 5.1 Paraderos GTFS en SRID 5361 recortados a la caja en la base
        CREATE TABLE paraderos_gtfs AS
        SELECT
            stop_id,
            stop_code,
            stop_name,
            ST_Transform(
                ST_SetSRID(ST_MakePoint(stop_lon::double precision, stop_lat::double precision), 4326),
                5361
            )::geometry(Point, 5361) AS geom
        FROM gtfs_stops_raw
        WHERE stop_lon IS NOT NULL AND stop_lat IS NOT NULL
          AND stop_lon <> '' AND stop_lat <> ''
          AND ST_Intersects(
                ST_SetSRID(ST_MakePoint(stop_lon::double precision, stop_lat::double precision), 4326),
                ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326)
              );

        CREATE INDEX idx_paraderos_gtfs_geom ON paraderos_gtfs USING GIST (geom);
        CREATE INDEX idx_paraderos_gtfs_stop_id ON paraderos_gtfs (stop_id);

        -- 5.2 Recorridos GTFS: una LineString por shape_id ordenada por shape_pt_sequence
        CREATE TABLE recorridos AS
        WITH lineas_shape AS (
            SELECT
                shape_id,
                count(*) AS n_puntos,
                ST_Transform(
                    ST_SetSRID(
                        ST_MakeLine(
                            ST_MakePoint(shape_pt_lon::double precision, shape_pt_lat::double precision)
                            ORDER BY shape_pt_sequence::integer
                        ),
                        4326
                    ),
                    5361
                )::geometry(LineString, 5361) AS geom
            FROM gtfs_shapes_raw
            WHERE shape_pt_lon IS NOT NULL AND shape_pt_lat IS NOT NULL
              AND shape_pt_lon <> '' AND shape_pt_lat <> ''
            GROUP BY shape_id
            HAVING count(*) >= 2
        ),
        shape_ruta AS (
            SELECT DISTINCT ON (t.shape_id)
                t.shape_id,
                t.route_id,
                r.route_short_name,
                r.route_long_name,
                t.direction_id,
                count(*) AS n_viajes
            FROM gtfs_trips_raw t
            JOIN gtfs_routes_raw r ON r.route_id = t.route_id
            WHERE t.shape_id IS NOT NULL AND t.shape_id <> ''
            GROUP BY t.shape_id, t.route_id, r.route_short_name, r.route_long_name, t.direction_id
            ORDER BY t.shape_id, count(*) DESC
        )
        SELECT
            l.shape_id,
            sr.route_id,
            sr.route_short_name,
            sr.route_long_name,
            sr.direction_id,
            sr.n_viajes,
            l.n_puntos,
            ST_Length(l.geom) AS largo_m,
            l.geom
        FROM lineas_shape l
        JOIN shape_ruta sr ON sr.shape_id = l.shape_id
        WHERE ST_Intersects(
            l.geom,
            ST_Transform(ST_MakeEnvelope({xmin}, {ymin}, {xmax}, {ymax}, 4326), 5361)
        );

        CREATE INDEX idx_recorridos_geom ON recorridos USING GIST (geom);
        CREATE INDEX idx_recorridos_route_short ON recorridos (route_short_name);

        -- 5.3 Resumen de detenciones (stop_times) por paradero de la caja y ruta
        CREATE TABLE gtfs_frecuencia_paraderos AS
        SELECT
            p.stop_id,
            count(*) AS total_detenciones,
            count(DISTINCT t.route_id) AS rutas_distintas
        FROM paraderos_gtfs p
        JOIN gtfs_stop_times_raw st ON st.stop_id = p.stop_id
        JOIN gtfs_trips_raw t ON t.trip_id = st.trip_id
        GROUP BY p.stop_id;

        CREATE INDEX idx_frec_stop_id ON gtfs_frecuencia_paraderos (stop_id);

        ANALYZE paraderos_gtfs, recorridos, gtfs_frecuencia_paraderos;
    """, mostrar=False)

    print(f"⏱️  Geometrías GTFS construidas e indexadas en {time.perf_counter() - t0:.1f} s")


def resumen_final():
    """Paso 6: VACUUM ANALYZE, reinicio de estadísticas de índices y resumen para el README."""
    banner("6. Ejecutando VACUUM ANALYZE y generando reporte del esquema final")
    psql("VACUUM ANALYZE;", mostrar=False)
    # Reiniciar contadores de pg_stat_user_indexes tras el ETL para medir solo las consultas de la Parte C
    psql("SELECT pg_stat_reset();", mostrar=False)

    print("--- Resumen de Tablas Espaciales Finales (Para el README.md) ---")
    psql("""
        SELECT
            tabla,
            filas,
            srid,
            tipo_geom,
            pg_size_pretty(pg_relation_size(tabla::regclass)) AS tam_datos,
            pg_size_pretty(pg_indexes_size(tabla::regclass)) AS tam_indices
        FROM (
            SELECT 'comunas_cl' AS tabla, count(*) AS filas, max(ST_SRID(geom)) AS srid, max(ST_GeometryType(geom)) AS tipo_geom FROM comunas_cl
            UNION ALL SELECT 'comunas_sub', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM comunas_sub
            UNION ALL SELECT 'calles', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM calles
            UNION ALL SELECT 'edificios', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM edificios
            UNION ALL SELECT 'paraderos_osm', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM paraderos_osm
            UNION ALL SELECT 'amenidades', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM amenidades
            UNION ALL SELECT 'paraderos_gtfs', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM paraderos_gtfs
            UNION ALL SELECT 'recorridos', count(*), max(ST_SRID(geom)), max(ST_GeometryType(geom)) FROM recorridos
        ) t;
    """)

    print("--- Índices creados en el esquema propio ---")
    psql("""
        SELECT tablename AS tabla, indexname AS indice, indexdef AS definicion
        FROM pg_indexes
        WHERE tablename IN (
            'comunas_cl', 'comunas_sub', 'calles', 'edificios',
            'paraderos_osm', 'amenidades', 'paraderos_gtfs', 'recorridos'
        )
        ORDER BY 1, 2;
    """)


if __name__ == "__main__":
    t_inicio = time.perf_counter()
    asegurar_contenedor()
    ruta_comunas = asegurar_insumos()
    cargar_dpa(ruta_comunas)
    cargar_osm_crudo()
    reparar_osm()
    derivar_capas_osm()
    cargar_gtfs()
    resumen_final()
    print(f"\n🎉 Pipeline ETL completado con éxito en {time.perf_counter() - t_inicio:.1f} s.")