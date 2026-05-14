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


def one_itinerary_file(folder: Path) -> Path:
    files = itinerary_files(folder)
    if not files:
        raise FileNotFoundError(f"No JSON itinerary file found in {folder}")
    if len(files) > 1:
        labels = ", ".join(source_label(file) for file in files)
        raise ValueError(
            f"Expected one itinerary JSON file in {folder}, found {len(files)}: {labels}. "
            "Put multiple itinerary versions inside one JSON file under 'itineraries'."
        )
    return files[0]


def resolve_input_source(path: Path | None) -> tuple[Path, bool]:
    if path is not None:
        if path.is_dir():
            source = one_itinerary_file(path)
            return source, path.resolve() == EXAMPLE_ITINERARIES_DIR.resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        return path, path.parent.resolve() == EXAMPLE_ITINERARIES_DIR.resolve()

    personal_files = itinerary_files(PERSONAL_ITINERARIES_DIR)
    if personal_files:
        return one_itinerary_file(PERSONAL_ITINERARIES_DIR), False

    return one_itinerary_file(EXAMPLE_ITINERARIES_DIR), True


def read_trip_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        trip = json.load(handle)
    if not isinstance(trip, dict):
        raise ValueError(f"{path} must contain a JSON object.")
    return trip


def normalize_trip(trip: dict[str, Any]) -> dict[str, Any]:
    for key in ("supertitle", "title", "subtitle"):
        if not str(trip.get(key, "")).strip():
            raise ValueError(f"The trip must contain a non-empty '{key}' field.")
        trip[key] = str(trip[key]).strip()

    ideas = trip.get("ideas", [])
    if not isinstance(ideas, list):
        raise ValueError("'ideas' must be a list when provided.")
    for idea_index, idea in enumerate(ideas, start=1):
        if not isinstance(idea, dict):
            raise ValueError(f"Idea {idea_index} must be an object.")
        for key in ("id", "title"):
            if not str(idea.get(key, "")).strip():
                raise ValueError(f"Idea {idea_index} must contain a non-empty '{key}' field.")
            idea[key] = str(idea[key]).strip()
        idea["status"] = str(idea.get("status", "maybe")).strip() or "maybe"
        idea["priority"] = str(idea.get("priority", "medium")).strip() or "medium"
        idea["notes"] = str(idea.get("notes", "")).strip()
        for key in ("tags", "related_stops"):
            values = idea.get(key, [])
            if not isinstance(values, list):
                raise ValueError(f"Idea {idea_index} has invalid '{key}'; use a list.")
            idea[key] = [str(value).strip() for value in values if str(value).strip()]
    trip["ideas"] = ideas

    itineraries = trip.get("itineraries")
    if not isinstance(itineraries, list) or not itineraries:
        raise ValueError("'itineraries' must contain at least one itinerary version.")

    for itinerary_index, itinerary in enumerate(itineraries, start=1):
        if not isinstance(itinerary, dict):
            raise ValueError(f"Itinerary {itinerary_index} must be an object.")

        for key in ("id", "version_name", "name"):
            if not str(itinerary.get(key, "")).strip():
                raise ValueError(f"Itinerary {itinerary_index} must contain a non-empty '{key}' field.")
            itinerary[key] = str(itinerary[key]).strip()

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
            if not isinstance(meetups, list):
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} has invalid 'meetups'; use a list of names."
                )
            stop["meetups"] = [str(person) for person in meetups if str(person).strip()]

            if "tag" in stop:
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} uses legacy 'tag'; use 'tags' as a list."
                )
            tags = stop.get("tags")
            if not isinstance(tags, list):
                raise ValueError(
                    f"Itinerary {itinerary_index}, stop {stop_index} must contain 'tags' as a list."
                )
            stop["tags"] = [str(tag).strip() for tag in tags if str(tag).strip()]
            if not stop["tags"]:
                raise ValueError(f"Itinerary {itinerary_index}, stop {stop_index} must contain at least one tag.")

            has_flight_fields = any(key in stop for key in ("flying_from", "flying_via", "flying_to", "flight_path"))
            if str(stop.get("transport_to_next", "")).strip().lower() == "flight" or has_flight_fields:
                stop["transport_to_next"] = "flight"
                stop["flying_from"] = str(stop.get("flying_from", "")).strip()
                stop["flying_to"] = str(stop.get("flying_to", "")).strip()
                flying_via = stop.get("flying_via", [])
                if not isinstance(flying_via, list):
                    raise ValueError(
                        f"Itinerary {itinerary_index}, stop {stop_index} has invalid 'flying_via'; use a list."
                    )
                stop["flying_via"] = [str(place) for place in flying_via if str(place).strip()]

    trip.setdefault("map", {})
    trip.setdefault("route_service", {})
    trip["route_service"].setdefault("url", "https://router.project-osrm.org/route/v1")
    trip["route_service"].setdefault("profile", "driving")
    return trip


def load_trip(path: Path | None) -> dict[str, Any]:
    source, using_examples = resolve_input_source(path)
    trip = normalize_trip(read_trip_file(source))
    trip["using_example_itineraries"] = using_examples
    trip["source_file"] = source_label(source)
    return trip


