# QorTime official referee service (verified uploads). Build context = repo root.
# The signing key is NOT in the image: it comes from the QORTIME_REFEREE_SEED service variable at runtime.
FROM python:3.12-slim
ARG QORCAP_SHA=44e2af17566b567857e44e88f09626d02cf2bc87
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY qor_receipt ./qor_receipt
RUN pip install --no-cache-dir ".[referee,vision]"
# Pinned QorCap sidecar = the exact scorebug + end-of-game-menu readers QorCap ships (fail-closed if missing).
RUN curl -fsSL "https://github.com/ConWan30/qorcap/archive/${QORCAP_SHA}.tar.gz" | tar -xz -C /opt \
 && mv "/opt/qorcap-${QORCAP_SHA}/sidecar" /opt/qorcap-sidecar && rm -rf "/opt/qorcap-${QORCAP_SHA}" \
 && test -f /opt/qorcap-sidecar/qorcap_reader.py
ENV QORCAP_SIDECAR=/opt/qorcap-sidecar \
    QORTIME_REFEREE_WORKDIR=/tmp/qortime-referee \
    PYTHONUNBUFFERED=1
EXPOSE 8080
CMD ["sh", "-c", "uvicorn qor_receipt.referee_service.app:app --host 0.0.0.0 --port ${PORT:-8080} --timeout-keep-alive 75"]
