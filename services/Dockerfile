FROM python:3.11-slim

ARG SERVICE_NAME
ARG REQUIREMENTS_FILE=services/requirements-runtime.txt
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/shared

WORKDIR /app
COPY ${REQUIREMENTS_FILE} ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY shared ./shared
COPY services/${SERVICE_NAME}/app ./app

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