def source_label(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(resolved)


def render_html(trip: dict[str, Any]) -> str:
    trip_json = json.dumps(trip, ensure_ascii=True, indent=2)
    supertitle = html.escape(str(trip["supertitle"]), quote=True)
    title = html.escape(str(trip["title"]), quote=True)
    subtitle = html.escape(str(trip["subtitle"]), quote=True)
    document_title = title if not subtitle else f"{title} - {subtitle}"

    document = HTML_TEMPLATE
    document = document.replace("__DOCUMENT_TITLE__", document_title)
    document = document.replace("__TRIP_SUPERTITLE__", supertitle)
    document = document.replace("__TRIP_TITLE__", title)
    document = document.replace("__TRIP_SUBTITLE__", subtitle)
    document = document.replace("__TRIP_DATA__", trip_json)
    return document


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a road trip map HTML file.")
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help=(
            "Path to one itinerary JSON file or to a folder containing exactly one JSON file. "
            "Defaults to personal_itineraries, then example_itineraries."
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
  <title>__DOCUMENT_TITLE__</title>
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
    #app.view-timeline .panel,
    #app.view-ideas #map,
    #app.view-ideas .panel {
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

    .selected-itinerary {
      margin: 9px 0 0;
      color: var(--ink);
      font-size: 0.92rem;
      font-weight: 800;
      line-height: 1.3;
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
    .view-button,
    .idea-button {
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
    .view-button:focus,
    .idea-button:hover,
    .idea-button:focus {
      border-color: rgba(0, 122, 120, 0.7);
      outline: none;
    }

    .version-button.is-active,
    .view-button.is-active {
      color: #ffffff;
      border-color: var(--accent);
      background: var(--accent);
    }

    .idea-button.is-primary {
      color: #ffffff;
      border-color: var(--accent);
      background: var(--accent);
    }

    .idea-button.is-danger {
      color: #8b2d16;
      border-color: rgba(164, 72, 29, 0.35);
      background: #fff7f4;
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

    .tag-list {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin: 7px 0 0;
    }

    .tag-pill {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 7px;
      border-radius: 999px;
      color: #ffffff;
      background: var(--tag-color, var(--stop-color));
      font-size: 0.72rem;
      font-weight: 800;
      line-height: 1.2;
    }

    .tag-icon-list {
      display: inline-flex;
      align-items: center;
      gap: 3px;
      flex: 0 0 auto;
    }

    .tag-icon {
      width: 16px;
      height: 16px;
      display: inline-grid;
      place-items: center;
      flex: 0 0 auto;
      border-radius: 999px;
      color: #ffffff;
      background: var(--tag-color, var(--stop-color));
      line-height: 1;
    }

    .tag-icon svg {
      width: 11px;
      height: 11px;
      fill: none;
      stroke: currentColor;
      stroke-width: 2.3;
      stroke-linecap: round;
      stroke-linejoin: round;
    }

    .tag-pill .tag-icon {
      width: 14px;
      height: 14px;
      background: rgba(255, 255, 255, 0.2);
    }

    .tag-pill .tag-icon svg {
      width: 9px;
      height: 9px;
    }

    .tag-icon-fallback {
      font-size: 0.5rem;
      font-weight: 900;
      letter-spacing: 0;
      line-height: 1;
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
      overflow: visible;
      color: #ffffff;
      background: transparent;
      border: 0;
    }

    .stop-marker-frame {
      position: relative;
      width: var(--marker-size, 32px);
      height: var(--marker-size, 32px);
      display: block;
    }

    .stop-marker-core {
      position: absolute;
      top: 0;
      left: 0;
      width: var(--marker-size, 32px);
      height: var(--marker-size, 32px);
      display: grid;
      place-items: center;
      border: 3px solid #ffffff;
      border-radius: 999px;
      background: var(--stop-color, #315f9f);
      box-shadow: 0 5px 16px rgba(18, 25, 38, 0.30);
      font-size: var(--marker-font-size, 0.82rem);
      font-weight: 900;
    }

    .stop-marker-number {
      transform: translateY(-1px);
    }

    .stop-marker-tags {
      position: absolute;
      top: 50%;
      left: calc(var(--marker-size, 32px) - 2px);
      display: flex;
      align-items: center;
      gap: 2px;
      padding: 2px;
      transform: translateY(-50%);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.94);
      box-shadow: 0 4px 12px rgba(18, 25, 38, 0.20);
    }

    .stop-marker-tags .tag-icon {
      width: 14px;
      height: 14px;
    }

    .stop-marker-tags .tag-icon svg {
      width: 9px;
      height: 9px;
      stroke-width: 2.6;
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

    .timeline-view,
    .ideas-view {
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

    #app.view-ideas .ideas-view {
      display: block;
    }

    .timeline-shell,
    .ideas-shell {
      max-width: 1180px;
      min-height: calc(100vh - 122px);
      padding: 26px;
      background: rgba(255, 255, 255, 0.96);
      border: 1px solid rgba(196, 205, 217, 0.92);
      border-radius: 8px;
      box-shadow: var(--shadow);
    }

    .timeline-header,
    .ideas-header {
      display: grid;
      grid-template-columns: minmax(0, 1fr) minmax(250px, auto);
      gap: 18px;
      align-items: start;
      margin-bottom: 22px;
    }

    .timeline-header h2,
    .ideas-header h2 {
      margin: 0;
      font-size: 1.55rem;
      line-height: 1.1;
    }

    .timeline-header p,
    .ideas-header p {
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

    .ideas-counts {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      justify-content: end;
    }

    .ideas-count {
      min-width: 96px;
      padding: 9px 10px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #f7f9fb;
    }

    .ideas-count span,
    .idea-field label {
      display: block;
      color: var(--muted);
      font-size: 0.72rem;
      font-weight: 800;
      letter-spacing: 0;
      text-transform: uppercase;
    }

    .ideas-count strong {
      display: block;
      margin-top: 5px;
      font-size: 1rem;
    }

    .ideas-layout {
      display: grid;
      grid-template-columns: minmax(270px, 340px) minmax(0, 1fr);
      gap: 22px;
      align-items: start;
    }

    .idea-form,
    .ideas-save-prompt {
      display: grid;
      gap: 12px;
      padding: 16px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
    }

    .ideas-save-prompt {
      margin-top: 14px;
      border-color: rgba(0, 122, 120, 0.28);
      background: #edf8f6;
    }

    .ideas-save-prompt[hidden] {
      display: none;
    }

    .idea-field {
      display: grid;
      gap: 6px;
    }

    .idea-field input,
    .idea-field select,
    .idea-field textarea,
    .ideas-save-prompt textarea {
      width: 100%;
      border: 1px solid rgba(139, 151, 167, 0.72);
      border-radius: 8px;
      color: var(--ink);
      background: #ffffff;
      font: inherit;
      font-size: 0.9rem;
    }

    .idea-field input,
    .idea-field select {
      min-height: 38px;
      padding: 0 10px;
    }

    .idea-field textarea,
    .ideas-save-prompt textarea {
      min-height: 96px;
      padding: 10px;
      resize: vertical;
      line-height: 1.35;
    }

    .ideas-save-prompt textarea {
      min-height: 150px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 0.76rem;
    }

    .idea-form-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 10px;
    }

    .idea-form-actions,
    .idea-card-actions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }

    .ideas-list {
      display: grid;
      gap: 10px;
    }

    .ideas-empty {
      padding: 16px;
      border: 1px dashed rgba(139, 151, 167, 0.72);
      border-radius: 8px;
      color: var(--muted);
      background: #ffffff;
      font-weight: 700;
    }

    .idea-card {
      padding: 14px;
      border: 1px solid var(--line);
      border-left: 6px solid var(--idea-color, var(--accent));
      border-radius: 8px;
      background: #ffffff;
    }

    .idea-card-header {
      display: flex;
      gap: 10px;
      align-items: start;
      justify-content: space-between;
    }

    .idea-card h3 {
      margin: 0;
      font-size: 1rem;
      line-height: 1.2;
    }

    .idea-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 9px 0 0;
    }

    .idea-pill {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 3px 7px;
      border-radius: 999px;
      color: #334155;
      background: #edf1f5;
      font-size: 0.72rem;
      font-weight: 800;
      line-height: 1.2;
    }

    .idea-pill.is-local {
      color: #24515d;
      background: #dff2ef;
    }

    .idea-notes {
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 0.86rem;
      line-height: 1.4;
      white-space: pre-wrap;
    }

    .idea-notes a {
      color: var(--accent);
      font-weight: 800;
      text-decoration: underline;
      text-underline-offset: 2px;
    }

    .idea-related {
      margin: 9px 0 0;
      color: var(--accent-2);
      font-size: 0.8rem;
      font-weight: 750;
      line-height: 1.35;
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
      grid-template-columns: 96px minmax(0, 1fr);
      gap: 16px;
      min-height: var(--timeline-height);
    }

    .timeline-axis {
      position: relative;
      min-height: var(--timeline-height);
      border-right: 3px solid #c7d0dc;
    }

    .timeline-date-mark {
      position: absolute;
      right: 12px;
      transform: translateY(-50%);
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: var(--muted);
      font-size: 0.74rem;
      font-weight: 800;
      white-space: nowrap;
    }

    .timeline-date-mark.is-sunday {
      color: #b42318;
    }

    .timeline-date-mark::after {
      display: none;
    }

    .timeline-date-label {
      min-width: 40px;
      text-align: right;
    }

    .timeline-weekday-label {
      min-width: 28px;
      text-align: left;
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
      padding: 12px 88px 12px 52px;
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
      left: 14px;
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

    .timeline-duration {
      position: absolute;
      top: 14px;
      right: 14px;
      color: var(--accent-3);
      font-size: 0.8rem;
      font-weight: 850;
      line-height: 1.15;
      white-space: nowrap;
    }

    .timeline-stop h3 {
      margin: 0;
      font-size: 0.96rem;
      line-height: 1.2;
    }

    .timeline-note {
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 0.8rem;
      line-height: 1.35;
    }

    .timeline-summary {
      display: none;
    }

    .timeline-view[data-scale="0"] .timeline-track {
      min-height: 0;
      padding: 0 2px 16px;
    }

    .timeline-view[data-scale="0"] .timeline-axis-layout {
      grid-template-columns: 72px minmax(0, 1fr);
      gap: 10px;
    }

    .timeline-view[data-scale="0"] .timeline-axis {
      border-right-width: 1px;
    }

    .timeline-view[data-scale="0"] .timeline-date-mark {
      right: 6px;
      gap: 4px;
      font-size: 0.56rem;
    }

    .timeline-view[data-scale="0"] .timeline-date-label {
      min-width: 30px;
    }

    .timeline-view[data-scale="0"] .timeline-weekday-label {
      min-width: 20px;
    }

    .timeline-view[data-scale="0"] .timeline-stop {
      display: flex;
      align-items: center;
      width: min(760px, 100%);
      min-height: 16px;
      padding: 0 58px 0 30px;
      border: 0;
      border-left: 4px solid var(--stop-color);
      border-radius: 3px;
      background: var(--stop-bg);
      box-shadow: none;
      line-height: 1;
      overflow: visible;
    }

    .timeline-view[data-scale="0"] .timeline-dot {
      top: 50%;
      left: 6px;
      width: 18px;
      height: 18px;
      transform: translateY(-50%);
      border-width: 2px;
      box-shadow: none;
      font-size: 0.62rem;
    }

    .timeline-view[data-scale="0"] .timeline-duration {
      top: 50%;
      right: 6px;
      transform: translateY(-50%);
      font-size: 0.6rem;
    }

    .timeline-view[data-scale="0"] .timeline-summary {
      display: flex;
      align-items: center;
      gap: 6px;
      width: 100%;
      min-width: 0;
    }

    .timeline-place {
      min-width: 0;
      overflow: hidden;
      color: var(--ink);
      font-size: 0.72rem;
      font-weight: 850;
      line-height: 1.15;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .timeline-view[data-scale="0"] .timeline-summary .tag-icon {
      width: 14px;
      height: 14px;
    }

    .timeline-view[data-scale="0"] .timeline-summary .tag-icon svg {
      width: 9px;
      height: 9px;
    }

    .timeline-view[data-scale="0"] .timeline-stop > h3 {
      margin: 0;
      display: none;
    }

    .timeline-view[data-scale="0"] .meetups,
    .timeline-view[data-scale="0"] .timeline-note,
    .timeline-view[data-scale="0"] .tag-list,
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

      .timeline-view,
      .ideas-view {
        padding: 72px 12px 12px;
      }

      .timeline-shell,
      .ideas-shell {
        padding: 16px;
      }

      .timeline-header,
      .ideas-header,
      .ideas-layout {
        grid-template-columns: 1fr;
      }

      .timeline-controls {
        justify-items: stretch;
      }

      .ideas-counts {
        justify-content: start;
      }

      .idea-form-grid {
        grid-template-columns: 1fr;
      }

      .timeline-axis-layout {
        grid-template-columns: 78px minmax(0, 1fr);
        gap: 10px;
      }

      .timeline-date-mark {
        right: 6px;
        gap: 4px;
        font-size: 0.62rem;
      }

      .timeline-dot {
        left: 10px;
      }

      .timeline-view[data-scale="0"] .timeline-axis-layout {
        grid-template-columns: 68px minmax(0, 1fr);
        gap: 8px;
      }

      .timeline-view[data-scale="0"] .timeline-dot {
        left: 5px;
      }
    }
  </style>
</head>
<body>
  <div id="app">
    <div id="map" aria-label="__TRIP_TITLE__"></div>
    <aside class="panel" id="panel">
      <header>
        <p class="eyebrow" id="panelSupertitle">__TRIP_SUPERTITLE__</p>
        <h1 id="panelTitle">__TRIP_TITLE__</h1>
        <p class="subtitle" id="panelSubtitle">__TRIP_SUBTITLE__</p>
      </header>
      <section aria-label="Itinerary versions">
        <span class="section-label">Version</span>
        <div class="version-tabs" id="versionTabs"></div>
        <p class="selected-itinerary" id="selectedItineraryName"></p>
      </section>
      <div class="example-notice" id="exampleNotice" hidden>
        The example itinerary file is shown. Add your own JSON file to personal_itineraries/ and rerun the generator.
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
            <p class="eyebrow" id="timelineSupertitle">__TRIP_SUPERTITLE__</p>
            <h2 id="timelineTitle">__TRIP_TITLE__</h2>
            <p id="timelineSubtitle">__TRIP_SUBTITLE__</p>
            <div class="example-notice" id="timelineExampleNotice" hidden>
              The example itinerary file is shown. Add your own JSON file to personal_itineraries/ and rerun the generator.
            </div>
          </div>
          <div class="timeline-controls">
            <span class="section-label">Version</span>
            <div class="version-tabs" id="timelineVersionTabs"></div>
            <p class="selected-itinerary" id="timelineItineraryName"></p>
            <div class="timeline-scale">
              <span class="section-label">Scale</span>
              <input id="timelineScale" type="range" min="0" max="1" step="1" value="0" aria-label="Timeline scale">
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
    <section class="ideas-view" id="ideasView" aria-label="Ideas view">
      <div class="ideas-shell">
        <header class="ideas-header">
          <div>
            <p class="eyebrow" id="ideasSupertitle">__TRIP_SUPERTITLE__</p>
            <h2 id="ideasTitle">Bucket list</h2>
            <p id="ideasSubtitle">__TRIP_TITLE__</p>
          </div>
          <div class="ideas-counts" aria-label="Idea totals">
            <div class="ideas-count"><span>Trip file</span><strong id="seedIdeaCount">0</strong></div>
            <div class="ideas-count"><span>Local</span><strong id="localIdeaCount">0</strong></div>
          </div>
        </header>
        <div class="ideas-layout">
          <div>
            <form class="idea-form" id="ideaForm">
              <div class="idea-field">
                <label for="ideaTitleInput">Idea</label>
                <input id="ideaTitleInput" type="text" maxlength="90" required>
              </div>
              <div class="idea-form-grid">
                <div class="idea-field">
                  <label for="ideaStatusInput">Status</label>
                  <select id="ideaStatusInput">
                    <option value="maybe">Maybe</option>
                    <option value="shortlist">Shortlist</option>
                    <option value="planned">Planned</option>
                    <option value="skip">Skip</option>
                  </select>
                </div>
                <div class="idea-field">
                  <label for="ideaPriorityInput">Priority</label>
                  <select id="ideaPriorityInput">
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="low">Low</option>
                  </select>
                </div>
              </div>
              <div class="idea-field">
                <label for="ideaRelatedStopInput">Related stop</label>
                <select id="ideaRelatedStopInput"></select>
              </div>
              <div class="idea-field">
                <label for="ideaTagsInput">Tags</label>
                <input id="ideaTagsInput" type="text" placeholder="tramping, sightseeing">
              </div>
              <div class="idea-field">
                <label for="ideaNotesInput">Notes</label>
                <textarea id="ideaNotesInput"></textarea>
              </div>
              <div class="idea-form-actions">
                <button class="idea-button is-primary" type="submit">Save local idea</button>
              </div>
            </form>
            <section class="ideas-save-prompt" id="ideasSavePrompt" hidden>
              <p>
                To make local ideas part of future generated HTML, copy this JSON into Codex and ask to merge it into the trip file under <code>ideas</code>, then rerun the generator.
              </p>
              <textarea id="ideasExportJSON" readonly></textarea>
              <div class="idea-form-actions">
                <button class="idea-button" id="copyIdeasButton" type="button">Copy JSON</button>
              </div>
            </section>
          </div>
          <section>
            <div class="ideas-list" id="ideasList"></div>
          </section>
        </div>
      </div>
    </section>
    <nav class="view-switch" aria-label="View mode">
      <button class="view-button is-active" id="mapViewButton" type="button" aria-pressed="true">Map view</button>
      <button class="view-button" id="timelineViewButton" type="button" aria-pressed="false">Timeline view</button>
      <button class="view-button" id="ideasViewButton" type="button" aria-pressed="false">Ideas</button>
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
    const tagIcons = {
      friends: "friends",
      tramping: "mountain",
      sightseeing: "camera",
      camping: "tent",
      "luxury-bach": "home",
      travel: "route"
    };
    const timelineScales = [
      { pxPerDay: 18, minHeight: 120 },
      { pxPerDay: 148, minHeight: 320 }
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
    const weekdayLabels = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

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

    const itineraries = trip.itineraries;
    const seedIdeas = Array.isArray(trip.ideas) ? trip.ideas : [];
    const ideasStorageKey = `road-trip-ideas:${trip.source_file || trip.title}`;
    const routeCache = new Map();
    let activeItineraryIndex = 0;
    let activeRouteRun = 0;
    let activeBounds = L.latLngBounds([]);
    let markers = [];
    let localIdeas = loadLocalIdeas();

    const app = document.getElementById("app");
    const panelSupertitle = document.getElementById("panelSupertitle");
    const panelTitle = document.getElementById("panelTitle");
    const panelSubtitle = document.getElementById("panelSubtitle");
    const selectedItineraryName = document.getElementById("selectedItineraryName");
    const versionTabs = document.getElementById("versionTabs");
    const timelineVersionTabs = document.getElementById("timelineVersionTabs");
    const timelineSupertitle = document.getElementById("timelineSupertitle");
    const timelineTitle = document.getElementById("timelineTitle");
    const timelineSubtitle = document.getElementById("timelineSubtitle");
    const timelineItineraryName = document.getElementById("timelineItineraryName");
    const seedIdeaCount = document.getElementById("seedIdeaCount");
    const localIdeaCount = document.getElementById("localIdeaCount");
    const ideasList = document.getElementById("ideasList");
    const ideaForm = document.getElementById("ideaForm");
    const ideaTitleInput = document.getElementById("ideaTitleInput");
    const ideaStatusInput = document.getElementById("ideaStatusInput");
    const ideaPriorityInput = document.getElementById("ideaPriorityInput");
    const ideaRelatedStopInput = document.getElementById("ideaRelatedStopInput");
    const ideaTagsInput = document.getElementById("ideaTagsInput");
    const ideaNotesInput = document.getElementById("ideaNotesInput");
    const ideasSavePrompt = document.getElementById("ideasSavePrompt");
    const ideasExportJSON = document.getElementById("ideasExportJSON");
    const copyIdeasButton = document.getElementById("copyIdeasButton");
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
    const ideasViewButton = document.getElementById("ideasViewButton");

    mapViewButton.addEventListener("click", () => setViewMode("map"));
    timelineViewButton.addEventListener("click", () => setViewMode("timeline"));
    ideasViewButton.addEventListener("click", () => setViewMode("ideas"));
    timelineScale.addEventListener("input", () => renderTimeline(getActiveItinerary()));
    ideaForm.addEventListener("submit", saveLocalIdea);
    copyIdeasButton.addEventListener("click", copyIdeasExport);
    exampleNotice.hidden = !trip.using_example_itineraries;
    timelineExampleNotice.hidden = !trip.using_example_itineraries;
    window.addEventListener("load", () => refreshMapLayout());
    window.addEventListener("resize", () => map.invalidateSize({ animate: false }));

    populateIdeaStopOptions();
    renderIdeas();
    renderVersionTabs();
    renderActiveItinerary({ fitBounds: true });
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
          button.textContent = itinerary.version_name;
          button.setAttribute("aria-label", `${itinerary.version_name}: ${itinerary.name}`);
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

    function renderActiveItinerary(options = {}) {
      const itinerary = getActiveItinerary();
      const stops = itinerary.stops || [];
      const fitBounds = Boolean(options.fitBounds);
      activeRouteRun += 1;
      activeBounds = L.latLngBounds([]);
      markers = [];

      stopLayer.clearLayers();
      routeLayer.clearLayers();
      stopList.innerHTML = "";
      map.closePopup();

      panelSupertitle.textContent = trip.supertitle;
      panelTitle.textContent = trip.title;
      panelSubtitle.textContent = trip.subtitle;
      selectedItineraryName.textContent = itinerary.name;
      timelineSupertitle.textContent = trip.supertitle;
      timelineTitle.textContent = trip.title;
      timelineSubtitle.textContent = trip.subtitle;
      timelineItineraryName.textContent = itinerary.name;
      stopCount.textContent = String(stops.length);
      distanceTotal.textContent = "Routing";
      durationTotal.textContent = "Routing";
      routeStatus.textContent = stops.length > 1 ? "Routing road segments..." : "Add at least two stops.";

      stops.forEach((stop, index) => renderStop(stop, index));
      renderTimeline(itinerary);
      requestAnimationFrame(() => refreshMapLayout({ fitBounds }));

      if (stops.length > 1) {
        drawRoutes(itinerary, activeRouteRun);
      }
    }

    function renderStop(stop, index) {
      const latLng = [stop.lat, stop.lon];
      const color = stopColor(stop);
      const range = parseDateRange(stop.date_range);
      const markerMetrics = markerMetricsForDays(range.durationDays);
      const markerTags = stopTags(stop);
      activeBounds.extend(latLng);

      const marker = L.marker(latLng, {
        title: stop.name,
        icon: L.divIcon({
          className: "stop-marker",
          html: `
            <span class="stop-marker-frame">
              <span class="stop-marker-core">
                <span class="stop-marker-number">${index + 1}</span>
              </span>
              <span class="stop-marker-tags">${markerTags.map(tagIconHTML).join("")}</span>
            </span>
          `,
          iconSize: [markerMetrics.size, markerMetrics.size],
          iconAnchor: [markerMetrics.size / 2, markerMetrics.size / 2],
          popupAnchor: [0, -markerMetrics.size / 2]
        })
      }).addTo(stopLayer);

      const markerElement = marker.getElement();
      if (markerElement) {
        markerElement.style.setProperty("--stop-color", color);
        markerElement.style.setProperty("--marker-size", `${markerMetrics.size}px`);
        markerElement.style.setProperty("--marker-font-size", markerMetrics.fontSize);
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
      const timelineDays = timelineStart && timelineEnd ? Math.max(1, daysBetween(timelineStart, timelineEnd)) : 1;
      const timelineHeight = Math.max(scale.minHeight || 320, timelineDays * scale.pxPerDay);
      const marks = timelineStart && timelineEnd
        ? dateTicks(timelineStart, timelineEnd).map((date) => ({
          top: daysBetween(timelineStart, date) * scale.pxPerDay,
          dateLabel: formatAxisDate(date),
          weekdayLabel: formatWeekday(date),
          isSunday: date.getUTCDay() === 0
        }))
        : items.map((item, index) => ({
          top: index * scale.pxPerDay,
          dateLabel: "",
          weekdayLabel: "",
          isSunday: false
        }));

      timelineView.dataset.scale = String(scaleIndex);
      timelineTrack.innerHTML = `
        <div class="timeline-axis-layout" style="--timeline-height: ${timelineHeight}px">
          <div class="timeline-axis">
            ${marks.map((mark) => `
              <span class="timeline-date-mark${mark.isSunday ? " is-sunday" : ""}" style="top: ${mark.top}px">
                <span class="timeline-date-label">${escapeHTML(mark.dateLabel)}</span>
                <span class="timeline-weekday-label">${escapeHTML(mark.weekdayLabel)}</span>
              </span>
            `).join("")}
          </div>
          <div class="timeline-blocks">
            ${items.map((item) => {
              const top = timelineStart && item.range.start
                ? daysBetween(timelineStart, item.range.start) * scale.pxPerDay
                : item.index * scale.pxPerDay;
              const height = Math.max(scale.pxPerDay, item.range.durationDays * scale.pxPerDay);
              const color = stopColor(item.stop);
              const duration = formatDurationDays(item.range.durationDays);
              return `
              <article class="timeline-stop" style="--stop-color: ${color}; --stop-bg: ${colorWithAlpha(color, 0.16)}; top: ${top}px; height: ${height}px">
                <span class="timeline-dot">${item.index + 1}</span>
                <span class="timeline-duration">${escapeHTML(duration)}</span>
                <div class="timeline-summary">
                  <span class="timeline-place">${escapeHTML(item.stop.name)}</span>
                  ${tagIconsHTML(item.stop)}
                </div>
                <h3>${escapeHTML(item.stop.name)}</h3>
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

    function populateIdeaStopOptions() {
      const stopNames = [];
      itineraries.forEach((itinerary) => {
        (itinerary.stops || []).forEach((stop) => {
          const name = String(stop.name || "").trim();
          if (name && !stopNames.includes(name)) {
            stopNames.push(name);
          }
        });
      });

      ideaRelatedStopInput.innerHTML = `
        <option value="">None</option>
        ${stopNames.map((name) => `<option value="${escapeHTML(name)}">${escapeHTML(name)}</option>`).join("")}
      `;
    }

    function renderIdeas() {
      const ideas = [
        ...seedIdeas.map((idea) => normalizeIdea(idea, "trip")),
        ...localIdeas.map((idea) => normalizeIdea(idea, "local"))
      ];
      seedIdeaCount.textContent = String(seedIdeas.length);
      localIdeaCount.textContent = String(localIdeas.length);
      refreshIdeasExport();

      if (!ideas.length) {
        ideasList.innerHTML = `<p class="ideas-empty">No ideas saved.</p>`;
        return;
      }

      ideasList.innerHTML = ideas.map(ideaCardHTML).join("");
      ideasList.querySelectorAll("[data-delete-idea]").forEach((button) => {
        button.addEventListener("click", () => {
          localIdeas = localIdeas.filter((idea) => idea.id !== button.dataset.deleteIdea);
          storeLocalIdeas();
          renderIdeas();
        });
      });
    }

    function ideaCardHTML(idea) {
      const tags = idea.tags.length
        ? `<p class="tag-list">${idea.tags.map((tag) => `
          <span class="tag-pill" style="--tag-color: ${tagColor(tag)}">
            ${tagIconHTML(tag)}
            <span>${escapeHTML(normalizeTag(tag))}</span>
          </span>
        `).join("")}</p>`
        : "";
      const related = idea.related_stops.length
        ? `<p class="idea-related">Stop: ${escapeHTML(idea.related_stops.join(", "))}</p>`
        : "";
      const notes = idea.notes
        ? `<p class="idea-notes">${noteHTML(idea.notes)}</p>`
        : "";
      const deleteButton = idea.source === "local"
        ? `<div class="idea-card-actions"><button class="idea-button is-danger" type="button" data-delete-idea="${escapeHTML(idea.id)}">Delete local</button></div>`
        : "";

      return `
        <article class="idea-card" style="--idea-color: ${tagColor(idea.tags[0] || idea.status)}">
          <div class="idea-card-header">
            <h3>${escapeHTML(idea.title)}</h3>
            <span class="idea-pill${idea.source === "local" ? " is-local" : ""}">${idea.source === "local" ? "Local" : "Trip file"}</span>
          </div>
          <div class="idea-meta">
            <span class="idea-pill">${escapeHTML(idea.status)}</span>
            <span class="idea-pill">${escapeHTML(idea.priority)}</span>
          </div>
          ${related}
          ${tags}
          ${notes}
          ${deleteButton}
        </article>
      `;
    }

    function saveLocalIdea(event) {
      event.preventDefault();
      const title = ideaTitleInput.value.trim();
      if (!title) {
        return;
      }

      const relatedStop = ideaRelatedStopInput.value.trim();
      const idea = {
        id: `${slugify(title)}-${Date.now().toString(36)}`,
        title,
        status: ideaStatusInput.value,
        priority: ideaPriorityInput.value,
        related_stops: relatedStop ? [relatedStop] : [],
        tags: splitIdeaTags(ideaTagsInput.value),
        notes: ideaNotesInput.value.trim()
      };

      localIdeas = [...localIdeas, idea];
      storeLocalIdeas();
      ideaForm.reset();
      ideaStatusInput.value = "maybe";
      ideaPriorityInput.value = "medium";
      ideasSavePrompt.hidden = false;
      renderIdeas();
    }

    function splitIdeaTags(value) {
      return [...new Set(String(value || "")
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean))];
    }

    function normalizeIdea(idea, source) {
      return {
        id: String(idea.id || "").trim(),
        title: String(idea.title || "").trim(),
        status: String(idea.status || "maybe").trim() || "maybe",
        priority: String(idea.priority || "medium").trim() || "medium",
        related_stops: Array.isArray(idea.related_stops)
          ? idea.related_stops.map((stop) => String(stop).trim()).filter(Boolean)
          : [],
        tags: Array.isArray(idea.tags)
          ? idea.tags.map((tag) => String(tag).trim()).filter(Boolean)
          : [],
        notes: String(idea.notes || "").trim(),
        source
      };
    }

    function loadLocalIdeas() {
      try {
        const stored = JSON.parse(localStorage.getItem(ideasStorageKey) || "[]");
        return Array.isArray(stored)
          ? stored.map((idea) => normalizeIdea(idea, "local")).filter((idea) => idea.id && idea.title)
          : [];
      } catch (error) {
        console.warn("Local ideas could not be loaded", error);
        return [];
      }
    }

    function storeLocalIdeas() {
      try {
        localStorage.setItem(ideasStorageKey, JSON.stringify(localIdeas.map(serializableIdea)));
      } catch (error) {
        console.warn("Local ideas could not be saved", error);
      }
    }

    function refreshIdeasExport() {
      ideasSavePrompt.hidden = localIdeas.length === 0;
      ideasExportJSON.value = JSON.stringify({
        ideas: localIdeas.map(serializableIdea)
      }, null, 2);
    }

    function serializableIdea(idea) {
      return {
        id: idea.id,
        title: idea.title,
        status: idea.status,
        priority: idea.priority,
        related_stops: idea.related_stops,
        tags: idea.tags,
        notes: idea.notes
      };
    }

    function copyIdeasExport() {
      const text = ideasExportJSON.value;
      if (navigator.clipboard?.writeText) {
        navigator.clipboard.writeText(text)
          .then(() => showCopiedState())
          .catch(selectIdeasExport);
        return;
      }
      selectIdeasExport();
    }

    function showCopiedState() {
      copyIdeasButton.textContent = "Copied";
      window.setTimeout(() => {
        copyIdeasButton.textContent = "Copy JSON";
      }, 1400);
    }

    function selectIdeasExport() {
      ideasExportJSON.focus();
      ideasExportJSON.select();
    }

    function slugify(value) {
      return String(value || "")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "idea";
    }

    function setViewMode(mode) {
      const showTimeline = mode === "timeline";
      const showIdeas = mode === "ideas";
      app.classList.toggle("view-map", !showTimeline && !showIdeas);
      app.classList.toggle("view-timeline", showTimeline);
      app.classList.toggle("view-ideas", showIdeas);
      mapViewButton.classList.toggle("is-active", !showTimeline && !showIdeas);
      timelineViewButton.classList.toggle("is-active", showTimeline);
      ideasViewButton.classList.toggle("is-active", showIdeas);
      mapViewButton.setAttribute("aria-pressed", String(!showTimeline && !showIdeas));
      timelineViewButton.setAttribute("aria-pressed", String(showTimeline));
      ideasViewButton.setAttribute("aria-pressed", String(showIdeas));
      if (!showTimeline && !showIdeas) {
        requestAnimationFrame(() => refreshMapLayout());
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

    function refreshMapLayout(options = {}) {
      map.invalidateSize({ animate: false });
      if (options.fitBounds) {
        fitTripBounds();
      }
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
      return tagColor(stopTags(stop)[0]);
    }

    function tagColor(tag) {
      const normalized = normalizeTag(tag);
      return tagColors[normalized] || segmentColors[hashString(normalized) % segmentColors.length];
    }

    function normalizeTag(tag) {
      return String(tag || "sightseeing")
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, "-")
        .replace(/^-+|-+$/g, "") || "sightseeing";
    }

    function stopTags(stop) {
      const tags = stop.tags
        .map((tag) => normalizeTag(tag))
        .filter(Boolean);
      return [...new Set(tags)];
    }

    function tagIconsHTML(stop) {
      return `<span class="tag-icon-list">${stopTags(stop).map(tagIconHTML).join("")}</span>`;
    }

    function tagIconHTML(tag) {
      const normalized = normalizeTag(tag);
      return `
        <span class="tag-icon" style="--tag-color: ${tagColor(normalized)}" title="${escapeHTML(normalized)}" aria-label="${escapeHTML(normalized)}">
          ${tagIconGraphic(tagIcons[normalized] || "fallback", normalized)}
        </span>
      `;
    }

    function tagHTML(stop) {
      return `
        <p class="tag-list">
          ${stopTags(stop).map((tag) => `
            <span class="tag-pill" style="--tag-color: ${tagColor(tag)}">
              ${tagIconHTML(tag)}
              <span>${escapeHTML(tag)}</span>
            </span>
          `).join("")}
        </p>
      `;
    }

    function tagIconGraphic(icon, tag) {
      if (icon === "friends") {
        return svgIcon('<path d="M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z"></path><path d="M17 10a3 3 0 1 0 0-6"></path><path d="M3 21v-2a6 6 0 0 1 12 0v2"></path><path d="M15 21v-2a5 5 0 0 0-2-4"></path><path d="M18 21v-2a5 5 0 0 0-3-4"></path>');
      }
      if (icon === "mountain") {
        return svgIcon('<path d="M3 20h18L14 6l-4 8-2-3-5 9Z"></path><path d="M14 6l-2 6h5"></path>');
      }
      if (icon === "camera") {
        return svgIcon('<path d="M4 8h4l2-3h4l2 3h4v11H4V8Z"></path><circle cx="12" cy="14" r="3"></circle>');
      }
      if (icon === "tent") {
        return svgIcon('<path d="M3 20 12 4l9 16H3Z"></path><path d="M12 4v16"></path><path d="M12 20l4-7"></path>');
      }
      if (icon === "home") {
        return svgIcon('<path d="M4 11 12 4l8 7v9H4v-9Z"></path><path d="M9 20v-6h6v6"></path><path d="M17 5l.6 1.4L19 7l-1.4.6L17 9l-.6-1.4L15 7l1.4-.6L17 5Z"></path>');
      }
      if (icon === "route") {
        return svgIcon('<path d="M5 19c6-7 8 1 14-6"></path><circle cx="5" cy="19" r="2"></circle><circle cx="19" cy="13" r="2"></circle><path d="M9 5h6"></path><path d="m13 3 2 2-2 2"></path>');
      }
      return `<span class="tag-icon-fallback" aria-hidden="true">${escapeHTML(tag.slice(0, 2).toUpperCase())}</span>`;
    }

    function svgIcon(paths) {
      return `<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">${paths}</svg>`;
    }

    function markerMetricsForDays(days) {
      const safeDays = Math.max(1, Number(days) || 1);
      const weight = Math.log2(safeDays);
      return {
        size: Math.round(clamp(30 + weight * 7, 30, 58)),
        fontSize: `${clamp(12 + weight * 2.4, 12, 20).toFixed(1)}px`
      };
    }

    function clamp(value, min, max) {
      return Math.min(max, Math.max(min, value));
    }

    function colorWithAlpha(color, alpha) {
      const hex = String(color || "").replace("#", "");
      if (!/^[0-9a-f]{6}$/i.test(hex)) {
        return `rgba(0, 122, 120, ${alpha})`;
      }
      const red = parseInt(hex.slice(0, 2), 16);
      const green = parseInt(hex.slice(2, 4), 16);
      const blue = parseInt(hex.slice(4, 6), 16);
      return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
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

    function formatAxisDate(date) {
      return `${monthLabels[date.getUTCMonth()]} ${String(date.getUTCDate()).padStart(2, "0")}`;
    }

    function formatWeekday(date) {
      return weekdayLabels[date.getUTCDay()] || "";
    }

    function formatDurationDays(days) {
      const dayCount = Math.max(1, Number(days) || 1);
      return `${dayCount} day${dayCount === 1 ? "" : "s"}`;
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

    function noteHTML(value) {
      const text = String(value ?? "").replace(/<br\\s*\\/?>/gi, "\\n");
      return text
        .split(/((?:https?:\\/\\/|www\\.)[^\\s<>"']+)/gi)
        .map((part) => {
          if (!/^(?:https?:\\/\\/|www\\.)/i.test(part)) {
            return escapeHTML(part);
          }

          let urlText = part;
          let suffix = "";
          while (/[.,;:!?)]$/.test(urlText)) {
            suffix = urlText.slice(-1) + suffix;
            urlText = urlText.slice(0, -1);
          }
          if (!urlText) {
            return escapeHTML(part);
          }

          const href = /^www\\./i.test(urlText) ? `https://${urlText}` : urlText;
          return `<a href="${escapeHTML(href)}" target="_blank" rel="noopener noreferrer">${escapeHTML(urlText)}</a>${escapeHTML(suffix)}`;
        })
        .join("");
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
