"""Routendienst: Geocoding und Routenberechnung hinter austauschbaren Provider-Schnittstellen.

``providers`` kapselt die Dienste (Nominatim, OSRM, Valhalla); der Connector ``jarvis.connectors.routing``
macht daraus die Agent-Werkzeuge. Ein weiterer Anbieter (Google Maps, Mapbox …) implementiert nur
``Geocoder`` bzw. ``RoutingProvider`` und wird in ``build_router``/``build_geocoder`` eingehängt.
"""
