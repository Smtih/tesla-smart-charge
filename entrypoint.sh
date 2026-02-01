#!/bin/sh
# Ensure writable files exist (Docker bind mounts require them)
touch /app/token.json /app/scheduled_charges.json /app/smart-charge.log
exec "$@"
