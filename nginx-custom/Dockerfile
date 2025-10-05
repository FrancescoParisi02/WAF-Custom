FROM nginx:stable-alpine-slim

# Copia la configurazione personalizzata
COPY conf/nginx.conf /etc/nginx/nginx.conf
COPY conf/conf.d/default.conf /etc/nginx/conf.d/default.conf

# Installa curl e traceroute per debug
RUN apk add --no-cache curl traceroute
