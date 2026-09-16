FROM node:22-slim@sha256:53ada149d435c38b14476cb57e4a7da73c15595aba79bd6971b547ceb6d018bf

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 git ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/asys
COPY asys-runtime/asys_runtime ./asys-runtime/asys_runtime
COPY asys-runtime/tools ./asys-runtime/tools
COPY asys-runtime/javascript ./asys-runtime/javascript
COPY asys-runtime/LICENSE.multiagent ./asys-runtime/LICENSE.multiagent
COPY LICENSE ./LICENSE
RUN install -d -o node -g node -m 0700 /var/lib/asys/runtime
ENV PYTHONDONTWRITEBYTECODE=1
ENV ASYS_RUNTIME_HEALTH_FILE=/tmp/asys-runtime-health
USER node
STOPSIGNAL SIGTERM
ENTRYPOINT ["python3", "/opt/asys/asys-runtime/tools/asys-runtime"]
CMD ["run", "/etc/asys-runtime/runtime.json", "--root", "/var/lib/asys/runtime"]
HEALTHCHECK --interval=3s --timeout=2s --retries=3 CMD ["python3", "/opt/asys/asys-runtime/tools/asys-healthcheck", "/tmp/asys-runtime-health"]
