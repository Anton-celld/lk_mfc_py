# Услуги МФЦ (студенческий сервис). Сборка: docker build -t mfc .   Проверки: docker build --target test -t mfc-test . && docker run --rm mfc-test
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt requirements-soap.txt ./
ARG WITH_SOAP=1
RUN if [ "$WITH_SOAP" = "1" ]; then pip install -r requirements-soap.txt; else pip install -r requirements.txt; fi
COPY lk ./lk
COPY static ./static
COPY portal_run.py ./

FROM base AS test
RUN pip install pytest pytest-asyncio
COPY tests ./tests
COPY pytest.ini ./
# tests/test_lk.py и tests/e2e.py из архива требуют модуль mock1c, которого в нём нет: запускаются проверки входа из кабинета
CMD ["python", "-m", "pytest", "-q", "tests/test_portal_sso.py"]

FROM base AS runtime
# настройки задаются переменными окружения (см. .env.example), config.json в образ не кладётся
ENV MFC_HOST=0.0.0.0 MFC_PORT=8091 MFC_BASE_PATH=/mfc MFC_DATA_DIR=/data
RUN useradd --system --uid 10001 --home-dir /app mfc && mkdir /data && chown mfc /data
USER mfc
VOLUME /data
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s%s/health' % (os.environ['MFC_PORT'], os.environ.get('MFC_BASE_PATH', '')), timeout=3)"
CMD ["python", "portal_run.py"]
