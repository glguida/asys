FROM asys-workers:dev
COPY . /opt/world
ENV ASYS_WORLD_HEALTH_FILE=/tmp/asys-world-health
ENTRYPOINT ["python3", "/opt/world/serve.py"]
CMD ["--root", "/var/lib/asys/runtime", "--channel", "world"]
HEALTHCHECK --interval=3s --timeout=2s --retries=3 CMD ["python3", "/opt/asys/asys-runtime/tools/asys-healthcheck", "/tmp/asys-world-health"]
