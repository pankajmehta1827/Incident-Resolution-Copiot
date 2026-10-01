# Incident Resolution Copilot: container image for Railway (or any Docker host).
# The same image also runs the ServiceNow mock when APP_ROLE=servicenow-mock (see start.sh).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
COPY servicenow_mock/requirements.txt servicenow_mock/requirements.txt
RUN pip install -r requirements.txt -r servicenow_mock/requirements.txt

COPY . .
# Guard against Windows line endings breaking the shell script.
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

# Railway sets $PORT; defaults are 8501 (copilot) and 8600 (mock) when run elsewhere.
EXPOSE 8501 8600
CMD ["./start.sh"]
