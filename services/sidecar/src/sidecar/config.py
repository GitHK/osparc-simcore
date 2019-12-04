import logging
import os

SERVICES_MAX_NANO_CPUS = os.environ.get("SIDECAR_SERVICES_MAX_NANO_CPUS", 4 * pow(10, 9))
SERVICES_MAX_MEMORY_BYTES = os.environ.get("SIDECAR_SERVICES_MAX_MEMORY_BYTES", 2 * pow(1024, 3))
SERVICES_TIMEOUT_SECONDS = os.environ.get("SIDECAR_SERVICES_TIMEOUT_SECONDS", 20*60)
SWARM_STACK_NAME = os.environ["SWARM_STACK_NAME"]

SIDECAR_LOGLEVEL = getattr(
    logging,
    name=os.environ.get("SIDECAR_LOGLEVEL", "WARNING").upper(),
    default=logging.WARNING)

logging.basicConfig(level=SIDECAR_LOGLEVEL)
logging.getLogger('sqlalchemy.engine').setLevel(SIDECAR_LOGLEVEL)
logging.getLogger('sqlalchemy.pool').setLevel(SIDECAR_LOGLEVEL)
