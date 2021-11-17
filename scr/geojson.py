from ipyleaflet import Map, basemaps, Circle, WidgetControl, GeoJSON
from ipywidgets import IntSlider, ColorPicker, jslink

geojsonStructure = {
    "type": "FeatureCollection",
    "features": []
}
timeBudget = 40
m = Map(basemap=basemaps.OpenStreetMap.Mapnik, center=(39.963596, -83.000944), zoom=11)
slider = IntSlider(description='Time budget:', min=5, max=120, value=30)
for receivingStopID, ODRecord in results.items():
    radius = int(min((timeBudget * 60 - ODRecord["timeSC"]) * walkingSpeed, walkingDistanceLimit))
    if radius <= 0:
        continue
    singleFeature = {
        "type": "Feature",
        "properties": {
            "radius": radius
        },
        "geometry": {
            "type": "Point",
            "coordinates": [float(ODRecord["stop_lat"]), float(ODRecord["stop_lon"])]
        }
    }
    geojsonStructure["features"].append(singleFeature)

# print((geojsonStructure))
    
geojsonLayer = GeoJSON(
    data=geojsonStructure,
    style={'fillOpacity' : 1, "fillColor": "green", "radius": 700}
)

m.add_layer(geojsonLayer)