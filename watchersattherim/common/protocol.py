"""watchersattherim application protocol - the custom field-type identifiers we
stamp into LXMF's FIELD_CUSTOM_TYPE to namespace our payloads.

Pure data: no imports, no Reticulum. These are the wire contract that the
monitor, collector, and query client must agree on.
"""

APP_OBS = b"watchersattherim/observations"    # telemetry batch (monitor -> collector)
APP_QUERY = b"watchersattherim/query"          # query envelope (client -> collector)
APP_REPLY = b"watchersattherim/reply"          # query reply (collector -> client)
