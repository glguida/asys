FROM asys-runtime:dev
USER root
WORKDIR /opt/asys
COPY asys-inference/components/vendor/dcomp-component ./asys-inference/components/vendor/dcomp-component
COPY asys-inference/components/protocol/component ./asys-inference/components/protocol/component
COPY asys-inference/components/protocol/provider ./asys-inference/components/protocol/provider
COPY asys-workers/package.json asys-workers/package-lock.json asys-workers/.npmrc ./asys-workers/
WORKDIR /opt/asys/asys-workers
RUN npm ci --omit=dev
COPY asys-workers/src ./src
COPY asys-workers/gen ./gen
COPY asys-workers/extensions /opt/asys/asys-workers/extensions
COPY asys-workers/tools ./tools
COPY python/asys/system_agents /opt/asys/python/asys/system_agents
ENV NODE_ENV=production
USER node
ENTRYPOINT ["node", "/opt/asys/asys-workers/src/main.mjs"]
CMD []
HEALTHCHECK --interval=3s --timeout=2s --retries=3 CMD ["python3", "/opt/asys/asys-runtime/tools/asys-healthcheck", "/tmp/asys-runtime-health", "/tmp/asys-workers-health"]
