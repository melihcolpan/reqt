# The reqstorm command in a container:
#   docker run --rm -v "$PWD:/data" ghcr.io/melihcolpan/reqstorm urls.txt -o results.jsonl
# Files are read from and written to /data, the working directory.

FROM python:3.13-slim AS build
ARG VERSION
COPY dist/ /dist/
RUN python -m venv /venv \
 && /venv/bin/pip install --no-cache-dir "$(ls /dist/reqstorm-*.whl)[socks]" \
 && /venv/bin/reqstorm --version

FROM python:3.13-slim
LABEL org.opencontainers.image.title="reqstorm" \
      org.opencontainers.image.description="Send thousands of HTTP requests with rate limits, retries, progress and logs" \
      org.opencontainers.image.url="https://reqstorm.github.io" \
      org.opencontainers.image.source="https://github.com/melihcolpan/reqstorm" \
      org.opencontainers.image.licenses="MIT"
COPY --from=build /venv /venv
ENV PATH="/venv/bin:$PATH" PYTHONUNBUFFERED=1
RUN useradd --create-home --uid 1000 reqstorm && mkdir /data && chown reqstorm /data
USER reqstorm
WORKDIR /data
ENTRYPOINT ["reqstorm"]
CMD ["--help"]
