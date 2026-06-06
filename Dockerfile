FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# config.yaml is mounted at runtime (see docker-compose.yml)
ENTRYPOINT ["peugeot80"]
CMD ["run", "-c", "/config/config.yaml"]
