FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml README.md LICENSE ./
COPY norn/ ./norn/
COPY the-norn-machine/backend/ ./the-norn-machine/backend/
COPY the-norn-machine/frontend/ ./the-norn-machine/frontend/
RUN python -m pip wheel --no-deps --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NORN_DATA_DIR=/data
COPY --from=builder /wheels/ /wheels/
RUN python -m pip install --no-cache-dir --no-deps /wheels/*.whl \
    && rm -rf /wheels \
    && useradd --create-home --uid 10001 norn \
    && mkdir /data && chown norn /data
USER norn
WORKDIR /home/norn
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=2).read()"
# Consented records live in /data; mount a volume there to keep them.
VOLUME ["/data"]
CMD ["norn", "serve", "--mock", "--host", "0.0.0.0", "--port", "8765", "--allowed-host", "127.0.0.1:8765", "--allowed-host", "localhost:8765", "--log-format", "json"]
