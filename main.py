import os
import tkinter as tk
from tkinter import ttk, messagebox

import tkintermapview
import openrouteservice
from dotenv import load_dotenv
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry

load_dotenv()
ORS_API_KEY = os.getenv("ORS_API_KEY", "")

# ORS routing profiles available in the free tier.
# Key   = human-readable label shown in the GUI dropdown
# Value = ORS profile identifier sent to the API
TRAVEL_PROFILES: dict[str, str] = {
    "Car":      "driving-car",
    "Cycling":  "cycling-regular",
    "Walking":  "foot-walking",
}

DEFAULT_TRAVEL_MINUTES = 60   # default value for the time spinbox
MAP_CENTER_LAT = 50.0         # initial map centre – Czech Republic
MAP_CENTER_LON = 15.5
MAP_INITIAL_ZOOM = 8

# Rough conversion factor: 1 degree ≈ 111 km at mid-latitudes.
# Used only for the area estimate label; NOT used in geometry calculations.
KM_PER_DEGREE = 111.0

def fetch_isochrone( client: openrouteservice.Client, lon: float, lat: float, profile: str, minutes: int) -> BaseGeometry:
    response = client.isochrones(  locations=[[lon, lat]], profile=profile, range=[minutes * 60], range_type="time")# type: ignore[attr-defined]
    geojson_feature = response["features"][0]
    return shape(geojson_feature["geometry"])


def resolve_location(client: openrouteservice.Client, text: str) -> tuple[float, float]:
    text = text.strip()

    # Attempt to parse as 'lat, lon' – two comma-separated floats
    parts = text.split(",")
    if len(parts) == 2:
        try:
            lat = float(parts[0].strip())
            lon = float(parts[1].strip())
            return lon, lat
        except ValueError:
            pass  # not numeric → fall through to geocoding

    # Geocode the address using ORS / Pelias
    result = client.pelias_search(text=text) # type: ignore[attr-defined]
    coordinates = result["features"][0]["geometry"]["coordinates"]
    return coordinates[0], coordinates[1]  # (lon, lat)

def build_person_block(parent: tk.Widget, label: str, profiles: list[str], default_minutes: int) -> dict:
    ttk.Label(parent, text=label, font=("TkDefaultFont", 10, "bold")).pack(anchor=tk.W, pady=(6, 0))

    ttk.Label(parent, text='Address  –or–  "lat, lon"').pack(anchor=tk.W)
    address_var = tk.StringVar()
    ttk.Entry(parent, textvariable=address_var).pack(fill=tk.X)

    ttk.Label(parent, text="Travel mode", foreground="gray").pack(anchor=tk.W, pady=(4, 0))
    mode_var = tk.StringVar(value=profiles[0])
    ttk.Combobox(parent, textvariable=mode_var, values=profiles, state="readonly").pack(fill=tk.X)

    ttk.Label(parent, text="Max travel time (minutes)", foreground="gray").pack(anchor=tk.W, pady=(4, 0))
    time_var = tk.IntVar(value=default_minutes)
    ttk.Spinbox(parent, from_=10, to=180, increment=5, textvariable=time_var, width=6).pack(fill=tk.X)

    return {"address": address_var, "mode": mode_var, "time": time_var}

