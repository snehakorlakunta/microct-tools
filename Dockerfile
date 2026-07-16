# Dashboard/registry container (no GPU compute).
# For segmentation, run the worker on a GPU host with torch+nnunetv2 installed,
# or extend this image with the [seg] extra + an NVIDIA base image.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
RUN pip install --no-cache-dir -e .

ENV MICROCT_HOST=0.0.0.0 MICROCT_PORT=8000
EXPOSE 8000

# Override with `command: microct-worker` for the worker service.
CMD ["microct-web"]
