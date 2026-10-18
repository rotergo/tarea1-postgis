#!/usr/bin/env python3
"""
Tarea 1 — Parte C y Parte D:
1. Ejecuta las 6+1 consultas espaciales y guarda sus resultados.
2. Ejecuta EXPLAIN ANALYZE en las consultas clave y audita pg_stat_user_indexes.
3. Exporta las capas de resultados a salidas/resultados_santiago-centro.gpkg.
4. Genera los mapas cartográficos en salidas/*.png.
"""
import json
import os
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
SALIDAS = os.path.join(AQUI, "salidas")
REPORTE_TXT = os.path.join(SALIDAS, "reporte_consultas_y_planes.txt")
PG_CONN = "PG:host=localhost dbname=postgres user=postgres password=pmd2026"

FUENTES_PIE = (
    "Fuentes: OpenStreetMap vía Geofabrik (chile-261016.osm.pbf, 16-10-2026) · "
    "GTFS DTPM (GTFS_20261003.zip, 03-10-2026) · DPA INE/BCN (2023) · SRID 5361"
)


def compose(*args, mostrar=False, check=True):
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


def psql(sql):
    return compose(
        "exec", "-T", "db",
        "psql", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
        "-c", sql,
        mostrar=False,
    )


def psql_json(sql):
    """Devuelve un FeatureCollection GeoJSON (en EPSG:4326) desde una consulta con columna geom."""
    wrapped = f"""
        SELECT json_build_object(
            'type', 'FeatureCollection',
            'features', coalesce(json_agg(
                json_build_object(
                    'type', 'Feature',
                    'geometry', ST_AsGeoJSON(ST_Transform(q.geom, 4326))::json,
                    'properties', to_jsonb(q) - 'geom'
                )
            ), '[]'::json)
        )::text
        FROM ({sql}) AS q;
    """
    out = compose(
        "exec", "-T", "db",
        "psql", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "postgres",
        "-c", wrapped,
        mostrar=False,
    )
    return json.loads(out.strip())


def registrar(f_rep, titulo, contenido):
    bloque = f"\n{'=' * 78}\n{titulo}\n{'=' * 78}\n{contenido.strip()}\n"
    print(bloque)
    f_rep.write(bloque)


