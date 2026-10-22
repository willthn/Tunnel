# Backend WattShift pour Aiven Runtime (A : Data 1). Construit depuis la branche main sur GitHub.
FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.12.20 /uv /uvx /bin/

ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app

# Dépendances d'abord (couche mise en cache tant que uv.lock ne change pas)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)"

# Un seul processus (docs/DEPLOY.md) : en DATA_MODE=fake le plan vit en mémoire.
CMD ["sh", "-c", "exec /app/.venv/bin/fastapi run app/main.py --host 0.0.0.0 --port ${PORT:-8080}"]
