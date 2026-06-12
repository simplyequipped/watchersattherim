"""watchersattherim application protocol - the custom field-type identifiers we
stamp into LXMF's FIELD_CUSTOM_TYPE to namespace our payloads.

Pure data: no imports, no Reticulum. These are the wire contract that the
monitor, collector, and query client must agree on.
"""

APP_OBS = b"watchersattherim/observations/1"    # telemetry batch (monitor -> collector)
APP_QUERY = b"watchersattherim/query/1"          # query envelope (client -> collector)
APP_REPLY = b"watchersattherim/reply/1"          # query reply (collector -> client)
