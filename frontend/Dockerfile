# 构建阶段
FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci
COPY . .
RUN npm run build

# 生产阶段：输出静态文件供后端托管
FROM alpine:3.19
WORKDIR /app
COPY --from=build /app/dist ./dist
