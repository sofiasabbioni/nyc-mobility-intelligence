-- RQ1 - When? Average drop-offs and pickups per hour of the night, weekday vs weekend nights.
SELECT h.night_type,
       h.hour,
       round(sum(h.dropoffs)::numeric / n.nights, 1) AS avg_dropoffs_per_night,
       round(sum(h.pickups)::numeric  / n.nights, 1) AS avg_pickups_per_night
FROM night_zone_hour h
JOIN (SELECT DISTINCT night_type, nights FROM night_zone_metrics) n USING (night_type)
GROUP BY h.night_type, h.hour, h.hour_order, n.nights
ORDER BY h.night_type, h.hour_order;

-- RQ2 - Where? Zones ranked by nightlife score (L / H) on weekend nights.
SELECT zone_id, zone, borough,
       nightlife_score,
       avg_evening_arrivals, avg_late_departures, avg_late_arrivals,
       late_departures, late_arrivals
FROM night_zone_metrics
WHERE night_type = 'weekend'
  AND enough_trips
  AND excluded_reason IS NULL
ORDER BY nightlife_score DESC
LIMIT 20;

-- RQ3 - Weekend vs weekday: how much each zone's score and late departures change on weekends.
SELECT we.zone_id, we.zone, we.borough,
       wd.nightlife_score                         AS weekday_score,
       we.nightlife_score                         AS weekend_score,
       wd.avg_late_departures                     AS weekday_avg_late_departures,
       we.avg_late_departures                     AS weekend_avg_late_departures,
       round(we.avg_late_departures / nullif(wd.avg_late_departures, 0), 2) AS weekend_departure_ratio
FROM night_zone_metrics we
JOIN night_zone_metrics wd ON wd.zone_id = we.zone_id AND wd.night_type = 'weekday'
WHERE we.night_type = 'weekend'
  AND we.enough_trips AND wd.enough_trips
  AND we.excluded_reason IS NULL
ORDER BY weekend_departure_ratio DESC NULLS LAST;

-- Map - one row per zone for the choropleth: change 'weekend' to 'weekday' or 'all' for the toggle.
-- Zones without enough trips get no score, so the map shows them grey instead of a misleading colour.
SELECT zone_id, zone, borough,
       CASE WHEN enough_trips AND excluded_reason IS NULL THEN nightlife_score END AS map_score,
       evening_arrivals, late_departures, late_arrivals, excluded_reason
FROM night_zone_metrics
WHERE night_type = 'weekend';