def ejecutar_todas_las_consultas(f_rep):
    # Reiniciar estadísticas de índices antes de correr la batería de consultas de la Parte C
    psql("DROP INDEX IF EXISTS idx_comunas_cl_geom; SELECT pg_stat_reset();")

    # P1
    q1 = """
        SELECT
            c.comuna,
            count(p.stop_id) AS paraderos_gtfs,
            round((ST_Area(c.geom) / 1e6)::numeric, 2) AS area_comuna_km2,
            round((count(p.stop_id) / (ST_Area(c.geom) / 1e6))::numeric, 2) AS densidad_paraderos_km2
        FROM comunas_cl c
        JOIN paraderos_gtfs p ON ST_Contains(c.geom, p.geom)
        GROUP BY c.gid, c.comuna, c.geom
        ORDER BY densidad_paraderos_km2 DESC;
    """
    registrar(f_rep, "PREGUNTA 1: Paraderos GTFS por comuna y densidad (paraderos/km2)", psql(q1))

    # P2
    q2 = """
        SELECT
            c.comuna,
            count(e.id) AS total_edificios,
            count(e.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM paraderos_gtfs pg WHERE ST_DWithin(e.punto, pg.geom, 500)
            )) AS edif_sin_gtfs_500m,
            round(100.0 * count(e.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM paraderos_gtfs pg WHERE ST_DWithin(e.punto, pg.geom, 500)
            )) / count(e.id), 2) AS pct_sin_gtfs,
            count(e.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM paraderos_osm po WHERE ST_DWithin(e.punto, po.geom, 500)
            )) AS edif_sin_osm_500m,
            round(100.0 * count(e.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM paraderos_osm po WHERE ST_DWithin(e.punto, po.geom, 500)
            )) / count(e.id), 2) AS pct_sin_osm
        FROM comunas_cl c
        JOIN edificios e ON ST_Contains(c.geom, e.punto)
        WHERE c.cut_reg = '13'
        GROUP BY c.comuna
        ORDER BY total_edificios DESC;
    """
    registrar(f_rep, "PREGUNTA 2: Cobertura a 500 m (GTFS vs OSM por comuna)", psql(q2))

    q2_explain = """
        EXPLAIN ANALYZE
        SELECT c.comuna,
               count(*) AS edificios,
               count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM paraderos_gtfs p WHERE ST_DWithin(e.punto, p.geom, 500)
               )) AS sin_paradero_500m
        FROM comunas_cl c
        JOIN edificios e ON ST_Contains(c.geom, e.punto)
        WHERE c.cut_reg = '13'
        GROUP BY c.comuna;
    """
    registrar(f_rep, "PLAN DE EJECUCIÓN #1 — Pregunta 2 (Anti-join cobertura 500 m)", psql(q2_explain))

    # P3
    q3 = """
        SELECT
            (SELECT count(*) FROM paraderos_gtfs) AS total_gtfs,
            (SELECT count(*) FROM paraderos_gtfs g
             WHERE NOT EXISTS (
                 SELECT 1 FROM paraderos_osm o WHERE ST_DWithin(g.geom, o.geom, 50)
             )) AS gtfs_sin_osm_50m,
            round(100.0 * (SELECT count(*) FROM paraderos_gtfs g
             WHERE NOT EXISTS (
                 SELECT 1 FROM paraderos_osm o WHERE ST_DWithin(g.geom, o.geom, 50)
             )) / (SELECT count(*) FROM paraderos_gtfs), 2) AS pct_gtfs_sin_osm,
            (SELECT count(*) FROM paraderos_osm) AS total_osm,
            (SELECT count(*) FROM paraderos_osm o
             WHERE NOT EXISTS (
                 SELECT 1 FROM paraderos_gtfs g WHERE ST_DWithin(o.geom, g.geom, 50)
             )) AS osm_sin_gtfs_50m,
            round(100.0 * (SELECT count(*) FROM paraderos_osm o
             WHERE NOT EXISTS (
                 SELECT 1 FROM paraderos_gtfs g WHERE ST_DWithin(o.geom, g.geom, 50)
             )) / (SELECT count(*) FROM paraderos_osm), 2) AS pct_osm_sin_gtfs;
    """
    registrar(f_rep, "PREGUNTA 3: Dos fuentes, un objeto (Anti-join a 50 m en ambos sentidos)", psql(q3))

    # P4
    q4_cl = """
        SELECT c.comuna,
               round((sum(ST_Length(ST_Intersection(r.geom, c.geom))) / 1000.0)::numeric, 2) AS km_recorridos
        FROM comunas_cl c
        JOIN recorridos r ON ST_Intersects(r.geom, c.geom)
        WHERE c.cut_reg = '13'
        GROUP BY c.comuna
        ORDER BY km_recorridos DESC;
    """
    q4_sub = """
        SELECT c.comuna,
               round((sum(ST_Length(ST_Intersection(r.geom, c.geom))) / 1000.0)::numeric, 2) AS km_recorridos
        FROM comunas_sub c
        JOIN recorridos r ON ST_Intersects(r.geom, c.geom)
        WHERE c.cut_reg = '13'
        GROUP BY c.comuna
        ORDER BY km_recorridos DESC;
    """
    registrar(f_rep, "PREGUNTA 4: Kilómetros de recorrido por comuna (Resultado)", psql(q4_sub))
    registrar(f_rep, "PLAN DE EJECUCIÓN #2A — Pregunta 4 SIN ST_Subdivide (comunas_cl)", psql("EXPLAIN ANALYZE " + q4_cl))
    registrar(f_rep, "PLAN DE EJECUCIÓN #2B — Pregunta 4 CON ST_Subdivide (comunas_sub)", psql("EXPLAIN ANALYZE " + q4_sub))

    # P5
    q5 = """
        WITH paraderos_ruta AS (
            SELECT DISTINCT p.stop_id, p.stop_code, p.stop_name, p.geom
            FROM paraderos_gtfs p
            JOIN gtfs_stop_times_raw st ON st.stop_id = p.stop_id
            JOIN gtfs_trips_raw t ON t.trip_id = st.trip_id
            JOIN gtfs_routes_raw r ON r.route_id = t.route_id
            WHERE r.route_short_name = '210'
        )
        SELECT
            p.stop_code,
            p.stop_name AS paradero,
            coalesce(cercano.name, '(Sin nombre en OSM)') AS establecimiento_salud,
            cercano.amenity AS tipo_amenidad,
            round(ST_Distance(p.geom, cercano.geom)::numeric, 1) AS distancia_m
        FROM paraderos_ruta p
        CROSS JOIN LATERAL (
            SELECT a.name, a.amenity, a.geom
            FROM amenidades a
            WHERE a.amenity IN ('pharmacy', 'hospital', 'clinic', 'doctors')
            ORDER BY a.geom <-> p.geom
            LIMIT 1
        ) AS cercano
        ORDER BY distancia_m DESC;
    """
    registrar(f_rep, "PREGUNTA 5 (Resumen Top 15 más lejanos y estadísticos — Recorrido 210)", psql(q5.replace("ORDER BY distancia_m DESC;", "ORDER BY distancia_m DESC LIMIT 15;")))
    q5_stats = """
        WITH paraderos_ruta AS (
            SELECT DISTINCT p.stop_id, p.stop_code, p.stop_name, p.geom
            FROM paraderos_gtfs p
            JOIN gtfs_stop_times_raw st ON st.stop_id = p.stop_id
            JOIN gtfs_trips_raw t ON t.trip_id = st.trip_id
            JOIN gtfs_routes_raw r ON r.route_id = t.route_id
            WHERE r.route_short_name = '210'
        ),
        distancias AS (
            SELECT p.stop_code, cercano.amenity, ST_Distance(p.geom, cercano.geom) AS d
            FROM paraderos_ruta p
            CROSS JOIN LATERAL (
                SELECT a.amenity, a.geom FROM amenidades a
                WHERE a.amenity IN ('pharmacy', 'hospital', 'clinic', 'doctors')
                ORDER BY a.geom <-> p.geom LIMIT 1
            ) AS cercano
        )
        SELECT count(*) AS paraderos_ruta_210,
               round(min(d)::numeric, 1) AS min_m,
               round(avg(d)::numeric, 1) AS media_m,
               round(percentile_cont(0.5) WITHIN GROUP (ORDER BY d)::numeric, 1) AS mediana_m,
               round(max(d)::numeric, 1) AS max_m
        FROM distancias;
    """
    registrar(f_rep, "PREGUNTA 5 (Estadísticos globales de distancia en Recorrido 210)", psql(q5_stats))
    registrar(f_rep, "PLAN DE EJECUCIÓN #3 — Pregunta 5 (KNN con CROSS JOIN LATERAL + <->)", psql("EXPLAIN ANALYZE " + q5))

    # P6
    psql("""
        DROP MATERIALIZED VIEW IF EXISTS mv_grilla CASCADE;
        CREATE MATERIALIZED VIEW mv_grilla AS
        WITH hex AS (
            SELECT h.i, h.j, h.geom
            FROM ST_HexagonGrid(500, (SELECT ST_SetSRID(ST_Extent(punto), 5361) FROM edificios)) AS h
        ),
        edif_hex AS (
            SELECT h.i, h.j, count(e.id) AS edificios, round(coalesce(sum(e.area_m2), 0)) AS m2_construidos
            FROM hex h
            JOIN edificios e ON ST_Contains(h.geom, e.punto)
            GROUP BY h.i, h.j
        ),
        rec_hex AS (
            SELECT h.i, h.j,
                   count(DISTINCT r.route_id) AS recorridos_distintos,
                   count(DISTINCT r.shape_id) AS shapes_distintos
            FROM hex h
            JOIN recorridos r ON ST_Intersects(h.geom, r.geom)
            GROUP BY h.i, h.j
        )
        SELECT
            h.i, h.j,
            eh.edificios,
            eh.m2_construidos,
            coalesce(rh.recorridos_distintos, 0) AS recorridos_distintos,
            coalesce(rh.shapes_distintos, 0) AS shapes_distintos,
            round((eh.edificios::numeric / (coalesce(rh.recorridos_distintos, 0) + 1)), 1) AS indice_presion,
            h.geom::geometry(Polygon, 5361) AS geom
        FROM hex h
        JOIN edif_hex eh ON eh.i = h.i AND eh.j = h.j
        LEFT JOIN rec_hex rh ON rh.i = h.i AND rh.j = h.j;

        CREATE INDEX idx_mv_grilla_geom ON mv_grilla USING GIST (geom);
        ANALYZE mv_grilla;
    """)

    q6_resumen = """
        SELECT count(*) AS total_hexagonos_con_edificios,
               sum(edificios) AS suma_edificios,
               max(edificios) AS max_edificios_celda,
               max(recorridos_distintos) AS max_recorridos_celda,
               count(*) FILTER (WHERE recorridos_distintos = 0) AS celdas_sin_recorridos
        FROM mv_grilla;
    """
    q6_top10 = """
        SELECT
            g.i, g.j,
            c.comuna AS comuna,
            g.edificios,
            g.m2_construidos,
            g.recorridos_distintos,
            g.indice_presion
        FROM mv_grilla g
        LEFT JOIN comunas_cl c ON ST_Contains(c.geom, ST_PointOnSurface(g.geom))
        WHERE g.edificios >= 100
        ORDER BY g.recorridos_distintos ASC, g.edificios DESC
        LIMIT 10;
    """
    q6_top10_presion = """
        SELECT
            g.i, g.j,
            c.comuna AS comuna,
            g.edificios,
            g.m2_construidos,
            g.recorridos_distintos,
            g.indice_presion
        FROM mv_grilla g
        LEFT JOIN comunas_cl c ON ST_Contains(c.geom, ST_PointOnSurface(g.geom))
        ORDER BY g.indice_presion DESC
        LIMIT 10;
    """
    registrar(f_rep, "PREGUNTA 6: Resumen general de la Grilla Hexagonal (500 m)", psql(q6_resumen))
    registrar(f_rep, "PREGUNTA 6A: Top 10 celdas (>=100 edificios) con MENOS recorridos y MÁS edificios", psql(q6_top10))
    registrar(f_rep, "PREGUNTA 6B: Top 10 celdas con mayor razón Edificios / (Recorridos + 1)", psql(q6_top10_presion))

    # P7
    q7 = """
        WITH paraderos_vialidad AS (
            SELECT
                p.stop_id,
                p.stop_code,
                p.stop_name,
                coalesce(f.total_detenciones, 0) AS detenciones_gtfs,
                coalesce(f.rutas_distintas, 0) AS rutas_gtfs,
                via.highway AS tipo_via_osm,
                EXISTS (
                    SELECT 1 FROM paraderos_osm po
                    WHERE ST_DWithin(p.geom, po.geom, 30)
                      AND po.refugio = 'yes'
                ) AS tiene_refugio_osm
            FROM paraderos_gtfs p
            LEFT JOIN gtfs_frecuencia_paraderos f ON f.stop_id = p.stop_id
            CROSS JOIN LATERAL (
                SELECT c.highway
                FROM calles c
                WHERE ST_DWithin(p.geom, c.geom, 40)
                ORDER BY c.geom <-> p.geom
                LIMIT 1
            ) AS via
        )
        SELECT
            tipo_via_osm,
            count(*) AS paraderos_gtfs,
            sum(detenciones_gtfs) AS total_detenciones_programadas,
            round(avg(detenciones_gtfs), 1) AS promedio_detenciones_paradero,
            round(avg(rutas_gtfs), 1) AS promedio_rutas_paradero,
            count(*) FILTER (WHERE tiene_refugio_osm) AS con_refugio_osm,
            round(100.0 * count(*) FILTER (WHERE tiene_refugio_osm) / count(*), 1) AS pct_con_refugio_osm
        FROM paraderos_vialidad
        GROUP BY tipo_via_osm
        HAVING count(*) >= 20
        ORDER BY total_detenciones_programadas DESC;
    """
    registrar(f_rep, "PREGUNTA 7 (Propia): Frecuencia real GTFS (stop_times) vs Jerarquía Vial y Refugios OSM", psql(q7))

    # Auditoría de índices (Parte D)
    time.sleep(1)  # Dar tiempo al colector de estadísticas de PostgreSQL
    q_idx = """
        SELECT
            relname AS tabla,
            indexrelname AS indice,
            idx_scan AS lecturas_idx_scan,
            pg_size_pretty(pg_relation_size(indexrelid)) AS tamano_indice
        FROM pg_stat_user_indexes
        WHERE relname IN (
            'comunas_cl', 'comunas_sub', 'calles', 'edificios',
            'paraderos_osm', 'amenidades', 'paraderos_gtfs', 'recorridos', 'mv_grilla'
        )
        ORDER BY relname, indexrelname;
    """
    registrar(f_rep, "PARTE D: Auditoría de uso de índices (pg_stat_user_indexes)", psql(q_idx))


