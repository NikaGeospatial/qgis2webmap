"""Processing integration -- see docs/architecture.md.

The algorithm is a thin adapter over the same reader, writer and exporters the
dialog uses. It is deliberately not a second export implementation: qgis2web's
dialog and its algorithm drifted apart, and that is the failure this avoids.
"""
