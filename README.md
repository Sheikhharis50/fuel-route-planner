# Fuel Route Planner API

Django REST Framework API that, given a US start and finish city, returns the driving route as GeoJSON together with the cheapest places to refuel (500-mile range, 10 MPG, tank starts full) and the total fuel cost.

- **Routing:** public [OSRM](https://project-osrm.org/) HTTP API, **one call per request** (zero on cache hit).
- **Geocoding:** local US Census Gazetteer files — no external geocoding calls.
- **Fuel prices:** `data/fuel_stations.json` (6,626 US truck stops with coordinates).

## Setup

```bash
uv sync
uv run python manage.py runserver 8000
```

## Usage

### Curl

```bash
curl -G 'http://127.0.0.1:8000/api/route' \
  --data-urlencode 'start=Chicago, IL' \
  --data-urlencode 'finish=Dallas, TX'
```

### Postman

`Method`: GET
`URL`: `http://127.0.0.1:8000/api/route`
`Query Params`:

- `start=Chicago, IL`
- `finish=Dallas, TX`

### Browser

`URL`: `http://127.0.0.1:8000/api/route?start=Chicago,%20IL&finish=Dallas,%20TX`

Paste the response into <https://geojson.io> to see the map.

### Response

A GeoJSON `FeatureCollection` (coordinates are `[lng, lat]`, WGS84):

| Feature `properties.kind`     | Geometry                | Properties                                                                                                                                                                                                       |
| ----------------------------- | ----------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `route`                       | LineString (simplified) | `distance_miles`, `duration_seconds`                                                                                                                                                                             |
| `start` / `finish`            | Point                   | `city`, `state`, `input`                                                                                                                                                                                         |
| `fuel_stop` (in travel order) | Point                   | `stop_number`, `station_id`, `name`, `address`, `city`, `state`, `price_per_gallon`, `mile_marker`, `leg_miles`, `fuel_on_arrival_gallons`, `gallons`, `cost`, `distance_from_route_miles`, `location_precision` |

Top-level members:

- `summary`: `total_fuel_cost` (USD, fuel bought at stops only — starting tank excluded), `total_distance_miles`, `total_duration_seconds`/`hours`, `total_gallons_purchased`, `total_gallons_consumed`, `number_of_stops`, `average_price_paid_per_gallon`, `vehicle`.
- `meta`: `request_id`, `osrm_calls`, `cache_hit`, `osrm_latency_ms`, `candidate_stations`, `corridor_miles`, `route_points_full`, `route_points_returned`, `resolved` start/finish coordinates, `data_sources`, `timings_ms` (`resolve`, `route`, `corridor`, `optimize`, `simplify`, `serialize`, `total`).

Every response carries an `X-Request-ID` header (a client-supplied one is echoed if it is safe); all log lines for the request include `rid=<id>`.

### Errors

JSON body `{"code", "detail", "request_id", ...}`:

| Status | `code`                    | When                                                                              |
| ------ | ------------------------- | --------------------------------------------------------------------------------- |
| 400    | `invalid_request`         | `start`/`finish` missing or empty                                                 |
| 400    | `invalid_location_format` | not `"City, ST"`                                                                  |
| 400    | `unsupported_location`    | state is not a US state/DC                                                        |
| 400    | `location_not_found`      | city not found in that state (`field` says which)                                 |
| 422    | `route_infeasible`        | a stretch longer than 500 mi without a station (`gap_start_mile`, `gap_end_mile`) |
| 422    | `no_route`                | OSRM finds no drivable route                                                      |
| 502    | `routing_error`           | unexpected OSRM response                                                          |
| 503    | `routing_unavailable`     | OSRM timeout, unreachable, rate-limited, or 5xx                                   |

## How it works

1. Resolve `start`/`finish` against the in-memory gazetteer (places, then county subdivisions).
2. One OSRM `/route` call with `overview=full&geometries=polyline6`; result cached (Django cache) by coordinates.
3. Densify the route, build a KD-tree, and keep stations within `FUEL_CORRIDOR_MILES` of it; each gets a mile marker.
4. Greedy optimal refuelling: at each station, if a cheaper one is within range buy just enough to reach it, otherwise fill up and go to the cheapest in range.
5. Simplify the line (Douglas–Peucker) and return GeoJSON.

## Settings (environment variables)

| Variable                                                    | Default                               |
| ----------------------------------------------------------- | ------------------------------------- |
| `OSRM_BASE_URL`                                             | `https://router.project-osrm.org`     |
| `OSRM_TIMEOUT_S`                                            | `10`                                  |
| `OSRM_USER_AGENT`                                           | `fuel-route-planner/1.0 (Django API)` |
| `ROUTE_CACHE_TTL_S`                                         | `86400`                               |
| `FUEL_CORRIDOR_MILES`                                       | `10`                                  |
| `VEHICLE_RANGE_MILES` / `VEHICLE_MPG`                       | `500` / `10`                          |
| `ROUTE_SIMPLIFY_TOLERANCE_DEG`                              | `0.001`                               |
| `ROUTING_LOG_LEVEL`                                         | `INFO`                                |
| `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS` | dev defaults                          |

## OSRM demo server usage

`router.project-osrm.org` is a free community server: max 1 request/second, no SLA, non-commercial use only. For production, point `OSRM_BASE_URL` at your own OSRM instance and use a shared cache (e.g. Redis) when running multiple workers. Station coordinates are city centroids, so stops are approximate to the city.

## Tests

```bash
uv run pytest
```
