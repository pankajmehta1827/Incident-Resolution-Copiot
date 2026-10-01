#!/bin/sh
# Container entry point. One image, two roles:
#   APP_ROLE unset (default)    -> the Incident Copilot (Streamlit)
#   APP_ROLE=servicenow-mock    -> the ServiceNow mock (Table API + incident list)
# so a second Railway service only needs the APP_ROLE variable, not a different Dockerfile.
set -e

if [ "$APP_ROLE" = "servicenow-mock" ]; then
    export MOCK_SN_UI_AUTH="${MOCK_SN_UI_AUTH:-1}"          # deployed: the UI needs the login too
    export MOCK_SN_DB="${MOCK_SN_DB:-/data/incident.db}"     # mount a volume at /data to keep changes
    mkdir -p "$(dirname "$MOCK_SN_DB")"
    # 0.0.0.0: reachable through Railway's public domain and its private network (IPv4).
    exec uvicorn servicenow_mock.server:app --host 0.0.0.0 --port "${PORT:-8600}"
fi

exec streamlit run streamlit_app.py --server.port "${PORT:-8501}" --server.address 0.0.0.0
