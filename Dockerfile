# Incident Resolution Copilot: container image for Railway (or any Docker host).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Railway sets $PORT; default to 8501 when run elsewhere.
EXPOSE 8501
# exec makes streamlit the main process, so Railway's stop signal shuts it down cleanly.
CMD ["sh", "-c", "exec streamlit run streamlit_app.py --server.port ${PORT:-8501} --server.address 0.0.0.0"]
