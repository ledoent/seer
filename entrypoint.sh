#!/bin/bash
set -e

echo "=== Seer Container Startup ==="

# Run database migrations if not explicitly disabled
if [ "${SKIP_MIGRATIONS}" != "true" ] && [ "${SKIP_MIGRATIONS}" != "1" ]; then
    echo "Running database migrations..."

    # Set Flask app for migrations
    export FLASK_APP="src.seer.app:start_app()"

    # Run alembic migrations via flask-migrate.
    # `heads` (plural) is required: the kencove fork has a divergent migration
    # tree (multiple heads, e.g., the `kencove_001` branch from upstream).
    # `flask db upgrade` without `heads` fails with:
    #   Multiple head revisions are present for given argument 'head'
    # which masquerades as "migrations failed" and lets the container start
    # with an empty DB, producing SEER-5 (`relation "run_state" does not
    # exist`) when celery-beat fires. Idempotent under all conditions.
    if flask db upgrade heads; then
        echo "Database migrations completed successfully."
    else
        echo "WARNING: Database migrations failed. Container will continue starting."
        echo "This may cause runtime errors if tables are missing."
        # Don't exit - let the app try to start so we can see errors in logs
    fi
else
    echo "Skipping database migrations (SKIP_MIGRATIONS=${SKIP_MIGRATIONS})"
fi

echo "Starting supervisord..."
exec /usr/bin/supervisord -c /etc/supervisord.conf
