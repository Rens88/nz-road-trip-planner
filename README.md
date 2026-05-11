# New Zealand Road Trip Map Generator

Generate an interactive road trip planner with hoverable stops, date ranges, meetups, road-following route segments, itinerary versions, and a timeline view.

## Generate the Map

```bash
python3 scripts/generate_map.py
```

The map is written to:

```text
dist/nz-road-trip-map.html
```

Open that HTML file in a browser. It loads Leaflet map tiles and OSRM driving routes over the internet.

Generated HTML files embed the itinerary data. They are ignored by git by default, so you can share the standalone HTML file directly without accidentally publishing a private trip in the repository.

## Edit the Trip

For public repositories, keep real plans out of git:

- Put shareable demo data in `example_itineraries/`.
- Put private trip files in `personal_itineraries/`.
- `personal_itineraries/*` is ignored by git, except for the placeholder `.gitkeep`.

By default, the generator loads JSON files from `personal_itineraries/`. If that folder has no JSON files, it falls back to `example_itineraries/` and shows a notice in the app.

Update your private JSON files with actual itinerary versions, stops, coordinates, dates, activity tags, meetup names, and notes, then rerun the generator.

The demo file contains two versions:

- `North to South`
- `South to North`

Each version has its own `stops` list:

```json
{
  "id": "north-to-south",
  "name": "North to South",
  "subtitle": "Auckland to Christchurch via both islands",
  "stops": []
}
```

Each stop supports:

```json
{
  "name": "Rotorua",
  "date_range": "Option: Feb 07-09, 2027",
  "lat": -38.1368,
  "lon": 176.2497,
  "notes": "Geothermal parks, lakes, Redwoods.",
  "tag": "sightseeing",
  "meetups": ["Anika", "Jess"],
  "route_note": "Drive south through the Waikato and geothermal belt."
}
```

`route_note` describes the route from that stop to the next stop.
`meetups` is optional and can contain zero, one, or multiple names.
`tag` is optional and controls shared coloring for the activity type. Useful examples include `friends`, `tramping`, `sightseeing`, `camping`, `luxury-bach`, and `travel`.

Flight segments can be shown as dashed lines by adding flight metadata to the stop that starts the segment:

```json
{
  "transport_to_next": "flight",
  "flying_from": "Christchurch (CHC)",
  "flying_via": ["Singapore (SIN)"],
  "flying_to": "Amsterdam (AMS)",
  "flight_path": [
    { "label": "Christchurch (CHC)", "lat": -43.4894, "lon": 172.5322 },
    { "label": "Singapore (SIN)", "lat": 1.3644, "lon": 103.9915 },
    { "label": "Amsterdam (AMS)", "lat": 52.3105, "lon": 4.7683 }
  ]
}
```

`flight_path` is optional. If omitted, the dashed segment is drawn directly between the current stop and the next stop.

You can also explicitly generate from a file or folder:

```bash
python3 scripts/generate_map.py --input personal_itineraries/my_trip.json
python3 scripts/generate_map.py --input example_itineraries
```

## Dependencies

There are no required Python packages. `requirements.txt` contains only comments because the generator uses the Python standard library. The browser still needs internet access for map tiles and routing.

## Routing Notes

The generated map asks the public OSRM routing service for each segment when opened in the browser. If routing is temporarily unavailable, that segment is drawn as a dashed straight-line fallback so the trip still remains visible.
