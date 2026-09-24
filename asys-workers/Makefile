.PHONY: build
build:
	docker build -f ../asys-runtime/Dockerfile -t asys-runtime:dev ..
	docker build -f worlds/Dockerfile -t asys-worlds:dev ..
	docker build -f Dockerfile -t asys-workers:dev ..
	docker build -t asys-env-default:dev env/default
