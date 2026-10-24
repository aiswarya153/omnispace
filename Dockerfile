FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt

FROM base AS verified
COPY main.py index.html test_main.py ./
RUN python -m unittest test_main -q

FROM base AS runtime
RUN useradd --uid 10001 --create-home appuser
COPY --from=verified /app/main.py /app/index.html ./
ENV APP_ENV=production PORT=8000
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/readyz', timeout=3)"]
CMD ["python", "main.py"]