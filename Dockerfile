# CodexProbe runtime image.
#
#   docker build -t codex-probe .
#   docker run --rm -p 8135:8135 \
#       -e OPENAI_API_KEY \
#       -v "$(pwd)/examples:/app/examples:ro" \
#       -v "$(pwd)/logs:/app/logs" \
#       codex-probe --config /app/examples/openai_backend.json
#
# See compose.yaml for a ready-made topology (CodexProbe + a local Ollama
# backend) and docs/architecture.md for why a container is worth having
# at all: it pins the exact Python/dependency versions CodexProbe was
# tested with, so "it works on my machine" isn't a variable in someone
# else's reproduction of an experiment.
FROM python:3.12-slim

WORKDIR /app

# Copy only what the build needs before the source tree, so dependency
# installation is cached across rebuilds that only touch application code.
COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir .

RUN mkdir -p /app/logs

EXPOSE 8135

ENTRYPOINT ["codex-probe"]
CMD ["--config", "/app/examples/openai_backend.json"]
