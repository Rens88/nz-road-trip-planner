#!/usr/bin/env python3
"""Generate an interactive New Zealand road trip map.

The output is a self-contained HTML file except for map tiles, Leaflet assets,
and OSRM route requests, which are loaded by the browser.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ITINERARIES_DIR = ROOT / "example_itineraries"
PERSONAL_ITINERARIES_DIR = ROOT / "personal_itineraries"
DEFAULT_OUTPUT = ROOT / "dist" / "nz-road-trip-map.html"


def itinerary_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    return sorted(path for path in folder.glob("*.json") if path.is_file())


def resolve_input_sources(path: Path | None) -> tuple[list[Path], bool]:
    if path is not None:
        if path.is_dir():
            files = itinerary_files(path)
            if not files:
                raise FileNotFoundError(f"No JSON itinerary files found in {path}")
            return files, path.resolve() == EXAMPLE_ITINERARIES_DIR.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return [path], path.parent.resolve() == EXAMPLE_ITINERARIES_DIR.resolve()

    personal_files = itinerary_files(PERSONAL_ITINERARIES_DIR)
    if personal_files:
        return personal_files, False

    example_files = itinerary_files(EXAMPLE_ITINERARIES_DIR)
    if not example_files:
        raise FileNotFoundError(
            f"No personal itineraries found in {PERSONAL_ITINERARIES_DIR}, "
            f"and no example itineraries found in {EXAMPLE_ITINERARIES_DIR}."
        )
    return example_files, True


def read_trip_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        trip = json.load(handle)
    if not isinstance(trip, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return trip


def merge_trip_files(paths: list[Path]) -> dict[str, Any]:
    if len(paths) == 1:
        return read_trip_file(paths[0])

    first = read_trip_file(paths[0])
    merged: dict[str, Any] = {
        "title": first.get("title", "New Zealand Road Trip"),
        "subtitle": first.get("subtitle", "Combined itinerary files"),
        "map": first.get("map", {}),
        "route_service": first.get("route_service", {}),
        "itineraries": [],
    }

    for path in paths:
        trip = read_trip_file(path)
        if isinstance(trip.get("itineraries"), list):
            merged["itineraries"].extend(trip["itineraries"])
        elif isinstance(trip.get("stops"), list):
            merged["itineraries"].append(
                {
                    "id": path.stem,
                    "name": trip.get("title", path.stem.replace("_", " ").title()),
                    "subtitle": trip.get("subtitle", ""),
                    "stops": trip["stops"],
                }
            )
        else:
            raise ValueError(f"{path} must contain either 'stops' or 'itineraries'.")

    return merged


def normalize_trip(trip: dict[str, Any]) -> dict[str, Any]:

    if "itineraries" not in trip:
        stops = trip.pop("stops", None)
        if not isinstance(stops, list):
            raise ValueError("The itinerary must contain either 'stops' or 'itineraries'.")
        trip["itineraries"] = [
            {
                "id": "version-1",
                "name": trip.get("title", "Version 1"),
                "subtitle": trip.get("subtitle", ""),
                "stops": stops,
            }
        ]

    itineraries = trip.get("itineraries")
    if not isinstance(itineraries, list) or not itineraries:
        raise ValueError("'itineraries' must contain at least one itinerary version.")

    for itinerary_index, itinerary in enumerate(itineraries, start=1):
        if not isinstance(itinerary, dict):
            raise ValueError(f"Itinerary {itinerary_index} must be an object.")

        itinerary.setdefault("id", f"version-{itinerary_index}")
        itinerary.setdefault("name", f"Version {itinerary_index}")
        itinerary.setdefault("subtitle", "")

        stops = itinerary.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise ValueError(f"Itinerary {itinerary_index} must contain at least two stops.")

        for stop_index, stop in enumerate(stops, start=1):
            missing = [key for key in ("name", "date_range", "lat", "lon") if key not in stop]
            if missing:
                joined = ", ".join(missing)
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} is missing required field(s): {joined}"
                )
            stop["lat"] = float(stop["lat"])
            stop["lon"] = float(stop["lon"])
            if not -90 <= stop["lat"] <= 90:
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} has an invalid latitude: {stop['lat']}"
                )
            if not -180 <= stop["lon"] <= 180:
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} has an invalid longitude: {stop['lon']}"
                )

            meetups = stop.get("meetups", [])
            if isinstance(meetups, str):
                meetups = [meetups]
            if meetups is None:
                meetups = []
            if not isinstance(meetups, list):
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} has invalid 'meetups'; use a list of names."
                )
            stop["meetups"] = [str(person) for person in meetups if str(person).strip()]
            stop["tag"] = str(stop.get("tag", "sightseeing")).strip() or "sightseeing"

            has_flight_fields = any(key in stop for key in ("flying_from", "flying_via", "flying_to", "flight_path"))
            if str(stop.get("transport_to_next", "")).strip().lower() == "flight" or has_flight_fields:
                stop["transport_to_next"] = "flight"
                stop["flying_from"] = str(stop.get("flying_from", "")).strip()
                stop["flying_to"] = str(stop.get("flying_to", "")).strip()
                flying_via = stop.get("flying_via", [])
                if isinstance(flying_via, str):
                    flying_via = [flying_via]
                if flying_via is None:
                    flying_via = []
                if not isinstance(flying_via, list):
                    raise ValueError(
                        f"Itinerary {itinerary_index}, stop {stop_index} has invalid 'flying_via'; use a list."
                    )
                stop["flying_via"] = [str(place) for place in flying_via if str(place).strip()]

    trip.setdefault("title", "Road Trip Map")
    trip.setdefault("subtitle", "")
    trip.setdefault("map", {})
    trip.setdefault("route_service", {})
    trip["route_service"].setdefault("url", "https://router.project-osrm.org/route/v1")
    trip["route_service"].setdefault("profile", "driving")
    return trip


def load_trip(path: Path | None) -> dict[str, Any]:
    sources, using_examples = resolve_input_sources(path)
    trip = normalize_trip(merge_trip_files(sources))
    trip["using_example_itineraries"] = using_examples
    trip["source_files"] = [source_label(source) for source in sources]
    return trip


def source_label(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def render_html(trip: dict[str, Any]) -> str:
    trip_json = json.dumps(trip, ensure_ascii=True, indent=2)
    title = html.escape(str(trip.get("title", "Road Trip Map")), quote=True)
    subtitle = html.escape(str(trip.get("subtitle", "")), quote=True)

    document = HTML_TEMPLATE
    document = document.replace("__PAGE_TITLE__", title)
    document = document.replace("__PAGE_SUBTITLE__", subtitle)
    document = document.replace("__TRIP_DATA__", trip_json)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a road trip map HTML file.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=(
            "Path to an itinerary JSON file or folder. "
            "Defaults to personal_itineraries/*.json, then example_itineraries/*.json."
        ),
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Path to output HTML.")
    args = parser.parse_args()

    trip = load_trip(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render_html(trip), encoding="utf-8")
    print(f"Wrote {args.output}")


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>__PAGE_TITLE__</title>
  <link
    rel="stylesheet"
    href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
    integrity="sha256-p4NxAoJBhIINfQ3uyt0o9p7l1vfHMnDQfA8XygWd8B0="
    crossorigin=""
  >
  <style>
    /* Inline fallback for Leaflet's core layout rules. Some local HTML previews
       block external stylesheets while still allowing scripts, which leaves map
       tiles and markers in normal document flow. */
    .leaflet-pane,
    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow,
    .leaflet-tile-container,
    .leaflet-pane > svg,
    .leaflet-pane > canvas,
    .leaflet-zoom-box,
    .leaflet-image-layer,
    .leaflet-layer {
      position: absolute;
      left: 0;
      top: 0;
    }

    .leaflet-pane {
      z-index: 400;
    }

    .leaflet-tile-pane {
      z-index: 200;
    }

    .leaflet-overlay-pane {
      z-index: 400;
    }

    .leaflet-shadow-pane {
      z-index: 500;
    }

    .leaflet-marker-pane {
      z-index: 600;
    }

    .leaflet-tooltip-pane {
      z-index: 650;
    }

    .leaflet-popup-pane {
      z-index: 700;
    }

    .leaflet-map-pane canvas {
      z-index: 100;
    }

    .leaflet-map-pane svg {
      z-index: 200;
    }

    .leaflet-container {
      overflow: hidden;
      background: #d9e0e3;
      outline: 0;
      font-family: inherit;
      -webkit-tap-highlight-color: transparent;
    }

    .leaflet-container img,
    .leaflet-container svg {
      max-width: none !important;
      max-height: none !important;
    }

    .leaflet-tile,
    .leaflet-marker-icon,
    .leaflet-marker-shadow {
      user-select: none;
      -webkit-user-drag: none;
    }

    .leaflet-tile {
      visibility: hidden;
    }

    .leaflet-tile-loaded {
      visibility: inherit;
    }

    .leaflet-zoom-animated {
      transform-origin: 0 0;
    }

    .leaflet-interactive {
      cursor: pointer;
    }

    .leaflet-grab {
      cursor: grab;
    }

    .leaflet-dragging .leaflet-grab {
      cursor: move;
    }

    .leaflet-control {
      position: relative;
      z-index: 800;
      float: left;
      clear: both;
      pointer-events: auto;
    }

    .leaflet-top,
    .leaflet-bottom {
      position: absolute;
      z-index: 1000;
      pointer-events: none;
    }

    .leaflet-top {
      top: 0;
    }

    .leaflet-top.leaflet-right {
      top: 64px;
    }

    .leaflet-right {
      right: 0;
    }

    .leaflet-bottom {
      bottom: 0;
    }

    .leaflet-left {
      left: 0;
    }

    .leaflet-right .leaflet-control {
      float: right;
      margin-right: 10px;
    }

    .leaflet-top .leaflet-control {
      margin-top: 10px;
    }

    .leaflet-bottom .leaflet-control {
      margin-bottom: 10px;
    }

    .leaflet-left .leaflet-control {
      margin-left: 10px;
    }

    .leaflet-bar a {
      display: block;
      width: 30px;
      height: 30px;
      background: #fff;
      border-bottom: 1px solid #ccd3dc;
      color: #18212f;
      font: 700 18px/30px Arial, sans-serif;
      text-align: center;
      text-decoration: none;
    }

    .leaflet-bar a:first-child {
      border-top-left-radius: 8px;
      border-top-right-radius: 8px;
    }

    .leaflet-bar a:last-child {
      border-bottom: 0;
      border-bottom-left-radius: 8px;
      border-bottom-right-radius: 8px;
    }

    .leaflet-bar a:hover {
      background: #f4f7fa;
    }

    .leaflet-control-layers {
      background: #fff;
    }

    .leaflet-control-layers:not(.leaflet-control-layers-expanded) .leaflet-control-layers-list {
      display: none;
    }

    .leaflet-control-layers-toggle {
      position: relative;
      display: block;
      width: 42px;
      height: 38px;
      color: transparent;
      text-decoration: none;
    }

    .leaflet-control-layers-toggle::before {
      content: "Layers";
      position: absolute;
      inset: 0;
      display: grid;
      place-items: center;
      color: #18212f;
      font-size: 0.72rem;
      font-weight: 800;
    }

    .leaflet-control-layers-expanded {
      padding: 8px 10px;
      color: #18212f;
      background: #fff;
    }

    .leaflet-control-layers-selector {
      margin: 0 6px 0 0;
      vertical-align: middle;
    }

    .leaflet-control-attribution {
      margin: 0;
      padding: 3px 7px;
      color: #4f5d70;
      background: rgba(255, 255, 255, 0.84);
      font-size: 11px;
      line-height: 1.4;
    }

    .leaflet-control-attribution a {
      color: #315f9f;
      text-decoration: none;
    }

    .leaflet-popup {
      position: absolute;
      margin-bottom: 20px;
      text-align: center;
    }

    .leaflet-popup-content-wrapper {
      padding: 1px;
      background: #fff;
      text-align: left;
    }

    .leaflet-popup-content {
      min-width: 160px;
      margin: 14px 18px;
      line-height: 1.35;
    }

    .leaflet-popup-tip-container {
      position: absolute;
      left: 50%;
      width: 40px;
      height: 20px;
      margin-left: -20px;
      overflow: hidden;
      pointer-events: none;
    }

    .leaflet-popup-tip {
      width: 17px;
      height: 17px;
      margin: -10px auto 0;
      background: #fff;
      transform: rotate(45deg);
    }

    .leaflet-popup-close-button {
      position: absolute;
      top: 4px;
      right: 6px;
      width: 22px;
      height: 22px;
      color: #4f5d70;
      font: 700 18px/22px Arial, sans-serif;
      text-align: center;
      text-decoration: none;
    }

    .leaflet-tooltip {
      position: absolute;
      padding: 7px 9px;
      background: #fff;
      white-space: nowrap;
      pointer-events: none;
    }

    .leaflet-tooltip-top {
      margin-top: -6px;
    }

    :root {
      color-scheme: light;
      --ink: #18212f;
      --muted: #657080;
      --panel: #ffffff;
      --line: #d9dee7;
      --accent: #007a78;
      --accent-2: #315f9f;
      --accent-3: #a4481d;
      --shadow: 0 20px 50px rgba(18, 25, 38, 0.20);
    }

    * {
      box-sizing: border-box;
    }

    html,
    body {
      height: 100%;
      margin: 0;
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      letter-spacing: 0;
    }

    body {
      overflow: hidden;
      background: #eef2f5;
    }

    #app {
      position: relative;
      height: 100%;
      width: 100%;
    }

    #map {
      position: absolute;
      inset: 0;
      z-index: 1;
    }

    #app.view-timeline #map,
    #app.view-timeline .panel {
      visibility: hidden;
      pointer-events: none;
    }

    .panel {
      position: absolute;
      z-index: 500;
      top: 18px;
      left: 18px;
      width: min(380px, calc(100vw - 36px));
      max-height: calc(100vh - 36px);
      display: grid;
      grid-template-rows: auto auto 1fr auto;
      gap: 14px;
      padding: 18px;
      overflow: hidden;
      background: rgba(255, 255, 255, 0.94);
      border: 1px solid rgba(196, 205, 217, 0.92);
      border-radius: 8px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }

    .eyebrow {
      margin: 0 0 6px;
      color: var(--accent);
      font-size: 0.76rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    h1 {
      margin: 0;
      font-size: 2rem;
      line-height: 1.02;
      letter-spacing: 0;
    }

    .subtitle {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.96rem;
      line-height: 1.45;
    }

    .section-label {
      display: block;
      margin: 0 0 7px;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    .version-tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 7px;
    }

    .version-button,
    .view-button {
      min-height: 34px;
      padding: 0 11px;
      border: 1px solid rgba(139, 151, 167, 0.72);
      border-radius: 8px;
      color: var(--ink);
      background: #ffffff;
      font: inherit;
      font-size: 0.84rem;
      font-weight: 800;
      white-space: nowrap;
      cursor: pointer;
    }

    .version-button:hover,
    .version-button:focus,
    .view-button:hover,
    .view-button:focus {
      border-color: rgba(0, 122, 120, 0.7);
      outline: none;
    }

    .version-button.is-active,
    .view-button.is-active {
      color: #ffffff;
      border-color: var(--accent);
      background: var(--accent);
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 8px;
    }

    .stat {
      min-height: 66px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f7f9fb;
    }

    .stat span {
      display: block;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 700;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    .stat strong {
      display: block;
      margin-top: 7px;
      font-size: 1rem;
      line-height: 1.12;
      word-break: break-word;
    }

    .stop-list {
      min-height: 0;
      margin: 0;
      padding: 0 4px 0 0;
      overflow: auto;
      list-style: none;
    }

    .stop-row {
      display: grid;
      grid-template-columns: 30px minmax(0, 1fr);
      gap: 10px;
      margin: 0 0 8px;
      padding: 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      cursor: pointer;
    }

    .stop-row:hover,
    .stop-row:focus {
      border-color: rgba(0, 122, 120, 0.55);
      outline: none;
      box-shadow: 0 0 0 3px rgba(0, 122, 120, 0.12);
    }

    .stop-number {
      width: 28px;
      height: 28px;
      display: inline-grid;
      place-items: center;
      border-radius: 999px;
      color: #ffffff;
      background: var(--accent-2);
      font-size: 0.82rem;
      font-weight: 800;
    }

    .stop-name {
      margin: 0;
      font-size: 0.95rem;
      font-weight: 800;
      line-height: 1.2;
    }

    .stop-date {
      margin: 4px 0 0;
      color: var(--accent-3);
      font-size: 0.82rem;
      font-weight: 700;
      line-height: 1.25;
    }

    .stop-note {
      margin: 6px 0 0;
      color: var(--muted);
      font-size: 0.82rem;
      line-height: 1.35;
    }

    .meetups {
      margin: 7px 0 0;
      color: #315f9f;
      font-size: 0.79rem;
      font-weight: 750;
      line-height: 1.35;
    }

    .flight-info {
      margin: 7px 0 0;
      color: #4f5d70;
      font-size: 0.78rem;
      font-weight: 750;
      line-height: 1.35;
    }

    .tag-pill {
      display: inline-block;
      margin: 7px 0 0;
      padding: 3px 7px;
      border-radius: 999px;
      color: #ffffff;
      background: var(--stop-color);
      font-size: 0.72rem;
      font-weight: 800;
      line-height: 1.2;
    }

    .example-notice {
      padding: 10px 12px;
      border: 1px solid rgba(0, 122, 120, 0.28);
      border-radius: 8px;
      color: #24515d;
      background: #edf8f6;
      font-size: 0.82rem;
      font-weight: 700;
      line-height: 1.35;
    }

    .status {
      min-height: 20px;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.35;
    }

    .view-switch {
      position: absolute;
      z-index: 550;
      top: 18px;
      right: 18px;
      display: flex;
      gap: 6px;
      padding: 5px;
      border: 1px solid rgba(196, 205, 217, 0.92);
      border-radius: 8px;
      background: rgba(255, 255, 255, 0.94);
      box-shadow: 0 8px 26px rgba(18, 25, 38, 0.18);
      backdrop-filter: blur(14px);
    }

    .view-switch .view-button {
      min-height: 34px;
      background: #ffffff;
    }

    .view-switch .view-button.is-active {
      color: #ffffff;
      border-color: var(--accent);
      background: var(--accent);
    }

    .leaflet-control-layers,
    .leaflet-control-zoom {
      border: 0 !important;
      box-shadow: 0 8px 26px rgba(18, 25, 38, 0.18) !important;
    }

    .leaflet-control-layers {
      border-radius: 8px !important;
      overflow: hidden;
    }

    .leaflet-control-zoom a {
      color: var(--ink) !important;
    }

    .stop-marker {
      width: 32px;
      height: 32px;
      display: grid;
      place-items: center;
      border: 3px solid #ffffff;
      border-radius: 999px;
      color: #ffffff;
      background: var(--stop-color, #315f9f);
      box-shadow: 0 5px 16px rgba(18, 25, 38, 0.30);
      font-size: 0.82rem;
      font-weight: 900;
    }

    .stop-marker span {
      transform: translateY(-1px);
    }

    .stop-tooltip,
    .segment-tooltip {
      border: 0;
      border-radius: 8px;
      color: var(--ink);
      box-shadow: 0 10px 30px rgba(18, 25, 38, 0.22);
      font-size: 0.82rem;
      line-height: 1.35;
    }

    .leaflet-popup-content-wrapper {
      border-radius: 8px;
      box-shadow: 0 14px 34px rgba(18, 25, 38, 0.24);
    }

    .popup-title {
      margin: 0 0 4px;
      font-size: 1rem;
      font-weight: 800;
    }

    .popup-date {
      margin: 0 0 8px;
      color: var(--accent-3);
      font-size: 0.84rem;
      font-weight: 800;
    }

    .popup-note {
      margin: 0;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.4;
    }

    .timeline-view {
      position: absolute;
      inset: 0;
      z-index: 450;
      display: none;
      padding: 88px 34px 34px;
      overflow: auto;
      background: #eef2f5;
    }

    #app.view-timeline .timeline-view {
      display: block;
    }

    .timeline-shell {
      max-width: 1180px;
      min-height: calc(100vh - 122px);
      padding: 26px;
      background: rgba(255, 255, 255, 0.96);
      border: 1px solid rgba(196, 205, 217, 0.92);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .timeline-header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(250px, auto);
      gap: 18px;
      align-items: start;
      margin-bottom: 22px;
    }

    .timeline-header h2 {
      margin: 0;
      font-size: 1.55rem;
      line-height: 1.1;
    }

    .timeline-header p {
      margin: 7px 0 0;
      color: var(--muted);
      line-height: 1.4;
    }

    .timeline-controls {
      display: grid;
      gap: 14px;
      justify-items: end;
    }

    .timeline-scale {
      width: min(250px, 100%);
    }

    .timeline-scale input {
      width: 100%;
      accent-color: var(--accent);
    }

    .timeline-scale-labels {
      display: flex;
      justify-content: space-between;
      color: var(--muted);
      font-size: 0.74rem;
      font-weight: 700;
    }

    .timeline-track {
      position: relative;
      min-height: 420px;
      overflow: visible;
      padding: 8px 2px 34px;
    }

    .timeline-axis-layout {
      position: relative;
      display: grid;
      grid-template-columns: 118px minmax(0, 1fr);
      gap: 20px;
      min-height: var(--timeline-height);
    }

    .timeline-axis {
      position: relative;
      min-height: var(--timeline-height);
      border-right: 3px solid #c7d0dc;
    }

    .timeline-date-mark {
      position: absolute;
      right: 14px;
      transform: translateY(-50%);
      color: transparent;
      font-size: 0.78rem;
      font-weight: 800;
      white-space: nowrap;
    }

    .timeline-date-mark.has-label {
      color: var(--muted);
    }

    .timeline-date-mark::after {
      content: "";
      position: absolute;
      top: 50%;
      right: -17px;
      width: 9px;
      height: 2px;
      background: #9caabd;
    }

    .timeline-date-mark.has-label::after {
      width: 9px;
      background: #9caabd;
    }

    .timeline-blocks {
      position: relative;
      min-height: var(--timeline-height);
    }

    .timeline-stop {
      position: absolute;
      left: 0;
      width: min(540px, 100%);
      min-height: 48px;
      padding: 12px 14px;
      border: 1px solid var(--line);
      border-left: 8px solid var(--stop-color);
      border-radius: 8px;
      background: #ffffff;
      box-shadow: 0 8px 20px rgba(18, 25, 38, 0.08);
      overflow: hidden;
    }

    .timeline-dot {
      position: absolute;
      top: 12px;
      left: -50px;
      width: 30px;
      height: 30px;
      display: grid;
      place-items: center;
      border: 3px solid #ffffff;
      border-radius: 999px;
      color: #ffffff;
      background: var(--stop-color);
      box-shadow: 0 5px 16px rgba(18, 25, 38, 0.25);
      font-size: 0.82rem;
      font-weight: 900;
    }

    .timeline-stop h3 {
      margin: 0;
      font-size: 0.96rem;
      line-height: 1.2;
    }

    .timeline-date {
      margin: 7px 0 0;
      color: var(--accent-3);
      font-size: 0.82rem;
      font-weight: 800;
      line-height: 1.25;
    }

    .timeline-note {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.35;
    }

    .timeline-view[data-scale="0"] .timeline-stop {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) auto;
      gap: 8px;
      align-items: center;
      padding: 4px 10px;
      box-shadow: 0 4px 12px rgba(18, 25, 38, 0.06);
    }

    .timeline-view[data-scale="0"] .timeline-stop h3 {
      font-size: 0.84rem;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    .timeline-view[data-scale="0"] .timeline-date {
      margin: 0;
      font-size: 0.72rem;
      text-align: right;
      white-space: nowrap;
    }

    .timeline-view[data-scale="1"] .timeline-note {
      display: none;
    }

    .timeline-view[data-scale="0"] .meetups,
    .timeline-view[data-scale="0"] .timeline-note,
    .timeline-view[data-scale="0"] .tag-pill,
    .timeline-view[data-scale="0"] .flight-info {
      display: none;
    }

    @media (max-width: 720px) {
      body {
        overflow: hidden;
      }

      .panel {
        top: auto;
        bottom: 12px;
        left: 12px;
        width: calc(100vw - 24px);
        max-height: min(55vh, 470px);
        padding: 14px;
      }

      .stats {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }

      .stat {
        min-height: 58px;
        padding: 8px;
      }

      .stat strong {
        font-size: 0.9rem;
      }

      h1 {
        font-size: 1.55rem;
      }

      .leaflet-top.leaflet-right {
        top: 62px;
      }

      .view-switch {
        top: 12px;
        right: 12px;
      }

      .view-button {
        padding: 0 9px;
        font-size: 0.78rem;
      }

      .timeline-view {
        padding: 72px 12px 12px;
      }

      .timeline-shell {
        padding: 16px;
      }

      .timeline-header {
        grid-template-columns: 1fr;
      }

      .timeline-controls {
        justify-items: stretch;
      }

      .timeline-axis-layout {
        grid-template-columns: 82px minmax(0, 1fr);
        gap: 14px;
      }

      .timeline-date-mark {
        right: 10px;
        font-size: 0.7rem;
      }

      .timeline-date-mark::after {
        right: -13px;
        width: 7px;
      }

      .timeline-dot {
        left: -40px;
      }
    }
  </style>
</head>
<body>
  <div id="app">
    <div id="map" aria-label="__PAGE_TITLE__"></div>
    <aside class="panel" id="panel">
      <header>
        <p class="eyebrow">Road trip</p>
        <h1 id="panelTitle">__PAGE_TITLE__</h1>
        <p class="subtitle" id="panelSubtitle">__PAGE_SUBTITLE__</p>
      </header>
      <section aria-label="Itinerary versions">
        <span class="section-label">Version</span>
        <div class="version-tabs" id="versionTabs"></div>
      </section>
      <div class="example-notice" id="exampleNotice" hidden>
        Example itineraries are shown. Add your own JSON files to personal_itineraries/ and rerun the generator.
      </div>
      <section class="stats" aria-label="Trip totals">
        <div class="stat"><span>Stops</span><strong id="stopCount">-</strong></div>
        <div class="stat"><span>Distance</span><strong id="distanceTotal">Routing</strong></div>
        <div class="stat"><span>Drive time</span><strong id="durationTotal">Routing</strong></div>
      </section>
      <ol class="stop-list" id="stopList"></ol>
      <div class="status" id="routeStatus">Routing road segments...</div>
    </aside>
    <section class="timeline-view" id="timelineView" aria-label="Timeline view">
      <div class="timeline-shell">
        <header class="timeline-header">
          <div>
            <p class="eyebrow">Timeline</p>
            <h2 id="timelineTitle">__PAGE_TITLE__</h2>
            <p id="timelineSubtitle">__PAGE_SUBTITLE__</p>
            <div class="example-notice" id="timelineExampleNotice" hidden>
              Example itineraries are shown. Add your own JSON files to personal_itineraries/ and rerun the generator.
            </div>
          </div>
          <div class="timeline-controls">
            <span class="section-label">Version</span>
            <div class="version-tabs" id="timelineVersionTabs"></div>
            <div class="timeline-scale">
              <span class="section-label">Scale</span>
              <input id="timelineScale" type="range" min="0" max="2" step="1" value="0" aria-label="Timeline scale">
              <div class="timeline-scale-labels">
                <span>Compact</span>
                <span>Detailed</span>
              </div>
            </div>
          </div>
        </header>
        <div class="timeline-track" id="timelineTrack"></div>
      </div>
    </section>
    <nav class="view-switch" aria-label="View mode">
      <button class="view-button is-active" id="mapViewButton" type="button" aria-pressed="true">Map view</button>
      <button class="view-button" id="timelineViewButton" type="button" aria-pressed="false">Timeline view</button>
    </nav>
  </div>

  <script
    src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
    integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
    crossorigin=""
  ></script>
  <script>
    const trip = __TRIP_DATA__;

    const segmentColors = [
      "#007a78",
      "#315f9f",
      "#a4481d",
      "#6f5a9c",
      "#617000",
      "#b83280"
    ];
    const tagColors = {
      friends: "#315f9f",
      tramping: "#617000",
      sightseeing: "#a4481d",
      camping: "#007a78",
      "luxury-bach": "#6f5a9c",
      travel: "#4f5d70"
    };
    const timelineScales = [
      { pxPerDay: 52 },
      { pxPerDay: 96 },
      { pxPerDay: 148 }
    ];
    const monthLookup = {
      jan: 0,
      january: 0,
      feb: 1,
      february: 1,
      mar: 2,
      march: 2,
      apr: 3,
      april: 3,
      may: 4,
      jun: 5,
      june: 5,
      jul: 6,
      july: 6,
      aug: 7,
      august: 7,
      sep: 8,
      sept: 8,
      september: 8,
      oct: 9,
      october: 9,
      nov: 10,
      november: 10,
      dec: 11,
      december: 11
    };
    const monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

    const map = L.map("map", {
      preferCanvas: true,
      zoomControl: false
    }).setView(
      trip.map?.center || [-41.25, 172.5],
      trip.map?.zoom || 6
    );

    const baseLayers = {
      "Clean": L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        maxZoom: 20,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
      }),
      "OpenStreetMap": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      }),
      "Satellite": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}", {
        maxZoom: 19,
        attribution: "Tiles &copy; Esri"
      }),
      "Terrain": L.tileLayer("https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png", {
        maxZoom: 17,
        attribution: 'Map data: &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>, SRTM | Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a>'
      })
    };

    baseLayers.Clean.addTo(map);
    L.control.zoom({ position: "topright" }).addTo(map);

    const routeLayer = L.layerGroup().addTo(map);
    const stopLayer = L.layerGroup().addTo(map);
    L.control.layers(baseLayers, {
      "Route": routeLayer,
      "Stops": stopLayer
    }, {
      position: "bottomright",
      collapsed: true
    }).addTo(map);

    const itineraries = Array.isArray(trip.itineraries) && trip.itineraries.length
      ? trip.itineraries
      : [{
        id: "version-1",
        name: trip.title || "Version 1",
        subtitle: trip.subtitle || "",
        stops: trip.stops || []
      }];
    const routeCache = new Map();
    let activeItineraryIndex = 0;
    let activeRouteRun = 0;
    let activeBounds = L.latLngBounds([]);
    let markers = [];

    const app = document.getElementById("app");
    const panelTitle = document.getElementById("panelTitle");
    const panelSubtitle = document.getElementById("panelSubtitle");
    const versionTabs = document.getElementById("versionTabs");
    const timelineVersionTabs = document.getElementById("timelineVersionTabs");
    const timelineTitle = document.getElementById("timelineTitle");
    const timelineSubtitle = document.getElementById("timelineSubtitle");
    const exampleNotice = document.getElementById("exampleNotice");
    const timelineExampleNotice = document.getElementById("timelineExampleNotice");
    const timelineTrack = document.getElementById("timelineTrack");
    const timelineView = document.getElementById("timelineView");
    const timelineScale = document.getElementById("timelineScale");
    const stopList = document.getElementById("stopList");
    const stopCount = document.getElementById("stopCount");
    const distanceTotal = document.getElementById("distanceTotal");
    const durationTotal = document.getElementById("durationTotal");
    const routeStatus = document.getElementById("routeStatus");
    const mapViewButton = document.getElementById("mapViewButton");
    const timelineViewButton = document.getElementById("timelineViewButton");

    mapViewButton.addEventListener("click", () => setViewMode("map"));
    timelineViewButton.addEventListener("click", () => setViewMode("timeline"));
    timelineScale.addEventListener("input", () => renderTimeline(getActiveItinerary()));
    exampleNotice.hidden = !trip.using_example_itineraries;
    timelineExampleNotice.hidden = !trip.using_example_itineraries;
    window.addEventListener("load", refreshMapLayout);
    window.addEventListener("resize", () => map.invalidateSize({ animate: false }));

    renderVersionTabs();
    renderActiveItinerary();
    setViewMode("map");

    function getActiveItinerary() {
      return itineraries[activeItineraryIndex];
    }

    function renderVersionTabs() {
      [versionTabs, timelineVersionTabs].forEach((container) => {
        container.innerHTML = "";
        itineraries.forEach((itinerary, index) => {
          const button = document.createElement("button");
          button.type = "button";
          button.className = `version-button${index === activeItineraryIndex ? " is-active" : ""}`;
          button.textContent = itinerary.name || `Version ${index + 1}`;
          button.setAttribute("aria-pressed", String(index === activeItineraryIndex));
          button.addEventListener("click", () => {
            activeItineraryIndex = index;
            renderVersionTabs();
            renderActiveItinerary();
          });
          container.appendChild(button);
        });
      });
    }

    function renderActiveItinerary() {
      const itinerary = getActiveItinerary();
      const stops = itinerary.stops || [];
      activeRouteRun += 1;
      activeBounds = L.latLngBounds([]);
      markers = [];

      stopLayer.clearLayers();
      routeLayer.clearLayers();
      stopList.innerHTML = "";
      map.closePopup();

      panelTitle.textContent = trip.title || "Road Trip Map";
      panelSubtitle.textContent = itinerary.subtitle || trip.subtitle || itinerary.name || "";
      timelineTitle.textContent = itinerary.name || trip.title || "Timeline";
      timelineSubtitle.textContent = itinerary.subtitle || trip.subtitle || "";
      stopCount.textContent = String(stops.length);
      distanceTotal.textContent = "Routing";
      durationTotal.textContent = "Routing";
      routeStatus.textContent = stops.length > 1 ? "Routing road segments..." : "Add at least two stops.";

      stops.forEach((stop, index) => renderStop(stop, index));
      renderTimeline(itinerary);
      fitTripBounds();
      requestAnimationFrame(refreshMapLayout);

      if (stops.length > 1) {
        drawRoutes(itinerary, activeRouteRun);
      }
    }

    function renderStop(stop, index) {
      const latLng = [stop.lat, stop.lon];
      const color = stopColor(stop);
      activeBounds.extend(latLng);

      const marker = L.marker(latLng, {
        title: stop.name,
        icon: L.divIcon({
          className: "stop-marker",
          html: `<span>${index + 1}</span>`,
          iconSize: [32, 32],
          iconAnchor: [16, 16],
          popupAnchor: [0, -16]
        })
      }).addTo(stopLayer);

      const markerElement = marker.getElement();
      if (markerElement) {
        markerElement.style.setProperty("--stop-color", color);
      }

      marker.bindTooltip(stopTooltip(stop), {
        direction: "top",
        sticky: true,
        opacity: 0.96,
        className: "stop-tooltip"
      });
      marker.bindPopup(stopPopup(stop));
      markers.push(marker);

      const row = document.createElement("li");
      row.className = "stop-row";
      row.tabIndex = 0;
      row.innerHTML = `
        <span class="stop-number" style="background: ${color}">${index + 1}</span>
        <span>
          <p class="stop-name">${escapeHTML(stop.name)}</p>
          <p class="stop-date">${escapeHTML(stop.date_range)}</p>
          ${tagHTML(stop)}
          ${stop.notes ? `<p class="stop-note">${escapeHTML(stop.notes)}</p>` : ""}
          ${flightHTML(stop)}
          ${meetupsHTML(stop)}
        </span>
      `;
      row.addEventListener("mouseenter", () => marker.openTooltip());
      row.addEventListener("mouseleave", () => marker.closeTooltip());
      row.addEventListener("focus", () => marker.openTooltip());
      row.addEventListener("blur", () => marker.closeTooltip());
      row.addEventListener("click", () => {
        setViewMode("map");
        map.flyTo(latLng, Math.max(map.getZoom(), 10), { duration: 0.65 });
        marker.openPopup();
      });
      row.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          row.click();
        }
      });
      stopList.appendChild(row);
    }

    function renderTimeline(itinerary) {
      const stops = itinerary.stops || [];
      const scaleIndex = Number(timelineScale.value || 1);
      const scale = timelineScales[scaleIndex] || timelineScales[1];
      const items = stops.map((stop, index) => {
        const range = parseDateRange(stop.date_range);
        return {
          stop,
          index,
          range
        };
      });
      const datedItems = items.filter((item) => item.range.start && item.range.end);
      const timelineStart = datedItems.reduce(
        (earliest, item) => earliest && earliest < item.range.start ? earliest : item.range.start,
        datedItems[0]?.range.start || null
      );
      const timelineEnd = datedItems.reduce(
        (latest, item) => latest && latest > item.range.end ? latest : item.range.end,
        datedItems[0]?.range.end || null
      );
      const boundaryDates = new Set();
      datedItems.forEach((item) => {
        boundaryDates.add(dateKey(item.range.start));
        boundaryDates.add(dateKey(item.range.end));
      });
      const timelineDays = timelineStart && timelineEnd ? Math.max(1, daysBetween(timelineStart, timelineEnd)) : 1;
      const timelineHeight = Math.max(320, timelineDays * scale.pxPerDay);
      const marks = timelineStart && timelineEnd
        ? dateTicks(timelineStart, timelineEnd).map((date) => ({
          top: daysBetween(timelineStart, date) * scale.pxPerDay,
          label: boundaryDates.has(dateKey(date)) ? formatAxisDate(date) : ""
        }))
        : items.map((item, index) => ({
          top: index * scale.pxPerDay,
          label: item.stop.date_range
        }));

      timelineView.dataset.scale = String(scaleIndex);
      timelineTrack.innerHTML = `
        <div class="timeline-axis-layout" style="--timeline-height: ${timelineHeight}px">
          <div class="timeline-axis">
            ${marks.map((mark) => `
              <span class="timeline-date-mark${mark.label ? " has-label" : ""}" style="top: ${mark.top}px">${escapeHTML(mark.label)}</span>
            `).join("")}
          </div>
          <div class="timeline-blocks">
            ${items.map((item) => {
              const top = timelineStart && item.range.start
                ? daysBetween(timelineStart, item.range.start) * scale.pxPerDay
                : item.index * scale.pxPerDay;
              const height = Math.max(scale.pxPerDay, item.range.durationDays * scale.pxPerDay);
              return `
              <article class="timeline-stop" style="--stop-color: ${stopColor(item.stop)}; top: ${top}px; height: ${height}px">
                <span class="timeline-dot">${item.index + 1}</span>
                <h3>${escapeHTML(item.stop.name)}</h3>
                <p class="timeline-date">${escapeHTML(item.range.label || item.stop.date_range)}</p>
                ${tagHTML(item.stop)}
                ${flightHTML(item.stop)}
                ${meetupsHTML(item.stop)}
                ${item.stop.notes ? `<p class="timeline-note">${escapeHTML(item.stop.notes)}</p>` : ""}
              </article>
            `;}).join("")}
          </div>
        </div>
      `;
    }

    function setViewMode(mode) {
      const showTimeline = mode === "timeline";
      app.classList.toggle("view-map", !showTimeline);
      app.classList.toggle("view-timeline", showTimeline);
      mapViewButton.classList.toggle("is-active", !showTimeline);
      timelineViewButton.classList.toggle("is-active", showTimeline);
      mapViewButton.setAttribute("aria-pressed", String(!showTimeline));
      timelineViewButton.setAttribute("aria-pressed", String(showTimeline));
      if (!showTimeline) {
        requestAnimationFrame(refreshMapLayout);
      }
    }

    async function drawRoutes(itinerary, routeRun) {
      const stops = itinerary.stops || [];
      let totalMeters = 0;
      let totalSeconds = 0;
      let routed = 0;
      let fallback = 0;
      let flights = 0;

      for (let index = 0; index < stops.length - 1; index += 1) {
        if (routeRun !== activeRouteRun) {
          return;
        }

        const from = stops[index];
        const to = stops[index + 1];
        const color = stopColor(from);

        if (isFlightSegment(from)) {
          drawFlightSegment(from, to, color);
          flights += 1;
          routeStatus.textContent = routeStatusText(routed, fallback, flights, stops.length - 1);
          continue;
        }

        try {
          const route = await fetchSegment(from, to);
          if (routeRun !== activeRouteRun) {
            return;
          }
          const latLngs = route.geometry.coordinates.map(([lon, lat]) => [lat, lon]);
          drawSegmentHalo(latLngs);
          const line = L.polyline(latLngs, {
            color,
            weight: 5,
            opacity: 0.92,
            lineCap: "round",
            lineJoin: "round"
          }).addTo(routeLayer);

          line.bindTooltip(segmentTooltip(from, to, route.distance, route.duration, false), {
            sticky: true,
            className: "segment-tooltip"
          });

          totalMeters += route.distance;
          totalSeconds += route.duration;
          routed += 1;
        } catch (error) {
          if (routeRun !== activeRouteRun) {
            return;
          }
          const latLngs = [[from.lat, from.lon], [to.lat, to.lon]];
          const line = L.polyline(latLngs, {
            color,
            weight: 4,
            opacity: 0.76,
            dashArray: "7 9",
            lineCap: "round"
          }).addTo(routeLayer);

          line.bindTooltip(segmentTooltip(from, to, null, null, true), {
            sticky: true,
            className: "segment-tooltip"
          });
          fallback += 1;
          console.warn(`Route fallback for ${from.name} to ${to.name}`, error);
        }

        distanceTotal.textContent = totalMeters > 0 ? formatDistance(totalMeters) : "Routing";
        durationTotal.textContent = totalSeconds > 0 ? formatDuration(totalSeconds) : "Routing";
        routeStatus.textContent = routeStatusText(routed, fallback, flights, stops.length - 1);
      }
    }

    async function fetchSegment(from, to) {
      const baseUrl = (trip.route_service?.url || "https://router.project-osrm.org/route/v1").replace(/\\/$/, "");
      const profile = trip.route_service?.profile || "driving";
      const coords = `${from.lon},${from.lat};${to.lon},${to.lat}`;
      const cacheKey = `${profile}:${coords}`;
      if (routeCache.has(cacheKey)) {
        return routeCache.get(cacheKey);
      }

      const params = new URLSearchParams({
        overview: "full",
        geometries: "geojson",
        steps: "false",
        alternatives: "false"
      });

      const routeRequest = fetch(`${baseUrl}/${profile}/${coords}?${params}`)
        .then((response) => {
          if (!response.ok) {
            throw new Error(`OSRM HTTP ${response.status}`);
          }
          return response.json();
        })
        .then((payload) => {
          if (payload.code !== "Ok" || !payload.routes?.length) {
            throw new Error(payload.message || payload.code || "No route returned");
          }
          return payload.routes[0];
        });

      routeCache.set(cacheKey, routeRequest);
      return routeRequest;
    }

    function drawSegmentHalo(latLngs) {
      L.polyline(latLngs, {
        color: "#ffffff",
        weight: 9,
        opacity: 0.76,
        lineCap: "round",
        lineJoin: "round"
      }).addTo(routeLayer);
    }

    function drawFlightSegment(from, to, color) {
      const latLngs = flightLatLngs(from, to);
      L.polyline(latLngs, {
        color: "#ffffff",
        weight: 8,
        opacity: 0.72,
        dashArray: "8 10",
        lineCap: "round",
        lineJoin: "round"
      }).addTo(routeLayer);

      const line = L.polyline(latLngs, {
        color,
        weight: 4,
        opacity: 0.9,
        dashArray: "8 10",
        lineCap: "round",
        lineJoin: "round"
      }).addTo(routeLayer);

      line.bindTooltip(segmentTooltip(from, to, null, null, false), {
        sticky: true,
        className: "segment-tooltip"
      });
    }

    function refreshMapLayout() {
      map.invalidateSize({ animate: false });
      fitTripBounds();
    }

    function fitTripBounds() {
      if (!activeBounds.isValid()) {
        return;
      }

      const panelVisible = app.classList.contains("view-map");
      const isNarrow = window.matchMedia("(max-width: 720px)").matches;
      const options = {
        maxZoom: 8,
        animate: false,
        paddingTopLeft: panelVisible && !isNarrow ? [430, 48] : [48, 48],
        paddingBottomRight: panelVisible && isNarrow ? [48, 320] : [48, 48]
      };

      map.fitBounds(activeBounds.pad(0.08), options);
    }

    function stopTooltip(stop) {
      const meetups = meetupsText(stop);
      const flight = flightText(stop);
      return `
        <strong>${escapeHTML(stop.name)}</strong><br>
        ${escapeHTML(stop.date_range)}
        ${flight ? `<br>${escapeHTML(flight)}` : ""}
        ${meetups ? `<br>Meet: ${escapeHTML(meetups)}` : ""}
      `;
    }

    function stopPopup(stop) {
      return `
        <p class="popup-title">${escapeHTML(stop.name)}</p>
        <p class="popup-date">${escapeHTML(stop.date_range)}</p>
        ${stop.notes ? `<p class="popup-note">${escapeHTML(stop.notes)}</p>` : ""}
        ${flightHTML(stop)}
        ${meetupsHTML(stop)}
      `;
    }

    function segmentTooltip(from, to, distance, duration, isFallback) {
      const routeNote = from.route_note ? `<br>${escapeHTML(from.route_note)}` : "";
      const flight = flightText(from);
      const metrics = flight
        ? `<br>${escapeHTML(flight)}`
        : distance && duration
        ? `<br>${formatDistance(distance)} - ${formatDuration(duration)}`
        : "<br>Straight-line fallback";
      return `
        <strong>${escapeHTML(from.name)} to ${escapeHTML(to.name)}</strong>
        <br>${escapeHTML(from.date_range)} -> ${escapeHTML(to.date_range)}
        ${metrics}
        ${routeNote}
      `;
    }

    function stopColor(stop) {
      const tag = normalizeTag(stop?.tag);
      return tagColors[tag] || segmentColors[hashString(tag) % segmentColors.length];
    }

    function normalizeTag(tag) {
      return String(tag || "sightseeing")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "sightseeing";
    }

    function tagLabel(stop) {
      return normalizeTag(stop?.tag);
    }

    function tagHTML(stop) {
      return `<p class="tag-pill" style="--stop-color: ${stopColor(stop)}">${escapeHTML(tagLabel(stop))}</p>`;
    }

    function isFlightSegment(stop) {
      return String(stop?.transport_to_next || "").toLowerCase() === "flight";
    }

    function flightText(stop) {
      if (!isFlightSegment(stop)) {
        return "";
      }

      const parts = [];
      if (stop.flying_from) {
        parts.push(`from ${stop.flying_from}`);
      }
      if (Array.isArray(stop.flying_via) && stop.flying_via.length) {
        parts.push(`via ${stop.flying_via.join(", ")}`);
      }
      if (stop.flying_to) {
        parts.push(`to ${stop.flying_to}`);
      }
      return parts.length ? `Flight ${parts.join(" ")}` : "Flight";
    }

    function flightHTML(stop) {
      const flight = flightText(stop);
      return flight ? `<p class="flight-info">${escapeHTML(flight)}</p>` : "";
    }

    function flightLatLngs(from, to) {
      const points = Array.isArray(from.flight_path) ? from.flight_path : [];
      const latLngs = points
        .map((point) => [Number(point.lat), Number(point.lon)])
        .filter(([lat, lon]) => Number.isFinite(lat) && Number.isFinite(lon));
      return latLngs.length >= 2 ? latLngs : [[from.lat, from.lon], [to.lat, to.lon]];
    }

    function hashString(value) {
      let hash = 0;
      for (const char of String(value)) {
        hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
      }
      return Math.abs(hash);
    }

    function parseDateRange(dateRange) {
      const fallback = {
        start: null,
        end: null,
        durationDays: 1,
        label: String(dateRange || "")
      };
      const raw = String(dateRange || "").replace(/^[^:]+:\\s*/, "").trim();
      const yearMatch = raw.match(/(\\d{4})/);
      if (!yearMatch) {
        return fallback;
      }

      const year = Number(yearMatch[1]);
      const rangeText = raw.replace(/,?\\s*\\d{4}/, "").trim();
      const parts = rangeText.split(/\\s*-\\s*/);
      if (!parts.length) {
        return fallback;
      }

      const startParts = parseMonthDay(parts[0], year);
      if (!startParts) {
        return fallback;
      }

      const endParts = parts[1]
        ? parseMonthDay(parts[1], year, startParts.month)
        : startParts;
      if (!endParts) {
        return fallback;
      }

      const start = new Date(Date.UTC(startParts.year, startParts.month, startParts.day));
      let end = new Date(Date.UTC(endParts.year, endParts.month, endParts.day));
      if (end < start) {
        end = new Date(Date.UTC(endParts.year + 1, endParts.month, endParts.day));
      }

      return {
        start,
        end,
        durationDays: Math.max(1, daysBetween(start, end)),
        label: `${formatAxisDate(start)} - ${formatAxisDate(end)}`
      };
    }

    function parseMonthDay(value, year, fallbackMonth = null) {
      const text = String(value || "").trim();
      const match = text.match(/^(?:(\\w+)\\s+)?(\\d{1,2})$/);
      if (!match) {
        return null;
      }

      const month = match[1]
        ? monthLookup[match[1].toLowerCase()]
        : fallbackMonth;
      if (month === undefined || month === null) {
        return null;
      }

      return {
        year,
        month,
        day: Number(match[2])
      };
    }

    function daysBetween(start, end) {
      return Math.max(0, Math.round((end - start) / 86400000));
    }

    function dateTicks(start, end) {
      const ticks = [];
      for (let date = start; date <= end; date = addDays(date, 1)) {
        ticks.push(date);
      }
      return ticks;
    }

    function addDays(date, days) {
      return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + days));
    }

    function dateKey(date) {
      return date.toISOString().slice(0, 10);
    }

    function formatAxisDate(date) {
      return `${monthLabels[date.getUTCMonth()]} ${String(date.getUTCDate()).padStart(2, "0")}`;
    }

    function stopMeetups(stop) {
      return Array.isArray(stop.meetups)
        ? stop.meetups.map((person) => String(person).trim()).filter(Boolean)
        : [];
    }

    function meetupsText(stop) {
      return stopMeetups(stop).join(", ");
    }

    function meetupsHTML(stop) {
      const meetups = meetupsText(stop);
      if (!meetups) {
        return "";
      }
      return `<p class="meetups">Meet: ${escapeHTML(meetups)}</p>`;
    }

    function routeStatusText(routed, fallback, flights, total) {
      if (routed + fallback + flights < total) {
        return `Routing ${routed + fallback + flights} of ${total} segments...`;
      }
      const parts = [`${routed} road segment${routed === 1 ? "" : "s"} routed`];
      if (flights > 0) {
        parts.push(`${flights} flight segment${flights === 1 ? "" : "s"} shown`);
      }
      if (fallback > 0) {
        parts.push(`${fallback} fallback segment${fallback === 1 ? "" : "s"} shown`);
      }
      return `${parts.join("; ")}.`;
    }

    function formatDistance(meters) {
      return `${Math.round(meters / 1000).toLocaleString()} km`;
    }

    function formatDuration(seconds) {
      const hours = seconds / 3600;
      if (hours < 10) {
        return `${hours.toFixed(1)} h`;
      }
      return `${Math.round(hours).toLocaleString()} h`;
    }

    function escapeHTML(value) {
      return String(value ?? "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
    }
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
