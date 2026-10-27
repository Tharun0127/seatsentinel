FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# Dev requirements are installed too so tests and the load test run inside
# the same container as the API.
RUN pip install -r requirements.txt -r requirements-dev.txt

COPY . .
RUN sed -i 's/\r$//' /app/entrypoint.sh

EXPOSE 8000
CMD ["sh", "/app/entrypoint.sh"]
