FROM node:22-alpine AS web
WORKDIR /build
COPY apps/web/package*.json ./
RUN npm ci
COPY apps/web/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY apps/api/ ./apps/api/
COPY data/ ./data/
COPY --from=web /build/dist ./apps/web/dist
EXPOSE 8000
CMD ["uvicorn","apps.api.app.main:app","--host","0.0.0.0","--port","8000"]
