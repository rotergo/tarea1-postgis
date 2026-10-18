-- ============================================================================
-- TAREA 1: PostGIS de punta a punta (Santiago centro)
-- Parte C: Consultas Espaciales (6 obligatorias + 1 propia)
-- ============================================================================

-- ----------------------------------------------------------------------------
-- PREGUNTA 1: Paraderos por comuna y densidad (paraderos / km2 de comuna)
-- Patrón: Join espacial punto-en-polígono (ST_Contains)
-- ----------------------------------------------------------------------------
SELECT
    c.comuna,
    count(p.stop_id) AS paraderos_gtfs,
    round((ST_Area(c.geom) / 1e6)::numeric, 2) AS area_comuna_km2,
    round((count(p.stop_id) / (ST_Area(c.geom) / 1e6))::numeric, 2) AS densidad_paraderos_km2
FROM comunas_cl c
JOIN paraderos_gtfs p ON ST_Contains(c.geom, p.geom)
GROUP BY c.gid, c.comuna, c.geom
ORDER BY densidad_paraderos_km2 DESC;

-- ----------------------------------------------------------------------------
-- PREGUNTA 2: Cobertura (% de edificios a más de 500 m de cualquier paradero)
-- Patrón: Anti-join con NOT EXISTS + ST_DWithin (comparando GTFS vs OSM)
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- PREGUNTA 3: Dos fuentes, un objeto (Desfase a menos de 50 m en ambos sentidos)
-- Patrón: Anti-join bidireccional con NOT EXISTS + ST_DWithin
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- PREGUNTA 4: Kilómetros de recorrido por comuna (ST_Intersection)
-- Patrón: Recorte de líneas contra polígonos (comunas_cl vs comunas_sub)
-- ----------------------------------------------------------------------------
-- 4A: Contra comunas_cl (polígonos completos)
SELECT
    c.comuna,
    round((sum(ST_Length(ST_Intersection(r.geom, c.geom))) / 1000.0)::numeric, 2) AS km_recorridos
FROM comunas_cl c
JOIN recorridos r ON ST_Intersects(r.geom, c.geom)
WHERE c.cut_reg = '13'
GROUP BY c.comuna
ORDER BY km_recorridos DESC;

-- 4B: Contra comunas_sub (polígonos subdivididos con ST_Subdivide)
SELECT
    c.comuna,
    round((sum(ST_Length(ST_Intersection(r.geom, c.geom))) / 1000.0)::numeric, 2) AS km_recorridos
FROM comunas_sub c
JOIN recorridos r ON ST_Intersects(r.geom, c.geom)
WHERE c.cut_reg = '13'
GROUP BY c.comuna
ORDER BY km_recorridos DESC;

-- ----------------------------------------------------------------------------
-- PREGUNTA 5: Vecino más cercano (Salud/Farmacia a paraderos de ruta 210)
-- Patrón: CROSS JOIN LATERAL + operador <-> + LIMIT 1
-- ----------------------------------------------------------------------------
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

-- ----------------------------------------------------------------------------
-- PREGUNTA 6: Grilla hexagonal de 500 m (Edificios vs Recorridos distintos)
-- Patrón: ST_HexagonGrid + Vista Materializada
-- ----------------------------------------------------------------------------
DROP MATERIALIZED VIEW IF EXISTS mv_grilla CASCADE;

CREATE MATERIALIZED VIEW mv_grilla AS
WITH hex AS (
    SELECT h.i, h.j, h.geom
    FROM ST_HexagonGrid(
        500,
        (SELECT ST_SetSRID(ST_Extent(punto), 5361) FROM edificios)
    ) AS h
),
edif_hex AS (
    SELECT
        h.i,
        h.j,
        count(e.id) AS edificios,
        round(coalesce(sum(e.area_m2), 0)) AS m2_construidos
    FROM hex h
    JOIN edificios e ON ST_Contains(h.geom, e.punto)
    GROUP BY h.i, h.j
),
rec_hex AS (
    SELECT
        h.i,
        h.j,
        count(DISTINCT r.route_id) AS recorridos_distintos,
        count(DISTINCT r.shape_id) AS shapes_distintos
    FROM hex h
    JOIN recorridos r ON ST_Intersects(h.geom, r.geom)
    GROUP BY h.i, h.j
)
SELECT
    h.i,
    h.j,
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

-- Top 10 celdas críticas: alta densidad de edificios y menor oferta de recorridos
SELECT
    g.i,
    g.j,
    c.comuna AS comuna_centroide,
    g.edificios,
    g.m2_construidos,
    g.recorridos_distintos,
    g.indice_presion
FROM mv_grilla g
LEFT JOIN comunas_cl c ON ST_Contains(c.geom, ST_PointOnSurface(g.geom))
WHERE g.edificios >= 100
ORDER BY g.recorridos_distintos ASC, g.edificios DESC
LIMIT 10;

-- ----------------------------------------------------------------------------
-- PREGUNTA 7 (PROPIA): Intensidad operacional GTFS (stop_times.txt) según
-- jerarquía vial OSM (calles) e infraestructura de refugio (paraderos_osm)
-- ----------------------------------------------------------------------------
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