def exportar_geopackage():
    print("\n📦 Exportando capas de resultados a salidas/resultados_santiago-centro.gpkg...")
    gpkg_out = "/salidas/resultados_santiago-centro.gpkg"
    local_out = os.path.join(SALIDAS, "resultados_santiago-centro.gpkg")
    if os.path.exists(local_out):
        os.remove(local_out)

    # 1. Grilla hexagonal (P6)
    compose(
        "exec", "-T", "db", "ogr2ogr", "-f", "GPKG", gpkg_out, PG_CONN,
        "-sql", "SELECT i, j, edificios, m2_construidos, recorridos_distintos, indice_presion, geom FROM mv_grilla",
        "-nln", "grilla_hexagonal_500m",
        mostrar=False,
    )
    # 2. Resumen comunal (P1 y P2)
    sql_comunas = """
        SELECT
            c.comuna,
            count(DISTINCT e.id) AS total_edificios,
            round(100.0 * count(DISTINCT e.id) FILTER (WHERE NOT EXISTS (
                SELECT 1 FROM paraderos_gtfs pg WHERE ST_DWithin(e.punto, pg.geom, 500)
            )) / nullif(count(DISTINCT e.id), 0), 2) AS pct_edif_sin_gtfs_500m,
            c.geom
        FROM comunas_cl c
        JOIN edificios e ON ST_Contains(c.geom, e.punto)
        WHERE c.cut_reg = '13'
        GROUP BY c.gid, c.comuna, c.geom
    """
    compose(
        "exec", "-T", "db", "ogr2ogr", "-f", "GPKG", gpkg_out, PG_CONN, "-update",
        "-sql", sql_comunas,
        "-nln", "cobertura_comunal",
        mostrar=False,
    )
    mb = os.path.getsize(local_out) / 1e6
    print(f"✅ Archivo exportado listo para el repositorio: salidas/resultados_santiago-centro.gpkg ({mb:.2f} MB)")


