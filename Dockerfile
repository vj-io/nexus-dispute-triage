FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MODE=recorded \
    DATABASE_URL=sqlite:////data/nexus.db \
    AMD_BASE_URL=https://developer.amd.com.cn/radeon/api/v1 \
    AMD_MODEL=Gemma-4-31B-IT \
    AMD_SEND_MAX_TOKENS=0

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN useradd --create-home --uid 10001 nexus && mkdir -p /data && chown nexus /data
USER nexus
VOLUME ["/data"]

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/', timeout=4).status == 200 else 1)"
# Inside the container the server must listen on all interfaces; publish it to
# loopback only on the host: docker run -p 127.0.0.1:8000:8000 ...
# MODE=live in Docker requires ACCESS_TOKEN: without one the app answers the API with 503
# auth_not_configured to every non-loopback peer, and published-port traffic arrives from the bridge.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
