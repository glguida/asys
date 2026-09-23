FROM asys-runtime:dev
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3-jsonschema \
 && rm -rf /var/lib/apt/lists/*
COPY asys-swarm/asys_swarm /opt/asys/asys-swarm/asys_swarm
RUN install -d -o node -g node -m 0700 /var/lib/asys-swarm
ENV PYTHONPATH=/opt/asys/asys-swarm:/opt/asys/asys-runtime
ENV ASYS_SWARM_HEALTH_FILE=/tmp/asys-swarm-health
WORKDIR /opt/asys/asys-swarm
USER node
ENTRYPOINT ["python3", "-m", "asys_swarm"]
CMD ["--root", "/var/lib/asys/runtime", "--state", "/var/lib/asys-swarm", "--workspace", "/var/lib/asys/workspace", "--package", "/opt/asys/swarm-package", "--channel", "swarm"]
HEALTHCHECK --interval=3s --timeout=2s --retries=3 CMD ["python3", "/opt/asys/asys-runtime/tools/asys-healthcheck", "/tmp/asys-swarm-health"]