def generar_mapas():
    try:
        import matplotlib.pyplot as plt
        from matplotlib.cm import ScalarMappable
        from matplotlib.colors import Normalize
    except ImportError:
        print("⚠️  matplotlib no está instalado en este Python. Ejecuta: pip install matplotlib")
        return

    print("\n🗺️  Generando mapas cartográficos en salidas/...")

    def dibujar_fc(fc, ax, color="tab:blue", alpha=0.6, lw=0.8, s=8, por=None, cmap="viridis", ec="k", elw=0.3, vmin=None, vmax=None):
        vals = [f["properties"].get(por) for f in fc["features"] if f["properties"].get(por) is not None] if por else []
        if vmin is None:
            vmin = min(vals) if vals else 0
        if vmax is None:
            vmax = max(vals) if vals else 1
        cm = plt.get_cmap(cmap)
        for f in fc["features"]:
            g = f["geometry"]
            if g is None:
                continue
            val = f["properties"].get(por, 0) if por else 0
            c = cm((val - vmin) / max(vmax - vmin, 1e-9)) if por else color
            t, cs = g["type"], g["coordinates"]
            if t == "Point":
                ax.scatter(cs[0], cs[1], s=s, color=c, alpha=alpha)
            elif t == "LineString":
                ax.plot([p[0] for p in cs], [p[1] for p in cs], color=c, lw=lw, alpha=alpha)
            elif t == "MultiLineString":
                for line in cs:
                    ax.plot([p[0] for p in line], [p[1] for p in line], color=c, lw=lw, alpha=alpha)
            elif t in ("Polygon", "MultiPolygon"):
                for poly in (cs if t == "MultiPolygon" else [cs]):
                    ax.fill([p[0] for p in poly[0]], [p[1] for p in poly[0]], color=c, alpha=alpha, lw=elw, ec=ec)
        ax.set_aspect(1 / 0.835)
        return vmin, vmax

    fc_comunas = psql_json("SELECT comuna, geom FROM comunas_cl WHERE cut_reg = '13'")
    fc_grilla = psql_json("SELECT i, j, edificios, recorridos_distintos, indice_presion, geom FROM mv_grilla")
    fc_top10 = psql_json("""
        SELECT i, j, edificios, recorridos_distintos, geom
        FROM mv_grilla
        WHERE edificios >= 100
        ORDER BY recorridos_distintos ASC, edificios DESC
        LIMIT 10
    """)
    fc_recorridos = psql_json("SELECT shape_id, geom FROM recorridos")

    # -------------------------------------------------------------------------
    # MAPA 1 (PRINCIPAL - P6): Grilla hexagonal 500 m (Edificios vs Recorridos)
    # -------------------------------------------------------------------------
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7.8))

    # Panel izquierdo: Densidad de edificios por hexágono + Top 10 celdas críticas
    dibujar_fc(fc_comunas, ax1, color="#f2f2f2", alpha=0.5, ec="#666666", elw=0.6)
    vmin1, vmax1 = dibujar_fc(fc_grilla, ax1, por="edificios", cmap="YlOrRd", alpha=0.88, ec="#444444", elw=0.2)
    dibujar_fc(fc_top10, ax1, color="none", alpha=1.0, ec="#00ffff", elw=2.0)
    ax1.set_xlim(-70.755, -70.515)
    ax1.set_ylim(-33.525, -33.355)
    ax1.set_title("A) Edificios por hexágono de 500 m (OSM)\n[Borde cian = Top 10 celdas críticas (>=100 edif. y menos rutas)]", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Longitud (°)")
    ax1.set_ylabel("Latitud (°)")
    sm1 = ScalarMappable(norm=Normalize(vmin=vmin1, vmax=vmax1), cmap="YlOrRd")
    plt.colorbar(sm1, ax=ax1, fraction=0.038, pad=0.03, label="N° de edificios (OSM)")

    # Panel derecho: Recorridos distintos que cruzan cada hexágono
    dibujar_fc(fc_comunas, ax2, color="#f2f2f2", alpha=0.5, ec="#666666", elw=0.6)
    vmin2, vmax2 = dibujar_fc(fc_grilla, ax2, por="recorridos_distintos", cmap="viridis", alpha=0.88, ec="#444444", elw=0.2)
    dibujar_fc(fc_top10, ax2, color="none", alpha=1.0, ec="#ff1744", elw=2.0)
    ax2.set_xlim(-70.755, -70.515)
    ax2.set_ylim(-33.525, -33.355)
    ax2.set_title("B) Recorridos distintos por hexágono de 500 m (GTFS)\n[Borde rojo = Top 10 celdas críticas (>=100 edif. y menos rutas)]", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Longitud (°)")
    ax2.set_ylabel("Latitud (°)")
    sm2 = ScalarMappable(norm=Normalize(vmin=vmin2, vmax=vmax2), cmap="viridis")
    plt.colorbar(sm2, ax=ax2, fraction=0.038, pad=0.03, label="N° de recorridos distintos (route_id)")

    fig.suptitle("Santiago Centro — Relación entre Densidad Edificada (OSM) y Oferta de Transporte Público (GTFS)", fontsize=13, fontweight="bold", y=0.98)
    fig.text(0.5, 0.01, FUENTES_PIE, ha="center", fontsize=8.5, style="italic", color="#333333")
    plt.tight_layout(rect=[0, 0.03, 1, 0.94])
    ruta_m1 = os.path.join(SALIDAS, "mapa1_grilla_p6.png")
    plt.savefig(ruta_m1, dpi=220)
    plt.close(fig)
    print(f"   ✅ {ruta_m1}")

    # -------------------------------------------------------------------------
    # MAPA 2 (P2 y P3): Desfase espacial entre Paraderos GTFS y Paraderos OSM
    # -------------------------------------------------------------------------
    fc_gtfs_huerfanos = psql_json("""
        SELECT stop_id, geom FROM paraderos_gtfs g
        WHERE NOT EXISTS (SELECT 1 FROM paraderos_osm o WHERE ST_DWithin(g.geom, o.geom, 50))
    """)
    fc_gtfs_pareados = psql_json("""
        SELECT stop_id, geom FROM paraderos_gtfs g
        WHERE EXISTS (SELECT 1 FROM paraderos_osm o WHERE ST_DWithin(g.geom, o.geom, 50))
    """)
    fc_edif_lejos = psql_json("""
        SELECT id, punto AS geom FROM edificios e
        WHERE NOT EXISTS (SELECT 1 FROM paraderos_gtfs p WHERE ST_DWithin(e.punto, p.geom, 500))
    """)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 7.8))

    # Panel A: Cobertura de paraderos GTFS y edificios a > 500 m (P2)
    dibujar_fc(fc_comunas, ax1, color="#eeeeee", alpha=0.6, ec="#555555", elw=0.7)
    dibujar_fc(fc_recorridos, ax1, color="#90caf9", alpha=0.25, lw=0.4)
    dibujar_fc(fc_edif_lejos, ax1, color="#e53935", alpha=0.5, s=2)
    dibujar_fc(fc_gtfs_pareados, ax1, color="#1e88e5", alpha=0.6, s=3)
    ax1.set_xlim(-70.755, -70.515)
    ax1.set_ylim(-33.525, -33.355)
    ax1.set_title("A) Cobertura a 500 m (Pregunta 2)\nAzul: Paraderos GTFS · Rojo: Edificios OSM a > 500 m de paradero", fontsize=11, fontweight="bold")
    ax1.set_xlabel("Longitud (°)")
    ax1.set_ylabel("Latitud (°)")

    # Panel B: Concordancia GTFS vs OSM a 50 m (P3)
    dibujar_fc(fc_comunas, ax2, color="#eeeeee", alpha=0.6, ec="#555555", elw=0.7)
    dibujar_fc(fc_gtfs_pareados, ax2, color="#2e7d32", alpha=0.65, s=4)
    dibujar_fc(fc_gtfs_huerfanos, ax2, color="#ff6f00", alpha=0.8, s=5)
    ax2.set_xlim(-70.755, -70.515)
    ax2.set_ylim(-33.525, -33.355)
    ax2.set_title("B) Completitud OSM vs GTFS a 50 m (Pregunta 3)\nVerde: Paraderos GTFS con par en OSM (<50 m) · Naranja: Sin par en OSM", fontsize=11, fontweight="bold")
    ax2.set_xlabel("Longitud (°)")
    ax2.set_ylabel("Latitud (°)")

    fig.suptitle("Santiago Centro — Cobertura Territorial (500 m) y Brecha de Completitud GTFS vs. OSM (50 m)", fontsize=13, fontweight="bold", y=0.98)
    fig.text(0.5, 0.01, FUENTES_PIE, ha="center", fontsize=8.5, style="italic", color="#333333")
    plt.tight_layout(rect=[0, 0.03, 1, 0.94])
    ruta_m2 = os.path.join(SALIDAS, "mapa2_cobertura_y_fuentes.png")
    plt.savefig(ruta_m2, dpi=220)
    plt.close(fig)
    print(f"   ✅ {ruta_m2}")


if __name__ == "__main__":
    os.makedirs(SALIDAS, exist_ok=True)
    t0 = time.perf_counter()
    with open(REPORTE_TXT, "w", encoding="utf-8") as f_rep:
        ejecutar_todas_las_consultas(f_rep)
    exportar_geopackage()
    generar_mapas()
    print(f"\n🎉 Parte C y D ejecutadas en {time.perf_counter() - t0:.1f} s.")
    print(f"📄 Reporte completo guardado en: salidas/reporte_consultas_y_planes.txt")