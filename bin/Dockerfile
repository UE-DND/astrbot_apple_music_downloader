FROM debian:stable-slim

ENV args=""

COPY ./rootfs /app/rootfs
COPY ./wrapper /app
RUN chmod +x /app/wrapper
WORKDIR /app

CMD ["bash", "-c", "/app/wrapper $args"]

EXPOSE 10020 20020 30020
