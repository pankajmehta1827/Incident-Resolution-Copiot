# ServiceNow mock: container image for Railway (second service in the same project).
# Build context is the repository root (it needs the incident workbook to seed the database).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MOCK_SN_UI_AUTH=1 \
    MOCK_SN_DB=/data/incident.db

WORKDIR /app
COPY servicenow_mock/requirements.txt servicenow_mock/requirements.txt
RUN pip install -r servicenow_mock/requirements.txt

COPY servicenow_mock/ servicenow_mock/
COPY data/Incident/ data/Incident/

EXPOSE 8600
# 0.0.0.0: reachable through Railway's public domain and its private network (IPv4).
CMD ["sh", "-c", "exec uvicorn servicenow_mock.server:app --host 0.0.0.0 --port ${PORT:-8600}"]
