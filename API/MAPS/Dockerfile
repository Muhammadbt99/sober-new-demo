FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Runtime user (the app only needs to write sober.db inside /app).
# /app is created and chowned HERE, before the COPY: `COPY --chown` only
# chowns the copied entries, not the destination directory, so a WORKDIR-created
# /app would stay root:root and the `sober` user could not create sober.db.
# See moby/moby#35783.
RUN groupadd --gid 10001 sober \
    && useradd --uid 10001 --gid sober --create-home sober \
    && mkdir -p /app \
    && chown sober:sober /app

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=sober:sober . .

USER sober

# SQLite lives outside the app directory so the image can be mounted read-only.
ENV SOBER_DB_PATH=/tmp/sober.db

EXPOSE 5000

# The healthcheck must follow $PORT: the CMD binds ${PORT:-5000}, so a
# hard-coded 5000 would mark the container unhealthy on any platform that
# assigns a different port.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,sys,urllib.request; port=os.environ.get('PORT','5000'); sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+port+'/health', timeout=4).status == 200 else 1)"

CMD ["sh", "-c", "gunicorn app:app --workers 1 --threads 4 --timeout 60 --access-logfile - --error-logfile - --bind 0.0.0.0:${PORT:-5000}"]