class CommuteCompassApp(tk.Tk):

    def __init__(self) -> None:
        super().__init__()
        self.title("Commute Compass  –  v0.1 prototype")
        self.geometry("1280x800")
        self.minsize(900, 600)

        self._build_sidebar()
        self._build_map()

    def _build_sidebar(self) -> None:
        sidebar = ttk.Frame(self, width=300, padding=12)
        sidebar.pack(side=tk.LEFT, fill=tk.Y)
        sidebar.pack_propagate(False)   # prevent shrinking to content

        # --- API key ---
        ttk.Label(sidebar, text="ORS API Key").pack(anchor=tk.W)
        self._api_key_var = tk.StringVar(value=ORS_API_KEY)
        ttk.Entry(sidebar, textvariable=self._api_key_var, show="●").pack(fill=tk.X)

        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=10)

        # --- Person A & Person B input blocks ---
        profile_labels = list(TRAVEL_PROFILES.keys())

        self._person_a = build_person_block(sidebar, label="Person A  –  Workplace", profiles=profile_labels, default_minutes=DEFAULT_TRAVEL_MINUTES)
        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=10)

        self._person_b = build_person_block(sidebar, label="Person B  –  Workplace", profiles=profile_labels, default_minutes=DEFAULT_TRAVEL_MINUTES)
        ttk.Separator(sidebar, orient="horizontal").pack(fill=tk.X, pady=10)

        # --- Action button ---
        ttk.Button(sidebar, text="▶  Calculate overlap",command=self._on_calculate).pack(fill=tk.X, ipady=4)

        # --- Status label ---
        self._status_var = tk.StringVar(value="Enter workplaces and press Calculate.")
        ttk.Label(sidebar, textvariable=self._status_var, wraplength=276, foreground="gray", justify=tk.LEFT).pack(anchor=tk.W, pady=(10, 0))

    def _build_map(self) -> None:
        self._map = tkintermapview.TkinterMapView(self, corner_radius=0)
        self._map.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)
        self._map.set_position(MAP_CENTER_LAT, MAP_CENTER_LON)
        self._map.set_zoom(MAP_INITIAL_ZOOM)

    def _on_calculate(self) -> None:
        self._set_status("Connecting to ORS API…")

        try:
            client = openrouteservice.Client(key=self._api_key_var.get())

            # Resolve addresses to coordinates
            self._set_status("Resolving locations…")
            lon_a, lat_a = resolve_location(client, self._person_a["address"].get())
            lon_b, lat_b = resolve_location(client, self._person_b["address"].get())

            # Fetch isochrones
            self._set_status("Fetching isochrone for Person A…")
            iso_a = fetch_isochrone( client, lon_a, lat_a, profile=TRAVEL_PROFILES[self._person_a["mode"].get()], minutes=self._person_a["time"].get(),)

            self._set_status("Fetching isochrone for Person B…")
            iso_b = fetch_isochrone( client, lon_b, lat_b, profile=TRAVEL_PROFILES[self._person_b["mode"].get()], minutes=self._person_b["time"].get(),)

            # Compute the geometric intersection
            self._set_status("Computing intersection…")
            overlap = iso_a.intersection(iso_b)

            # Render everything on the map
            self._draw_results(lon_a, lat_a, lon_b, lat_b, iso_a, iso_b, overlap)

        except Exception as exc:                         # broad catch for prototype stage
            messagebox.showerror("Error", str(exc))
            self._set_status(f"Error: {exc}")

    def _draw_results( self, lon_a: float, lat_a: float, lon_b: float, lat_b: float, iso_a, iso_b, overlap) -> None:
        self._map.delete_all_polygon()
        self._map.delete_all_marker()

        # Draw isochrones A (blue) & B (red)
        self._draw_geometry(iso_a, fill_color=None, outline_color="#2563eb", border_width=2) 
        self._draw_geometry(iso_b, fill_color=None, outline_color="#dc2626", border_width=2) 

        # Draw the overlap – green fill (may be Polygon OR MultiPolygon)
        if not overlap.is_empty:
            self._draw_geometry(overlap, fill_color="#16a34a", outline_color="#14532d", border_width=2)

        # Workplace markers
        self._map.set_marker(lat_a, lon_a, text="Person A  –  Workplace")
        self._map.set_marker(lat_b, lon_b, text="Person B  –  Workplace")

        # Re-centre the map on the midpoint of the two workplaces
        self._map.set_position((lat_a + lat_b) / 2, (lon_a + lon_b) / 2)

        # Rough area estimate (degrees² → km²)
        if not overlap.is_empty:
            area_km2 = overlap.area * (KM_PER_DEGREE ** 2)
            self._set_status(
                f"Done.\n"
                f"Overlap area ≈ {area_km2:.0f} km²\n"
                f"\n"
                f"🔵 Blue  = Person A reachable zone\n"
                f"🔴 Red   = Person B reachable zone\n"
                f"🟢 Green = Ideal area to live"
            )
        else:
            self._set_status(
                "Done – but no overlap found.\n"
                "Try increasing travel time or changing transport mode."
            )

    def _draw_geometry( self, geometry: BaseGeometry, fill_color, outline_color: str, border_width: int) -> None:
        if isinstance(geometry, Polygon):
            self._draw_single_polygon(geometry, fill_color, outline_color, border_width)
        elif isinstance(geometry, MultiPolygon):
            for part in geometry.geoms:
                self._draw_single_polygon(part, fill_color, outline_color, border_width)
        # Other geometry types (Point, LineString, …) are silently ignored;
        # they can appear as degenerate intersection artefacts.

    def _draw_single_polygon( self, polygon: Polygon, fill_color, outline_color: str, border_width: int) -> None:
        coords = [(lat, lon) for lon, lat in polygon.exterior.coords]
        self._map.set_polygon( coords, fill_color=fill_color, outline_color=outline_color, border_width=border_width)

    def _set_status(self, message: str) -> None:
        self._status_var.set(message)
        self.update_idletasks()

if __name__ == "__main__":
    app = CommuteCompassApp()
    app.mainloop()