FROM python:3.12-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY app ./app
COPY data ./data

RUN pip install --no-cache-dir .

# Cloud Run 會注入 PORT（預設 8080）
ENV PORT=8080
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
