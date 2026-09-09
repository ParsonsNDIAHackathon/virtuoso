FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

ARG BUILD=dev
ENV FUSION_BUILD=$BUILD
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY fusion ./fusion
# data/ is git-ignored and bind-mounted by docker-compose; create the mount point so a fresh clone builds.
RUN mkdir -p /app/data

EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]
