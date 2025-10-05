FROM powshield/pow-shield:latest

# Rimuove il vecchio file .env, se presente
RUN rm -f /usr/src/pow-shield/.env

# Installa curl/traceroute per debug
RUN apk add --no-cache curl traceroute

# Imposta la working directory
WORKDIR /usr/src/pow-shield

# Comando di avvio
CMD ["node", "bin/server.js"]
