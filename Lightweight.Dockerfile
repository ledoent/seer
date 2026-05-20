FROM python:3.11-slim

# Allow statements and log messages to immediately appear in the Cloud Run logs
ARG TEST
ARG DEV
ENV PYTHONUNBUFFERED True

ARG PORT
ENV PORT=$PORT

ENV APP_HOME /app
WORKDIR $APP_HOME

# Install libpq-dev for psycopg & git for 'sentry-sdk[flask] @ git://' in requirements.txt
RUN apt-get update && \
    apt-get install -y \
    supervisor \
    curl \
    libpq-dev \
    ripgrep \
    git && \
    rm -rf /var/lib/apt/lists/*

# Install uv for faster dependency management
RUN pip install uv

# Install td-grpc-bootstrap
RUN curl -L https://storage.googleapis.com/traffic-director/td-grpc-bootstrap-0.16.0.tar.gz | tar -xz && \
    mv td-grpc-bootstrap-0.16.0/td-grpc-bootstrap /usr/local/td-grpc-bootstrap && \
    rm -rf td-grpc-bootstrap-0.16.0

COPY pyproject.toml .

# Install dependencies with uv (faster than pip)
COPY setup.py requirements.txt ./
# pytorch without gpu (increase timeout for large downloads)
ENV UV_HTTP_TIMEOUT=300
RUN uv pip install --system torch==2.2.0 --index-url https://download.pytorch.org/whl/cpu
RUN uv pip install --system -r requirements.txt

# Aider has a sprawling dep graph (litellm, prompt-toolkit, gitpython, …)
# that pins different versions of packages we already use. Install it in
# a dedicated venv so its deps don't fight ours, then symlink the binary
# onto PATH so `subprocess.run(["aider", ...])` in src/seer/automation/harness/
# Just Works. Only loaded when AUTOFIX_HARNESS=aider; the default
# AUTOFIX_HARNESS=builtin path never touches this venv.
RUN python -m venv /opt/aider-venv \
 && /opt/aider-venv/bin/pip install --no-cache-dir aider-chat==0.65.0 \
 && ln -s /opt/aider-venv/bin/aider /usr/local/bin/aider \
 && aider --version

# Copy model files (assuming they are in the 'models' directory)
COPY models/ models/
# Copy scripts
COPY celeryworker.sh celerybeat.sh gunicorn.sh grpcserver.sh flower.sh entrypoint.sh ./
RUN chmod +x ./celeryworker.sh ./celerybeat.sh ./gunicorn.sh ./grpcserver.sh ./flower.sh ./entrypoint.sh

# Copy source code
COPY src/ src/
COPY .test_durations .


# Copy the supervisord.conf file into the container
COPY supervisord.conf /etc/supervisord.conf

# Ignore dependencies, as they are already installed and docker handles the caching
# this skips annoying rebuilds where requirements would technically be met anyways.
RUN uv pip install --system -e . --no-deps

ENV FLASK_APP=src.seer.app:start_app()

# Supports sentry releases
ARG SEER_VERSION_SHA
ENV SEER_VERSION_SHA ${SEER_VERSION_SHA}
ARG SENTRY_ENVIRONMENT=production
ENV SENTRY_ENVIRONMENT ${SENTRY_ENVIRONMENT}

# entrypoint.sh runs `flask db upgrade heads` then execs supervisord. Without
# this wrapper, fresh deploys land with an empty seer-db and the autofix
# celery-beat task SEER-5 fires "relation \"run_state\" does not exist". Set
# SKIP_MIGRATIONS=1 to bypass (e.g., for compose dev with an externally-managed
# database). The CMD below is purely informational — entrypoint.sh execs
# supervisord with a hardcoded config path; CMD args are not forwarded.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisord.conf"]

# TCP-listen probe rather than HTTP /health/ready — readiness tries to load the
# ML embeddings (issue_severity_v0/...) which the open fork doesn't ship yet
# (ROADMAP item #1), so the HTTP probe would always return 500 and mark the
# container unhealthy even when gunicorn is serving fine. start-period gives
# gunicorn time to bind 9091 (worker boot ~15-25s once dependencies+celery init).
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
  CMD python -c "import socket; s=socket.socket(); s.settimeout(3); s.connect(('localhost', 9091))" || exit 1
