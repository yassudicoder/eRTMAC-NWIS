# Build the dashboard, then hand the static bundle to the backend image.
FROM node:20-alpine AS build

WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM busybox:stable
COPY --from=build /app/dist /dist
